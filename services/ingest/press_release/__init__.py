# services/ingest/press_release/
"""
Press-release ingestion — newswire RSS polling, issuer verification,
materiality classification, and dedup fingerprinting.

Runs alongside the 8-K EDGAR pipeline in the same worker (see main.py).
Disabled unless PR_INGEST_ENABLED=1.
"""
