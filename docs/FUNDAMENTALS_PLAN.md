# Fundamentals explorer — plan

> **Status:** Phase 0 built (API + pipeline, `services/api/app/services/fundamentals/`);
> Phase 1 (the page) in progress in sensybull-web. **Last updated:** 2026-09-18.
>
> Decisions taken 2026-09-18: FMP Ultimate plan (3,000 calls/min, full history,
> transcripts, 13F); whole universe synced up front (no lazy tail, though the
> on-demand path still exists for tickers that appear between cron runs);
> database moved to `basic-1gb` + 5 GB disk; pages public; every substitution in
> 2.3 confirmed; **balance sheet is Assets → Liabilities → Equity** (US 10-K
> order — screener's liabilities-first layout is Indian Schedule III, and US
> readers expect assets first); Phase 1 before charts or peers.
>
> Goal: a screener.in-quality company page for US equities — same information
> architecture, same density, same "everything on one page, nothing to click
> through" feel — minus the screener (query) feature. Data from Financial
> Modeling Prep (FMP). Frontend in `sensybull-web`, API + data pipeline here.

---

## 0. Ground truth before anything else

Three things this plan is built on that you should push back on if wrong.

**1. This is a supporting surface, not the product.** `PRODUCT_VISION.md`
says the product is thesis monitoring. A fundamentals page earns its place by
being *where the reader looks up the company* when a filing lands — so it links
tightly into the feed, the watchlist and (later) the thesis panel. It must not
grow its own roadmap. Each phase below ships something usable on its own so the
build can stop at any phase boundary without leaving debris.

**2. We store the data, we don't proxy it.** Every alternative was considered
(section 3); proxying FMP per page view fails the latency target, cannot do
peers or industry medians, and pins the product to FMP's rate limits and terms.
Fundamentals change four times a year per company. They belong in our Postgres.

**3. "Blindly copy screener.in" has a short list of things that do not map to
US markets** (section 2.3). Each has a proposed substitute. None of them is a
reason to change the layout.

I could not open screener.in or FMP from the build sandbox (egress is
blocked), so section 2 is from memory of the site. Please skim it against a
real company page — RELIANCE or TCS — and tell me what I have wrong or missed.

---

## 1. Current state of the codebase (what we build on)

| Already exists | Where | Reuse |
| --- | --- | --- |
| `Company` table: ticker, CIK, SIC, shares outstanding, last price, market cap, ATR | `services/api/app/models/company.py` | The anchor row. Fundamentals hang off `company_id`. `market_cap`/`last_price` stay the price source of truth. |
| Daily cron: `sync-companies` + `sync-market-data` (EDGAR shares × Alpaca price) | `Dockerfile.cron`, `render.yaml` | Add `sync-fundamentals` to the same cron chain, or a second cron (see 5.3). |
| Redis JSON cache helpers that no-op without Redis | `services/market_data/cache.py` | Response cache for the new endpoints. |
| Alpaca bars/quote proxies, snapshot price selection | `routes/companies.py`, `market_data/alpaca.py` | Price chart ≤ 5Y, live price in the header. |
| EDGAR `companyfacts`/`frames` client with SEC rate limiting | `market_data/edgar_facts.py` | Later: XBRL cross-check of FMP numbers; EDGAR `submissions` for the Documents section. |
| SIC → sector mapping | `utils/sectors.py` | Fallback when FMP has no industry. |
| Public SSR page pattern with `generateMetadata` + `fetch(..., { next: { revalidate } })` | web `src/app/add/[symbol]/page.tsx` | The company page is built the same way. |
| Design system: tokens, `Table/TH/TD numeric`, `Section`, `Chip/SegmentedControl`, `Kbd` | web `docs/DESIGN_SYSTEM.md`, `components/ui/` | Every table on the page is the existing `Table` primitive. |
| `PriceChart` on lightweight-charts with event markers | web `components/company/price-chart.tsx` | Extended, not replaced, for the chart section. |
| Per-company briefing history (`GET /events/company/:id`) | `routes/events.py` | Becomes the "Announcements" list in Documents — our briefings are better than screener's raw BSE feed. |
| `⌘K` command palette hitting `/companies/search` | web `components/command-palette.tsx` | Search is done; results just need to route to the new page. |

Not present: any financial statement data, any industry classification finer
than SIC division, any cron with a long-running backfill budget, any public
unauthenticated company endpoint (today `GET /companies/<id>` is `jwt_required`).

---

## 2. What we are copying (screener.in, section by section)

### 2.1 Page anatomy (top to bottom)

```
┌ Sticky sub-nav ─ Chart · Analysis · Peers · Quarters · Profit & Loss · Balance Sheet · Cash Flow · Ratios · Investors · Documents ┐
│
│ HEADER      Company name · ticker · exchange   [website] [SEC] [IR]
│             Current price  ▲ 1.2%          (live, refreshed client-side)
│             ┌ Market Cap ─ Current Price ─ High / Low ─ Stock P/E ─ Book Value ─ Dividend Yield ─ ROCE ─ ROE ─ (Face Value → see 2.3) ┐
│             3×3 ratio grid, "+ add ratio" (customisable, remembered per reader)
│
│ ABOUT       2–3 lines of description · Key points (bullets) · [Pros] [Cons]  ← "Analysis"
│
│ CHART       Price line · ranges 1M 6M 1Y 3Y 5Y 10Y Max · overlays: Volume, DMA50, DMA200
│             tabs: Price | PE ratio (+ EPS) | Sales & Margin (quarterly bars + OPM line) | EPS
│
│ PEERS       Same-industry table: Name · CMP · P/E · Mkt Cap · Div Yld · NP Qtr · Qtr Profit Var % · Sales Qtr · Qtr Sales Var % · ROCE
│             median row · "all companies in <industry>" link
│
│ QUARTERLY RESULTS   12 quarters, newest right. Rows: Sales · Expenses · Operating Profit · OPM % · Other Income · Interest ·
│                     Depreciation · Profit before tax · Tax % · Net Profit · EPS      [+ expandable rows, row → chart]
│
│ PROFIT & LOSS       12 years + TTM. Same rows + Dividend Payout %.
│                     Below: four small grids — Compounded Sales Growth, Compounded Profit Growth, Stock Price CAGR, Return on Equity
│                     each for 10Y / 5Y / 3Y / 1Y (or TTM / last year)
│
│ BALANCE SHEET       12 years. Equity Capital · Reserves · Borrowings · Other Liabilities · Total Liabilities ·
│                     Fixed Assets · CWIP · Investments · Other Assets · Total Assets      [+ expandable]
│
│ CASH FLOWS          12 years. Cash from Operating · Investing · Financing · Net Cash Flow      [+ expandable]
│
│ RATIOS              12 years. Debtor Days · Inventory Days · Days Payable · Cash Conversion Cycle · Working Capital Days · ROCE %
│
│ INVESTORS           Shareholding pattern (quarterly / yearly toggle) → see 2.3
│
│ DOCUMENTS           Announcements · Annual reports · Credit ratings · Concalls (transcript / notes / PPT)  → see 2.3
└
```

Interaction rules we copy exactly:

- **Everything is on one page.** No tabs that hide sections; the sub-nav scrolls.
- **Every table row is a chart.** Clicking a row label opens a bar chart of that
  row across the periods, above the table. One chart open at a time.
- **Expandable rows (`+`)** reveal a breakdown (Expenses → material / employee /
  other %, Borrowings → short / long term, Sales → segments where available).
- **Newest period on the right**, header row sticky, numbers tabular, negatives
  as `-1,234` not parentheses, percentages with 0 decimals.
- **Units toggle** per table: `$ Mn` default, `$ Bn` optional; per-share values in `$`.
- **Search is the home page.** Typeahead, keyboard first, ticker or name.
- **Public.** Reading needs no account (screener gates only saved screens and
  notes behind login). Accounts add: remembered ratio grid, watchlist link,
  later thesis notes.

### 2.2 Row definitions (screener row → FMP standardized field)

This mapping is the single most important artifact in the build; it lives in
one file (`services/api/app/services/fundamentals/rows.py`) and is mirrored by
the web row specs. Everything below is *computed by us* from the three
statements, never taken from FMP's own `ratios`/`key-metrics` endpoints, so
that one definition produces the table, the row chart, the header ratio, the
peer column and the pros/cons rule. FMP's derived endpoints are used only as a
cross-check in tests.

**Income statement (Quarters + P&L)**

| Row | Definition |
| --- | --- |
| Sales | `revenue` |
| Expenses | Sales − Operating Profit |
| Operating Profit | `ebitda` if present, else `operatingIncome + depreciationAndAmortization` |
| OPM % | Operating Profit / Sales |
| Other Income | `totalOtherIncomeExpensesNet + interestExpense` (non-operating items, interest stripped out so it can be its own row) |
| Interest | `interestExpense` |
| Depreciation | `depreciationAndAmortization` |
| Profit before tax | `incomeBeforeTax` |
| Tax % | `incomeTaxExpense / incomeBeforeTax` |
| Net Profit | `netIncome` |
| EPS | `epsdiluted` (US convention; screener uses reported EPS) |
| Dividend Payout % | `−dividendsPaid / netIncome` (cash flow statement) |
| Expandable under Expenses | `costOfRevenue`, `sellingGeneralAndAdministrativeExpenses`, `researchAndDevelopmentExpenses`, other — each as % of Sales |
| Expandable under Sales | product / geographic segments (`revenue-product-segmentation`, `revenue-geographic-segmentation`) where FMP has them |

Reconciliation check per period: `Sales − Expenses − Depreciation − Interest +
Other Income ≈ PBT` within 1%. A period that fails is stored with a quality
flag and rendered with the raw `operatingIncome` path instead (see 5.5).

**Balance sheet**

| Row | Definition |
| --- | --- |
| Equity Capital | `commonStock` |
| Reserves | `totalStockholdersEquity − commonStock` |
| Borrowings | `totalDebt` (expand: `shortTermDebt`, `longTermDebt`, `capitalLeaseObligations`) |
| Other Liabilities | `totalLiabilities − totalDebt` (+ `minorityInterest`) (expand: `accountPayables`, `deferredRevenue`, other) |
| Total Liabilities | `totalLiabilitiesAndStockholdersEquity` (screener's "total" is the balance-sheet total) |
| Fixed Assets | `propertyPlantEquipmentNet + goodwill + intangibleAssets` (expand: the three) |
| CWIP | **dropped** — not a US GAAP standardized line (row hidden, not zero) |
| Investments | `longTermInvestments + shortTermInvestments` |
| Other Assets | `totalAssets − Fixed Assets − Investments` (expand: `cashAndCashEquivalents`, `netReceivables`, `inventory`, other) |
| Total Assets | `totalAssets` |

**Cash flows**

| Row | Definition |
| --- | --- |
| Cash from Operating Activity | `netCashProvidedByOperatingActivities` (expand: net income, D&A, working-capital change) |
| Cash from Investing Activity | `netCashUsedForInvestingActivites` (sic — FMP's field is misspelled; pin it in one constant) (expand: capex, acquisitions, investments) |
| Cash from Financing Activity | `netCashUsedProvidedByFinancingActivities` (expand: debt issued/repaid, buybacks, dividends) |
| Net Cash Flow | `netChangeInCash` |
| Free Cash Flow (extra, expandable) | CFO + capex |

**Ratios (annual)**

| Row | Definition |
| --- | --- |
| Debtor Days | `netReceivables / revenue × 365` |
| Inventory Days | `inventory / costOfRevenue × 365` |
| Days Payable | `accountPayables / costOfRevenue × 365` |
| Cash Conversion Cycle | Debtor + Inventory − Payable |
| Working Capital Days | `(totalCurrentAssets − totalCurrentLiabilities) / revenue × 365` |
| ROCE % | `EBIT / (totalAssets − totalCurrentLiabilities)` where EBIT = `incomeBeforeTax + interestExpense` |

**Header ratios (the 3×3 grid; default nine, "+ add" from the full list)**

| Ratio | Definition |
| --- | --- |
| Market Cap | `Company.market_cap` (existing daily sync) |
| Current Price | live quote (existing `useQuote`) |
| High / Low | 52-week, from bars |
| Stock P/E | price / EPS TTM (sum of last 4 quarters' diluted EPS; null when EPS ≤ 0, shown as "—" like screener) |
| Book Value | `totalStockholdersEquity / shares outstanding` (per share) |
| Dividend Yield | trailing-12-month dividends per share / price |
| ROCE | as above, latest FY |
| ROE | `netIncome / average totalStockholdersEquity`, latest FY |
| Face Value | **replaced** — see 2.3 |
| Add-able | EV, EV/EBITDA, P/B, Debt/Equity, Interest coverage, OPM (TTM), Sales growth 3Y, Profit growth 3Y, Free cash flow (TTM), Shares outstanding, Employees, Promoter-equivalent (see 2.3) |

**Growth grids (below P&L)** — each 10Y / 5Y / 3Y / TTM:
Compounded Sales Growth, Compounded Profit Growth (both from annual rows, TTM
vs prior TTM for the last cell), Stock Price CAGR (from monthly closes), Return
on Equity (average over the window; last cell = latest FY).

### 2.3 What does not map to US markets, and the substitute

| screener.in | Why it doesn't map | Substitute |
| --- | --- | --- |
| Consolidated / Standalone toggle | SEC filers report consolidated only | Dropped. No toggle. |
| Face Value | Par value is meaningless in the US ($0.0001) | Replaced in the default grid by **Shares Outstanding** (the number readers actually use with EPS). |
| Rs crores | — | `$ Mn` default, `$ Bn` toggle. Never mixed within a table. |
| Shareholding pattern: Promoters / FIIs / DIIs / Government / Public | No promoter concept; 13F covers institutions only | **Ownership**: Insiders % (Form 4 / proxy, via FMP), Institutions % (13F, via FMP), Float %, top 10 institutional holders with quarter-on-quarter change, number of holders of record (10-K cover). Quarterly history where FMP has it. |
| BSE/NSE announcement feed | — | Our own per-company briefing history (8-K + press releases), which is already better than a raw list. |
| Annual reports (PDF list) | — | 10-K list with EDGAR links, plus 10-Q, DEF 14A (proxy), 8-K count. From the free EDGAR `submissions` API (we have the CIK) — no FMP needed. |
| Credit ratings | Moody's/S&P ratings are not available on any plan we'd pay for | Dropped. Slot stays empty rather than showing something worse. |
| Concalls: transcript / notes / PPT | Investor PPTs are not centrally filed in the US | Earnings-call transcripts (FMP, higher plan only — see 6). PPT link dropped. |
| Bank / NBFC layout (Revenue, Interest, Financing Profit, Financing Margin %) | Same problem here: banks and insurers don't fit Sales/Expenses/OPM | Phase 4: a `bank` row set keyed on SIC 6000–6299 and FMP industry. Phase 1 renders them with the standard rows and a visible "Financial-company layout coming" note rather than silently wrong OPM figures. |
| Notes per company (login) | — | Not now. This is the natural home for thesis capture later (`PRODUCT_VISION.md` step 1) — one free-text "why I own this" on the company page. Out of this plan's scope. |
| Screens / query builder | Explicitly not wanted | Out. |

---

## 3. Architecture

### 3.1 Store vs. proxy (decision: store)

| | Proxy FMP per request | Store in Postgres (chosen) |
| --- | --- | --- |
| Latency | 8–12 FMP calls per page (~2–4 s) unless aggressively cached; cold pages always slow | One indexed read per page; SSR HTML in <200 ms server time |
| Peers / industry medians / "companies in industry" | Impossible (needs every company's numbers) | Trivial SQL over the snapshot table |
| Derived metrics consistency | Recomputed per request, drift between surfaces | Computed once at sync, same number everywhere |
| Rate limits | Every visitor spends our quota; a crawler exhausts it | Quota spent on the nightly delta only |
| Vendor lock-in | Total | Raw payload kept beside normalized columns; swap to SEC XBRL later without a frontend change |
| Cost | Higher plan for the call volume | Lowest plan that has the history and licence we need |
| Freshness | Live | Nightly + on-demand refresh when a company reports (earnings calendar) — fundamentals don't move intraday |

### 3.2 Data flow

```
FMP /stable ──► fundamentals/fmp_client.py ──► fundamentals/mapper.py ──► fundamentals_period (Postgres)
                (rate-limited, retried,        (FMP fields → canonical            + fundamentals_segment
                 raw JSON retained)             columns, reconciliation)
                                                       │
Company.last_price / bars (Alpaca, existing) ──────────┼──► fundamentals/derive.py ──► company_fundamentals (snapshot)
                                                       │     (TTM, CAGRs, ROE/ROCE, header ratios, pros/cons)
EDGAR submissions (free) ──────────────────────────────┘
                                                       ▼
                                   routes/fundamentals.py  (public, Redis-cached, rate-limited)
                                                       ▼
                       sensybull-web  /company/[symbol]  (server component, ISR, client islands)
```

Prices stay on Alpaca (existing). Only long-range chart windows (>5Y, "Max")
use FMP's monthly history, on demand, cached a day.

### 3.3 Storage (Postgres, `services/api/migrations/`)

**`fundamentals_period`** — one row per company × period. Typed columns for
the ~60 canonical line items we render (`BIGINT` dollars for amounts,
`NUMERIC(12,4)` per-share), plus `raw JSONB` holding the three FMP records
as received so the mapping can be re-run without re-fetching.

```
company_id, period_type ('annual'|'quarter'), fiscal_year, fiscal_period ('FY'|'Q1'..'Q4'),
period_end, filing_date, reported_currency, source ('fmp'), source_fetched_at,
quality_flags TEXT[],  -- e.g. {'pbt_reconcile_fail','no_ebitda'}
revenue, cost_of_revenue, gross_profit, sga, rnd, other_opex, depreciation_amortization,
operating_income, ebitda, interest_expense, interest_income, other_income_net, pretax_income,
income_tax, net_income, eps_basic, eps_diluted, weighted_shares_diluted,
cash, short_term_investments, receivables, inventory, total_current_assets, ppe_net, goodwill,
intangibles, long_term_investments, total_assets, payables, deferred_revenue, short_term_debt,
total_current_liabilities, long_term_debt, capital_leases, total_debt, total_liabilities,
common_stock, retained_earnings, total_equity, minority_interest,
cfo, capex, acquisitions, cfi, debt_issued, debt_repaid, buybacks, dividends_paid, cff,
net_change_in_cash, free_cash_flow,
raw JSONB
UNIQUE (company_id, period_type, period_end)
```

**`company_fundamentals`** — one row per company, the snapshot the header,
peers and analysis read. Rebuilt nightly (price-dependent) and whenever the
company's periods change.

```
company_id PK, exchange, industry, sector, description, website, ir_url, ceo, employees,
ipo_date, fiscal_year_end_month, is_adr, has_fundamentals, coverage_from (year),
latest_annual_end, latest_quarter_end, last_synced_at,
-- statement-derived
revenue_ttm, net_income_ttm, eps_ttm, ebitda_ttm, fcf_ttm, book_value_ps, roe, roce, opm_ttm,
debt_to_equity, interest_coverage, cash_conversion_cycle, dividend_payout,
sales_cagr_1y/3y/5y/10y, profit_cagr_1y/3y/5y/10y, roe_avg_3y/5y/10y,
-- price-derived (from Company.last_price + bars)
pe_ttm, pb, ev, ev_ebitda, dividend_yield, high_52w, low_52w, price_cagr_1y/3y/5y/10y,
-- generated
analysis JSONB   -- {"pros": [...], "cons": [...], "key_points": [...]}
```

**`fundamentals_segment`** — `(company_id, period_end, kind 'product'|'geo', name, value)`.

**`ownership_holder`** (Phase 3) — top institutional holders per quarter.

`Company` gains nothing; `company_fundamentals.has_fundamentals` is the flag
search uses to rank real operating companies above the SEC-ticker noise.

Size: ~6,000 US operating companies × (~20 annual + 40 quarterly periods) ≈
360k period rows. Typed columns ≈ 250 MB; `raw` roughly doubles it. **The
current `basic-256mb` Render database is too small for the full universe with
raw retained** — see 6.

### 3.4 Universe and backfill strategy (decision: warm the head, lazy-load the tail)

The `company` table holds ~10k SEC tickers, including funds, SPACs and shells
with no statements. FMP's `profile` (`isEtf`, `isFund`, `isActivelyTrading`)
plus a non-empty income statement decides `has_fundamentals`.

- **Head (nightly cron):** every company that is watchlisted by anyone, has
  had a feed event in the last 90 days, or is in the top ~3,000 by market cap.
  Full history, kept current.
- **Tail (on demand):** first visit to a company outside the head triggers a
  backfill (~10 FMP calls, ~3 s). The API returns `202 {status: "building"}`
  and the page shows the header with a "Loading financials…" skeleton and
  polls. Once built, the company joins the head. Nobody waits twice.
- **Incremental:** the nightly job asks FMP's earnings calendar for companies
  that reported since the last run and refreshes only those, plus a slow
  rolling full-refresh (≈1/30th of the head per night) to pick up restatements.

Budget: full head backfill ≈ 3,000 × 10 calls = 30k calls, a few hours at
300 calls/min. Nightly delta ≈ 200–2,000 calls.

### 3.5 API surface (all public, no JWT, rate-limited, Redis-cached)

| Endpoint | Returns | Cache |
| --- | --- | --- |
| `GET /api/v1/fundamentals/<symbol>` | profile + header ratios + analysis + **all** annual periods + last 40 quarters + segments + growth grids + ratios rows. One payload, ~20 KB gzipped, so the page is one fetch. | Redis 1 h; `Cache-Control: public, s-maxage=3600, stale-while-revalidate=86400` |
| `GET /api/v1/fundamentals/<symbol>/peers` | up to 10 same-industry companies by market cap with the peer columns + industry median | Redis 1 h |
| `GET /api/v1/fundamentals/<symbol>/documents` | 10-K / 10-Q / DEF 14A / 8-K list from EDGAR `submissions`, plus our own event ids | Redis 6 h |
| `GET /api/v1/fundamentals/<symbol>/chart?range=10Y` | monthly closes beyond Alpaca's reach + PE-ratio and EPS-TTM series aligned to dates (Phase 2) | Redis 24 h |
| `GET /api/v1/fundamentals/<symbol>/ownership` | Phase 3 | Redis 24 h |
| `GET /api/v1/industries/<slug>` | companies in an industry with peer columns (Phase 3) | Redis 1 h |
| `GET /api/v1/companies/search` | existing; extended with `industry`, `market_cap`, `has_fundamentals` and ranked by market cap | — |

`<symbol>` is the ticker, so URLs are shareable (`/company/AAPL`), the
existing `normalizeSymbol` handles case and `BRK.B`/`BRK-B`.

### 3.6 Frontend (sensybull-web)

- Route: `src/app/(dashboard)/company/[symbol]/page.tsx` — a **server
  component** inside the dashboard group (rail visible, like `/e/[id]`). It
  fetches the one payload with `next: { revalidate: 3600 }`, renders every
  table as HTML, sets metadata (`AAPL: Apple Inc. — financials, ratios,
  filings`) and JSON-LD. Client islands hydrate only what needs JS: live
  price, row charts, expandable rows, units toggle, section-nav scroll-spy,
  the chart. Signed-out readers get the identical page.
- Why SSR: the latency target is "the numbers are on screen before the
  JavaScript arrives". A client-rendered page here would waterfall
  auth → company → statements → render, exactly what screener.in doesn't do.
- Components (`src/components/fundamentals/`): `CompanyHeader`, `RatioGrid`,
  `AboutSection` (+ pros/cons), `SectionNav`, `FinancialTable` (generic: row
  spec + periods → sticky-header table with expandable rows and row-chart
  trigger), `RowChart`, `GrowthGrids`, `PeersTable`, `RatiosTable`,
  `OwnershipSection`, `DocumentsSection`, `FundamentalsChart` (extends
  `PriceChart` with line mode, ranges to Max, overlays, Sales & Margin tab).
- Row specs (`src/lib/fundamentals/rows.ts`) mirror `rows.py` in name and
  order; formatters (`format.ts`) own `$ Mn`/`$ Bn`, `%`, per-share and
  "—" for nulls. `deal-terms.ts` is the precedent for a mirrored rules file.
- Entry points: `⌘K` results, the ticker on every `FilingCard` and
  `WatchlistItem`, a "Company page" button in `CompanySheet`, and
  `/company` (search box + a "most viewed" list) as the section's home.
  `NAV_ITEMS` gains a third workspace destination — **Explore** (`/company`)
  — per the design system's "named for what it holds" rule.
- Design-system constraints that bite here: `Table` scrolls inside its own
  container (12-year tables at 390 px), `text-micro` floor means dense tables
  use 12–13 px mono figures, no colour except price data, negatives are
  plain ink not `danger`.

---

## 4. Phases

Each phase ends in a pushed, tested, deployable state. Estimates are effort
for a focused build, not calendar promises.

### Phase 0 — Data foundation (API only) — ~1 week

Deliverables:
1. `services/api/app/services/fundamentals/` — `fmp_client.py` (`/stable`
   base, API key from env, token-bucket limiter, retries, 429 back-off, raw
   response capture), `mapper.py` (FMP → canonical columns + reconciliation
   flags), `derive.py` (TTM, CAGRs, ROE/ROCE, header ratios), `analysis.py`
   (rule-based pros/cons), `sync.py` (head/tail/incremental logic).
2. Migration for the tables in 3.3.
3. CLI: `flask sync-fundamentals [--symbols AAPL,MSFT] [--full] [--limit N]`.
4. `GET /fundamentals/<symbol>` and `/documents` with Redis cache and the
   202-building path.
5. Tests with recorded FMP fixtures (no network in CI) for a deliberately
   awkward set: AAPL (Sept FY), MSFT (June FY), COST (52/53-week), JPM
   (bank), BRK-B (insurer/conglomerate), O (REIT), TSM (ADR, 20-F),
   a micro-cap with 3 years of history, one company with a restated year.
6. Cron wiring in `render.yaml` (see 5.3) and `API_CHANGES.md` entry.

Acceptance: for AAPL, MSFT, JPM the Sales, Net Profit, Total Assets and CFO
rows match the 10-K to the dollar for the last 5 years; reconciliation flags
are empty for AAPL/MSFT and present (expected) for JPM; a 500-company backfill
completes inside FMP's per-minute limit with zero unhandled errors.

Needs from you before starting: FMP key + plan (6.1), universe decision (6.2),
DB plan decision (6.3).

### Phase 1 — The page — ~2 weeks

Deliverables: `/company/[symbol]` SSR with header + ratio grid (default nine,
no customisation yet), About + pros/cons, sticky section nav, Quarterly
Results, Profit & Loss + growth grids, Balance Sheet, Cash Flows, Ratios,
Documents (EDGAR list + our briefings). Units toggle. Expandable rows for the
breakdowns FMP gives us directly (expenses, borrowings, cash flow
components). Mobile layout. `/company` home with search. All entry points
wired. Search ranks by market cap and hides non-operating tickers.

Explicitly not in Phase 1: chart section, row charts, peers, ownership,
"+ add ratio".

Acceptance: server render <200 ms with a warm cache; LCP <1.5 s on a
throttled 4G profile; Lighthouse performance ≥90; every table readable with
JS disabled; visual QA on both themes at 390/1440 per the design-system
checklist; a reader who knows screener.in finds every section where they
expect it.

### Phase 2 — Charts — ~1 week

Row charts on every row of every table. Chart section: price line with
1M…Max ranges (Alpaca ≤5Y, FMP monthly beyond), Volume / DMA50 / DMA200
overlays, PE-ratio tab with EPS overlay, Sales & Margin tab (quarterly
Sales bars + OPM % line), EPS tab. Chart marks stay the existing
"moved the stock" filter (`chart-signals.ts`), not every filing.

### Phase 3 — Comparison and ownership — ~1.5 weeks

Peers table with industry median and column sort; industry pages
(`/industry/[slug]`); Ownership section (insiders %, institutions %, top
holders with QoQ change, holders of record); "+ add ratio" with the choice
remembered (localStorage for guests, account preference for signed-in);
Sales segment expansion where FMP has segments; pros/cons rule tuning
against ~50 hand-checked companies.

### Phase 4 — Depth (each item its own decision)

Bank/insurer row layout; earnings-call transcripts (plan-dependent); an
as-reported statement view; SEC XBRL cross-check job that flags FMP
discrepancies; a feed hook so "Q3 results out" for a followed company lands
in the reader's updates (product decision — this touches the thesis judge's
"no financial-statement parsing" rule and needs its own write-up).

---

## 5. Engineering details worth settling now

### 5.1 FMP API version
FMP moved to `/stable` endpoints (`/stable/income-statement?symbol=AAPL&period=annual&limit=…`)
and has been retiring `/api/v3` for new keys. The client targets `/stable`
with the base URL and each path in one constants block, so a version change
is a one-file edit. Field names are pinned as constants, including FMP's
misspelled `netCashUsedForInvestingActivites`.

### 5.2 Fiscal periods
Rows are labelled by `period_end` (`Sep 2025`, `Jun 2025`), never by
calendar quarter, so MSFT's June year and COST's 52/53-week year render
honestly. TTM = sum of the four latest quarters; if quarters are missing,
TTM is null, not approximated. Screener's "TTM" column appears only when
the four quarters exist.

### 5.3 Where the sync runs
`services/api` (has the models and DB). Two Render cron entries: extend the
existing 06:00 UTC `sensybull-company-sync` chain with
`flask sync-fundamentals --incremental`, and add a weekly
`sensybull-fundamentals-refresh` for the rolling full refresh. The one-time
head backfill runs once by hand with `--full --limit` in batches. Tail
backfills run inside the API process as a gevent greenlet; a Redis lock per
symbol prevents double-building.

### 5.4 Caching and invalidation
Response cache keys carry the snapshot's `last_synced_at`, so a refresh
naturally misses. Next's ISR (`revalidate: 3600`) is the second layer. Live
price bypasses both (existing `useQuote`, 60 s).

### 5.5 Data quality
Per-period `quality_flags` from the reconciliation checks; a company whose
latest FY fails renders the P&L from `operatingIncome` upward with a small
"Some rows derived differently — see note" line rather than a wrong OPM.
`scripts/audit_fundamentals.py` diffs 20 random companies against EDGAR
`companyfacts` (`Revenues`, `NetIncomeLoss`, `Assets`,
`NetCashProvidedByUsedInOperatingActivities`) and prints mismatches >2%.
This is the check that decides whether FMP stays the source.

### 5.6 Pros / cons rules (deterministic, no LLM)
Same spirit as screener's, with thresholds in one table:

- Pro: debt reduced (total_debt down ≥20% over 3 FYs, or debt-free);
  profit CAGR 5Y ≥20%; ROE 3Y avg ≥20%; dividend payout ≥25% maintained
  3 years; FCF positive 5 straight years.
- Con: P/B >5 with ROE <15%; sales CAGR 5Y <5%; ROE 3Y avg <10%;
  interest coverage <2; receivables days rose >30% over 3 years;
  net income negative 2 of last 3 years.

Each sentence is generated from the number it cites so the copy can never
drift from the table. Rules are tunable in Phase 3 after eyeballing.

### 5.7 Security / abuse
Public endpoints get Flask-Limiter rules (per-IP) tighter than the
authenticated ones and the tail-backfill trigger is capped per IP per hour so
a crawler cannot burn the FMP quota by walking every ticker.

---

## 6. What I need from you

**6.1 FMP account and plan** — an API key, and the plan's actual limits. Read
them off the pricing page and paste: calls/minute, years of statement
history, whether quarterly statements, segments, institutional holders and
transcripts are included, and **the licence terms on displaying data to the
public on a website** — some FMP tiers prohibit redistribution, and a public
company page is redistribution. This is the one item that can change the
plan: if the licence requires an enterprise tier, the fallback is SEC XBRL
(`companyfacts`, free, no licence, but 2–3 extra weeks for the normalization
FMP would otherwise do). I could not verify current tiers from the sandbox;
my recollection is that the free tier caps history at 5 years and 250
calls/day, which is unusable, and that transcripts sit on the top tier.

**6.2 Universe** — confirm "head + lazy tail" (3.4), or say "everything up
front" and accept a longer first backfill and the bigger DB.

**6.3 Render** — the database needs a bigger plan (rough need: 1 GB now, 2 GB
with raw payloads retained for the full universe), and one extra cron
service. Say yes, or give a ceiling and I'll fit it (dropping `raw` halves the
storage).

**6.4 Public vs. signed-in** — confirm the company page is public with no
account, like screener.in. This decides SSR, SEO metadata and the
rate-limit design. My recommendation is public.

**6.5 The substitutions in 2.3** — confirm or override each. The two that
change layout: dropping Consolidated/Standalone and replacing Face Value with
Shares Outstanding.

**6.6 A reality check on section 2** — open a screener.in company page and
tell me anything the anatomy in 2.1 gets wrong or leaves out. I'm working
from memory; you're looking at it.

**6.7 Priority if we must cut** — Phase 1 alone is a complete product. If
budget or attention runs out after it, would you rather have charts (Phase 2)
or peers (Phase 3) next? My guess is peers — it is the part readers can't get
from a brokerage app.

Nothing else is needed: both repos are already attached, and the existing
Alpaca and SEC credentials cover prices and documents.
