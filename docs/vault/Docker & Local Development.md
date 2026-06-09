# Docker & Local Development

The entire Sensybull stack runs with a single command using Docker Compose.

---

## Quick Start

```bash
# 1. Clone and configure
cp services/api/.env.example services/api/.env
cp services/ingest/.env.example services/ingest/.env
# Edit .env files with your API keys

# 2. Start everything
docker-compose up

# Services:
# - API:      http://localhost:5000
# - Web:      http://localhost:3000
# - Redis:    localhost:6379
```

---

## Docker Compose Services

```yaml
services:
  redis:        # Message broker
  api:          # Flask backend
  ingest:       # SEC filing poller
  web:          # React frontend
```

### Redis

```yaml
redis:
  image: redis:7-alpine
  ports: ["6379:6379"]
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
```

Alpine-based for minimal footprint (~13MB). Health check ensures dependent services wait for Redis to be ready.

### API

```yaml
api:
  build: ./services/api
  depends_on:
    redis: { condition: service_healthy }
  ports: ["5000:5000"]
  command: bash -c "flask db upgrade && python main.py"
  volumes:
    - ./services/api:/app
```

**Startup sequence:**
1. Wait for Redis health check
2. Run database migrations (`flask db upgrade`)
3. Start the Flask-SocketIO server (`python main.py`)

**Volume mount:** `./services/api:/app` enables live code reloading — edit Python files and restart the process.

### Ingest

```yaml
ingest:
  build: ./services/ingest
  depends_on:
    redis: { condition: service_healthy }
  command: python main.py
  volumes:
    - ./services/ingest:/app
```

No port exposure — the ingest service only publishes to Redis. The `seen.json` file persists in the volume mount.

### Web

```yaml
web:
  build: ./services/web
  ports: ["3000:3000"]
  command: npm run dev -- --host 0.0.0.0
  volumes:
    - ./services/web:/app
    - /app/node_modules     # Exclude host node_modules
```

**`--host 0.0.0.0`** — Makes Vite's dev server accessible from outside the container.

**`/app/node_modules`** — Anonymous volume prevents the host's `node_modules` (if any) from overwriting the container's. This is a common Docker + Node.js pattern.

---

## Networking

All services share the `sensybull-net` Docker network. Services reference each other by name:

```
api → redis:6379        (Redis connection)
ingest → redis:6379     (Redis connection)
web → localhost:5000    (API via host port mapping)
```

---

## Environment Files

### `services/api/.env`

```bash
# Required
SECRET_KEY=your-secret-key
JWT_SECRET_KEY=your-jwt-secret
REDIS_URL=redis://redis:6379/0
SEC_USER_AGENT="Your Name your@email.com"

# Optional
DATABASE_URL=sqlite:///sensybull.db
# RESEND_API_KEY=               # Emails disabled when unset
GOOGLE_CLIENT_ID=               # For OAuth
FRONTEND_URL=http://localhost:3000
```

### `services/ingest/.env`

```bash
# Required
REDIS_URL=redis://redis:6379/0
SEC_USER_AGENT="Your Name your@email.com"
GROQ_API_KEY=your-groq-key     # Or GROQ_API_KEYS=key1,key2

# Optional (no other config needed)
```

---

## Running Without Docker

For faster iteration or debugging:

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: API
cd services/api
pip install -r requirements.txt
python main.py

# Terminal 3: Ingest
cd services/ingest
pip install -r requirements.txt
python main.py

# Terminal 4: Web
cd services/web
npm install
npm run dev
```

---

## Database Management

### Migrations

```bash
# Inside the api container or directory
flask db migrate -m "Add new column"   # Generate migration
flask db upgrade                        # Apply migrations
flask db downgrade                      # Rollback last migration
```

Migrations are stored in `services/api/migrations/versions/`.

### Reset Database

```bash
# Delete the SQLite file and re-run migrations
rm services/api/sensybull.db
flask db upgrade
# Companies will auto-load from SEC on next API startup
```

---

## See Also

- [[Architecture Overview]] — Service topology
- [[API Service]] — API startup details
- [[Ingest Service]] — Ingest startup details
- [[Company Loading]] — How companies are imported
