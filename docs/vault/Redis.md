# Redis

Redis is the communication backbone between Sensybull's two backend services. It's the only shared infrastructure.

---

## Role in the Architecture

```
Ingest ──PUBLISH──► Redis ──SUBSCRIBE──► API
                     │
                     └── Also used for rate limiting (production)
```

Redis serves two purposes:

### 1. Message Broker (Pub/Sub)

Channel `filing:new` carries [[Redis Pub-Sub Contract|FilingEvent JSON]] from the ingest service to the API's subscriber thread. This is the primary role — it's the decoupling point between services.

**Properties of pub/sub:**
- **Fire-and-forget** — If no subscriber is listening, the message is lost
- **No persistence** — Messages are not stored (unlike Redis Streams)
- **No acknowledgment** — The publisher doesn't know if the subscriber received it
- **Low latency** — Typically < 10ms for message delivery

### 2. Rate Limit Backend (Production)

Flask-Limiter can use Redis as its storage backend for production deployments:

```
RATELIMIT_STORAGE_URI=redis://redis:6379/0
```

This enables rate limiting across multiple API instances (if scaled horizontally). In development, it defaults to in-memory storage.

---

## Configuration

```bash
REDIS_URL=redis://localhost:6379/0   # Local development
REDIS_URL=redis://redis:6379/0       # Docker Compose (service name)
```

Both services connect to the same Redis instance using the same URL.

---

## Docker Setup

```yaml
redis:
  image: redis:7-alpine    # Minimal Alpine-based image
  ports:
    - "6379:6379"
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 3s
    retries: 5
```

The health check ensures the API and ingest services don't start until Redis is ready. Both services have `depends_on: redis: condition: service_healthy`.

---

## Why Redis, Not Something Else?

| Alternative | Why not |
|---|---|
| RabbitMQ | Overkill for 1 publisher, 1 subscriber. Adds operational complexity. |
| Kafka | Designed for massive scale. We process dozens of messages/day, not millions. |
| SQS/SNS | Cloud-specific. We want to run locally with Docker. |
| Direct HTTP | Couples the services. If the API is down, ingest can't publish. |
| Shared database | Introduces tight coupling. Polling a table is wasteful. |

Redis is the simplest option that decouples the services while adding minimal infrastructure.

---

## Upgrading to Redis Streams

If we outgrow pub/sub (multiple consumers, guaranteed delivery), Redis Streams is the natural next step:

```
Current:  PUBLISH filing:new → subscriber gets it (or doesn't)
Future:   XADD filing:new → consumer group reads, acknowledges
```

Streams add:
- Message persistence (messages survive restarts)
- Consumer groups (multiple API instances share the workload)
- Acknowledgment (guaranteed at-least-once delivery)
- Message replay (new consumers can read history)

The migration would be localized to `publisher.py` and `subscriber.py`.

---

## See Also

- [[Redis Pub-Sub Contract]] — The message format
- [[Architecture Overview]] — Why services are decoupled
- [[Real-Time System]] — How the subscriber processes messages
- [[Technology Decisions]] — More on the Redis choice
