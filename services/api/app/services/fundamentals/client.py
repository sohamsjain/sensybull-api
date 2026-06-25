"""
client.py — HTTP access to SEC's XBRL companyfacts API.

Returns the raw companyfacts JSON for a CIK. Stateless; no DB. The SEC
requires a descriptive User-Agent (same ``SEC_USER_AGENT`` env var the ingest
service uses) and asks callers to stay polite (<10 req/s) — analysis runs at
filing cadence, so a small retry/backoff is plenty.
"""
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def _user_agent() -> str:
    # Fall back to a generic but identifiable UA if the env var is unset, so a
    # misconfigured deploy degrades (analysis skipped) rather than crashing.
    return os.environ.get("SEC_USER_AGENT", "Sensybull analysis (support@sensybull.com)")


def fetch_company_facts(cik: str, retries: int = 3, base_timeout: int = 20):
    """Fetch raw companyfacts JSON for a CIK (any format — zero-padded here).

    Returns the parsed dict, or ``None`` if the company has no XBRL facts
    (HTTP 404) or every attempt fails. Never raises.
    """
    if not cik:
        return None
    padded = str(cik).strip().lstrip("0").zfill(10)
    url = COMPANYFACTS_URL.format(cik=padded)
    headers = {"User-Agent": _user_agent(), "Accept": "application/json"}

    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=base_timeout + attempt * 15)
            if resp.status_code == 404:
                log.info("companyfacts: no XBRL data for CIK %s (404)", padded)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — network/JSON errors are all recoverable here
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    log.warning("companyfacts: fetch failed for CIK %s — %s", padded, last_exc)
    return None
