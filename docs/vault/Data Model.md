# Data Model

The database schema captures three domains: **users & access control**, **companies & watchlists**, and **filing events & signals**.

---

## Entity Relationship Diagram

```
┌──────────┐         ┌─────────────┐         ┌───────────┐
│   User   │────1:N──│  Watchlist   │──N:M────│  Company  │
└──────────┘         └─────────────┘         └─────┬─────┘
     │                                              │
     │                                            1:N
  1:N│                                              │
     │                                       ┌──────▼──────┐
┌────▼─────┐                                 │ FilingEvent  │
│AuthToken │                                 └──────┬──────┘
└──────────┘                                    │       │
                                              1:N     1:N
                                                │       │
                                         ┌──────▼──┐ ┌──▼───────┐
                                         │EventType│ │ Catalyst  │
                                         └─────────┘ └──────────┘
```

---

## Core Entities

### User

**File:** `services/api/app/models/user.py`

The user account. Supports both email/password and Google OAuth login.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | String(100) | Display name |
| `email` | String(120) | Unique, required |
| `phone` | String(20) | Optional |
| `password_hash` | String(128) | Bcrypt hash (nullable for OAuth-only users) |
| `google_id` | String(100) | Google OAuth subject ID |
| `email_verified` | Boolean | Default false |
| `email_verified_at` | DateTime | Timestamp of verification |
| `role` | String(20) | Default "user" (future: "admin") |
| `created_at` | DateTime | Auto-set |
| `updated_at` | DateTime | Auto-updated |

**Relationships:**
- `watchlists` → 1:N Watchlist (cascade delete)
- `auth_tokens` → 1:N AuthToken (cascade delete)

---

### Company

**File:** `services/api/app/models/company.py`

A publicly traded company, loaded from SEC EDGAR's company database.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | String(200) | Company name |
| `ticker` | String(20) | Stock ticker, unique |
| `cik` | String(20) | SEC Central Index Key, indexed |
| `sic` | String(10) | Standard Industrial Classification code |
| `state_of_incorporation` | String(10) | State abbreviation |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

**Relationships:**
- `filings` → 1:N Filing
- `filing_events` → 1:N FilingEvent
- `watchlists` → N:M Watchlist (via join table)

**How companies get loaded:** See [[Company Loading]] — bulk-imported from SEC's `company_tickers.json` on first startup, or via the `load_companies.py` script.

---

### Watchlist

**File:** `services/api/app/models/watchlist.py`

A user-defined collection of companies they want to monitor. See [[Watchlists]] for the concept.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | String(100) | e.g., "Tech Watchlist" |
| `description` | Text | Optional |
| `user_id` | UUID (FK) | Owner |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

**Relationships:**
- `user` → N:1 User
- `companies` → N:M Company (via `watchlist_companies` join table)

**Join table: `watchlist_companies`**
| Column | Type |
|---|---|
| `watchlist_id` | UUID (FK) |
| `company_id` | UUID (FK) |

---

### FilingEvent — The Core Signal

**File:** `services/api/app/models/filing_event.py`

This is the star of the schema. Each row represents one processed 8-K filing with its AI-generated briefing.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `edgar_id` | String | **Unique** — the dedup key from ingest |
| `signal_type` | String | Always "8-K" for now |
| `company_id` | UUID (FK) | Nullable (resolved on persistence) |
| `cik` | String | SEC company ID |
| `ticker` | String | Stock symbol |
| `company_name` | String | |
| `filing_date` | DateTime | When filed with SEC |
| `edgar_url` | String | Link to original filing |
| `accession_number` | String | SEC's filing ID |
| `max_tier` | Integer | 1=critical, 2=important, 3=routine |
| `items_json` | Text (JSON) | Parsed items array |
| `exhibits_json` | Text (JSON) | Exhibit metadata array |
| `briefing_json` | Text (JSON) | Full AI briefing object |
| `event_types_json` | Text (JSON) | List of classified type names |
| `created_at` | DateTime | When we processed it |

**Relationships:**
- `company` → N:1 Company
- `event_types` → 1:N EventType (cascade delete)
- `catalysts` → 1:N Catalyst (cascade delete)

**Why denormalized JSON columns?** The items, exhibits, and briefing are complex nested structures that are always loaded together. Storing them as JSON avoids N+1 queries and complex joins. The structured data (event types, catalysts) is *also* stored in normalized tables for querying — this is intentional duplication for different access patterns.

---

### EventType

**File:** `services/api/app/models/event_type.py`

Normalized storage of event classifications. One filing can have 1-3 event types.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `filing_event_id` | UUID (FK) | Parent event, cascade delete |
| `type_name` | String | Canonical label from [[Event Type Taxonomy]] |
| `attributes` | Text (JSON) | Flexible metadata (e.g., deal value, counterparty) |

**Why a separate table?** To enable queries like "show me all Acquisition events" or "what event types occurred this week." The `event_types_json` on FilingEvent is for display; this table is for filtering and aggregation.

---

### Catalyst

**File:** `services/api/app/models/catalyst.py`

Future dates extracted from the AI briefing. See [[Catalysts]] for the concept.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `filing_event_id` | UUID (FK) | Source event, cascade delete |
| `event_description` | String | What happens on this date |
| `catalyst_date` | Date | **Indexed** for date range queries |
| `ticker` | String | Quick reference |
| `company_name` | String | Quick reference |

**Why denormalize ticker/company_name?** The catalysts endpoint (`/events/catalysts`) needs to return upcoming dates with company context. Denormalizing avoids a join through FilingEvent → Company for every catalyst row.

---

### AuthToken

**File:** `services/api/app/models/auth_token.py`

Single-use tokens for email verification and password reset. See [[Authentication System]] for the full flow.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `user_id` | UUID (FK) | Token owner, cascade delete |
| `token_hash` | String | **SHA-256 hash** of the token (raw token never stored) |
| `purpose` | Enum | `email_verify` or `password_reset` |
| `expires_at` | DateTime | Configurable expiry |
| `used_at` | DateTime | Null until used (single-use enforcement) |
| `ip` | String | Requester IP (audit trail) |
| `user_agent` | String | Requester user agent (audit trail) |

**Composite index:** `(token_hash, purpose)` — for fast lookup during verification.

**Why hash the token?** If the database is compromised, an attacker can't use stored token hashes to verify emails or reset passwords. The raw token exists only in the email sent to the user.

---

### Filing (Legacy)

**File:** `services/api/app/models/filing.py`

Historical filing records. This predates the FilingEvent system and stores raw filing metadata without AI enrichment. Still in the schema but not actively used for the real-time pipeline.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | Primary key |
| `company_id` | UUID (FK) | |
| `form_type` | String | "8-K", "10-K", etc. |
| `filing_date` | Date | |
| `accession_number` | String | Unique |
| `document_url` | String | |

---

## Database Choice

See [[Technology Decisions#SQLite (not Postgres)]] for why we use SQLite with WAL mode. The key takeaway: single-writer, multi-reader is fine for our workload. The ORM makes Postgres migration a config change.

---

## See Also

- [[Watchlists]] — How users curate their company universe
- [[Event Type Taxonomy]] — The 34 canonical labels
- [[Catalysts]] — Upcoming dates from filings
- [[Authentication System]] — Token model details
- [[Idempotency & Deduplication]] — How `edgar_id` prevents duplicates
