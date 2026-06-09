# Sensybull — Launch Plan

> **Last updated:** 2026-06-09
> **Status:** Launched

---

## URGENT — Do Today

- [x] **1. Rotate exposed secrets**
  - [x] Rotate all 5 Groq API keys
  - [x] Rotate Resend API key
  - [x] Audit git history for committed `.env` files → **CLEAN** (never committed)
  - [x] ~~Scrub secrets from git history if found~~ → not needed

- [x] **2. Fix CORS wildcard**
  - [x] Replace `cors_allowed_origins="*"` in `socketio_setup.py` — now reads from config
  - [x] Flask-CORS reads `CORS_ALLOWED_ORIGINS` env var, falls back to `FRONTEND_URL`
  - [x] Added `CORS_ALLOWED_ORIGINS` to config.py and .env.example

- [x] **3. Fail-fast on missing secrets**
  - [x] `SECRET_KEY` now raises `RuntimeError` on startup if missing
  - [x] `JWT_SECRET_KEY` now raises `RuntimeError` on startup if missing
  - [x] Added `_require_env()` helper in config.py

---

## Phase 1 — Foundation (Week 1)

- [x] **4. Switch to PostgreSQL**
  - [x] Add `psycopg2-binary` to `requirements.txt`
  - [x] Update `config.py` engine options — auto-detects SQLite vs Postgres, applies appropriate settings
  - [ ] Test all migrations against Postgres *(requires running docker-compose up)*
  - [x] Add Postgres service to `docker-compose.yml` (with health check, named volume)
  - [x] Document `DATABASE_URL` format for Postgres in `.env.example`

- [x] **5. Production Dockerfiles (API + Ingest)**
  - [x] Add non-root `appuser` to API Dockerfile
  - [x] Add non-root `appuser` to Ingest Dockerfile
  - [x] Add `HEALTHCHECK` to API Dockerfile (hits `/health` endpoint)
  - [x] Add `STOPSIGNAL SIGTERM` for graceful shutdown
  - [x] Create `docker-compose.prod.yml` (no volumes, no web, resource limits, restart policies)

- [x] **6. Automated test suite** — 46 tests, all passing
  - [x] Set up pytest + conftest fixtures + in-memory SQLite test DB
  - [x] Auth tests (12): register, login, refresh, token types, change password, missing fields
  - [x] Subscriber tests (9): idempotency, company auto-create, event types, catalysts, fan-out, invalid JSON
  - [x] Event API tests (12): watchlist filtering, tier filter, event_type filter, pagination, access control, catalysts
  - [ ] ~~WebSocket tests~~ — deferred (requires Socket.IO test client, lower priority)
  - [x] Watchlist CRUD tests (13): create, read, update, delete, add/remove companies, access control

- [x] **7. CI pipeline (GitHub Actions)**
  - [x] Workflow: run pytest on every PR (with Postgres + Redis services)
  - [x] Workflow: lint with ruff (API + Ingest)
  - [x] Workflow: build Docker images
  - [x] Workflow: run migrations against test Postgres

- [x] **Bonus: Health check endpoint**
  - [x] Added `GET /health` — checks Redis + database connectivity
  - [x] Returns `{status: "ok"}` or `{status: "degraded"}` with component details

---

## Phase 2 — API Contract (Week 1-2)

- [x] **8. OpenAPI / Swagger documentation**
  - [x] Hand-written OpenAPI 3.0 spec in `app/openapi.py` (full control, no extra deps)
  - [x] All auth endpoints documented with request/response schemas
  - [x] All event endpoints documented with query params, filtering, pagination
  - [x] Watchlist and company endpoints documented
  - [x] WebSocket protocol documented in `docs/vault/WebSocket Protocol.md`
  - [x] Swagger UI served at `/docs`, spec JSON at `/docs/openapi.json`

- [x] **9. CORS configuration** *(done in Urgent phase)*
  - [x] Support multiple allowed origins via `CORS_ALLOWED_ORIGINS` env var
  - [x] Falls back to `FRONTEND_URL` if not set

- [x] **10. API versioning strategy**
  - [x] URL prefix: all routes now under `/api/v1/` (e.g. `/api/v1/auth/login`)
  - [x] Applied prefix to all 6 route blueprints
  - [x] OpenAPI spec, tests, and docs updated to match

---

## Phase 3 — Reliability (Week 2)

- [x] **11. Structured logging**
  - [x] Add `python-json-logger` to requirements
  - [x] Replace `print()` in `app/utils/auth.py` with proper `logging.warning()`
  - [x] Configure log level via `LOG_LEVEL` env var
  - [x] Support JSON log format via `LOG_FORMAT=json` env var
  - [x] Add request IDs to all log entries (generated per-request, returned in `X-Request-ID` header)
  - [x] Subscriber thread already logs edgar_id, ticker, tier (was done correctly from the start)

- [x] **12. Redis-backed rate limiting**
  - [x] `RATELIMIT_STORAGE_URI` already wired in config.py
  - [x] Documented Redis URI for production in `.env.example`
  - [ ] Verify rate limits work across multiple API instances *(requires multi-instance deploy)*

- [x] **13. Error monitoring**
  - [x] Add `sentry-sdk[flask]` to requirements
  - [x] Sentry initialized in `create_app()` when `SENTRY_DSN` is set
  - [x] Configurable `SENTRY_TRACES_SAMPLE_RATE` and `SENTRY_ENVIRONMENT`
  - [x] Subscriber thread exceptions already logged via `log.exception()` — Sentry captures these automatically
  - [ ] Add Sentry to ingest service *(separate requirements.txt — do when deploying)*
  - [ ] Set up alerting for critical errors *(Sentry dashboard config)*

- [x] **14. Health check endpoint** *(done in Phase 1)*
  - [x] `GET /health` — checks Redis + database connectivity
  - [x] Returns `{status: "ok"}` or `{status: "degraded"}` with component details
  - [x] Wired into Docker HEALTHCHECK

---

## Phase 4 — Deploy (Week 2-3)

- [x] **15. Choose a host & deploy**
  - [x] Evaluated hosting options — chose **Render** (managed Postgres + Redis, IaC blueprint, auto-HTTPS)
  - [x] Created `render.yaml` blueprint — provisions API, Ingest, Postgres, and Redis in one click
  - [x] Created deployment guide in `docs/vault/Deployment Guide.md`
  - [x] Deploy via Render Blueprint — connected GitHub repo, all services provisioned
  - [x] Fixed Blueprint: removed `startCommand` (not allowed with Docker runtime), moved migration to Dockerfile CMD
  - [x] Fixed Blueprint: upgraded API + Ingest to `starter` plan (free tier doesn't support Docker/workers)
  - [x] Fixed ingest `seen.json` permission error — added writable `/app/data` dir for non-root user
  - [x] Fixed Redis subscriber disconnects — added `socket_keepalive`, `health_check_interval`, `retry_on_timeout`
  - [x] Fill in env vars: GROQ_API_KEYS, SEC_USER_AGENT set on ingest service
  - [x] Fill in remaining API env vars: FRONTEND_URL, CORS_ALLOWED_ORIGINS, RESEND_API_KEY, MAIL_FROM_ADDRESS done
  - [ ] Set SENTRY_DSN on API service *(optional — when Sentry project is created)*
  - [x] Verify end-to-end: EDGAR poll → Redis → persist → WebSocket

- [x] **16. Domain + HTTPS**
  - [x] Register/configure domain
  - [x] Add CNAME record pointing to Render service
  - [x] Render auto-provisions TLS certificate
  - [x] Update `FRONTEND_URL` and `CORS_ALLOWED_ORIGINS` for production domain

- [x] **17. Database backups**
  - [x] Set up database backups

- [x] **18. Email deliverability**
  - [x] Verify sending domain in Resend
  - [x] Set up SPF record
  - [x] Set up DKIM record
  - [x] Set up DMARC record
  - [x] Send test emails, verify not landing in spam

---

## Phase 5 — Monitoring (Deferred)

- [ ] **19. Ops dashboard** — deferred until there's real traffic to monitor
- [ ] **20. Consider Redis Streams upgrade** — deferred; current pub/sub works fine with single API instance

---

## Notes

- `services/web/` is a **dev prototype only** — excluded from production deployment
- Production frontend will be a separate project consuming this API
- This file is the source of truth for launch progress — update checkboxes as tasks complete
