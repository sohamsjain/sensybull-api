# Market Data Layer

> **Status:** Design, August 2026. Not yet implemented.
> **Supersedes:** the Alpaca integration described in [[Technology Decisions]].

This note is the plan for replacing Alpaca with **Massive** (the rebranded
Polygon.io) and building a market-data layer that can drive TradingView's
Advanced Charting Library with historical *and* streaming bars.

---

## The decisions, up front

| Question | Decision |
|---|---|
| Provider | Massive (`api.massive.com`, ex-`api.polygon.io`), Stocks Starter |
| Store or query? | **Both, split by timeframe.** Daily bars stored forever. Intraday stored for a warm set, fetched on demand otherwise. Second aggregates never stored. |
| Database | **Stay on PostgreSQL.** No ClickHouse, no Timescale — yet. See [[#Why not ClickHouse]]. |
| Storage unit | **Immutable pre-serialized chunks**, not rows-per-bar, for intraday |
| Wire format | TradingView **UDF** columnar JSON, gzipped once and reused end to end |
| Hot path | L1 process LRU → L2 Redis → L3 Postgres → L4 vendor, with single-flight |
| Streaming | One dedicated WS worker → Redis pub/sub → Socket.IO `/md` namespace |
| Alpaca | Deleted at the end of Phase 5, not before |

---

## What the Starter plan actually gives us

From the plan sheet, confirmed against Massive's docs:

- **All US stock tickers, 100% market coverage** — every exchange, not IEX's
  ~2.5% of consolidated volume. This alone fixes the sparse-price problem
  `alpaca.snapshot_price()` works around with its three-way fallback.
- **Unlimited API calls** — no 200 req/min ceiling. The `CALL_DELAY = 0.35`
  politeness sleep in the price-reaction worker can go.
- **5 years of history** — roughly Aug 2021 onward.
- **Second + minute aggregates**, **snapshots**, **WebSockets**, **flat files**
  (S3), **reference data**, **corporate actions**, **technical indicators**.

### The one thing it does not give us: real time

Stocks Starter is **15-minute delayed**, including over WebSocket. The stream
connects to the delayed cluster, not the real-time one. Practical consequences:

1. We must say "15-minute delayed" in the UI. TradingView has a first-class
   slot for this — `data_status: "delayed_streaming"` on the symbol info makes
   the chart render the standard delayed badge. Do not skip it; misrepresenting
   delayed data as live is an exchange-agreement problem, not just a UX one.
2. The price-reaction worker's `5m` and `15m` measurements land ~15 min
   late. That is fine — they are already measured from bar timestamps, not
   wall clock, so the *numbers* stay correct; only their availability lags.
3. The design below is delay-agnostic. When the plan is upgraded to a
   real-time tier, the only change is the WebSocket hostname and one config
   flag that flips `data_status` to `"streaming"`. Nothing else moves.

> Verify current tier pricing on Massive's pricing page before budgeting the
> upgrade — the tier names and prices have moved since the rebrand.

---

## Store in-house, or query on demand?

Neither, exclusively. The answer falls out of the row counts.

### Daily bars — store, permanently

10,000 tickers x 252 sessions x 5 years = **~12.6M rows**. At ~80 bytes/row
that is ~1 GB plus indexes. This is a rounding error for Postgres, and it buys:

- Instant 1D / 1W / 1M charts with no vendor round trip.
- A local source for `market_cap`, `atr_14`, and price-reaction baselines.
- **History beyond the vendor's window.** Starter only reaches back 5 years.
  If we never store daily bars, then in 2031 we cannot draw a 2026 chart. If we
  do, our archive grows past what any plan tier sells. This is the strongest
  single argument for storing anything at all, and it costs a gigabyte.

Backfill is one call per session day via grouped daily aggregates
(`/v2/aggs/grouped/locale/us/market/stocks/{date}`) — the whole market in one
response. 1,260 calls for five years, then one call a day forever.

### Minute bars — store a warm set, fetch the rest

Whole market, five years, one-minute: 10,000 x 1,260 x 390 = **~4.9 billion
bars**. Row-per-bar in Postgres that is ~400 GB. Not happening, and not needed:
unlimited API calls mean an on-demand fetch is free, and the only cost is
latency we can hide behind a cache.

But some symbols are always hot, and we know exactly which:

```
warm set = companies on any watchlist
         ∪ companies with a FilingEvent in the last 30 days
```

That is ~2–4k symbols. Every one of them is a symbol a user might chart, *and*
every one is a symbol the price-reaction worker needs minute bars for anyway. One
materialization serves both.

### Second aggregates — never store

Only useful for the live tail of a 1-minute candle. Consume from the stream,
keep in Redis with a 60s TTL, never persist.

### Summary

| Resolution | Storage | Retention | Source of truth |
|---|---|---|---|
| 1 sec | Redis only | 60s | WebSocket `A.*` |
| 1 min | Chunked blobs, warm set | 13 months rolling | Flat files + REST |
| 5/15/30/60 min | Not stored — resampled from 1 min | — | derived |
| 1 day | Rows, all tickers | Forever | Grouped daily |
| 1 week / 1 month | Not stored — resampled from daily | — | derived |

Never store a resolution you can derive. Two stored resolutions that disagree
is a bug report waiting to happen; one stored resolution and a resampler cannot
disagree with itself.

---

## The core idea: immutable chunks

This is the piece everything else hangs off, so it comes before the module
layout.

Chart clients do not query bars. They **range-fetch** them — "give me AAPL
1-minute from March 3rd" — and then scroll, asking for the adjacent range.
So make the range the unit of storage.

Cut time into fixed chunks:

- **1 minute** → one chunk per symbol per **trading day** (~390 bars)
- **1 day** → one chunk per symbol per **year** (~252 bars)

Each chunk is stored as the **exact bytes we serve**: a gzipped UDF-format
columnar JSON body.

```json
{"s":"ok","t":[...],"o":[...],"h":[...],"l":[...],"c":[...],"v":[...]}
```

Three properties make this fast in a way a row store cannot be:

1. **A closed chunk is immutable.** Yesterday's minute bars for AAPL will never
   change (modulo corporate actions — see [[#Corporate actions and the adjustment epoch]]).
   Cache it forever, at every layer, and serve it with
   `Cache-Control: public, max-age=31536000, immutable` + a strong `ETag`. Chart
   scroll-back becomes a CDN hit that never reaches us. Only the *current*
   chunk is mutable, and it gets a short TTL.
2. **Zero serialization on the hot path.** The same bytes sit in the process
   LRU, in Redis, in Postgres, and on the CDN. A cache hit is a `memcpy` and a
   `Content-Encoding: gzip` passthrough — no row hydration, no JSON encode, no
   compression. Nothing in the request path ever looks at an individual bar.
3. **It collapses the storage problem.** The warm set at chunk granularity is
   3,000 x 252 = 756k rows and ~3 GB for a year of minute data. Row-per-bar for
   the same data is 295M rows and ~23 GB. Even mirroring the *entire* market's
   five years of minute bars is ~50 GB as chunks versus ~400 GB as rows — which
   turns "mirror everything" from impossible into a future option.

The cost is that you cannot write SQL against an individual intraday bar. That
is a real trade, and it is the right one here: daily bars stay row-per-bar
precisely so ATR, screening, and market-cap math keep their SQL, and those all
work at daily resolution. If an intraday analytic ever needs SQL, that is the
signal to revisit — see [[#Why not ClickHouse]].

### Assembling a request

`getBars(AAPL, 5, from, to)` becomes:

1. Map `[from, to)` to the covering minute chunk keys.
2. Fetch each chunk: L1 → L2 → L3 → vendor (single-flight, see below).
3. Resample 1-min → 5-min, trim to the exact range, concatenate.
4. Cache the resampled result under its own key, since the same
   `(symbol, resolution, range)` will be asked for again.

Steps 3–4 only run on a miss of the resampled key, which is rare in practice.

---

## Database choice

**Stay on PostgreSQL.** Concretely:

```sql
-- Row-per-bar, small, SQL-queryable, permanent.
CREATE TABLE bar_daily (
  symbol_id  INT      NOT NULL REFERENCES md_symbol(id),
  d          DATE     NOT NULL,
  o, h, l, c DOUBLE PRECISION NOT NULL,
  v          BIGINT   NOT NULL,
  vw         DOUBLE PRECISION,
  n          INT,
  PRIMARY KEY (symbol_id, d)
);
-- BRIN on d: the table is naturally clustered by insertion date, and BRIN is
-- ~1000x smaller than a btree for a 12M-row append-only table.
CREATE INDEX ON bar_daily USING brin (d);

-- Chunked blobs, rolling retention, never queried by individual bar.
CREATE TABLE bar_chunk (
  symbol_id   INT   NOT NULL REFERENCES md_symbol(id),
  resolution  SMALLINT NOT NULL,   -- minutes; 1 today
  period      DATE  NOT NULL,      -- the trading day this chunk covers
  adj_epoch   INT   NOT NULL,      -- see corporate actions
  bar_count   INT   NOT NULL,
  body        BYTEA NOT NULL,      -- gzipped UDF JSON, served verbatim
  built_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol_id, resolution, period)
) PARTITION BY RANGE (period);     -- monthly partitions; DROP to expire
```

Monthly range partitioning on `bar_chunk` means retention is `DROP PARTITION`,
not a 20-million-row `DELETE` that bloats the table and blocks autovacuum.

`md_symbol` is a small interning table (`id, ticker, figi, primary_exchange,
active, delisted_utc, adj_epoch`). Interning to an `INT` rather than repeating
a `VARCHAR(10)` on every row is worth ~15% of `bar_daily` and makes ticker
renames a one-row update instead of a rewrite — Massive's reference data gives
us renames, which the current schema silently mishandles.

### Why not ClickHouse

ClickHouse is the right answer to "scan billions of rows and aggregate." It is
the wrong answer to "hand me these 390 contiguous bars." Charts do the second
thing, essentially always, and the chunk design means even the second thing is
usually a Redis hit that never reaches any database.

Against that, ClickHouse costs a second datastore: its own client, its own
failure mode, its own backups, no transactional consistency with `company` or
`filing_event`, and no reuse of the SQLAlchemy session already threaded through
this codebase. That price buys nothing the chart path can feel.

**Revisit when** we want market-wide analytics — screeners, "every 8-K that
preceded a 5% gap," backtests, indicator sweeps. That workload really is a
columnar scan. When it arrives, ClickHouse joins as a *read-side sibling* fed
from the same Massive flat files, and the serving path described here does not
change. Adding it later is cheap; adding it now is a tax on every deploy
between here and then.

### Why not TimescaleDB either — for now

Timescale is the natural upgrade if we ever move intraday to row-per-bar at
market scale: hypertables, native compression, and continuous aggregates are
precisely this problem. It is also a Postgres extension, so it is a migration
rather than a rewrite. But it is not available on Render's managed Postgres,
so adopting it means moving the database to Timescale Cloud — and the chunk
design means we do not need what it offers. Keep it on the shelf.

---

## Module layout

Everything lives under `services/api/app/services/market_data/`, replacing the
current four flat modules. The shape is deliberate: **one public entry point,
provider details sealed behind an interface, nothing above the service layer
knows the vendor's name.**

```
market_data/
  providers/
    base.py            # MarketDataProvider protocol — the seam
    massive.py         # REST client: aggregates, grouped, snapshot, reference
    massive_flatfiles.py  # S3 bulk loader for backfill
    alpaca.py          # kept until Phase 5, then deleted
    __init__.py        # get_provider() -> reads MARKET_DATA_PROVIDER
  bars/
    service.py         # get_bars() — the ONLY thing routes/workers call
    chunks.py          # chunk keying, assembly, immutability rules
    store.py           # bar_daily + bar_chunk read/write
    resample.py        # 1m -> 5/15/30/60m; 1d -> 1W/1M
    codec.py           # UDF columnar encode/decode + gzip
  stream/
    consumer.py        # the WS worker: Massive -> Redis
    contract.py        # Redis channel + payload schema (mirrors Redis Pub-Sub Contract)
  quotes.py            # snapshot -> last price / day change
  corporate_actions.py # splits + dividends -> adj_epoch bumps
  symbols.py           # md_symbol sync from /v3/reference/tickers
  cache.py             # existing Redis helper + L1 LRU + single-flight
  edgar_facts.py       # unchanged — shares outstanding stays EDGAR's job
  sync.py              # daily cron, rewritten against the provider
  reaction_worker.py   # unchanged logic, now reading from bars.service
```

### The provider seam

The single most important modularity decision: **symbol spelling belongs to the
provider, not the caller.** Today `alpaca.normalize_ticker()` leaks the fact
that Alpaca wants `BRK.B` while we store `BRK-B` into every call site. Massive
also uses dots, so the migration hides the bug — until the third vendor.

```python
class MarketDataProvider(Protocol):
    name: str

    def get_bars(self, symbols: list[str], resolution: Resolution,
                 start: datetime, end: datetime | None,
                 adjusted: bool = True) -> dict[str, list[Bar]]: ...

    def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]: ...

    def get_grouped_daily(self, day: date) -> dict[str, Bar]: ...

    def list_symbols(self, active: bool = True) -> Iterator[SymbolRef]: ...

    def get_splits(self, since: date) -> list[Split]: ...
    def get_dividends(self, since: date) -> list[Dividend]: ...
```

`Bar`, `Snapshot`, `SymbolRef` are our dataclasses, in our field names, in our
symbol spelling. Each provider translates at its own boundary and nowhere else.
Selection is `MARKET_DATA_PROVIDER=massive|alpaca`, which makes Phase 1 a config
flip and Phase 1's rollback a config flip.

Both providers get run against the **same contract test suite** — same inputs,
same assertions on shape, tolerance-based assertions on values. That suite is
what makes the swap boring, and it is what makes the *next* swap boring.

### Single-flight

Fifty users open NVDA at 9:31. Without coordination that is fifty identical
vendor calls, or worse, fifty concurrent Postgres chunk builds. `cache.py`
grows a `single_flight(key)` helper: the first caller for a key acquires a short
Redis lock and does the work; the rest wait on the result. Standard, cheap, and
the difference between a warm-up spike and a thundering herd.

---

## Serving path: TradingView UDF

Expose a new blueprint at `/api/v1/md`, shaped as **UDF** rather than as
TradingView's raw JS datafeed API. The reason is leverage: TradingView ships a
reference `UDFCompatibleDatafeed` adapter, so a UDF backend gives the frontend
a working datafeed for free, and we only override `subscribeBars` to add
streaming. Hand-writing the JS API means writing and maintaining the caching,
chunking, and range-merging that adapter already does.

| Route | UDF method | Notes |
|---|---|---|
| `GET /md/config` | `onReady` | `supported_resolutions`, `supports_marks: true`, `supports_search: true` |
| `GET /md/symbols?symbol=` | `resolveSymbol` | `data_status: "delayed_streaming"`, session, timezone, `pricescale` |
| `GET /md/search?query=` | `searchSymbols` | reuses `_search_query()` from `routes/companies.py` |
| `GET /md/history` | `getBars` | the hot path; `?symbol&resolution&from&to&countback` |
| `GET /md/marks` | `getMarks` | **filing events pinned to bars** |
| `GET /md/time` | `getServerTime` | clock skew correction |
| `GET /md/quotes?symbols=` | — | batch quote, replaces `/companies/:id/quote` |

`/md/marks` is the interesting one. The chart already pins filings to bars in
`price-chart.tsx` by hand; `getMarks` is TradingView's native slot for exactly
that, with hover text and per-mark color. Sentiment colors carry over unchanged
(emerald / red / slate, per [[Web Frontend]]). A chart that shows *why* the
stock moved, sourced from our own briefings, is the thing no generic charting
integration has — it should be built in Phase 3, not deferred.

Keep `/companies/:id/bars` and `/companies/:id/quote` alive as thin shims over
`bars.service` until the web app has fully moved. They are already the right
shape; they just stop knowing about Alpaca.

### Response headers

```
Closed chunk:  Cache-Control: public, max-age=31536000, immutable
               ETag: "<symbol>:<res>:<period>:<adj_epoch>"
Current chunk: Cache-Control: public, max-age=5, stale-while-revalidate=30
Always:        Content-Encoding: gzip   (served from the stored bytes)
```

`stale-while-revalidate` on the live chunk is what keeps the current candle
feeling instant without hammering the origin at every tick.

---

## Streaming path

```
Massive WS (delayed cluster)
        │  AM.*  (minute aggregates, whole market)
        ▼
services/api  →  `flask market-stream`  (one Render worker, one connection)
        │  writes  Redis: md:last:<sym>, md:cur:<sym>:<res>
        │  publishes Redis: md:bar
        ▼
API replicas  →  Socket.IO `/md` namespace  →  rooms md:<sym>:<res>
        ▼
    TradingView subscribeBars → onRealtimeCallback(bar)
```

**One connection, one worker.** Massive limits concurrent WebSocket connections
per cluster (typically one on lower tiers), so the stream cannot live inside the
API process — every replica would fight for the socket. It runs as a dedicated
Render `worker` reusing the existing API image with a different command, exactly
the pattern `Dockerfile.cron` already establishes. No third codebase.

**Subscribe to `AM.*`, the whole market.** ~11k messages per minute, bursting at
each minute boundary. That is nothing for one Python process with `orjson`, and
it means *every* symbol's current bar is always warm in Redis — a chart opened
on any ticker gets its live candle with no subscription round trip. If the plan
turns out not to permit wildcard subscription, fall back to a dynamic
subscription set driven by a Redis set of symbols with open charts; the consumer
is written to support both from day one.

**Resolution-aware fan-out.** TradingView's `onRealtimeCallback` expects a bar
whose `time` is the bucket start *for the resolution the user is viewing*. So
the worker folds each incoming minute bar into the open 5m/15m/30m/1h/1D
buckets and publishes one message per `(symbol, resolution)` that has
subscribers. Doing this server-side keeps the client dumb and guarantees the
streaming bar and the historical bar agree — the classic source of the
"last candle jumps when you change timeframe" bug.

**Namespace `/md`, not `/feed`.** Filing events are low-volume and
user-scoped; market data is high-volume and symbol-scoped. Separate namespaces
mean a chart-heavy session cannot delay a filing alert, and either can be split
onto its own process later. Auth reuses the existing `{token}` dict handshake
from [[WebSocket Protocol]]. Set `message_queue=REDIS_URL` on Flask-SocketIO so
fan-out works across API replicas — the current single-replica setup gets away
without it, market data will not.

---

## Corporate actions and the adjustment epoch

Immutable caching has one enemy: a split rewrites history. Cache a chunk
"forever" and a 4-for-1 split leaves every cached chart wrong, at every layer,
with no natural expiry.

The fix is an **adjustment epoch** per symbol:

- `md_symbol.adj_epoch` is an integer, bumped whenever a split or other
  price-affecting action for that symbol is ingested.
- `adj_epoch` is part of every cache key and every ETag.
- A bump therefore invalidates that symbol's entire cached history atomically,
  at every layer, without deleting anything. Old entries fall out of Redis on
  TTL and out of Postgres on the next rebuild.

A daily cron polls `/v3/reference/splits` and `/v3/reference/dividends` for
actions since the last run, bumps the affected epochs, and re-fetches
`bar_daily` for those symbols. Bars are always requested with `adjusted=true`,
so the vendor does the adjustment math and we do the invalidation.

This also fixes a bug that exists today: `Company.last_price`, `market_cap`, and
`atr_14` are all computed from unadjusted, un-invalidated prices, so every split
silently corrupts them until the next daily sync overwrites the price but not
the ATR.

---

## What changes in the existing code

| Today | After |
|---|---|
| `alpaca.get_snapshots()` over ~10k tickers, 20+ batched calls | one grouped-daily call for the whole market |
| `alpaca.py` imported directly by routes, sync, worker | everything goes through `bars.service` / `quotes` |
| `normalize_ticker()` called by every consumer | sealed inside the provider |
| `CALL_DELAY = 0.35` between reaction-worker calls | removed — unlimited calls |
| Reaction worker fetches bars over the network per event | reads warm-set minute chunks locally |
| `BAR_TIMEFRAMES` / `BAR_LOOKBACK_DAYS` whitelists | arbitrary ranges, bounded by chunk count |
| IEX feed, sparse prints, three-way price fallback | full market coverage, fallback becomes vestigial |
| Splits silently corrupt `atr_14` and `market_cap` | `adj_epoch` invalidation |
| Ticker renames silently break the join | `md_symbol` tracks renames and delistings |

`edgar_facts.py` is untouched. Shares outstanding should keep coming from
filings — it is the number our product is *about*, and Massive's is derived.
Massive's ticker details can fill the tail the EDGAR crawl has not converged on
yet, which shortens the "converges over a few cron runs" window, but EDGAR
stays authoritative.

---

## Capacity and cost

Real numbers, because two of these break the current plan:

| Item | Size | Note |
|---|---|---|
| `bar_daily`, all tickers, 5y | ~12.6M rows, ~1.5 GB with indexes | grows ~250 MB/year |
| `bar_chunk`, warm set, 13 months | ~750k rows, ~3 GB | monthly partitions, `DROP` to expire |
| Redis working set | 1–3 GB | current + recent chunks, quotes, locks |
| Vendor calls/day | ~1 grouped + warm-set deltas | well inside "unlimited" |

**Render Postgres free tier is 1 GB — this does not fit.** A paid plan is
required regardless of which storage design we pick; the chunk design just
determines whether that is a 10 GB plan or a 500 GB one. **Render Redis free
tier is 25 MB**, which also does not fit; the cache is load-bearing here, not a
nicety. Both upgrades belong in the Phase 2 budget, not discovered in Phase 4.

### Latency budget

| Layer | Typical | Share of chart loads |
|---|---|---|
| L1 process LRU | ~0.05 ms | ~40% (repeat views) |
| L2 Redis | ~0.5–1 ms | ~50% |
| L3 Postgres chunk | ~2–5 ms | ~8% |
| L4 vendor REST | 80–300 ms | ~2% (cold symbols) |

Target: **p99 under 25 ms** for a warm chart load, which is dominated by
network, not by us. The vendor is only ever on the cold path, and single-flight
means a cold symbol costs one vendor call no matter how many users arrive at
once.

---

## Rollout

Each phase ships independently and is safe to stop after.

**Phase 0 — the seam.** `providers/base.py`, `providers/massive.py`, contract
tests both providers pass. `MARKET_DATA_PROVIDER` still defaults to `alpaca`.
Nothing user-visible changes. *This is the phase that makes every later phase
reversible — do not compress it.*

**Phase 1 — flip the provider.** `MARKET_DATA_PROVIDER=massive` in Render.
Bars, quotes, sync, and reactions all run on Massive with no schema change.
Rollback is one env var. Verify: prices populate for symbols IEX never had,
reaction-worker `no_prints` skips drop toward zero.

**Phase 2 — daily store.** `md_symbol`, `bar_daily`, symbol sync, grouped-daily
backfill of 5 years, daily cron. `/companies/:id/bars` serves 1D from Postgres.
Postgres and Redis plan upgrades land here.

**Phase 3 — chunks, UDF, and marks.** `bars/` package, `/api/v1/md/*`, and the
TradingView widget behind a feature flag in the web app, side by side with the
existing `lightweight-charts` view. Ship `/md/marks` in this phase — the filing
markers are the reason to do this at all.

**Phase 4 — streaming.** `stream/consumer.py`, the `market-stream` Render
worker, `/md` Socket.IO namespace, `subscribeBars` in the datafeed. Chart goes
live (delayed-live).

**Phase 5 — warm set and cleanup.** Minute-chunk materialization for the warm
set, reaction worker reads locally, retention partitions. Delete `alpaca.py`,
the `ALPACA_*` config, and the `ALPACA_*` env vars from `render.yaml`.

---

## Open questions

1. **Real-time upgrade — when?** The design is delay-agnostic, but the product
   claim is not. If "real-time filing intelligence" is the pitch, a 15-minute
   chart sitting next to a real-time alert is a visible seam. Worth pricing the
   upgrade before Phase 4 rather than after.
2. **TradingView licensing.** The Advanced Charting Library is not on npm. It
   requires a free license and access to a private repository, and the files are
   vendored, not redistributed. Apply early — approval is not instant, and
   Phase 3 is blocked on it. See `docs/TRADINGVIEW_CHART.md` in sensybull-web.
3. **Flat files vs. REST for backfill.** Grouped-daily REST is 1,260 calls and
   needs no S3 credentials, which is simpler for daily bars. Flat files win
   decisively if we ever backfill minute bars at market scale. Start with REST;
   reach for S3 when the warm set stops being warm enough.
4. **ETFs and indices.** The plan covers all US tickers, but `company` only
   holds SEC filers. Charting SPY or QQQ for context means `md_symbol` has to
   be allowed to hold symbols with no `company` row. Cheap to allow now,
   annoying to retrofit — the schema above already permits it.

---

## See also

- [[Technology Decisions]] — why Postgres, why Redis
- [[Real-Time System]] — the existing Socket.IO fan-out this extends
- [[Redis Pub-Sub Contract]] — the pattern `stream/contract.py` mirrors
- `services/api/app/services/market_data/reaction_worker.py` — the biggest internal consumer of this layer
- [[Data Model]] — where `md_symbol`, `bar_daily`, `bar_chunk` slot in
