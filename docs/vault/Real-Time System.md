# Real-Time System

Sensybull delivers filing events to users the moment they're processed — no page refresh required. This note covers the full real-time stack: Redis subscriber, Socket.IO server, and client connection lifecycle.

---

## Overview

```
Redis (filing:new)
    │
    ▼
┌─────────────────────────┐
│  Subscriber Thread       │  ← Daemon thread, runs forever
│  (subscriber.py)         │
│                          │
│  Parse → Persist → Emit  │
└──────────┬──────────────┘
           │
     Socket.IO emit
           │
    ┌──────▼──────────┐
    │  Room: user:123  │ ──► Client A (watching AAPL)
    │  Room: user:456  │ ──► Client B (watching AAPL + TSLA)
    │  Room: public    │ ──► Client C (unauthenticated)
    └─────────────────┘
```

---

## The Subscriber Thread

**File:** `services/api/app/services/realtime/subscriber.py`

### Lifecycle

1. **Started once** on app startup (`start_subscriber()` in `app/__init__.py`)
2. Protected by a lock — safe to call multiple times (idempotent)
3. Runs as a **daemon thread** — dies when the main process exits
4. Subscribes to Redis channel `filing:new` and blocks forever waiting for messages

### Message Handler (`_handle_event`)

When a message arrives on `filing:new`:

```
1. Parse JSON payload
2. Check idempotency:
   └─ SELECT edgar_id FROM filing_event → skip if exists
3. Resolve company:
   ├─ Try ticker match first
   ├─ Fall back to CIK match
   └─ Auto-create Company if neither matches
4. Create database records:
   ├─ FilingEvent (core record)
   ├─ EventType × N (one per classified type)
   └─ Catalyst × M (one per extracted date)
5. Commit transaction
6. Fan out via Socket.IO:
   ├─ Find all users with this company in a watchlist
   ├─ Emit to room "user:{id}" for each
   └─ Emit to room "public" for all
7. Log: "Subscriber: stored + emitted edgar_id=... ticker=... tier=... users=N"
```

### Why a Thread (Not Async)?

Flask is synchronous. The subscriber needs to:
- Run SQLAlchemy queries (synchronous)
- Access the Flask app context (thread-local)
- Emit Socket.IO events (thread-safe in eventlet)

A daemon thread with `app.app_context()` is the simplest solution. [[Eventlet]] makes the blocking Redis subscription cooperative, so it doesn't prevent the main Flask thread from handling HTTP requests.

---

## Socket.IO Server

**File:** `services/api/app/services/realtime/socketio_setup.py`

### Configuration

```python
socketio = SocketIO(
    cors_allowed_origins="*",   # Matches Flask CORS config
    async_mode='eventlet',      # Use Eventlet for async
    logger=False,               # Quiet in production
    engineio_logger=False
)
```

### Namespace: `/feed`

All real-time events use the `/feed` namespace. This separates filing events from any future namespaces (e.g., `/chat`, `/alerts`).

### Connection Lifecycle

```
Client connects to /feed with auth={token: "eyJ..."}
    │
    ▼
Server: decode JWT
    ├─ Invalid → disconnect with error
    │
    ├─ Valid → join room "user:{user_id}"
    │          join room "public"
    │
    ▼
Server: fetch last 50 events for user's watchlist
         (Tier 1 and 2 only — skip routine)
    │
    ▼
Server: emit each as 'filing_event' (replay missed events)
    │
    ▼
Server: emit 'connected' confirmation
    │
    ▼
Client is now live — receives 'filing_event' in real-time
```

### Event Replay

When a client connects (or reconnects after a network drop), the server replays the last 50 significant events. This ensures users don't miss filings that arrived while they were disconnected.

**Why only Tier 1-2?** Tier 3 events are routine (bylaw amendments, Reg FD disclosures). Replaying all tiers would flood the client with noise. Users can always fetch the full history via REST.

### Ticker Subscription

Clients can subscribe to specific tickers for cross-watchlist monitoring:

```
Client emits: 'subscribe_ticker' { ticker: 'AAPL' }
Server: joins client to room "ticker:AAPL"
Server emits: 'subscribed' confirmation
```

This is separate from watchlist-based delivery. It's used for the search/explore flow where a user wants to monitor a company they haven't added to their watchlist yet.

---

## Room Architecture

```
Rooms:
├── user:abc-123     ← Authenticated user, gets watchlist events
├── user:def-456     ← Another authenticated user
├── public           ← Everyone, gets all events
└── ticker:AAPL      ← Optional, subscribed by specific clients
```

**Why rooms?** Socket.IO rooms are server-side groupings. Emitting to a room delivers the message to all sockets in that room without iterating all connections. This makes fan-out efficient:

```python
# O(1) per room — Socket.IO handles the fan-out internally
socketio.emit('filing_event', data, room=f'user:{uid}', namespace='/feed')
```

---

## Client Side

**File:** `services/web/src/api/socket.js`

The React frontend uses `socket.io-client` to maintain a persistent connection:

```javascript
const socket = io(API_URL + '/feed', {
    auth: { token: accessToken },
    transports: ['websocket'],     // Skip HTTP polling, go straight to WS
    reconnection: true,            // Auto-reconnect on disconnect
    reconnectionDelay: 1000,       // Start with 1s delay
    reconnectionDelayMax: 5000     // Cap at 5s
});
```

### Reconnection

Socket.IO handles reconnection automatically. When the connection drops:
1. Client waits 1 second, then attempts reconnect
2. On success, server replays missed events (last 50 Tier 1-2)
3. Client is back to real-time

This means brief network interruptions (switching WiFi, laptop sleep/wake) are invisible to the user.

---

## [[Eventlet]] — The Async Runtime

Flask is synchronous, but WebSocket requires handling many concurrent connections. Eventlet solves this by **monkey-patching** Python's standard library:

```python
import eventlet
eventlet.monkey_patch()  # Must be first import!
```

After monkey-patching:
- `socket.recv()` → yields to other greenlets instead of blocking the thread
- `time.sleep()` → cooperative sleep
- `threading.Lock()` → greenlet-safe lock
- Redis `pubsub.listen()` → cooperative blocking

This means our synchronous Flask code, SQLAlchemy queries, and Redis subscription all run concurrently in a single OS thread using cooperative multitasking (green threads).

**Critical:** The `eventlet.monkey_patch()` call must be the very first import in `main.py`. If any module loads `socket` or `threading` before the patch, it gets the unpatched version and blocks the entire process.

---

## Failure Modes

| Scenario | Behavior |
|---|---|
| Redis goes down | Subscriber thread retries connection. API keeps serving cached data via REST. |
| Client disconnects | Socket.IO auto-reconnects. Server replays missed events. |
| Ingest publishes while subscriber is restarting | Message lost (pub/sub is fire-and-forget). Caught on next poll via [[Idempotency & Deduplication]]. |
| Duplicate message from Redis | Subscriber checks `edgar_id` uniqueness — silently skips. |
| Company not in database | Auto-created from CIK/ticker in the message. |

---

## See Also

- [[Eventlet]] — Deep dive on the async runtime
- [[Data Flow — End to End]] — Full pipeline context
- [[Watchlists]] — How user targeting works
- [[Redis Pub-Sub Contract]] — The message format
