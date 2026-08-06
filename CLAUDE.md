# Sensybull API

## Tech Stack
- Flask 2.3, SQLAlchemy 2.0, PostgreSQL (SQLite fallback), Redis pub/sub
- JWT auth (Flask-JWT-Extended), Resend for transactional email
- Flask-SocketIO (gevent) for real-time filing event delivery
- Ingest pipeline: SEC EDGAR polling → Groq LLM briefing → Redis → API. A second poller in the same worker (`services/ingest/press_release/`, flag `PR_INGEST_ENABLED`, off by default) ingests newswire press releases (GlobeNewswire/PR Newswire RSS; Business Wire/Accesswire only via `PR_FEED_URLS_<WIRE>` — no confirmed public feed): promo prefilter + law-firm blocklist + issuer-vs-ticker verification + LLM first-party/materiality gate; publishes `signal_type="PR"` events on the same Redis contract (synthetic `edgar_id="pr:<wire>:<guid>"`, no facts_only fallback — unclassifiable releases are dropped)
- Briefings are a single Groq pass with prompt-level "stick to the filing text" guidance (`briefing.mode = "llm"`). Narrative voice (third person, no "we"/"our", don't inherit the source's promotional framing) lives in one shared `briefing.VOICE_RULES` block that both the 8-K prompt and the press-release prompt embed — filings and wire releases are written in the company's own voice, and the model mirrors it unless told not to. Add voice guidance there, not per-prompt. When the LLM call fails, the model reports insufficient content, or the filing has too little text to summarize, the event publishes as a deterministic facts-only briefing (`mode = "facts_only"`). July 2026: the mechanical grounding checks (`grounding.py`) and second LLM verifier pass were rolled back as net-negative (too many false rejections); historical events may carry `mode = "llm_verified"` / `"structured"`.

## Project Structure
- `services/api/` — Flask REST API + WebSocket server
- `services/ingest/` — SEC EDGAR polling + LLM briefing pipeline
- Production frontend lives in the separate `sensybull-web` repo (Next.js)

## Product surface notes
- Ingest is 8-K only among SEC forms (July 2026 rollback): the multi-form pipeline (SC 13D/G, tenders, merger/contested proxies, delistings, NT late filings, Form 4 insider buys) was deleted — other form types were too hard to debug. Only `8-K` and `8-K/A` are in `services/ingest/forms.py`. Historical non-8-K events remain in the DB and still serialize normally; don't re-add forms without an explicit decision. Press releases (`signal_type="PR"`) are the one non-EDGAR source (July 2026 decision).
- Event types are a deliberately small list of highly material categories (12 labels incl. "Other"; "Regulatory / Clinical" added with press releases) defined in `services/ingest/briefing.py` EVENT_TYPES and mirrored in `services/api/app/routes/events.py` (`GET /events/types` backs the feed's category filter). Keep the two in sync; don't grow the list casually.
- PR↔8-K dedup lives in the subscriber (`services/api/app/services/realtime/pr_dedup.py`): the same announcement appears once. Cross-wire dups and PRs arriving after their 8-K are dropped; an 8-K arriving after its PR backfills the PR event (`related_filing_url`, emitted as socket `filing_event_update`) and is suppressed when it's a pure wrapper (items ⊆ {2.02, 7.01, 8.01, 9.01} and exhibit fingerprint matches; `PR_SUPPRESS_8K=0` disables suppression). Fingerprint match rule is duplicated in `services/ingest/press_release/fingerprint.py` — keep in sync.
- Priority is binary at the API boundary: `FilingEvent.to_ws_payload()` exposes `important` (briefing significance High → true, else tier-1 fallback). The ingest pipeline still grades High/Medium/Low internally — that stays; only the product surface is binary.
- Thesis/positions feature was hard-rolled-back (July 2026): no `/positions` routes, no `services/api/app/services/thesis/`, no thesis-aware alert formatting. The `position` / `thesis_assessment` / `thesis_version` DB tables were intentionally left in place (historical data), but no model maps them — don't re-add mappings casually.
- `GET /events/catalysts` was removed with the catalyst calendar; catalysts are still persisted per event and serialized inside each event payload.
- Market data reaches the frontend through two Alpaca proxies on `routes/companies.py`, both Redis-cached: `GET /companies/<id>/bars` (chart OHLCV, 5 min) and `GET /companies/<id>/quote` (last price + day change for the watchlist header, 60s). The quote route falls back to the daily-synced `Company.last_price` with `stale: true` when Alpaca is unreachable, and only 503s when there's no price at all. Snapshot→price selection is `alpaca.snapshot_price()`, shared with the daily `sync-market-data` cron.
- The watchlist inbox (`routes/watchlist_inbox.py`) has bulk counterparts to its per-company actions — `POST /watchlist/read`, `PUT /watchlist/mute`, `POST /watchlist/remove`, all taking `{company_ids}` — backing the web multi-select. They share `_bulk_target_ids()`, which intersects the request with the companies the caller follows: unknown ids are dropped (a client's list goes stale whenever another tab removes a company), and only an empty intersection is a 403.

## Related Projects
- Frontend: ~/Projects/sensybull-web (Next.js)
- API base: /api/v1
- When adding/changing endpoints, write a brief summary to ~/Projects/sensybull-web/API_CHANGES.md

## Conventions
- Models extend BaseModel (UUID id, created_at) in `services/api/app/models/`
- Routes are Flask blueprints registered at `/api/v1/<name>`
- Public share surface (`/api/v1/share/*`, `/api/v1/watchlists/track`) backs the frontend's `/add/<symbol>` track links — docs in sensybull-web `docs/TRACK_LINKS.md`
- Schemas use Marshmallow in `services/api/app/utils/schemas.py`
- Email templates require both `.html` and `.txt` in `services/api/app/services/email/templates/`
- Tests in `services/api/tests/`, run with `cd services/api && python -m pytest tests/`
- CI: GitHub Actions (ruff lint + pytest + Docker build)
- Deployed on Render (see `render.yaml`)
