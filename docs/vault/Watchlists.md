# Watchlists

Watchlists are how users define their **investment universe** — the set of companies they care about. They're the primary access control mechanism for event delivery.

---

## Concept

A watchlist is a named collection of companies:

```
"Tech Watchlist"
├── AAPL (Apple Inc.)
├── MSFT (Microsoft Corp.)
├── NVDA (NVIDIA Corp.)
└── GOOGL (Alphabet Inc.)

"Biotech Watchlist"
├── MRNA (Moderna Inc.)
└── PFE (Pfizer Inc.)
```

Users can have multiple watchlists. A company can appear in multiple watchlists.

---

## How Watchlists Drive Event Delivery

### REST API

When a user requests `/events/`, the API filters events to only show companies in their watchlists:

```sql
SELECT fe.* FROM filing_event fe
JOIN company c ON fe.company_id = c.id
JOIN watchlist_companies wc ON c.id = wc.company_id
JOIN watchlist w ON wc.watchlist_id = w.id
WHERE w.user_id = :current_user_id
ORDER BY fe.created_at DESC
```

No watchlist = no events (use `/events/all` for unfiltered access).

### WebSocket

When a new filing event arrives, the subscriber checks which users have the company in any watchlist:

```python
watchlists = Watchlist.query.filter(
    Watchlist.companies.any(Company.id == company_id)
).all()

user_ids = {w.user_id for w in watchlists}

for uid in user_ids:
    socketio.emit('filing_event', data, room=f'user:{uid}')
```

Only users watching the company receive the push notification.

### Connection Replay

When a user connects via WebSocket, the server replays the last 50 Tier 1-2 events **filtered to their watchlist**. This ensures they see relevant history, not every filing that occurred.

---

## CRUD Operations

| Endpoint | Action |
|---|---|
| `GET /watchlists/` | List user's watchlists |
| `POST /watchlists/` | Create a new watchlist |
| `GET /watchlists/:id` | Get watchlist with companies |
| `PUT /watchlists/:id` | Update name/description |
| `DELETE /watchlists/:id` | Delete watchlist |
| `POST /watchlists/:id/companies` | Add company to watchlist |
| `DELETE /watchlists/:id/companies/:cid` | Remove company |

All endpoints require JWT authentication. Users can only manage their own watchlists.

---

## Data Model

```
User (1) ──► (N) Watchlist (N) ──► (M) Company
                                      via watchlist_companies
```

The many-to-many relationship uses a join table (`watchlist_companies`) with no extra columns — just two foreign keys.

**Cascade delete:** Deleting a user cascades to their watchlists. Deleting a watchlist removes the join table entries but not the companies.

---

## See Also

- [[Data Model]] — Full schema details
- [[Real-Time System]] — How watchlists drive WebSocket fan-out
- [[API Routes Reference]] — Watchlist endpoints
- [[Authentication System]] — JWT requirement for watchlist access
