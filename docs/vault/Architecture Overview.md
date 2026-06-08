# Architecture Overview

Sensybull is a **monorepo** containing three independent services that compose into a real-time SEC filing intelligence platform.

---

## Why a Monorepo?

All three services live under `services/` in one Git repository. This gives us:

- **Atomic changes** — A schema change in ingest and the corresponding handler in the API ship in the same commit
- **Shared documentation** — This vault, the `CLAUDE.md`, and integration tests live at the root
- **Single `docker-compose up`** — One command spins up the entire stack

But the services are **not coupled at the code level**. They share zero Python imports. The only contract between them is the [[Redis Pub-Sub Contract]] — a JSON schema published on a Redis channel.

---

## The Three Services

### 1. [[Ingest Service]] — `services/ingest/`

**Role:** Data acquisition and enrichment

- Polls SEC EDGAR's Atom feed every 10 minutes
- Parses 8-K HTML documents, extracts structured items
- Sends filing text to [[Groq LLM]] for AI briefing + event classification
- Publishes enriched JSON events to Redis

**Why it's separate:** Ingestion is a background process with completely different scaling needs. It does network I/O to external APIs (SEC, Groq), has its own retry/backoff logic, and could run on a different machine entirely. If it crashes, the API keeps serving cached data.

### 2. [[API Service]] — `services/api/`

**Role:** Persistence, access control, real-time delivery

- Flask web server with REST API + Socket.IO WebSocket
- Subscribes to Redis `filing:new` channel in a background thread
- Persists events to SQLite, resolves company references
- Pushes events to connected WebSocket clients based on their [[Watchlists]]
- Handles auth (JWT + Google OAuth), user management, company search

**Why Flask?** Flask's simplicity and ecosystem (SQLAlchemy, Marshmallow, JWT-Extended) make it fast to build a CRUD API. [[Flask-SocketIO]] adds WebSocket support without switching frameworks.

### 3. [[Web Frontend]] — `services/web/`

**Role:** User interface

- React 19 SPA with Vite bundler
- Connects to API via REST (history) and Socket.IO (live updates)
- Dark-themed dashboard with filtering by significance and event type

---

## Communication Patterns

```
┌──────────┐                    ┌──────────┐                  ┌──────────┐
│  Ingest  │──PUBLISH──────────►│  Redis   │◄──SUBSCRIBE──────│   API    │
│          │   filing:new       │          │   filing:new      │          │
└──────────┘                    └──────────┘                  └────┬─────┘
                                                                   │
                                                              Socket.IO
                                                                   │
                                                              ┌────▼─────┐
                                                              │   Web    │
                                                              │ Frontend │
                                                              └──────────┘
```

**Ingest → API:** Asynchronous, fire-and-forget via Redis pub/sub. Ingest doesn't know or care if the API is running.

**API → Frontend:** Bidirectional via Socket.IO. The API pushes events; the frontend can subscribe to specific tickers.

**Frontend → API:** REST for CRUD operations (auth, watchlists, historical queries). WebSocket for live feed.

---

## What This Architecture Gives Us

| Property | How |
|---|---|
| **Loose coupling** | Services share only a JSON contract over Redis |
| **Independent failure** | Ingest can crash without losing the API |
| **Replay safety** | Events are [[Idempotency & Deduplication\|idempotent]] — re-publishing is harmless |
| **Simple deployment** | `docker-compose up` runs everything |
| **Future flexibility** | Could swap SQLite for Postgres, add more consumers, or scale ingest horizontally |

---

## See Also

- [[Technology Decisions]] — Why each library was chosen
- [[Data Flow — End to End]] — Step-by-step trace of a filing
- [[Redis Pub-Sub Contract]] — The JSON schema between services
