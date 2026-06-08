# Event Type Taxonomy

Sensybull classifies every 8-K filing into 1-3 canonical event types using the [[Groq LLM]]. This taxonomy enables filtering, aggregation, and pattern detection across filings.

---

## The 34 Canonical Labels

| Category | Event Types |
|---|---|
| **M&A / Deals** | M&A / Merger, Acquisition, Divestiture, Tender Offer, Going-Private |
| **Corporate Strategy** | Strategic Review, Spin-Off, Activist Proxy |
| **Capital Markets** | Shelf Registration, Share Offering, Stock Split, Capital Return, Dividend Change |
| **Financial Distress** | Restructuring, Insolvency, Liquidation, Bankruptcy, Delisting, Impairment |
| **Financial Reporting** | Earnings, Restatement |
| **Governance** | Leadership Change |
| **Legal / Regulatory** | Litigation, Regulatory Action |
| **Debt** | Debt / Financing |
| **Cyber** | Cybersecurity Incident |
| **Contracts** | Material Agreement |
| **Corporate Structure** | Domicile Change |
| **Other** | Other |

---

## How Classification Works

The [[Groq LLM]] receives the filing text and a system prompt that includes the full list of canonical labels. It returns:

```json
{
  "primary_event_type": "Acquisition",
  "event_types": ["Acquisition", "Debt / Financing"]
}
```

- **`primary_event_type`** — The single most applicable label
- **`event_types`** — 1 to 3 labels, ordered by relevance

### Why 1-3 Labels?

Many filings involve multiple event types. An acquisition (2.01) often comes with new debt (2.03) and a leadership change (5.02). Forcing a single label loses important context. More than 3 creates noise.

### Why a Fixed List?

A fixed taxonomy gives us:
- **Consistent filtering** — The frontend can build a static dropdown
- **Aggregation** — "How many acquisitions this week?"
- **Cross-filing patterns** — "Companies with both Restructuring and Leadership Change"

Free-form labels would fragment into synonyms ("Acquisition" vs "Buyout" vs "Takeover").

---

## Storage

Event types are stored in two places (intentionally):

### 1. Denormalized: `FilingEvent.event_types_json`

```json
["Acquisition", "Debt / Financing"]
```

A simple list for display. Loaded with the filing event in one query.

### 2. Normalized: `EventType` table

| filing_event_id | type_name | attributes |
|---|---|---|
| abc-123 | Acquisition | `{"counterparty": "Acme Corp", "deal_value": "$2.3B"}` |
| abc-123 | Debt / Financing | `{"amount": "$1.5B", "type": "term loan"}` |

Separate rows for querying and filtering. The `attributes` JSON field stores event-specific metadata extracted from the briefing.

### Why Both?

- **JSON column** → Fast read path. Load one row, get everything.
- **Normalized table** → Fast query path. `WHERE type_name = 'Acquisition'` with an index.

Different access patterns, different storage strategies.

---

## API Usage

### List All Event Types

```
GET /events/types
→ ["M&A / Merger", "Acquisition", "Divestiture", ...]
```

Returns the full canonical list. The frontend uses this to populate filter dropdowns.

### Filter Events by Type

```
GET /events/?event_type=Acquisition
GET /events/all?event_type=Restructuring
```

Filters on the `event_types_json` column (JSON contains check).

---

## See Also

- [[Groq LLM]] — How classification happens
- [[Item Tier Classification]] — Complementary importance ranking
- [[Data Model]] — EventType table schema
