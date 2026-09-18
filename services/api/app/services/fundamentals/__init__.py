"""Fundamentals pipeline: FMP → canonical periods → derived snapshot → API.

Modules:
- fmp_client  — HTTP client for FMP's /stable API (rate limited, retried)
- fields      — FMP field aliases across API generations, safe accessors
- mapper      — FMP statement records → FundamentalsPeriod column dicts
- rows        — canonical screener-style rows computed from a period
- derive      — TTM, CAGRs, header ratios, growth grids (the snapshot)
- analysis    — deterministic pros / cons
- sync        — backfill + incremental orchestration
- payload     — API response assembly
- edgar_docs  — SEC filings list for the Documents section
"""
