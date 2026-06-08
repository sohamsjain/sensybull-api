# Deployment Guide

> Covers Phase 4 of the launch plan: host, domain, backups, and email.

---

## 1. Deploy to Render

The repo includes a `render.yaml` blueprint that provisions everything:
- **sensybull-api** — web service (Docker, Flask + Socket.IO)
- **sensybull-ingest** — background worker (Docker, EDGAR poller)
- **sensybull-db** — managed PostgreSQL
- **sensybull-redis** — managed Redis

### Steps

1. Push the repo to GitHub (if not already).
2. Go to [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**.
3. Connect the GitHub repo and select the branch.
4. Render reads `render.yaml` and shows the resources it will create. Click **Apply**.
5. After creation, go to each service's **Environment** tab and fill in the `sync: false` values:
   - `FRONTEND_URL` — your production frontend URL (e.g. `https://app.sensybull.com`)
   - `CORS_ALLOWED_ORIGINS` — same as FRONTEND_URL (comma-separated if multiple)
   - `SENTRY_DSN` — from your Sentry project (optional, but recommended)
   - `RESEND_API_KEY` — from [Resend dashboard](https://resend.com/api-keys)
   - `MAIL_FROM_ADDRESS` — e.g. `alerts@sensybull.com` (must match verified domain)
   - `SEC_USER_AGENT` — `Your Name your@email.com` (required by SEC fair-access policy)
   - `GROQ_API_KEYS` — comma-separated Groq API keys (on ingest service)
6. The API service runs `flask db upgrade && python main.py` on startup — migrations run automatically.

### Verify end-to-end

1. Hit `https://<your-api>.onrender.com/health` — should return `{"status": "ok", "api": "ok", "redis": "ok", "database": "ok"}`
2. Hit `https://<your-api>.onrender.com/docs` — Swagger UI should load
3. Register a user via `POST /api/v1/auth/register`
4. Check that the ingest worker logs show `Polling every Xs` (Render dashboard → Logs)
5. Wait for an 8-K filing to come through, then check `GET /api/v1/events/all` — should have events

---

## 2. Custom Domain + HTTPS

Render provides automatic HTTPS (Let's Encrypt) for custom domains.

### Steps

1. In Render dashboard → **sensybull-api** → **Settings** → **Custom Domains**.
2. Add your domain (e.g. `api.sensybull.com`).
3. Render gives you a CNAME target. Add a DNS record at your registrar:
   ```
   CNAME  api.sensybull.com  →  <your-service>.onrender.com
   ```
4. Wait for DNS propagation (usually < 5 minutes, can take up to 48 hours).
5. Render automatically provisions a TLS certificate.
6. Update env vars:
   - `FRONTEND_URL` → your production frontend domain
   - `CORS_ALLOWED_ORIGINS` → your production frontend domain

---

## 3. Database Backups

Render's managed Postgres (Starter plan and above) includes:
- **Automatic daily backups** with 7-day retention
- **Point-in-time recovery** (PITR) on paid plans

### Verify

1. Render dashboard → **sensybull-db** → **Backups** tab
2. Confirm daily backups are listed
3. Test a restore at least once:
   - Create a new Postgres instance from a backup
   - Point a test API instance at it
   - Verify data is intact

### Manual backup (optional)

```bash
# From a machine that can reach the Render Postgres external URL:
pg_dump "$RENDER_EXTERNAL_DATABASE_URL" > sensybull_backup_$(date +%Y%m%d).sql
```

---

## 4. Email Deliverability

For transactional emails (verification, password reset) to reach inboxes reliably:

### Resend domain verification

1. Go to [Resend → Domains](https://resend.com/domains) → **Add Domain**.
2. Enter your sending domain (e.g. `sensybull.com`).
3. Resend provides DNS records to add at your registrar:

### DNS records to add

| Type  | Name                          | Value                          | Purpose |
|-------|-------------------------------|--------------------------------|---------|
| TXT   | `sensybull.com`               | `v=spf1 include:resend.com ~all` | SPF     |
| CNAME | `resend._domainkey.sensybull.com` | *(provided by Resend)*       | DKIM    |
| TXT   | `_dmarc.sensybull.com`        | `v=DMARC1; p=quarantine; rua=mailto:dmarc@sensybull.com` | DMARC |

4. Click **Verify** in Resend after adding records (propagation takes 5–60 minutes).
5. Update env vars:
   - `MAIL_FROM_ADDRESS` → `alerts@sensybull.com` (or your chosen address)
   - `MAIL_PROVIDER` → `resend`

### Test deliverability

1. Register a new user with a real email address
2. Check inbox — verification email should arrive (not spam)
3. Check Resend dashboard → **Emails** for delivery status
4. Test with Gmail, Outlook, and a corporate email if possible

---

## Alternative: Docker Compose on a VPS

If you prefer a VPS (e.g. Hetzner, DigitalOcean), the repo includes `docker-compose.prod.yml`:

```bash
# On the VPS:
export POSTGRES_PASSWORD=<strong-random-password>
# Create services/api/.env and services/ingest/.env with production values
docker compose -f docker-compose.prod.yml up -d

# Run migrations:
docker compose -f docker-compose.prod.yml exec api flask db upgrade
```

You'll need to handle TLS yourself (Caddy or nginx + Let's Encrypt) and set up a cron job for Postgres backups.
