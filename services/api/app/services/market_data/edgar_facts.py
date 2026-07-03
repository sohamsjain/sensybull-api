# services/api/app/services/market_data/edgar_facts.py
"""
Shares outstanding from SEC EDGAR XBRL APIs.

Bulk path: the "frames" API returns every filer's value for one concept in
one period (a few requests cover the whole market). Fallback path: the
per-company "companyfacts" API, used only for companies the frames data
missed. Both require SEC_USER_AGENT (same convention as company_loader.py)
and stay well under the SEC's 10 req/s limit.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta

import requests

log = logging.getLogger(__name__)

FRAMES_URL = (
    "https://data.sec.gov/api/xbrl/frames/dei/"
    "EntityCommonSharesOutstanding/shares/CY{year}Q{quarter}I.json"
)
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Delay between per-company companyfacts calls (~8 req/s, SEC allows 10)
FALLBACK_DELAY = 0.125


def _user_agent() -> str | None:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        log.warning("SEC_USER_AGENT not set — cannot fetch EDGAR XBRL data")
    return ua


def _get_json(url: str, user_agent: str) -> dict | None:
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    except requests.RequestException:
        log.exception("EDGAR request failed: %s", url)
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        log.warning("EDGAR returned %d for %s", resp.status_code, url)
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("EDGAR returned non-JSON for %s", url)
        return None


def _recent_quarters(today: date | None = None, count: int = 3) -> list[tuple[int, int]]:
    """(year, quarter) for the current quarter and the `count - 1` before it."""
    d = today or datetime.utcnow().date()
    year, quarter = d.year, (d.month - 1) // 3 + 1
    out = []
    for _ in range(count):
        out.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
    return out


def fetch_shares_by_cik() -> dict[str, tuple[int, date]]:
    """Bulk shares outstanding: {zero-padded CIK: (shares, as_of_date)}.

    Queries the current + 2 prior quarterly frames; the most recent frame
    that reports a CIK wins (recently-ended quarters are sparsely populated
    until filers catch up, hence the lookback).
    """
    ua = _user_agent()
    if not ua:
        return {}

    result: dict[str, tuple[int, date]] = {}
    for year, quarter in _recent_quarters():
        data = _get_json(FRAMES_URL.format(year=year, quarter=quarter), ua)
        if not data:
            continue
        for entry in data.get("data") or []:
            cik = str(entry.get("cik", "")).zfill(10)
            val = entry.get("val")
            if not val or cik in result:
                continue
            try:
                as_of = datetime.strptime(entry.get("end", ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                as_of = None
            result[cik] = (int(val), as_of)
        time.sleep(FALLBACK_DELAY)
    log.info("EDGAR frames: shares outstanding for %d CIKs", len(result))
    return result


def fetch_company_shares(cik: str) -> tuple[int, date | None] | None:
    """Per-company fallback via companyfacts.

    Tries dei:EntityCommonSharesOutstanding then
    us-gaap:CommonStockSharesOutstanding; returns the most recent value.
    """
    ua = _user_agent()
    if not ua:
        return None

    data = _get_json(COMPANYFACTS_URL.format(cik=str(cik).zfill(10)), ua)
    if not data:
        return None
    facts = data.get("facts") or {}

    for taxonomy, concept in (
        ("dei", "EntityCommonSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
    ):
        units = ((facts.get(taxonomy) or {}).get(concept) or {}).get("units") or {}
        entries = units.get("shares") or []
        best = None
        for entry in entries:
            val, end = entry.get("val"), entry.get("end")
            if not val or not end:
                continue
            if best is None or end > best[1]:
                best = (int(val), end)
        if best:
            try:
                as_of = datetime.strptime(best[1], "%Y-%m-%d").date()
            except ValueError:
                as_of = None
            # Ignore stale values — a >2-year-old share count is worse
            # than none for market-cap math
            if as_of and as_of < datetime.utcnow().date() - timedelta(days=730):
                continue
            return best[0], as_of
    return None
