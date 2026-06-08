# Company Loading

Companies are imported from SEC EDGAR's public company database. This provides the master list of ~13,000 publicly traded companies that users can add to their [[Watchlists]].

---

## Auto-Load on Startup

When the API starts and the `company` table is empty, `company_loader.py` automatically fetches SEC's `company_tickers.json`:

```
SEC company_tickers.json
    │
    ▼
~13,000 companies with:
  - CIK (SEC unique ID)
  - Ticker
  - Company name
    │
    ▼
Bulk insert with batch commit (every 1000 rows)
```

This happens once — subsequent startups skip the load if the table has data.

---

## Manual Loading Script

**File:** `services/api/scripts/load_companies.py`

For more detailed company data (SIC codes, state of incorporation, exchange), use the script with [[EdgarTools]]:

```bash
# Load a specific company
python scripts/load_companies.py --ticker AAPL

# Load first 100 companies
python scripts/load_companies.py --limit 100

# Load by exchange
python scripts/load_companies.py --exchange NYSE
```

EdgarTools fetches richer company metadata than the simple ticker map, but it's slower (one API call per company).

---

## How Companies Connect to Events

When the [[Real-Time System|subscriber]] receives a filing event:

1. Try matching by `ticker`
2. Fall back to matching by `cik`
3. If no match: **auto-create** a new Company record from the message data

This ensures every filing event gets linked to a company, even if the company wasn't in the initial SEC load.

---

## See Also

- [[Data Model]] — Company schema
- [[Ingest Pipeline Deep Dive]] — Where CIK/ticker mapping happens
- [[Docker & Local Development]] — Startup sequence
