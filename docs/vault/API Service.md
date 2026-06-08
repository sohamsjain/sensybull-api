# API Service

The API service (`services/api/`) is the central backend — it persists data, authenticates users, serves the REST API, and pushes real-time events via WebSocket.

---

## Entry Point

**File:** `services/api/main.py`

```python
import eventlet
eventlet.monkey_patch()      # Must be first! See [[Eventlet]]

from app import create_app
from app.services.realtime.socketio_setup import socketio

app = create_app()
socketio.run(app, host='0.0.0.0', port=5000)
```

The Eventlet monkey-patch enables cooperative async. The SocketIO server replaces Flask's default WSGI server to handle both HTTP and WebSocket.

---

## App Factory

**File:** `services/api/app/__init__.py`

`create_app()` builds the Flask application:

1. Load config from `config.py` (environment variables)
2. Initialize extensions:
   - SQLAlchemy (ORM)
   - Flask-Migrate (migrations)
   - Flask-JWT-Extended (auth)
   - Flask-CORS (cross-origin)
   - Flask-Limiter (rate limiting)
3. Initialize Socket.IO server
4. Register route blueprints:
   - `/auth` — [[Authentication System]]
   - `/events` — Filing events
   - `/users` — User profiles
   - `/companies` — Company search
   - `/watchlists` — [[Watchlists]]
   - `/filings` — Legacy filing records
5. Create database tables (if needed)
6. Load companies from SEC (if table empty) — see [[Company Loading]]
7. Start Redis subscriber thread — see [[Real-Time System]]

---

## Configuration

**File:** `services/api/config.py`

Key settings loaded from environment:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing |
| `DATABASE_URL` | SQLAlchemy connection string |
| `JWT_SECRET_KEY` | JWT token signing |
| `REDIS_URL` | Redis connection |
| `GOOGLE_CLIENT_ID` | OAuth |
| `FRONTEND_URL` | CORS origins + email links |
| `MAIL_PROVIDER` | Email backend (console/smtp/resend) |
| `SEC_USER_AGENT` | SEC API identification |

SQLite-specific: WAL mode and `check_same_thread=False` are configured in the engine options for concurrent access.

---

## Directory Structure

```
services/api/
├── main.py                          # Entry point
├── config.py                        # Configuration
├── requirements.txt                 # Dependencies
├── app/
│   ├── __init__.py                  # App factory
│   ├── models/                      # SQLAlchemy models
│   │   ├── user.py                  # User account
│   │   ├── company.py               # SEC company
│   │   ├── watchlist.py             # User watchlist
│   │   ├── filing.py                # Legacy filing record
│   │   ├── filing_event.py          # Core signal record
│   │   ├── event_type.py            # Classified event type
│   │   ├── catalyst.py              # Future date from briefing
│   │   └── auth_token.py            # Email/password tokens
│   ├── routes/                      # REST endpoints
│   │   ├── auth.py                  # Authentication
│   │   ├── events.py                # Filing events
│   │   ├── users.py                 # User profiles
│   │   ├── companies.py             # Company CRUD
│   │   ├── watchlists.py            # Watchlist CRUD
│   │   └── filings.py               # Legacy filings
│   ├── services/
│   │   ├── realtime/
│   │   │   ├── socketio_setup.py    # Socket.IO server
│   │   │   └── subscriber.py       # Redis listener thread
│   │   ├── email/
│   │   │   ├── sender.py            # High-level email dispatch
│   │   │   ├── client.py            # Abstract email client
│   │   │   ├── console_client.py    # Dev: print to stdout
│   │   │   ├── smtp_client.py       # SMTP backend
│   │   │   ├── resend_client.py     # Resend API backend
│   │   │   ├── renderer.py          # Jinja2 templates
│   │   │   └── factory.py           # Provider selection
│   │   └── company_loader.py        # SEC bulk company import
│   └── utils/
│       ├── schemas.py               # Marshmallow serializers
│       ├── tokens.py                # Token generation/hashing
│       └── auth.py                  # Auth decorators
├── migrations/                       # Alembic migration files
└── scripts/
    ├── load_companies.py            # Company import utility
    └── stream_8k_filings.py         # Demo filing streamer
```

---

## Key Design Patterns

### Blueprints

Each route module is a Flask Blueprint, registered with a URL prefix:

```python
auth_bp = Blueprint('auth', __name__)
# Registered as: app.register_blueprint(auth_bp, url_prefix='/auth')
```

This keeps routes modular — each file handles one domain.

### Marshmallow Schemas

**File:** `app/utils/schemas.py`

Request/response serialization uses Marshmallow with SQLAlchemy integration:

```python
class FilingEventSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = FilingEvent
        include_fk = True
```

`SQLAlchemyAutoSchema` generates fields from the model definition — no duplication.

### Auth Decorators

```python
@jwt_required()           # Requires valid access token
@email_verified_required  # Requires verified email
@admin_required           # Requires role = "admin"
```

These compose on route handlers for layered access control.

---

## See Also

- [[Architecture Overview]] — Where the API fits in the system
- [[Data Model]] — Database schema
- [[Real-Time System]] — WebSocket and subscriber
- [[Authentication System]] — Auth flow details
- [[API Routes Reference]] — All endpoints
