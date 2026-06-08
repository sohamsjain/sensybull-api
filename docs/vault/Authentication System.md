# Authentication System

Sensybull supports email/password and Google OAuth login, with JWT tokens for stateless API authentication.

---

## Auth Flow Overview

```
┌─────────────┐          ┌─────────────┐          ┌─────────────┐
│   Register  │          │    Login     │          │ Google OAuth │
│  /auth/reg  │          │ /auth/login  │          │ /auth/google │
└──────┬──────┘          └──────┬──────┘          └──────┬──────┘
       │                        │                        │
       │  Create user           │  Verify password       │  Verify Google JWT
       │  Hash password         │  (bcrypt)               │  Find/create user
       │  Send verify email     │                        │
       │                        │                        │
       └────────┬───────────────┴────────────────────────┘
                │
                ▼
        Issue JWT tokens:
        ├── Access token  (short-lived, ~15 min)
        └── Refresh token (long-lived, ~30 days)
```

---

## JWT Tokens

### Why JWT?

The frontend is a separate React SPA served from a different origin. Traditional cookie-based sessions don't work well cross-origin. JWT tokens are:

- **Stateless** — The server doesn't need a session store
- **Portable** — Sent as `Authorization: Bearer <token>` header
- **Self-contained** — Carry the user ID and expiry inside the token

### Token Types

**Access Token** (~15 minutes)
- Used for every authenticated API request
- Short-lived to limit damage if stolen
- Contains: `user_id`, `exp`, `type: "access"`

**Refresh Token** (~30 days)
- Used only to get a new access token when the current one expires
- Stored in the client (localStorage)
- Single endpoint: `POST /auth/refresh`

### Auto-Refresh (Frontend)

The API client (`services/web/src/api/client.js`) intercepts 401 responses and automatically refreshes:

```
Request → 401 → POST /auth/refresh → New access token → Retry original request
```

This is transparent to the user — they stay logged in for up to 30 days without re-entering credentials.

---

## Password Security

### Hashing

Passwords are hashed with **bcrypt** before storage:

```python
password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
```

Bcrypt is intentionally slow (configurable work factor), making brute-force attacks impractical even if the database is compromised.

### Password Reset Flow

```
1. User requests reset     → POST /auth/forgot-password {email}
2. Server generates token  → 256-bit random, URL-safe
3. Token hash stored       → SHA-256(token) saved to auth_tokens table
4. Email sent              → Contains link with raw token
5. User clicks link        → POST /auth/reset-password {token, new_password}
6. Server verifies          → Hash incoming token, compare to stored hash
7. Token marked used       → used_at set, can't be reused
```

**Why hash the token?** If the database is breached, attackers can't use stored hashes to reset passwords. The raw token only exists in the email.

**Single-use:** The `used_at` timestamp ensures each token works exactly once. Replaying the same link does nothing.

**Expiry:** Configurable via `PASSWORD_RESET_TOKEN_HOURS` (default: 24 hours).

---

## Email Verification

Same flow as password reset, but for confirming email ownership:

```
1. Registration → auto-sends verification email
2. Email contains link with token
3. POST /auth/verify-email {token}
4. email_verified = true, email_verified_at = now()
```

The `@email_verified_required` decorator can be applied to routes that need confirmed email (future use for sensitive operations).

---

## Google OAuth

```
1. Frontend: Google Sign-In button → gets Google JWT
2. POST /auth/google {credential: "eyJ..."}
3. Server: verify JWT against Google's public keys
4. Server: extract email, name, google_id
5. Find existing user by google_id or email
   └─ If not found: create new user (email_verified = true)
6. Issue Sensybull JWT tokens
```

**Why verify server-side?** The Google JWT could be forged client-side. Server-side verification against Google's public keys ensures authenticity.

**Email auto-verified:** Google has already verified the email, so we mark it verified on first login.

---

## Rate Limiting

Sensitive auth endpoints are rate-limited via Flask-Limiter:

| Endpoint | Limit |
|---|---|
| `/auth/login` | 5/minute |
| `/auth/register` | 3/minute |
| `/auth/forgot-password` | 3/minute |
| `/auth/reset-password` | 5/minute |

**Backend:** In-memory for development, Redis for production (`RATELIMIT_STORAGE_URI`).

---

## Email Service

The email system is pluggable — configured via `MAIL_PROVIDER`:

| Provider | Use Case |
|---|---|
| `console` | Development — prints to stdout |
| `smtp` | Self-hosted SMTP server |
| `resend` | Production — Resend API |

**Templates:** Jinja2 HTML templates rendered by `email/renderer.py`. Includes verification, password reset, welcome, and password-changed emails.

---

## See Also

- [[Data Model]] — User and AuthToken schemas
- [[API Routes Reference]] — Auth endpoint details
- [[Technology Decisions]] — Why JWT, bcrypt, Resend
