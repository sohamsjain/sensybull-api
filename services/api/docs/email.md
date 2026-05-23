# Email Integration Guide

Sensybull API sends transactional email for the following customer
journeys:

| Trigger | Template | Expiry |
|---|---|---|
| Email/password signup | `verify_email` | 24h |
| First verification or Google signup | `welcome` | n/a |
| `POST /auth/forgot-password` | `password_reset` | 1h, single-use |
| `POST /auth/reset-password` / `/change-password` | `password_changed` | n/a |

All email is routed through `app/services/email/` which provides a
provider-agnostic `MailClient` abstraction with three backends:

- **console** (default, dev) — prints the rendered message to stdout.
- **resend** — uses the [Resend](https://resend.com) API. Recommended for
  production with a custom domain.
- **smtp** — any SMTP server (Google Workspace, Zoho, Microsoft 365,
  AWS SES SMTP, MailHog locally).

Emails are dispatched via a background `ThreadPoolExecutor` so the HTTP
response is not blocked on delivery. Failures are logged but never raised
into auth flows.

---

## 1. Local development

The easiest path is the console backend — no setup required:

```bash
cp .env.example .env
# MAIL_PROVIDER=console (the default)
python scripts/apply_email_schema.py   # adds new columns + auth_token table
python main.py
```

Hit `POST /auth/register` and watch the rendered verification email print to
stdout including the token URL.

### Capturing emails with MailHog

If you want to see rendered HTML in a real inbox UI:

```bash
docker run -d -p 1025:1025 -p 8025:8025 mailhog/mailhog
```

Then in `.env`:

```
MAIL_PROVIDER=smtp
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USE_TLS=false
SMTP_USER=
SMTP_PASS=
```

Inbox: http://localhost:8025.

---

## 2. Production: Resend on a custom domain (recommended)

### Step 1 — sign up and add your domain

1. Create an account at <https://resend.com> and a team.
2. **Domains → Add Domain** → enter `yourdomain.com`.
3. Resend shows three DNS records to add at your registrar.

### Step 2 — DNS records

Add the following at your DNS host (Cloudflare, Route 53, Namecheap, etc.):

| Type | Name | Value | Purpose |
|---|---|---|---|
| TXT | `@` (root) | `v=spf1 include:_spf.resend.com ~all` | SPF — authorises Resend to send as your domain |
| CNAME | `resend._domainkey` | (value provided by Resend) | DKIM — signs outgoing messages |
| CNAME | `send` (or as provided) | (value provided by Resend) | Return-Path — bounces and feedback |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@yourdomain.com; pct=100` | DMARC — policy + reporting |

**SPF caveat**: a domain may only have **one** `v=spf1` TXT record. If you
already use Google Workspace or another provider, merge their `include:`
into a single record, e.g.:

```
v=spf1 include:_spf.google.com include:_spf.resend.com ~all
```

**DMARC progression**: start with `p=none` for 2–4 weeks, monitor the
aggregate reports (`rua=`), then tighten to `p=quarantine`, finally
`p=reject` once you're confident all legitimate sources are authenticated.

### Step 3 — verify

Wait for DNS propagation (minutes to a few hours), then click **Verify** in
Resend. All three records must show green.

### Step 4 — wire up the app

In Resend, **API Keys → Create**. Grant **sending only** scope. Copy the
key, then in your production environment:

```
MAIL_PROVIDER=resend
RESEND_API_KEY=re_xxxxxxxxxxxx
MAIL_FROM_ADDRESS=no-reply@yourdomain.com
MAIL_FROM_NAME=Sensybull
MAIL_REPLY_TO=support@yourdomain.com
FRONTEND_URL=https://app.yourdomain.com
APP_NAME=Sensybull
SUPPORT_EMAIL=support@yourdomain.com
```

### Step 5 — deliverability check

1. Send to `test-xxxx@mail-tester.com` from staging. Target **10/10**.
2. Enroll the domain in
   [Google Postmaster Tools](https://postmaster.google.com/).
3. Inspect raw headers in Gmail (⋮ → **Show original**) and confirm all of
   SPF / DKIM / DMARC say **PASS**.

---

## 3. Alternative: SMTP via Google Workspace

If you already own the Workspace mailbox for your domain and don't want a
second vendor:

1. Admin console → Users → Add `no-reply@yourdomain.com`.
2. On that user, enable **2-Step Verification** and generate an
   [App Password](https://myaccount.google.com/apppasswords).
3. Your SPF is already `include:_spf.google.com` (Google Workspace sets
   this up during onboarding) — confirm in the Admin console. DKIM must be
   enabled manually under **Apps → Gmail → Authenticate email**.
4. Add the DMARC `TXT` record as above.
5. Environment:
   ```
   MAIL_PROVIDER=smtp
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USE_TLS=true
   SMTP_USER=no-reply@yourdomain.com
   SMTP_PASS=<app password>
   MAIL_FROM_ADDRESS=no-reply@yourdomain.com
   MAIL_FROM_NAME=Sensybull
   MAIL_REPLY_TO=support@yourdomain.com
   ```
6. **Quota**: Workspace caps outbound at ~2,000 recipients/day per user.
   Fine early on, insufficient past modest scale — switch to Resend/SES.

### Zoho / Microsoft 365 / AWS SES

Same pattern, different SMTP host and SPF `include:`:

| Provider | SMTP host:port | SPF include |
|---|---|---|
| Zoho | `smtp.zoho.com:587` | `include:zoho.com` |
| Microsoft 365 | `smtp.office365.com:587` | `include:spf.protection.outlook.com` |
| AWS SES | `email-smtp.<region>.amazonaws.com:587` | `include:amazonses.com` |

---

## 4. Security & best practices (already enforced in code)

- **Opaque tokens, hashed at rest**: `secrets.token_urlsafe(32)` →
  SHA-256 stored in `auth_token.token_hash`. Raw tokens never touch the DB.
- **Constant-time compare**: `secrets.compare_digest` to block timing attacks.
- **Single-use tokens**: `used_at` is set after the first successful use;
  any reset or password change invalidates all other outstanding reset tokens.
- **Short expiries**: verification 24h, reset 1h.
- **No email enumeration**: `/auth/forgot-password` and
  `/auth/resend-verification` always return the same 200 message
  regardless of whether the account exists.
- **Rate limits**: `5/hour` on forgot-password and resend-verification,
  `10/hour` on reset/change-password.
- **Fire-and-forget**: mail is dispatched to a thread pool; a downed
  provider does not break signup / reset.
- **Custom-domain hygiene**: use `no-reply@yourdomain.com` as From and
  `support@yourdomain.com` as Reply-To.

---

## 5. Applying the schema change

Run once after pulling this change:

```bash
pip install -r requirements.txt
python scripts/apply_email_schema.py
```

It adds `email_verified` and `email_verified_at` on `user` and creates the
`auth_token` table. Idempotent — re-running is safe.

When you're ready to adopt Flask-Migrate, `flask db init` followed by
`flask db migrate -m "baseline"` will capture the current schema;
subsequent changes go through `flask db migrate && flask db upgrade`.

---

## 6. Testing the flows end-to-end

```bash
# Register
curl -X POST http://localhost:5000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada","email":"ada@example.com","password":"hunter22"}'

# Copy the token from the stdout email, then verify:
curl -X POST http://localhost:5000/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"token":"<paste-here>"}'

# Forgot password
curl -X POST http://localhost:5000/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"email":"ada@example.com"}'

# Reset
curl -X POST http://localhost:5000/auth/reset-password \
  -H "Content-Type: application/json" \
  -d '{"token":"<paste-reset-token>","new_password":"newhunter22"}'
```

You should see the corresponding email for each step printed to stdout
(console provider) or landing in MailHog / your real inbox.
