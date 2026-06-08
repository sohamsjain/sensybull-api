# Redis Pub-Sub Contract

The JSON schema published on Redis channel `filing:new` is the **only interface** between the [[Ingest Service]] and the [[API Service]]. Neither service imports code from the other. This contract is the boundary.

---

## Channel

```
filing:new
```

One publisher (ingest), one subscriber (API). Messages are fire-and-forget — if the subscriber is down, the message is lost. [[Idempotency & Deduplication]] handles recovery.

---

## Message Schema

```json
{
  "edgar_id": "0001234567-24-012345",
  "signal_type": "8-K",
  "cik": "0001234567",
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "filing_date": "2024-03-15T16:30:00",
  "edgar_url": "https://www.sec.gov/Archives/edgar/data/...",
  "accession_number": "0001234567-24-012345",
  "max_tier": 1,

  "items": [
    {
      "number": "5.02",
      "title": "Departure of Directors or Certain Officers",
      "tier": 2,
      "category": "Leadership",
      "text": "On March 14, 2024, John Smith notified..."
    }
  ],

  "exhibits": [
    {
      "type": "EX-99.1",
      "description": "Press Release dated March 15, 2024",
      "url": "https://www.sec.gov/Archives/edgar/data/..."
    }
  ],

  "briefing": {
    "headline": "Apple CFO Departure Signals Leadership Transition",
    "summary": "Apple Inc. announced the departure of...",
    "primary_event_type": "Leadership Change",
    "significance": "High",
    "sentiment": "Neutral",
    "investor_takeaway": "Watch for successor announcement...",
    "catalysts": [
      {
        "date": "2024-06-01",
        "event": "New CFO appointment expected"
      }
    ],
    "deal_terms": {}
  },

  "event_types": ["Leadership Change"]
}
```

---

## Field Reference

### Top-Level

| Field | Type | Required | Description |
|---|---|---|---|
| `edgar_id` | string | Yes | Unique dedup key (accession number) |
| `signal_type` | string | Yes | Always `"8-K"` for now |
| `cik` | string | Yes | SEC Central Index Key |
| `ticker` | string | Yes | Stock symbol (from SEC ticker map) |
| `company_name` | string | Yes | Full company name |
| `filing_date` | ISO-8601 | Yes | When filed with SEC |
| `edgar_url` | string | Yes | Link to filing on EDGAR |
| `accession_number` | string | Yes | SEC's filing identifier |
| `max_tier` | integer | Yes | 1 (critical), 2 (important), or 3 (routine) |
| `items` | array | Yes | Parsed 8-K items |
| `exhibits` | array | Yes | Exhibit metadata (may be empty) |
| `briefing` | object | Yes | AI-generated briefing (may be null on failure) |
| `event_types` | array | Yes | 1-3 canonical labels |

### Items Array

| Field | Type | Description |
|---|---|---|
| `number` | string | Item number (e.g., "5.02") |
| `title` | string | Official SEC item title |
| `tier` | integer | 1, 2, or 3 |
| `category` | string | Grouping (e.g., "Leadership", "Financial") |
| `text` | string | Extracted and cleaned item body text |

### Briefing Object

| Field | Type | Description |
|---|---|---|
| `headline` | string | Max 100 chars, investor-focused |
| `summary` | string | 2-4 sentences |
| `primary_event_type` | string | Single canonical label |
| `significance` | enum | "High", "Medium", or "Low" |
| `sentiment` | enum | "Positive", "Negative", "Neutral", or "Mixed" |
| `investor_takeaway` | string | One actionable sentence |
| `catalysts` | array | `[{date, event}]` — upcoming dates |
| `deal_terms` | object | Flexible: counterparty, deal_value, premium, etc. |

---

## How the Subscriber Consumes This

See [[Real-Time System]] for the full handler. In summary:

1. **`edgar_id`** → Idempotency check
2. **`ticker` / `cik`** → Company resolution (match or auto-create)
3. **`items`, `exhibits`, `briefing`** → Stored as JSON columns on FilingEvent
4. **`event_types`** → Stored both as JSON and normalized EventType rows
5. **`briefing.catalysts`** → Stored as Catalyst rows
6. **`max_tier`** → Stored on FilingEvent for filtering

---

## Evolving the Contract

Since the services are in the same monorepo, contract changes are atomic:
1. Update the `events.py` dataclass in ingest
2. Update the `_handle_event()` handler in the subscriber
3. Both changes ship in the same commit

**Adding a field:** The subscriber should handle missing fields gracefully (`.get()` with defaults) so that old messages in transit don't break.

**Removing a field:** Remove from publisher first, then remove from subscriber after a deploy cycle.

---

## See Also

- [[Architecture Overview]] — Why this contract exists
- [[Ingest Pipeline Deep Dive]] — What builds the message
- [[Real-Time System]] — What consumes the message
- [[Idempotency & Deduplication]] — What happens when messages are duplicated or lost
