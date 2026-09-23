# Company Loading

The company table is the US-listed common stocks Financial Modeling Prep knows — NYSE, NASDAQ and AMEX, actively trading, no ETFs or funds, and none of the warrants, units, rights, preferreds or notes listed beside them. It replaced SEC EDGAR's `company_tickers.json` in Sept 2026: that file lists every SEC filer with a symbol (OTC shells and non-traded vehicles included), and its tickers drifted from FMP's, which sent readers to "No company with that ticker".

---

## Daily sync

`flask sync-companies` (`services/api/app/services/company_loader.py`), first step of the daily cron:

```
FMP /company-screener  ×3 exchanges (includeAllShareClasses)
    │  keep common stock only (is_common_stock)
    ▼
symbol already a Company.ticker  → listed = True, FMP's name
new symbol → one /profile call for its CIK, then:
    issuer's row has a ticker that left the universe → rename it (old symbol → `former` alias)
    issuer already has a live row                    → `share_class` alias (GOOG → GOOGL's row)
    otherwise                                        → create the company
    │
    ▼
rows whose ticker left the universe → listed = False (never deleted)
symbols stored feed events used     → `former` aliases
```

A thin or truncated screener answer never de-lists anything. Knobs: `COMPANY_EXCHANGES`, `COMPANY_PROFILE_LIMIT`. `flask check-company-universe` live-checks the screener assumptions with the production key.

On startup, `ensure_companies_loaded()` runs the same sync when the table is nearly empty.

---

## Resolving a symbol

Every symbol → company lookup (fundamentals pages, share links, track-by-ticker) goes through `app/services/company_resolver.py`:

1. the live ticker, in either class-share spelling (BRK.B / BRK-B)
2. a `company_ticker_alias` row (former ticker, secondary share class)
3. the symbol a stored feed event carries

A live ticker always beats an alias, so a reused symbol resolves to its current owner.

---

## How Companies Connect to Events

When the [[Real-Time System|subscriber]] receives a filing event it resolves the company by CIK first, then by symbol (via the resolver), and stores the company's ticker on the event — so feed links, price reactions and PR↔8-K dedup all use one symbol. With no match it **auto-creates** the company (`listed` NULL until the next sync judges it), so 8-Ks from filers outside the listed universe still land.

---

## See Also

- [[Data Model]] — Company schema
- [[Ingest Pipeline Deep Dive]] — Where CIK/ticker mapping happens
- [[Docker & Local Development]] — Startup sequence
