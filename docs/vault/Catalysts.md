# Catalysts

Catalysts are **upcoming dates** extracted from filing briefings that represent future events investors should watch for.

---

## What's a Catalyst?

In event-driven investing, a "catalyst" is a future event that could move a stock price. Examples:

- **"Shareholder vote on merger — 2024-04-15"** → If approved, stock likely moves to deal price
- **"Debt maturity date — 2024-06-01"** → Company must refinance or pay; liquidity risk
- **"FDA advisory committee meeting — 2024-03-20"** → Drug approval decision incoming
- **"Earnings release — 2024-05-02"** → Quarterly results reported

---

## How They're Extracted

The [[Groq LLM]] extracts catalysts from the filing text as part of the briefing:

```json
{
  "briefing": {
    "catalysts": [
      {"date": "2024-04-15", "event": "Shareholder vote on proposed merger"},
      {"date": "2024-06-01", "event": "Senior notes maturity date"}
    ]
  }
}
```

The LLM looks for:
- Explicit dates mentioned in the filing text
- Regulatory deadlines implied by the event type
- Scheduled events referenced in press releases (EX-99.x exhibits)

---

## Storage

Each catalyst becomes a row in the `catalyst` table:

| Column | Purpose |
|---|---|
| `catalyst_date` | The date (indexed for range queries) |
| `event_description` | What happens on that date |
| `ticker` | Quick reference (denormalized) |
| `company_name` | Quick reference (denormalized) |
| `filing_event_id` | Link back to the source filing |

**Why denormalize ticker and company_name?** The `/events/catalysts` endpoint returns a list of upcoming dates with company context. Joining through FilingEvent → Company for each row would be expensive. The denormalized fields make the query a simple:

```sql
SELECT * FROM catalyst
WHERE catalyst_date >= :today
ORDER BY catalyst_date ASC
```

---

## API Endpoint

```
GET /events/catalysts
```

Returns upcoming catalyst dates across all companies. No authentication required — this is a public discovery endpoint.

Use case: "What's coming up this week that could move stocks?"

---

## See Also

- [[Groq LLM]] — How catalysts are extracted
- [[Data Model]] — Catalyst table schema
- [[API Routes Reference]] — Endpoint details
