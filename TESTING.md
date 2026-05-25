# Testing & Committing — 8-K Ingest Integration

A step-by-step runbook for verifying the Redis-pub/sub integration between
`services/api` (Flask) and `services/ingest` (FastAPI 8-K streamer), then
pushing any follow-up changes.

> The integration is already committed and pushed to branch
> `claude/sensybull-8k-ingest-integration-eCKoI` in **both** repos
> (`sensybull-api` = the monorepo, `8k` = restructured upstream). This file
> helps you reproduce the tests locally and push further edits.

---

## 0. Prerequisites

- Python 3.11+ and `pip`
- Redis 7 — either Docker (`redis:7-alpine`) or a local `redis-server`
- (optional) Docker + Docker Compose for the full-stack path
- A `GROQ_API_KEY` and a `SEC_USER_AGENT` (e.g. `Your Name you@email.com`) only
  if you want to run the **ingest** service against live EDGAR

### Get the code (if testing from a fresh clone)

```bash
git fetch origin claude/sensybull-8k-ingest-integration-eCKoI
git checkout claude/sensybull-8k-ingest-integration-eCKoI
```

---

## Path A — Full stack with Docker Compose (recommended)

From the monorepo root (`sensybull-api/`):

```bash
# 1. Create per-service .env files from the merged example
cp .env.example services/api/.env
cp .env.example services/ingest/.env
#   then edit services/ingest/.env -> set SEC_USER_AGENT and GROQ_API_KEY

# 2. Bring up Redis + API + Ingest
docker compose up --build
```

In a second terminal, simulate an ingest publish and watch the API persist +
fan it out:

```bash
docker compose exec redis redis-cli PUBLISH filing:new '{
  "edgar_id":"e2e-test-001","signal_type":"8-K","cik":"0000320193",
  "ticker":"AAPL","exchange":"NASDAQ","company_name":"Apple Inc.",
  "filing_date":"2024-01-01T20:00:00+00:00",
  "edgar_url":"https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000320193",
  "accession_number":"0000320193-24-000001","max_tier":1,
  "items":[{"number":"5.02","title":"Departure of Directors","tier":2,
            "category":"Leadership","text":"CFO resigned."}],
  "exhibits":[],"briefing":{"headline":"Apple CFO Resigns",
    "bullets":["CFO departed effective immediately."],
    "company_context":"Apple Inc."}}'
```

**Expected** in the API container logs:

```
Subscriber: stored + emitted edgar_id=e2e-test-001 ticker=AAPL tier=1 users=N
```

(`users=0` until a user has AAPL in a watchlist — see §A.4 of Path B to seed one.)

Tear down with `Ctrl-C` then `docker compose down`.

---

## Path B — Local verification without Docker

### B.0 — One-time setup (isolated venv)

Use a **fresh** virtualenv (do *not* pass `--system-site-packages`) so you get a
clean, consistent `cryptography`/`PyJWT`:

```bash
cd services/api
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> `edgartools` (in requirements) is large and only used by the company-loader
> scripts — **not** by the realtime integration. To skip it while testing:
> `grep -v edgartools requirements.txt | pip install -r /dev/stdin`

The ingest publisher test below only needs `redis`, which the API venv already
has. To actually *run* the ingest service you also need its own deps:
`pip install -r ../ingest/requirements.txt`.

### B.1 — Ingest publisher swallows Redis errors (Step 13a)

With Redis **not** running this must still succeed (errors are swallowed):

```bash
cd services/ingest
python -c "
from publisher import publish_filing
import json
publish_filing(json.dumps({'edgar_id':'t1','signal_type':'8-K','cik':'0000320193',
  'ticker':'AAPL','exchange':'NASDAQ','company_name':'Apple Inc.',
  'filing_date':'2024-01-01T00:00:00Z','edgar_url':'https://e.x','accession_number':'',
  'max_tier':2,'items':[],'exhibits':[],'briefing':None}))
print('publish_filing: OK')
"
```

**Expected:** a `Redis publish failed ...` warning line, then `publish_filing: OK`.

### B.2 — API app boots cleanly (Step 13b)

```bash
cd services/api
python -c "from app import create_app; app = create_app(); print('create_app: OK')"
```

**Expected:** `create_app: OK`. A Redis "connection refused" warning from the
background subscriber thread is normal when Redis isn't running.

### B.3 — Create the `filing_event` table (Step 13c)

```bash
cd services/api
python scripts/add_filing_event_table.py
```

**Expected:** `[ok] filing_event table created (or already exists)`.
The SQLite DB is created at `services/api/instance/sensybull_api.db`
(Flask resolves the relative `sqlite:///` URI against the instance folder).

### B.4 — Start Redis

```bash
docker run --rm -p 6379:6379 redis:7-alpine    # or: redis-server
```

### B.5 — REST endpoint responds (Step 13d)

Start the API in one terminal:

```bash
cd services/api && python main.py        # http://localhost:5000
```

In another terminal, register a user (this returns an `access_token`) and hit
the feed:

```bash
TOKEN=$(curl -s -X POST http://localhost:5000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Test User","email":"test@example.com","password":"secret123"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:5000/events/ -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

**Expected:** `{"events": [], "total": 0, "page": 1, "per_page": 50}` for a new
user (the feed is filtered to the user's watchlist companies). A request with
no token returns `401`.

### B.6 — End-to-end publish (Step 13e)

With Redis + the API running, publish an event and watch the API logs:

```bash
python -c "
import redis, json
r = redis.from_url('redis://localhost:6379/0')
r.publish('filing:new', json.dumps({
  'edgar_id':'e2e-test-001','signal_type':'8-K','cik':'0000320193',
  'ticker':'AAPL','exchange':'NASDAQ','company_name':'Apple Inc.',
  'filing_date':'2024-01-01T20:00:00+00:00',
  'edgar_url':'https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000320193',
  'accession_number':'0000320193-24-000001','max_tier':1,
  'items':[{'number':'5.02','title':'Departure of Directors','tier':2,
            'category':'Leadership','text':'CFO resigned.'}],
  'exhibits':[],'briefing':{'headline':'Apple CFO Resigns',
    'bullets':['CFO departed effective immediately.'],'company_context':'Apple Inc.'}}))
print('Published. Check API logs for subscriber output.')
"
```

**Expected** API log line:

```
Subscriber: stored + emitted edgar_id=e2e-test-001 ticker=AAPL tier=1 users=N
```

---

## Path C — One-shot automated check (no Redis needed)

Save the script below as `services/api/verify_integration.py`, then run it from
`services/api/` with the venv active: `python verify_integration.py`. It seeds a
user + company + watchlist in a throwaway DB, drives the subscriber's handler
directly, and exercises the REST routes — covering persistence, company
matching, watchlist fan-out, idempotency, and the wire contract.

```python
import os, json, tempfile, sys
_fd, _db = tempfile.mkstemp(suffix=".db"); os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:////{_db}"

from app import create_app, db
from app.models.user import User
from app.models.company import Company
from app.models.watchlist import Watchlist
from app.models.filing_event import FilingEvent
from app.services.realtime.subscriber import _handle_event
from flask_jwt_extended import create_access_token

app = create_app()
EVENT = {"edgar_id":"verify-001","signal_type":"8-K","cik":"0000320193",
  "ticker":"AAPL","exchange":"NASDAQ","company_name":"Apple Inc.",
  "filing_date":"2024-01-01T20:00:00+00:00","edgar_url":"https://e.x",
  "accession_number":"0000320193-24-000001","max_tier":1,
  "items":[{"number":"5.02","title":"Departure","tier":2,"category":"Leadership","text":"CFO resigned."}],
  "exhibits":[],"briefing":{"headline":"Apple CFO Resigns","bullets":["x"],"company_context":"Apple"}}

class FakeSIO:
    def __init__(self): self.emits=[]
    def emit(self,e,p=None,**k): self.emits.append((e,k.get("room")))

fails=[]
def check(c,m): print(("PASS" if c else "FAIL")+": "+m); fails.append(m) if not c else None

with app.app_context():
    db.create_all()
    u=User(name="T",email="v@x.com"); u.set_password("secret123")
    c=Company(name="Apple Inc.",ticker="AAPL",cik="0000320193")
    db.session.add_all([u,c]); db.session.flush()
    wl=Watchlist(name="L",user_id=u.id); wl.companies.append(c); db.session.add(wl); db.session.commit()
    uid, cid = u.id, c.id

fake=FakeSIO(); _handle_event(app, fake, json.dumps(EVENT))
with app.app_context():
    ev=FilingEvent.query.filter_by(edgar_id="verify-001").first()
    check(ev is not None, "event persisted")
    check(ev.company_id==cid, "company matched via ticker")
    check(ev.briefing_json["headline"]=="Apple CFO Resigns", "briefing stored")
check(any(r==f"user:{uid}" for _,r in fake.emits), "fanned out to user's room")
check(any(r=="public" for _,r in fake.emits), "fanned out to public room")

fake2=FakeSIO(); _handle_event(app, fake2, json.dumps(EVENT))   # duplicate
with app.app_context():
    check(FilingEvent.query.filter_by(edgar_id="verify-001").count()==1, "idempotent (no dup)")
check(len(fake2.emits)==0, "idempotent (no re-emit)")

with app.app_context(): tok=create_access_token(identity=uid)
cli=app.test_client()
r=cli.get("/events/", headers={"Authorization":f"Bearer {tok}"})
check(r.status_code==200 and r.get_json()["total"]==1, "GET /events/ returns the event")
check(cli.get("/events/").status_code in (401,422), "GET /events/ without JWT rejected")

os.unlink(_db)
print("\n"+("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED")); sys.exit(1 if fails else 0)
```

**Expected:** all `PASS` lines, then `ALL CHECKS PASSED`. The background-thread
Redis "connection refused" lines on stderr are harmless.

> This is a temporary helper — delete it (or keep it under a `tests/` folder) when
> you're done; don't commit it to the repo root.

---

## Committing & pushing follow-up changes

Both repos already track the branch and push to `origin`. For any further edits:

```bash
# --- in sensybull-api (the monorepo) ---
git add services/ docker-compose.yml README.md .env.example .gitignore
git commit -m "Your message"
git push -u origin claude/sensybull-8k-ingest-integration-eCKoI

# --- in 8k (the upstream streamer) ---
git add services/ README.md
git commit -m "Your message"
git push -u origin claude/sensybull-8k-ingest-integration-eCKoI
```

If a push fails on a network error, retry with backoff (2s, 4s, 8s, 16s).

**Never commit:** `.env`, `*.db`, `instance/`, `seen.json`, `.venv/`,
`__pycache__/` — all are already in `.gitignore`. Stage files by name rather
than `git add -A` so a stray secret or DB never sneaks in.

---

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `pyo3_runtime.PanicException` importing `cryptography` | A system/Debian `cryptography` clashing with pip's. Use a clean venv created **without** `--system-site-packages`. |
| `create_app` import error on `flask_socketio`/`redis` | Deps not installed in the active venv: `pip install -r requirements.txt`. |
| `filing_event` table "missing" when you query a stray `*.db` | The real DB is `services/api/instance/sensybull_api.db` (Flask instance path), not `services/api/sensybull_api.db`. |
| Publish shows `subscribers reached: 0` | The API isn't running, or its subscriber hasn't connected to Redis yet — start the API first and give it ~1s. |
| Event persisted but not in `GET /events/` | `/events/` is filtered to the user's watchlist companies. Add a `Company` matching the event's ticker/CIK to one of the user's watchlists (see Path C for the shape). |
