# Data Flow — End to End

This is the complete journey of an SEC 8-K filing from the moment it appears on EDGAR to the moment a user sees it in their browser.

---

## Phase 1: Discovery (Ingest Service)

**File:** `services/ingest/main.py` → `fetcher.py`

```
SEC EDGAR Atom Feed
    │
    ▼
┌─────────────────────┐
│  Poll every 600s    │  ← fetch_feed() hits EDGAR's /cgi-bin/browse-edgar
│  (10 minutes)       │     with form-type=8-K, count=40
│                     │
│  Parse XML entries  │  ← Each entry has: company name, CIK, accession #,
│                     │     filing date, entry URL
│                     │
│  Dedup check        │  ← Compare entry_id against seen.json
│  (skip if seen)     │     seen.json maps {entry_id → ISO timestamp}
└─────────┬───────────┘     Entries older than 30 days are pruned
          │
          ▼ (new filings only)
```

**Why poll instead of webhook?** SEC EDGAR has no webhook API. The Atom feed is the official real-time-ish interface. 10-minute polling balances freshness against SEC's rate limit expectations (they require a `User-Agent` header with contact info and expect polite request rates).

---

## Phase 2: Fetching & Parsing (Ingest Service)

**Files:** `fetcher.py` → `parser.py`

```
For each new filing:
    │
    ├─► fetch_filing_detail()
    │   │
    │   ├── Fetch filing index page (HTML)
    │   │   └── Find primary 8-K document link
    │   │
    │   └── Fetch primary 8-K HTML
    │       └── strip_html() — custom parser removes scripts,
    │           styles, collapses whitespace
    │
    ├─► extract_items()
    │   │
    │   │  Regex scan for "Item X.XX" headers
    │   │  Each item gets:
    │   │   - number (e.g., "5.02")
    │   │   - title (e.g., "Departure of Directors...")
    │   │   - tier (1, 2, or 3) from ITEM_TIERS map
    │   │   - category (e.g., "Leadership")
    │   │   - text (extracted section body, cleaned)
    │   │
    │   └── Text cleaning:
    │       - Truncate at signature blocks
    │       - Remove forward-looking statements boilerplate
    │       - Merge orphaned short lines
    │       - Collapse blank line runs
    │
    └─► fetch_exhibit_text() (up to 3 exhibits)
        │
        ├── Only EX-99.x (press releases)
        ├── Only HTML (skip PDFs/binaries)
        └── 0.1s delay between requests (SEC politeness)
```

**Why custom HTML parsing?** SEC filings are notoriously messy HTML — inline styles, nested tables, Word-generated markup. A custom `HTMLParser` subclass gives us precise control over what gets stripped vs. preserved.

**Why only EX-99.x exhibits?** These are press releases — the human-readable narrative. Other exhibit types (agreements, certificates) are legal boilerplate that doesn't help generate useful investor briefings.

---

## Phase 3: AI Enrichment (Ingest Service)

**File:** `briefing.py`

```
Filing text + items + exhibits
    │
    ▼
┌──────────────────────────┐
│  Groq LLM API Call       │
│                          │
│  System Prompt:          │
│  "You are a senior       │
│   event-driven analyst"  │
│                          │
│  User Prompt:            │
│  Filing text + items     │
│  + exhibit text          │
│                          │
│  Output (JSON):          │
│  ┌────────────────────┐  │
│  │ headline           │  │
│  │ summary            │  │
│  │ primary_event_type │  │
│  │ event_types [1-3]  │  │  ← From [[Event Type Taxonomy]]
│  │ significance       │  │  ← High / Medium / Low
│  │ sentiment          │  │  ← Positive / Negative / Neutral / Mixed
│  │ investor_takeaway  │  │
│  │ catalysts [{date}] │  │  ← [[Catalysts]]
│  │ deal_terms {}      │  │
│  └────────────────────┘  │
└──────────────────────────┘
```

**Why Groq?** Groq provides extremely fast inference on open-source LLMs (Llama). For a real-time system where filings need to be processed quickly, Groq's speed advantage over cloud LLM providers is significant. Cost is also much lower than GPT-4 class models.

**Why Llama models?** Two models in rotation:
- `meta-llama/llama-4-scout-17b-16e-instruct` — Primary, higher quality
- `llama-3.1-8b-instant` — Fallback on rate limits, still good enough

Round-robin between multiple API keys if provided, with automatic fallback to the smaller model on rate-limit errors.

**What the LLM does in one call:**
1. Generates a human-readable briefing (headline, summary, takeaway)
2. Classifies the event into 1-3 canonical types from [[Event Type Taxonomy]]
3. Extracts deal terms (counterparty, deal value, premium, etc.)
4. Identifies upcoming catalyst dates
5. Assesses significance and sentiment

---

## Phase 4: Publishing (Ingest Service → Redis)

**Files:** `events.py` → `publisher.py`

```
FilingEvent dataclass
    │
    ▼
┌─────────────────────────┐
│  Build JSON payload     │
│                         │
│  {                      │
│    edgar_id,            │  ← Unique dedup key
│    signal_type: "8-K",  │
│    cik, ticker,         │
│    company_name,        │
│    filing_date,         │
│    edgar_url,           │
│    accession_number,    │
│    max_tier,            │  ← Highest (lowest number) tier across items
│    items: [...],        │
│    exhibits: [...],     │
│    briefing: {...},     │
│    event_types: [...]   │
│  }                      │
│                         │
│  redis.publish(         │
│    "filing:new",        │
│    json_payload         │
│  )                      │
└─────────────────────────┘
```

**Why Redis pub/sub instead of a queue?** Simplicity. We have exactly one publisher and one subscriber. Redis pub/sub is zero-config, ephemeral (no queue management), and the subscriber handles [[Idempotency & Deduplication|idempotency]] on its own. If we needed guaranteed delivery or multiple consumers, we'd upgrade to Redis Streams or a proper message broker.

---

## Phase 5: Persistence (API Service)

**File:** `services/api/app/services/realtime/subscriber.py`

```
Redis message received
    │
    ▼
┌─────────────────────────────────┐
│  Parse JSON                     │
│                                 │
│  Idempotency check:             │
│  SELECT * FROM filing_event     │
│  WHERE edgar_id = ?             │
│  → Skip if exists               │
│                                 │
│  Company resolution:            │
│  1. Try ticker lookup           │
│  2. Fall back to CIK lookup     │
│  3. Auto-create if no match     │
│                                 │
│  Persist:                       │
│  ├── FilingEvent row            │
│  ├── N × EventType rows         │  ← One per classified type
│  └── M × Catalyst rows          │  ← One per extracted date
│                                 │
│  db.session.commit()            │
└─────────────┬───────────────────┘
              │
              ▼
```

---

## Phase 6: Real-Time Fan-Out (API Service)

**Files:** `subscriber.py` → `socketio_setup.py`

```
              │
              ▼
┌─────────────────────────────────┐
│  Find affected users:           │
│                                 │
│  SELECT DISTINCT user_id        │
│  FROM watchlist                  │
│  JOIN watchlist_companies        │
│  WHERE company_id = ?           │
│                                 │
│  Emit to each user's room:      │
│  socketio.emit(                 │
│    'filing_event',              │
│    event_data,                  │
│    room=f'user:{user_id}',      │
│    namespace='/feed'            │
│  )                              │
│                                 │
│  Also emit to 'public' room    │  ← For unauthenticated clients
└─────────────────────────────────┘
```

**Why room-based fan-out?** Socket.IO rooms let us target specific users without iterating all connections. Each authenticated user joins `user:{their_id}` on connect. When an event arrives, we query which users watch that company and emit only to their rooms. This is O(watchlist_users) not O(all_connected_users).

---

## Phase 7: Display (Web Frontend)

**Files:** `services/web/src/api/socket.js` → `App.jsx` → `Feed.jsx`

```
Socket.IO client receives 'filing_event'
    │
    ▼
┌──────────────────────────┐
│  Add to Feed state       │
│  (prepend to list)       │
│                          │
│  Apply client-side       │
│  filters:                │
│  - Significance tier     │
│  - Event type            │
│  - Search text           │
│                          │
│  Render FilingCard:      │
│  ┌────────────────────┐  │
│  │ TierBadge (1/2/3)  │  │
│  │ Company + Ticker   │  │
│  │ Headline           │  │
│  │ Summary            │  │
│  │ Event type tags     │  │
│  │ Sentiment badge    │  │
│  │ Catalysts list     │  │
│  │ EDGAR link         │  │
│  └────────────────────┘  │
└──────────────────────────┘
```

---

## Timing

| Phase | Typical Duration |
|---|---|
| EDGAR poll interval | 600 seconds (10 min) |
| Fetch + parse filing | 2-5 seconds |
| Groq LLM briefing | 1-3 seconds |
| Redis publish → subscribe | < 10 milliseconds |
| Persist + fan-out | < 100 milliseconds |
| WebSocket → browser render | < 50 milliseconds |
| **Total (after discovery)** | **~3-8 seconds** |

The bottleneck is the 10-minute poll interval. Once a filing is discovered, it reaches the user's screen in under 10 seconds.

---

## See Also

- [[Ingest Pipeline Deep Dive]] — Detailed look at parsing and enrichment
- [[Real-Time System]] — Socket.IO architecture
- [[Idempotency & Deduplication]] — How we handle duplicates
- [[Redis Pub-Sub Contract]] — The JSON schema between services
