# API Routes Reference

Complete reference for all REST endpoints served by the [[API Service]].

---

## Authentication — `/auth`

| Method | Path                    | Auth          | Description                        |
| ------ | ----------------------- | ------------- | ---------------------------------- |
| POST   | `/auth/register`        | No            | Create account, returns JWT        |
| POST   | `/auth/login`           | No            | Login with email/password          |
| POST   | `/auth/google`          | No            | Login with Google OAuth            |
| POST   | `/auth/refresh`         | Refresh token | Get new access token               |
| POST   | `/auth/verify-email`    | No            | Confirm email with token           |
| POST   | `/auth/forgot-password` | No            | Request password reset email       |
| POST   | `/auth/reset-password`  | No            | Reset password with token          |
| POST   | `/auth/change-password` | JWT           | Change password (requires current) |

See [[Authentication System]] for flow details.

---

## Events — `/events`

The main data endpoints. FilingEvents are the core signal.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/events/` | JWT | Events for user's [[Watchlists\|watchlist]] companies |
| GET | `/events/all` | No | All events (unfiltered, public) |
| GET | `/events/<id>` | JWT | Single event detail |
| GET | `/events/company/<id>` | JWT | Events for one company |
| GET | `/events/types` | No | List of 34 canonical event type labels |
| GET | `/events/catalysts` | No | Upcoming [[Catalysts\|catalyst]] dates |

### Query Parameters (for list endpoints)

| Param | Type | Description |
|---|---|---|
| `page` | int | Page number (default: 1) |
| `per_page` | int | Items per page (default: 20) |
| `max_tier` | int | Filter: only events at this tier or more critical (1, 2, or 3) |
| `signal_type` | string | Filter by signal type (currently only "8-K") |
| `event_type` | string | Filter by [[Event Type Taxonomy\|event type]] label |

### Example

```
GET /events/all?max_tier=2&event_type=Acquisition&page=1&per_page=10

→ Tier 1-2 acquisition events, first page of 10
```

---

## Users — `/users`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/users/me` | JWT | Current user basic info |
| GET | `/users/profile` | JWT | Extended profile |
| PATCH | `/users/profile` | JWT | Update name, phone |

---

## Companies — `/companies`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/companies/` | No | Paginated company list |
| GET | `/companies/<id>` | No | Company detail |
| GET | `/companies/search` | No | Search by ticker or name |

### Search

```
GET /companies/search?q=apple

→ Companies matching "apple" in name or ticker
```

---

## Watchlists — `/watchlists`

All endpoints require JWT authentication. Users can only access their own watchlists.

| Method | Path | Description |
|---|---|---|
| GET | `/watchlists/` | List user's watchlists |
| POST | `/watchlists/` | Create watchlist |
| GET | `/watchlists/<id>` | Get watchlist with companies |
| PUT | `/watchlists/<id>` | Update name/description |
| DELETE | `/watchlists/<id>` | Delete watchlist |
| POST | `/watchlists/<id>/companies` | Add company |
| DELETE | `/watchlists/<id>/companies/<cid>` | Remove company |

---

## Filings — `/filings`

Legacy endpoints for raw filing records (predates FilingEvent system).

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/filings/` | No | List historical filings |
| POST | `/filings/` | JWT | Create filing record |

---

## WebSocket — `/feed` namespace

Not REST, but included for completeness. See [[Real-Time System]] for details.

| Direction | Event | Description |
|---|---|---|
| Server → Client | `filing_event` | New filing event (real-time) |
| Server → Client | `connected` | Auth confirmed, session ready |
| Server → Client | `subscribed` | Ticker subscription confirmed |
| Client → Server | `subscribe_ticker` | Subscribe to specific ticker |

---

## Error Responses

All endpoints return consistent error JSON:

```json
{
  "error": "Description of what went wrong",
  "status": 400
}
```

| Status | Meaning |
|---|---|
| 400 | Bad request (missing/invalid params) |
| 401 | Not authenticated (missing/expired JWT) |
| 403 | Not authorized (wrong user, unverified email) |
| 404 | Resource not found |
| 429 | Rate limited |
| 500 | Server error |

---

## See Also

- [[API Service]] — Server architecture
- [[Authentication System]] — Auth flow
- [[Watchlists]] — Watchlist concept
- [[Event Type Taxonomy]] — Event type labels
