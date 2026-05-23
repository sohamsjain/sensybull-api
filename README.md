# Sensybull — Real-Time Financial Intelligence Platform

A monorepo of two cooperating services that turn raw SEC EDGAR 8-K filings into
authenticated, watchlist-filtered real-time signals.

```
/
├── services/
│   ├── api/        Flask backend: auth, users, watchlists, companies,
│   │               filings, event history, authenticated WebSocket fan-out.
│   └── ingest/     FastAPI + background poll service: streams 8-K filings
│                   from SEC EDGAR, parses them, generates LLM briefings,
│                   and publishes each as a JSON event.
├── docker-compose.yml
├── .env.example
└── README.md
```

## Architecture

The two services never import from each other. They communicate only through a
documented JSON contract over **Redis pub/sub** (channel `filing:new`).

```
EDGAR ──poll──> ingest ──publish(filing:new)──> Redis ──subscribe──> api ──WebSocket──> users
                  │                                                    │
                  └── also broadcasts on its own /ws (direct feed)     └── persists each event
```

- **ingest** publishes a `FilingEvent` (see `services/ingest/events.py`) for
  every processed filing. A Redis outage never breaks the ingest pipeline —
  the publisher fails silently.
- **api** runs a background daemon thread that subscribes to `filing:new`,
  persists each event (`filing_event` table, idempotent on `edgar_id`), and
  fans it out over an authenticated Socket.IO namespace (`/feed`) to every user
  whose watchlist contains the relevant company.

The JSON envelope is the only coupling. New signal types
(`earnings`, `insider_trade`, …) publish to the same channel with the same
envelope and are routed identically.

## Quick start (Docker)

```bash
cp .env.example services/api/.env      # fill in secrets
cp .env.example services/ingest/.env   # set SEC_USER_AGENT + GROQ_API_KEY
docker compose up
```

Services:
- API + WebSocket: http://localhost:5000
- Ingest health:   http://localhost:8000/health
- Redis:           localhost:6379

## Running locally (without Docker)

```bash
# Redis
docker compose up redis

# API (terminal 2)
cd services/api
pip install -r requirements.txt
python scripts/add_filing_event_table.py   # one-time: create filing_event table
python main.py                              # http://localhost:5000

# Ingest (terminal 3)
cd services/ingest
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

## Event REST API

| Method | Path                          | Description                                  |
|--------|-------------------------------|----------------------------------------------|
| GET    | `/events/`                    | Paginated feed for the user's watchlist      |
| GET    | `/events/<event_id>`          | Single event detail                          |
| GET    | `/events/company/<company_id>`| Events for one watchlist company             |

Query params: `page`, `per_page`, `max_tier` (only `<=` this tier),
`signal_type` (`8-K`, `earnings`, …). All endpoints require a JWT.

## WebSocket

Connect to the `/feed` Socket.IO namespace. Send `{ token: "<JWT>" }` in the
connect `auth` payload to join your personal room and receive
`filing_event` messages for your watchlist companies. Unauthenticated clients
join a `public` room (backward compatible with the ingest service's
`client.html` debug feed).
