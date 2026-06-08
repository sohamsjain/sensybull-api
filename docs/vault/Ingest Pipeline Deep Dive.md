# Ingest Pipeline Deep Dive

The ingest service (`services/ingest/`) is the heart of Sensybull's data acquisition. It turns raw SEC filings into structured, AI-enriched investment signals.

---

## Architecture

```
main.py (poll loop)
    │
    ├── fetcher.py      → HTTP requests, ATOM parsing, HTML fetching
    ├── parser.py        → HTML stripping, item extraction, text cleaning
    ├── briefing.py      → Groq LLM calls, event classification
    ├── events.py        → FilingEvent dataclass (the JSON contract)
    ├── publisher.py     → Redis publish
    ├── seen.py          → Dedup state management
    └── models.py        → Filing, Item, Exhibit, Briefing dataclasses
```

Each module has a single responsibility. The `main.py` poll loop orchestrates them in sequence.

---

## The Poll Loop

**File:** `main.py`

```python
# Pseudocode of the main loop
while True:
    entries = fetch_feed()           # Get latest 40 8-K filings
    new_entries = filter_unseen()    # Remove already-processed ones

    for entry in new_entries:
        filing = fetch_and_parse()   # Get full filing text
        briefing = generate()        # LLM enrichment
        event = build_event()        # Assemble FilingEvent
        publish(event)               # Send to Redis
        mark_seen(entry)             # Update dedup state

    sleep(600)                       # Wait 10 minutes
```

**Startup sequence:**
1. Load the SEC ticker map (CIK → ticker/company name). This is cached for 24 hours from SEC's `company_tickers.json` endpoint.
2. Load the seen set from `seen.json` on disk.
3. Enter the poll loop.

**Error handling:** Each filing is processed independently. If one filing fails (bad HTML, LLM error), the others still process. Failures are logged but don't crash the loop.

---

## Fetching from EDGAR

**File:** `fetcher.py`

### The Feed

SEC EDGAR provides an Atom XML feed of recent filings at:
```
/cgi-bin/browse-edgar?action=getcompany&type=8-K&dateb=&owner=include&count=40&search_text=&action=getcompany&output=atom
```

Each entry contains:
- Company name and CIK (Central Index Key — SEC's unique company ID)
- Accession number (unique filing ID)
- Filing date
- Link to the filing index page

### Filing Detail

For each new filing, we fetch two pages:
1. **Index page** — Lists all documents in the filing (the 8-K itself, exhibits, etc.)
2. **Primary 8-K document** — The actual filing HTML

### Exhibits

Exhibits are attachments to the filing. We only fetch:
- **EX-99.x** — Press releases. These are the human-readable narratives.
- **HTML only** — PDFs and binary formats are skipped.
- **Max 3** — More than 3 exhibits rarely adds useful information.

### HTTP Robustness

```python
# Retry with escalating timeouts
Attempt 1: 45 second timeout
Attempt 2: 65 second timeout
Attempt 3: 85 second timeout
```

SEC EDGAR can be slow during high-filing periods (market close, earnings season). The escalating timeouts handle this gracefully.

**Politeness:** 0.1-second delay between requests. SEC requires a `User-Agent` header with your name and email — they will block IPs that don't comply.

---

## HTML Parsing

**File:** `parser.py`

SEC filings are some of the worst HTML on the internet. They're often generated from Microsoft Word, resulting in:
- Deeply nested tables for layout
- Inline styles on every element
- Mixed encodings
- Non-semantic markup

### The Custom Parser

We use Python's `html.parser.HTMLParser` (stdlib) instead of BeautifulSoup because we need precise control:

```python
class HTMLStripper(HTMLParser):
    """Removes tags, keeps text. Handles SEC's messy HTML."""

    # Skipped entirely (tag + contents):
    #   <script>, <style>

    # Block elements get newlines:
    #   <p>, <div>, <br>, <tr>, <li>, <h1>-<h6>

    # Everything else: just extract text content
```

### Text Cleaning Pipeline

After HTML stripping, the raw text goes through:

1. **Boilerplate removal** — Signature blocks, page numbers, forward-looking statement disclaimers
2. **Short line merging** — Lines under a threshold get merged with the next line (fixes Word's habit of breaking mid-sentence)
3. **Blank line collapse** — Runs of blank lines become a single separator
4. **Truncation** — Text after the first signature block or "SIGNATURES" heading is discarded

---

## Item Extraction

**File:** `parser.py` → `extract_items()`

8-K filings are organized into numbered "Items," each covering a different type of event. The parser uses regex to find item headers:

```
Item 1.01 — Entry into a Material Definitive Agreement
Item 2.01 — Completion of Acquisition or Disposition
Item 5.02 — Departure of Directors or Certain Officers
```

Each extracted item gets classified into a [[Item Tier Classification|tier]]:

| Tier | Meaning | Examples |
|---|---|---|
| **1 (Critical)** | Market-moving events | Bankruptcy (1.03), Material cybersecurity (1.05), Director/officer departure (5.01) |
| **2 (Important)** | Significant but less urgent | Material agreements (1.01), Acquisitions (2.01), Earnings triggers (2.02) |
| **3 (Routine)** | Administrative/procedural | Bylaw amendments (5.03), Regulation FD (7.01), Other events (8.01) |

The filing's **max_tier** is the most critical tier across all its items (lowest number = most critical).

---

## LLM Briefing Generation

**File:** `briefing.py`

See [[Groq LLM]] for the full details on the LLM integration.

The briefing call sends the filing text to Groq and gets back a structured JSON object. The key insight is that **one LLM call does multiple jobs:**

1. **Summarization** — headline, summary, investor takeaway
2. **Classification** — event types from [[Event Type Taxonomy]]
3. **Extraction** — deal terms, catalyst dates
4. **Assessment** — significance level, sentiment

This single-call approach is faster and cheaper than running separate models for each task.

---

## The FilingEvent Contract

**File:** `events.py`

The `FilingEvent` dataclass defines the exact JSON shape published to Redis. See [[Redis Pub-Sub Contract]] for the full schema.

Key design decisions:
- **`edgar_id`** — A unique identifier derived from the EDGAR entry. This is the dedup key — see [[Idempotency & Deduplication]].
- **`max_tier`** — Pre-computed so consumers don't need to scan all items to know the filing's importance.
- **Denormalized** — The full briefing, all items, and all exhibits are embedded in one message. No follow-up queries needed.

---

## Deduplication State

**File:** `seen.py`

The seen set is a simple JSON file mapping entry IDs to timestamps:

```json
{
  "0001234567-24-012345": "2024-03-15T14:30:00",
  "0001234567-24-012346": "2024-03-15T14:35:00"
}
```

**Pruning:** Entries older than 30 days are removed on each save. This keeps the file small while ensuring we don't re-process recent filings.

**Atomic writes:** The file is written atomically (write to temp → rename) to prevent corruption if the process crashes mid-write.

**Why a file, not Redis?** The seen set is local to the ingest service. It survives Redis restarts. It's also human-readable for debugging ("was this filing already processed?").

---

## See Also

- [[What is an 8-K Filing]] — Background on the SEC form we process
- [[Item Tier Classification]] — How items are ranked
- [[Groq LLM]] — LLM integration details
- [[Event Type Taxonomy]] — The 34 canonical event labels
- [[Redis Pub-Sub Contract]] — What gets published
