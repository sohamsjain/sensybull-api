"""Financial Modeling Prep client (`/stable` API).

One place owns the base URL, the paths, the API key, the rate limit and the
retry policy. Everything else in the pipeline calls the typed methods below
and never sees an HTTP detail.

Rate limiting is a simple per-process token bucket (`FMP_CALLS_PER_MINUTE`,
default 600 — well under the Ultimate plan's 3,000 so a second process, the
API's on-demand backfill, can share the account). 429s back off and retry.
"""

import logging
import os
import threading
import time
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

BASE_URL = os.environ.get('FMP_BASE_URL', 'https://financialmodelingprep.com/stable')
TIMEOUT = 30
MAX_RETRIES = 4

# Paths, pinned. FMP retired /api/v3 for new keys; if a path moves, it moves here.
PATH_PROFILE = '/profile'
PATH_INCOME = '/income-statement'
PATH_BALANCE = '/balance-sheet-statement'
PATH_CASHFLOW = '/cash-flow-statement'
PATH_DIVIDENDS = '/dividends'
PATH_EOD_LIGHT = '/historical-price-eod/light'
PATH_EARNINGS_CALENDAR = '/earnings-calendar'

# How much history to hold. Annual: everything FMP has. Quarterly: 15 years
# (the table shows 12 quarters; row charts rarely want more than 60).
ANNUAL_LIMIT = 40
QUARTER_LIMIT = 60


class FMPError(Exception):
    pass


class FMPNotConfigured(FMPError):
    pass


class _TokenBucket:
    def __init__(self, per_minute: int):
        self.capacity = max(1, per_minute)
        self.tokens = float(self.capacity)
        self.refill_per_sec = self.capacity / 60.0
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def take(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_sec)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.refill_per_sec
            time.sleep(wait)


class FMPClient:
    def __init__(self, api_key: str | None = None, session: requests.Session | None = None,
                 calls_per_minute: int | None = None):
        self.api_key = api_key or os.environ.get('FMP_API_KEY') or ''
        self.session = session or requests.Session()
        per_minute = calls_per_minute or int(os.environ.get('FMP_CALLS_PER_MINUTE', '600'))
        self.bucket = _TokenBucket(per_minute)
        self.calls = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    # ── transport ──────────────────────────────────────────────────────
    def get(self, path: str, **params):
        if not self.api_key:
            raise FMPNotConfigured('FMP_API_KEY is not set')
        url = f'{BASE_URL}{path}'
        params = {k: v for k, v in params.items() if v is not None}
        params['apikey'] = self.api_key
        delay = 1.0
        for attempt in range(MAX_RETRIES):
            self.bucket.take()
            self.calls += 1
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES - 1:
                    raise FMPError(f'{path}: {exc}') from exc
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    raise FMPError(f'{path}: HTTP {resp.status_code}')
                retry_after = resp.headers.get('Retry-After')
                time.sleep(float(retry_after) if retry_after else delay)
                delay *= 2
                continue
            if resp.status_code in (401, 403):
                raise FMPError(f'{path}: HTTP {resp.status_code} (check FMP_API_KEY / plan)')
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                raise FMPError(f'{path}: HTTP {resp.status_code}: {resp.text[:200]}')
            try:
                data = resp.json()
            except ValueError as exc:
                raise FMPError(f'{path}: non-JSON response') from exc
            # FMP reports errors as 200 + {"Error Message": ...}
            if isinstance(data, dict) and ('Error Message' in data or 'error' in data):
                raise FMPError(f'{path}: {data}')
            return data
        raise FMPError(f'{path}: retries exhausted')

    # ── typed endpoints ────────────────────────────────────────────────
    def profile(self, symbol: str) -> dict | None:
        data = self.get(PATH_PROFILE, symbol=symbol)
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    def income_statements(self, symbol: str, period: str, limit: int | None = None) -> list[dict]:
        limit = limit or (ANNUAL_LIMIT if period == 'annual' else QUARTER_LIMIT)
        return self._list(PATH_INCOME, symbol=symbol, period=period, limit=limit)

    def balance_sheets(self, symbol: str, period: str, limit: int | None = None) -> list[dict]:
        limit = limit or (ANNUAL_LIMIT if period == 'annual' else QUARTER_LIMIT)
        return self._list(PATH_BALANCE, symbol=symbol, period=period, limit=limit)

    def cash_flows(self, symbol: str, period: str, limit: int | None = None) -> list[dict]:
        limit = limit or (ANNUAL_LIMIT if period == 'annual' else QUARTER_LIMIT)
        return self._list(PATH_CASHFLOW, symbol=symbol, period=period, limit=limit)

    def dividends(self, symbol: str, limit: int = 40) -> list[dict]:
        return self._list(PATH_DIVIDENDS, symbol=symbol, limit=limit)

    def eod_light(self, symbol: str, years: int = 10) -> list[dict]:
        """Daily closes: [{date, price, volume}], newest first."""
        end = date.today()
        start = end - timedelta(days=365 * years + 10)
        return self._list(PATH_EOD_LIGHT, symbol=symbol,
                          **{'from': start.isoformat(), 'to': end.isoformat()})

    def earnings_calendar(self, start: date, end: date) -> list[dict]:
        return self._list(PATH_EARNINGS_CALENDAR,
                          **{'from': start.isoformat(), 'to': end.isoformat()})

    def _list(self, path, **params) -> list[dict]:
        data = self.get(path, **params)
        if isinstance(data, dict):
            # some endpoints wrap: {"historical": [...]} / {"symbol":..., "historical": [...]}
            for key in ('historical', 'data', 'results'):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data or []


def fmp_symbol(ticker: str) -> str:
    """SEC form → FMP form. FMP uses BRK-B (dash), same as SEC."""
    return ticker.strip().upper().replace('.', '-')
