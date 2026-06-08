# Technology Decisions

Every technology choice in Sensybull was made for a reason. This note explains *what* we use and *why* — not just the "how."

---

## Backend — API Service

### Flask (not FastAPI, not Django)

**What:** Lightweight Python web framework.

**Why Flask over Django?** Django's ORM, admin panel, and batteries-included philosophy are overkill. We need a thin REST layer over SQLAlchemy with WebSocket support. Flask lets us pick exactly the extensions we want.

**Why Flask over FastAPI?** FastAPI is async-native and would theoretically be a better fit for a real-time system. But Flask-SocketIO with [[Eventlet]] gives us WebSocket support with a mature ecosystem. Flask also has richer extension support for auth (Flask-JWT-Extended), rate limiting (Flask-Limiter), and migrations (Flask-Migrate). The API service is I/O-bound on database queries, not compute-bound — async doesn't buy us much here.

### SQLAlchemy + Flask-SQLAlchemy

**What:** Python's most powerful ORM, wrapped in Flask integration.

**Why an ORM?** Our data model is relational (users have watchlists, watchlists have companies, companies have events). SQL is the natural fit. SQLAlchemy gives us:
- Declarative models that serve as documentation
- Relationship loading (eager/lazy) to avoid N+1 queries
- Migration support via Alembic (Flask-Migrate)
- Database-agnostic queries (works with SQLite now, Postgres later)

### SQLite (not Postgres)

**What:** Embedded database, stored as a single file.

**Why?** For the current scale (single server, single writer), SQLite with WAL mode is:
- **Zero ops** — No database server to run, configure, or back up separately
- **Fast** — Embedded = no network round-trips for queries
- **WAL mode** — Allows concurrent reads while writing
- **Portable** — The database is a file; copy it to back up

**When to upgrade:** If we need multiple API server instances writing concurrently, or if the dataset outgrows what SQLite handles comfortably (~100GB), switch to Postgres. The SQLAlchemy ORM makes this a config change, not a rewrite.

### Marshmallow (not Pydantic)

**What:** Serialization and validation library for request/response schemas.

**Why Marshmallow?** It integrates tightly with SQLAlchemy via `marshmallow-sqlalchemy`, auto-generating schemas from models. This eliminates the duplication of defining fields in both the model and the serializer. Marshmallow also has mature Flask integration and handles nested object serialization cleanly.

### Flask-JWT-Extended

**What:** JWT (JSON Web Token) authentication for Flask.

**Why JWT over sessions?** The frontend is a separate React SPA — it can't share cookies with the API easily (different origins in dev). JWT tokens are:
- **Stateless** — No server-side session store needed
- **Cross-origin friendly** — Sent as `Authorization: Bearer` header
- **Mobile-ready** — Same auth works for future mobile clients

The extension handles access tokens (short-lived, 15 min) and refresh tokens (long-lived, 30 days), plus built-in token blocklisting hooks.

### Flask-Limiter

**What:** Rate limiting middleware.

**Why?** Sensitive endpoints (login, password reset, registration) need abuse protection. Flask-Limiter gives us decorator-based rate limits (`@limiter.limit("5/minute")`) with pluggable backends (in-memory for dev, Redis for prod).

### Bcrypt

**What:** Password hashing algorithm.

**Why bcrypt over argon2 or scrypt?** Bcrypt is the industry standard for password hashing. It's:
- **Slow by design** — Resistant to brute-force
- **Configurable work factor** — Can be tuned as hardware gets faster
- **Battle-tested** — Decades of cryptographic review

---

## Backend — Ingest Service

### Pure Python (stdlib + minimal deps)

**What:** The ingest service uses Python's standard library for HTTP (`urllib`), XML parsing (`xml.etree`), and HTML parsing (`html.parser`).

**Why no requests/httpx/aiohttp?** The ingest service makes a small number of sequential HTTP requests (one feed fetch + a few filing pages per cycle). `urllib` with manual retry/backoff is sufficient and avoids adding dependencies. The custom `HTMLParser` subclass gives us precise control over SEC's messy HTML — third-party parsers like BeautifulSoup would add overhead without solving the core problem of extracting structured items from unstructured filings.

### Groq (not OpenAI, not local LLM)

**What:** LLM inference API running open-source Llama models.

**Why Groq?**
- **Speed** — Groq's custom LPU hardware delivers tokens 5-10x faster than GPU-based providers. For a real-time pipeline, this means briefings are ready in 1-3 seconds instead of 10-30.
- **Cost** — Open-source Llama models on Groq are significantly cheaper than GPT-4 or Claude API calls. At our volume (dozens of filings/day), cost per briefing is fractions of a cent.
- **Quality** — Llama-4-Scout-17B produces structured JSON reliably. The briefing prompt is carefully engineered to get consistent output.

**Model rotation:**
- Primary: `meta-llama/llama-4-scout-17b-16e-instruct` — Best quality
- Fallback: `llama-3.1-8b-instant` — Used when rate-limited, still adequate

### EdgarTools

**What:** Python library for SEC EDGAR API access.

**Why?** Used in the company loading script (`load_companies.py`) to fetch detailed company information (SIC codes, state of incorporation, exchange). The EDGAR API is complex and poorly documented; edgartools abstracts it into a clean Python interface.

---

## Messaging — Redis

### Redis Pub/Sub

**What:** In-memory message broker for inter-service communication.

**Why Redis over RabbitMQ/Kafka/SQS?**
- **Simplicity** — One publisher, one subscriber, no queue management
- **Already needed** — Redis is also used for rate limiting storage in production
- **Low latency** — In-memory pub/sub delivers messages in < 10ms
- **Zero config** — `docker run redis:7-alpine` and you're done

**Trade-off:** Pub/sub is fire-and-forget. If the subscriber is down when a message is published, that message is lost. We handle this with [[Idempotency & Deduplication]] — the ingest service tracks what it's published in `seen.json`, and the subscriber deduplicates on `edgar_id`. Missed messages are caught on the next poll cycle.

**When to upgrade:** If we add more consumers (e.g., an alerting service, an analytics pipeline), Redis Streams would give us consumer groups and message persistence. For now, pub/sub is simpler.

---

## Real-Time — Socket.IO

### Flask-SocketIO + Eventlet

**What:** WebSocket server implementing the Socket.IO protocol, running on the Eventlet async networking library.

**Why Socket.IO over raw WebSocket?**
- **Auto-reconnection** — Client reconnects automatically on network drops
- **Rooms** — Built-in concept of "rooms" for targeted message delivery
- **Fallback** — Falls back to HTTP long-polling if WebSocket is blocked
- **Namespaces** — Logical separation (`/feed` namespace) without running separate servers
- **Acknowledgments** — Built-in request-response pattern if needed

**Why Eventlet?** Flask-SocketIO needs an async runtime to handle concurrent WebSocket connections. Eventlet monkey-patches Python's standard library to make blocking I/O cooperative (non-blocking). This means our synchronous Flask code, SQLAlchemy queries, and Redis operations all work without modification — they just yield to other greenlets during I/O waits.

See [[Eventlet]] for more on how this works.

---

## Frontend

### React 19 + Vite

**What:** Modern React with the fastest available build tooling.

**Why React?** Component model is well-suited for the card-based feed UI. React 19's concurrent features improve perceived performance when many filing cards update simultaneously.

**Why Vite over Webpack/CRA?** Vite uses esbuild for dev and Rollup for prod. Dev server startup is near-instant, HMR is sub-100ms. For a frontend that's iterated on frequently, fast feedback loops matter.

### Tailwind CSS v4

**What:** Utility-first CSS framework.

**Why Tailwind?** The filing feed UI is component-heavy with many small, reusable visual elements (badges, cards, tags). Tailwind's utility classes make it fast to style these without writing CSS files. The dark theme (slate-900 background) is achieved with a few utility classes rather than a complex theme system.

### socket.io-client

**What:** JavaScript Socket.IO client library.

**Why?** Matches the Flask-SocketIO server. Provides auto-reconnection, event-based messaging, and namespace support. The client connects to the `/feed` namespace and listens for `filing_event` messages.

---

## Infrastructure

### Docker Compose

**What:** Multi-container orchestration for local development.

**Why?** Four services (Redis, API, Ingest, Web) need to talk to each other. Docker Compose gives us:
- One-command startup (`docker-compose up`)
- Automatic networking (services reference each other by name)
- Volume mounts for live code reloading
- Health checks (Redis must be healthy before API starts)

### Resend (Email)

**What:** Transactional email API.

**Why Resend over SendGrid/Mailgun?** Resend has the simplest API, competitive pricing, and excellent deliverability. The pluggable email system (`MAIL_PROVIDER` config) also supports SMTP and console output for dev/testing.

---

## See Also

- [[Architecture Overview]] — How the pieces fit together
- [[Eventlet]] — Deep dive on the async runtime
- [[Groq LLM]] — How we use LLMs for briefing generation
- [[Redis]] — Redis's role in the system
