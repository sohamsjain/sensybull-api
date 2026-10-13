"""
fmp.py — Financial Modeling Prep as the press-release source.

FMP's latest-press-releases endpoint aggregates the wires we need (PR
Newswire, GlobeNewswire, Business Wire, ACCESS Newswire) and attaches the
ticker, so one poll replaces four RSS/scrape adapters. Rows come back
newest first:

  {"symbol": "CNP", "publishedDate": "2026-10-10 06:00:00",
   "publisher": "PRNewsWire", "title": "...", "text": "...",
   "url": "https://www.prnewswire.com/news-releases/...", "site": "prnewswire.com",
   "image": "..."}

One release tagged with several symbols (BIP and BIPC) arrives as several
rows with the same URL; parse_fmp_rows folds them into one PRRelease with
every symbol as a metadata ticker, so issuer verification picks the
company that actually issued it and the release publishes once.

Config:
  FMP_API_KEY            required
  FMP_BASE_URL           default https://financialmodelingprep.com/stable
  PR_FMP_PATH            default /news/press-releases-latest
  PR_FMP_LIMIT           rows per poll, default 100
  PR_FMP_TIMEZONE        zone of publishedDate, default America/New_York
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from press_release.feeds import PRRelease

log = logging.getLogger(__name__)

_TIMEOUT = 10

# FMP site / publisher → our wire name (the `source` value on the event).
# Anything else is dropped and counted as `other_wire`.
_WIRE_BY_HOST = {
    "prnewswire.com": "prnewswire",
    "globenewswire.com": "globenewswire",
    "businesswire.com": "businesswire",
    "accessnewswire.com": "accesswire",
    "accesswire.com": "accesswire",
}
_WIRE_BY_PUBLISHER = {
    "prnewswire": "prnewswire",
    "pr newswire": "prnewswire",
    "globenewswire": "globenewswire",
    "globe newswire": "globenewswire",
    "businesswire": "businesswire",
    "business wire": "businesswire",
    "accesswire": "accesswire",
    "access newswire": "accesswire",
    "accessnewswire": "accesswire",
}


class FMPRateLimited(Exception):
    pass


def _host(url: str) -> str:
    host = urllib.parse.urlsplit(url or "").hostname or ""
    host = host.lower()
    for prefix in ("www.", "feed."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def wire_for(row: dict) -> str:
    """Our wire name for an FMP row, or "" when it's not one we ingest."""
    for candidate in (row.get("site") or "", _host(row.get("url") or "")):
        wire = _WIRE_BY_HOST.get(candidate.lower().removeprefix("www."))
        if wire:
            return wire
    return _WIRE_BY_PUBLISHER.get((row.get("publisher") or "").strip().lower(), "")


def _to_iso(raw: str) -> str:
    """FMP's naive "YYYY-MM-DD HH:MM:SS" (in PR_FMP_TIMEZONE) → ISO-8601 UTC."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        try:
            tz = ZoneInfo(os.environ.get("PR_FMP_TIMEZONE", "America/New_York"))
        except Exception:
            tz = timezone.utc
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).isoformat()


def parse_fmp_rows(rows: list, skipped: list | None = None) -> tuple[list[PRRelease], int]:
    """FMP rows → releases (newest first, one per URL) and the count of
    rows dropped for coming from a wire we don't ingest. Those rows are
    also appended to `skipped` when given, so the poller can log them."""
    by_url: dict[str, PRRelease] = {}
    order: list[str] = []
    other_wire = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = (row.get("url") or "").strip()
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        wire = wire_for(row)
        if not wire:
            other_wire += 1
            if skipped is not None:
                skipped.append(row)
            continue
        symbol = (row.get("symbol") or "").strip().upper()
        existing = by_url.get(url)
        if existing:
            if symbol and symbol not in existing.metadata_tickers:
                existing.metadata_tickers.append(symbol)
            continue
        by_url[url] = PRRelease(
            guid=url,
            wire=wire,
            url=url,
            headline=title,
            body_html=row.get("text") or "",
            published=_to_iso(row.get("publishedDate") or row.get("date") or ""),
            metadata_tickers=[symbol] if symbol else [],
            body_is_teaser=True,
        )
        order.append(url)
    return [by_url[u] for u in order], other_wire


def fetch_latest(limit: int | None = None) -> list:
    """One poll of FMP's latest press releases. Raises FMPRateLimited on
    429 and any other error as-is — the caller backs off; the next poll
    is the retry."""
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key:
        raise RuntimeError("FMP_API_KEY is not set")
    base = os.environ.get("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")
    path = os.environ.get("PR_FMP_PATH", "/news/press-releases-latest")
    if limit is None:
        limit = int(os.environ.get("PR_FMP_LIMIT", "100"))
    query = urllib.parse.urlencode({"page": 0, "limit": limit, "apikey": api_key})
    req = urllib.request.Request(
        f"{base}{path}?{query}",
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never log the URL: it carries the API key
        if exc.code == 429:
            raise FMPRateLimited("FMP 429") from None
        raise RuntimeError(f"FMP press releases: HTTP {exc.code}") from None
    if isinstance(data, dict):
        # FMP reports plan/key problems as {"Error Message": "..."}
        raise RuntimeError(f"FMP press releases: {str(data)[:200]}")
    return data
