# Ingest Service

The ingest service (`services/ingest/`) is Sensybull's data acquisition engine. It runs as a standalone process that polls SEC EDGAR, parses filings, generates AI briefings, and publishes enriched events to [[Redis]].

---

## Entry Point

**File:** `services/ingest/main.py`

The service runs a simple poll loop:

```
Startup:
  1. Load SEC ticker map (CIK → ticker/name)
  2. Load seen.json (dedup state)

Loop (every 600 seconds):
  1. Fetch EDGAR Atom feed (40 most recent 8-Ks)
  2. Filter out already-seen entries
  3. For each new filing:
     a. Fetch and parse filing HTML
     b. Extract items with tier classification
     c. Fetch exhibit press releases (up to 3)
     d. Generate LLM briefing via Groq
     e. Build FilingEvent payload
     f. Publish to Redis filing:new
     g. Mark as seen
  4. Save seen.json
  5. Sleep 600 seconds
```

---

## Module Map

```
services/ingest/
├── main.py          # Poll loop orchestrator
├── fetcher.py       # HTTP requests, ATOM parsing, HTML fetching
├── parser.py        # HTML stripping, item extraction, text cleaning
├── briefing.py      # Groq LLM API calls
├── events.py        # FilingEvent dataclass
├── models.py        # Filing, Item, Exhibit, Briefing dataclasses
├── publisher.py     # Redis publish
├── seen.py          # Dedup state (seen.json)
└── .env             # REDIS_URL, GROQ_API_KEY, SEC_USER_AGENT
```

Each module has a single responsibility. No module imports from the API service.

---

## Dependencies

The ingest service is intentionally **lightweight**:

- **Python stdlib** — `urllib`, `html.parser`, `xml.etree`, `json`, `re`, `time`, `threading`
- **groq** — Groq API client (the only heavy dependency)
- **redis** — Redis client for publishing
- **python-dotenv** — Environment variable loading

No web framework. No ORM. No serialization library. The ingest service is a data pipeline, not a web server.

---

## Data Flow

See [[Ingest Pipeline Deep Dive]] for the detailed step-by-step, and [[Data Flow — End to End]] for the full system context.

**Key stages:**
1. **Fetch** → `fetcher.py` handles HTTP with retry/backoff
2. **Parse** → `parser.py` strips HTML, extracts items, classifies tiers
3. **Enrich** → `briefing.py` calls [[Groq LLM]] for AI analysis
4. **Publish** → `publisher.py` sends to [[Redis Pub-Sub Contract|Redis channel]]
5. **Dedup** → `seen.py` persists processed entry IDs

---

## Error Resilience

The service is designed to keep running even when things fail:

| Failure | Behavior |
|---|---|
| EDGAR unavailable | Log error, sleep, retry next cycle |
| Filing page 404 | Skip filing, continue with next |
| HTML parsing error | Skip filing, log warning |
| Groq API rate limit | Fall back to smaller model |
| Groq API down | Publish event without briefing (null) |
| Redis down | Log error, event lost (caught on next cycle) |
| seen.json corrupted | Start fresh, [[Idempotency & Deduplication|Layer 2 dedup]] catches duplicates |

**Philosophy:** Never crash the loop. Each filing is independent — one failure shouldn't prevent processing the others.

---

## SEC EDGAR Compliance

SEC has strict rules for API access:

- **User-Agent required** — Must include your name and email: `"Your Name your@email.com"`
- **Rate limiting** — 10 requests/second max. We add 0.1s delays between requests.
- **No scraping** — We use the official Atom feed and EDGAR filing pages.

The `SEC_USER_AGENT` environment variable is required. The service won't start without it.

---

## See Also

- [[Ingest Pipeline Deep Dive]] — Detailed parsing and enrichment
- [[Groq LLM]] — AI briefing generation
- [[Redis Pub-Sub Contract]] — The output format
- [[What is an 8-K Filing]] — What we're processing
- [[Idempotency & Deduplication]] — Dedup strategy
