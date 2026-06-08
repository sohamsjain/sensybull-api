# Idempotency & Deduplication

Sensybull has two layers of deduplication to ensure each filing is processed and stored exactly once, even in the face of retries, restarts, and race conditions.

---

## Layer 1: Ingest-Side Dedup (seen.json)

**File:** `services/ingest/seen.py`

Before processing a filing, the ingest service checks if the entry ID has been seen before:

```python
seen = load_seen()  # {"entry_id": "2024-03-15T14:30:00", ...}

if entry_id in seen:
    continue  # Skip — already processed

# ... process filing ...

seen[entry_id] = datetime.now().isoformat()
save_seen(seen)
```

**Properties:**
- Persisted to disk as JSON (survives process restarts)
- Pruned: entries older than 30 days are removed (keeps file small)
- Atomic writes: write to temp file → rename (no corruption on crash)

**What it prevents:** Re-processing the same filing on the next poll cycle. Without this, every 10-minute poll would re-process all 40 filings from the feed.

---

## Layer 2: API-Side Dedup (edgar_id unique constraint)

**File:** `services/api/app/services/realtime/subscriber.py`

When the subscriber receives a Redis message, it checks the database before persisting:

```python
existing = FilingEvent.query.filter_by(edgar_id=data['edgar_id']).first()
if existing:
    return  # Skip — already in database
```

The `edgar_id` column on `FilingEvent` also has a **unique constraint** in the database, providing a final safety net.

**What it prevents:**
- Duplicate persistence if the ingest service publishes the same event twice (e.g., after a crash/restart)
- Race conditions if multiple subscriber instances run simultaneously (future scaling scenario)

---

## Why Two Layers?

```
Layer 1 (seen.json)     → Prevents redundant work (API calls, LLM calls, Redis publishes)
Layer 2 (edgar_id)      → Prevents duplicate database records
```

Layer 1 is an optimization — it avoids wasting Groq API calls and Redis bandwidth. But it's not a guarantee (the file could be lost, the process could crash between publishing and saving).

Layer 2 is the guarantee — even if Layer 1 fails, the database won't have duplicates.

---

## Edge Cases

| Scenario | What happens |
|---|---|
| Ingest crashes after Groq call, before saving seen.json | Re-processes on restart. Layer 2 prevents duplicate DB entry. |
| Redis message delivered twice | Layer 2 catches it. |
| seen.json deleted | All filings re-processed. All re-published to Redis. Layer 2 prevents duplicates. |
| Two ingest instances running | Both might process same filing. Both publish to Redis. Layer 2 deduplicates. |
| API restarts while ingest publishes | Messages lost (pub/sub is fire-and-forget). Next poll cycle re-publishes. Layer 2 deduplicates. |

---

## The `edgar_id` Key

The `edgar_id` is derived from the EDGAR feed entry. It's a combination of the accession number and any entry-specific identifiers. It's globally unique across all SEC filings.

```
edgar_id = "0001234567-24-012345"  # Accession number format
```

This ID is set by the ingest service and travels through the entire pipeline — Redis message → subscriber → database. It's the single identifier that ties all layers together.

---

## See Also

- [[Ingest Pipeline Deep Dive]] — Where Layer 1 operates
- [[Real-Time System]] — Where Layer 2 operates
- [[Data Model]] — `edgar_id` unique constraint
