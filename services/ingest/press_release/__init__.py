# services/ingest/press_release/
"""
Press-release ingestion — FMP (or newswire RSS) polling, language detection, issuer verification,
materiality classification, and dedup fingerprinting.

Runs alongside the 8-K EDGAR pipeline in the same worker (see main.py).
"""
