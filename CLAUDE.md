# Sensybull API

## Tech Stack
- Flask 2.3, SQLAlchemy 2.0, PostgreSQL (SQLite fallback), Redis pub/sub
- JWT auth (Flask-JWT-Extended), Resend for transactional email
- Flask-SocketIO (gevent) for real-time filing event delivery
- Ingest pipeline: SEC EDGAR polling → Groq LLM briefing → grounding verification → Redis → API
- Anti-hallucination invariant: no LLM-authored narrative reaches users unless it passes `services/ingest/grounding.py` (deterministic number/date/name checks against the exact text shown to the model) plus the LLM verifier pass in `briefing.py`. Sparse/unverifiable filings publish as facts-only briefings (`briefing.mode = "facts_only"`). Never weaken this path.

## Project Structure
- `services/api/` — Flask REST API + WebSocket server
- `services/ingest/` — SEC EDGAR polling + LLM briefing pipeline
- Production frontend lives in the separate `sensybull-web` repo (Next.js)

## Product surface notes
- Priority is binary at the API boundary: `FilingEvent.to_ws_payload()` exposes `important` (briefing significance High → true, else tier-1 fallback). The ingest pipeline still grades High/Medium/Low internally — that stays; only the product surface is binary.
- Thesis/positions feature was hard-rolled-back (July 2026): no `/positions` routes, no `services/api/app/services/thesis/`, no thesis-aware alert formatting. The `position` / `thesis_assessment` / `thesis_version` DB tables were intentionally left in place (historical data), but no model maps them — don't re-add mappings casually.
- `GET /events/catalysts` was removed with the catalyst calendar; catalysts are still persisted per event and serialized inside each event payload.

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
