# Event Type Taxonomy

Sensybull classifies every 8-K filing — and every ingested press release —
against a three-tier taxonomy, then shows the reader **one simple
category**. The tiers exist to make the model's job easy; they are not a
product surface.

---

## The three tiers

Defined in `services/ingest/taxonomy.py` (`TAXONOMY_VERSION = "1.0"`).

| Tier | Count | Example | Who sees it |
|---|---|---|---|
| Primary | 8 | `leadership_and_governance` | **The reader** — as "Leadership & Governance" |
| Secondary | 29 | `executive_leadership` | Nobody; grouping only |
| Tertiary (leaf) | 119 | `ceo_departure` | The model, and analytics |

### The 9 categories the reader sees

`taxonomy.CATEGORIES` — the 8 primary buckets plus `Other`:

Leadership & Governance · Financial Results · Strategic Transactions ·
Capital & Financing · Operations & Strategy · Risk Events ·
Regulatory & Compliance · Shareholder Activity · Other

This list is mirrored in `services/api/app/routes/events.py` (backing
`GET /events/types`) and in sensybull-web `src/lib/event-categories.ts`.
Keep the three in sync.

---

## How Classification Works

The [[Groq LLM]] is shown **only the leaf slugs** — never the categories.
Naming the specific event is a far easier problem than picking a bucket:
the leaves are close to mutually exclusive and their names carry their own
definition, so the model never has to guess where we would file a covenant
waiver or an FDA complete response letter. Showing it the buckets would
just invite it to answer with one.

It returns leaves; we collapse them:

```json
{ "primary_category": "merger_agreement",
  "categories": ["merger_agreement", "debt_issuance"] }
```

becomes

```json
{ "primary_event_type": "Strategic Transactions",
  "event_types": ["Strategic Transactions", "Capital & Financing"],
  "taxonomy": ["merger_agreement", "debt_issuance"],
  "taxonomy_version": "1.0" }
```

`taxonomy` is internal — analytics and future routing. **Never render the
leaves or the middle tier.**

### Why 1-3 labels?

Many filings involve several events. An acquisition (2.01) often comes
with new debt (2.03) and a leadership change (5.02). Note that sibling
leaves collapse together, so three leaves frequently yield one category —
which is the point.

### Prompt cost

The leaf list is ~1.3K tokens in every briefing prompt and is the reason
`_TOTAL_TEXT_CAP` dropped from 16K to 10K chars. See [[Groq LLM]] → Token
budget before growing either.

### Adding to the taxonomy

- **A new leaf** is cheap: it changes nothing the reader sees.
- **A new primary bucket** adds a filter chip in the feed. That is a
  product decision, and needs the API and web mirrors updated with it.

### Facts-only briefings

When the LLM never runs, no leaf-level claim is made. The SEC item number
maps straight to a category via `taxonomy.ITEM_CATEGORIES`; items 7.01
(Reg FD) and 8.01 (Other Events) are deliberately unmapped, because a
catch-all item says nothing about what happened, so those land in `Other`.

### Press releases

Wires classify against the same taxonomy, so releases and filings land in
the same categories. Unlike 8-Ks there is no facts-only fallback: a
release no leaf fits fails the materiality gate and is dropped.

---

## Legacy labels

Events classified before August 2026 carry the old 12-label vocabulary
("Acquisition", "Material Agreement", "Earnings", …) and their rows are
never rewritten. `taxonomy.LEGACY_LABELS` folds each one into its current
category at read time, mirrored in `routes/events.py`
(`LEGACY_EVENT_TYPES`, used by the `?event_type=` filter) and in
sensybull-web `src/lib/event-categories.ts` (used for both filtering and
display). Without that, every filter chip would show an empty feed and the
feed would render two vocabularies side by side.

---

## Storage

Event types are stored in two places (intentionally):

### 1. Denormalized: `FilingEvent.event_types_json`

```json
["Strategic Transactions", "Capital & Financing"]
```

A simple list for display. Loaded with the filing event in one query.

### 2. Normalized: `EventType` table

| filing_event_id | type_name | attributes |
|---|---|---|
| abc-123 | Strategic Transactions | `{"counterparty": "Acme Corp", "deal_value": "$2.3B"}` |
| abc-123 | Capital & Financing | `{"counterparty": "Acme Corp", "deal_value": "$2.3B"}` |

Rows carry the **category**, not the leaf — the leaf lives on
`briefing_json.taxonomy`.

Separate rows for querying and filtering. The `attributes` JSON field stores event-specific metadata extracted from the briefing.

### Why Both?

- **JSON column** → Fast read path. Load one row, get everything.
- **Normalized table** → Fast query path. `WHERE type_name = 'Strategic Transactions'` with an index.

Different access patterns, different storage strategies.

---

## API Usage

### List All Event Types

```
GET /events/types
→ ["Leadership & Governance", "Financial Results", "Strategic Transactions", ...]
```

Returns the 9 categories. The frontend uses this for its filter chips
(dropping `Other` — the All chip already covers it).

### Filter Events by Type

```
GET /events/?event_type=Strategic Transactions
GET /events/all?event_type=Risk Events
```

Filters on the `event_type` table by `type_name`, matching the requested
category **and** any legacy label that folds into it, so historical events
stay reachable.

---

## See Also

- [[Groq LLM]] — How classification happens
- [[Item Tier Classification]] — Complementary importance ranking
- [[Data Model]] — EventType table schema
