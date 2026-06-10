# Sensybull API

## Tech Stack
- Flask 2.3, SQLAlchemy 2.0, PostgreSQL (SQLite fallback), Redis pub/sub
- JWT auth (Flask-JWT-Extended), Resend for transactional email
- Flask-SocketIO (gevent) for real-time filing event delivery
- Ingest pipeline: SEC EDGAR polling → Groq LLM briefing → Redis → API

## Project Structure
- `services/api/` — Flask REST API + WebSocket server
- `services/ingest/` — SEC EDGAR polling + LLM briefing pipeline
- `services/web/` — Throwaway prototype (not production frontend)

## Related Projects
- Frontend: ~/Projects/sensybull-web (Next.js)
- API base: /api/v1
- When adding/changing endpoints, write a brief summary to ~/Projects/sensybull-web/API_CHANGES.md

## Conventions
- Models extend BaseModel (UUID id, created_at) in `services/api/app/models/`
- Routes are Flask blueprints registered at `/api/v1/<name>`
- Schemas use Marshmallow in `services/api/app/utils/schemas.py`
- Email templates require both `.html` and `.txt` in `services/api/app/services/email/templates/`
- Tests in `services/api/tests/`, run with `cd services/api && python -m pytest tests/`
- CI: GitHub Actions (ruff lint + pytest + Docker build)
- Deployed on Render (see `render.yaml`)
