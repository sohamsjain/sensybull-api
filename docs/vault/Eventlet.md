# Eventlet

Eventlet is the async runtime that lets Flask handle WebSocket connections concurrently. It's a critical but often misunderstood piece of the stack.

---

## The Problem

Flask is synchronous — each request blocks until it completes. But WebSocket requires:
- Hundreds of long-lived connections held open simultaneously
- A background thread listening to Redis pub/sub (blocking call)
- SQLAlchemy queries (blocking I/O)
- All running in the same process

Without async, a single `redis.pubsub.listen()` call would block the entire server.

---

## The Solution: Green Threads

Eventlet replaces Python's standard library I/O with **cooperative** versions via monkey-patching:

```python
import eventlet
eventlet.monkey_patch()  # MUST be first import in main.py
```

After this call:
- `socket.recv()` → yields to other greenlets while waiting for data
- `time.sleep(n)` → yields for n seconds, lets other greenlets run
- `threading.Lock()` → greenlet-aware lock
- `redis.pubsub.listen()` → yields between messages

**Green threads (greenlets)** are like OS threads but managed in userspace:
- Thousands of greenlets in a single OS thread
- Context switching is explicit (at I/O boundaries), not preemptive
- No GIL contention (there's only one OS thread)
- Much lower memory overhead than OS threads

---

## How It Works in Sensybull

```
One OS Thread
    │
    ├── Greenlet 1: Flask HTTP request handler
    │   └── SQLAlchemy query (yields during I/O)
    │
    ├── Greenlet 2: WebSocket connection (client A)
    │   └── Waiting for message (yielded)
    │
    ├── Greenlet 3: WebSocket connection (client B)
    │   └── Waiting for message (yielded)
    │
    ├── Greenlet 4: Redis subscriber
    │   └── pubsub.listen() (yields between messages)
    │
    └── Greenlet 5: Another HTTP request
        └── JWT decode + DB query (yields during I/O)
```

All five greenlets share one OS thread. When one does I/O, it yields and another runs. This is **cooperative multitasking** — greenlets explicitly give up control at I/O points.

---

## The Critical Monkey-Patch Rule

```python
# main.py — FIRST TWO LINES
import eventlet
eventlet.monkey_patch()

# Now safe to import everything else
from app import create_app
```

**Why first?** If any module imports `socket`, `threading`, or `time` before the monkey-patch, it gets the **unpatched** (blocking) version. That module's I/O will block the entire process, defeating the purpose.

**Common mistake:** Importing `redis` or `sqlalchemy` before `eventlet.monkey_patch()`. The connection pools inside these libraries cache the socket module at import time. If they get the unpatched socket, every database query blocks all WebSocket connections.

---

## Flask-SocketIO Integration

Flask-SocketIO detects Eventlet and uses it as the async mode:

```python
socketio = SocketIO(app, async_mode='eventlet')
```

This means:
- Each WebSocket connection gets its own greenlet
- `socketio.emit()` is non-blocking (dispatched to greenlets)
- The `socketio.run()` call uses Eventlet's WSGI server

---

## Trade-offs

### Advantages
- **Zero code changes** — Synchronous Flask/SQLAlchemy code works as-is
- **High concurrency** — Thousands of connections in one process
- **Low overhead** — Greenlets use ~4KB each vs ~8MB for OS threads

### Disadvantages
- **CPU-bound work blocks everyone** — No preemption, so a long computation freezes all greenlets
- **Monkey-patching is fragile** — Import order matters; some C extensions don't cooperate
- **Debugging is harder** — Stack traces show greenlet IDs, not thread IDs
- **Library compatibility** — Some libraries don't play well with monkey-patched sockets

### Why Not Gevent?

Gevent is the other major green-thread library. Flask-SocketIO supports both. We chose Eventlet because:
- Flask-SocketIO's documentation defaults to Eventlet
- Slightly better compatibility with Redis pub/sub in our testing
- Both are functionally equivalent for our use case

---

## See Also

- [[Real-Time System]] — How Socket.IO uses Eventlet
- [[Technology Decisions]] — Why this async model
- [[Ingest Service]] — Note: ingest uses `asyncio`, not Eventlet
