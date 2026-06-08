# WebSocket Protocol

This documents the Socket.IO WebSocket interface for real-time filing event delivery. This is the companion to the [[API Routes Reference]] REST docs.

---

## Connection

**URL:** `{API_BASE_URL}/feed`
**Protocol:** Socket.IO v4/v5 (not raw WebSocket)
**Transport:** WebSocket preferred, HTTP long-polling fallback

### Client Library

```javascript
import { io } from "socket.io-client";

const socket = io("https://api.sensybull.com/feed", {
    auth: { token: "<JWT access token>" },
    transports: ["websocket"],
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
});
```

### Authentication

Pass the JWT access token in the `auth` object on connect. The server validates it and either:
- **Accepts:** Joins the client to their personal room + public room, replays missed events
- **Rejects:** Disconnects with an error

Unauthenticated connections are allowed but only receive events on the `public` room (all events, unfiltered).

---

## Server → Client Events

### `connected`

Sent after successful authentication.

```json
{
    "status": "ok",
    "user_id": "abc-123-uuid"
}
```

### `filing_event`

A new SEC 8-K filing event. Sent in real-time as filings are processed, and during replay on connect.

```json
{
    "id": "event-uuid",
    "edgar_id": "0001234567-24-012345",
    "signal_type": "8-K",
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "company_id": "company-uuid",
    "cik": "0000320193",
    "filing_date": "2024-03-15T16:30:00+00:00",
    "edgar_url": "https://www.sec.gov/Archives/edgar/data/...",
    "accession_number": "0000320193-24-000001",
    "max_tier": 1,
    "items": [
        {
            "number": "5.02",
            "title": "Departure of Directors or Certain Officers",
            "tier": 2,
            "category": "Leadership",
            "text": "On March 14, 2024..."
        }
    ],
    "exhibits": [
        {
            "type": "EX-99.1",
            "description": "Press Release",
            "url": "https://www.sec.gov/..."
        }
    ],
    "briefing": {
        "headline": "Apple CFO Departure Signals Transition",
        "summary": "Apple Inc. announced the departure of...",
        "primary_event_type": "Leadership Change",
        "significance": "High",
        "sentiment": "Neutral",
        "investor_takeaway": "Watch for successor announcement.",
        "catalysts": [
            {"date": "2024-06-01", "event": "New CFO expected"}
        ],
        "deal_terms": {}
    },
    "event_types": ["Leadership Change"],
    "catalysts": [
        {"event": "New CFO expected", "date": "2024-06-01"}
    ],
    "received_at": "2024-03-15T16:35:00+00:00"
}
```

This is the same shape as the REST `FilingEvent` object from `/events/`.

### `subscribed`

Confirmation after subscribing to a ticker room.

```json
{
    "ticker": "AAPL"
}
```

---

## Client → Server Events

### `subscribe_ticker`

Subscribe to events for a specific ticker, regardless of watchlist.

```json
{
    "ticker": "AAPL"
}
```

---

## Room Architecture

| Room | Who joins | What they receive |
|---|---|---|
| `user:{user_id}` | Authenticated users (auto) | Events for companies in their watchlists |
| `public` | Everyone (auto) | All events (unfiltered) |
| `ticker:{TICKER}` | Clients who emit `subscribe_ticker` | Events for that specific ticker |

---

## Event Replay on Connect

When an authenticated client connects, the server immediately emits the **last 50 Tier 1-2 events** for the user's watchlist companies. This ensures:

- Reconnecting clients don't miss events from while they were offline
- New sessions get immediate context

Events are sent in chronological order (oldest first).

Tier 3 (routine) events are excluded from replay to reduce noise. They're available via the REST `/events/` endpoint.

---

## Reconnection

Socket.IO handles reconnection automatically. The recommended client config:

```javascript
{
    reconnection: true,
    reconnectionDelay: 1000,     // Start at 1s
    reconnectionDelayMax: 5000,  // Cap at 5s
    reconnectionAttempts: Infinity
}
```

On successful reconnect, the server replays missed events again.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Invalid/expired JWT | Connection rejected (Socket.IO `connect_error` event) |
| Network drop | Auto-reconnect with backoff |
| Server restart | Auto-reconnect, events replayed |

---

## See Also

- [[API Routes Reference]] — REST endpoints
- [[Real-Time System]] — Server-side architecture
- [[Authentication System]] — JWT token lifecycle
