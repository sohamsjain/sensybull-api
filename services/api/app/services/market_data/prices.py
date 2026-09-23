# services/api/app/services/market_data/prices.py
"""
Quotes and OHLCV bars from Financial Modeling Prep (`/stable`).

Replaced Alpaca in Sept 2026, so one vendor now backs fundamentals and
prices. Transport, the API key, retries and the rate limit are the
fundamentals client's (`fundamentals/fmp_client.py`); this module owns only
the price paths and turns FMP's shapes into the ones the rest of the API
already speaks:

- a bar is `{"t", "o", "h", "l", "c", "v"}` with `t` an ISO-8601 UTC string
  ending in `Z`, oldest → newest. Daily bars are stamped at midnight New
  York time (04:00Z / 05:00Z), so a bar's Eastern date is its session —
  the web chart (`chart-signals.ts` `sessionDate`) and the reaction worker
  both rely on that, and the web's history paging compares `t` as strings,
  so the format must not vary.
- a quote is FMP's quote record as received; read it through
  `quote_price()` / `quote_prev_close()` / `quote_time()`.

Two FMP conventions this module absorbs:
- intraday `date` is exchange-local wall time ("2026-09-22 15:59:00",
  America/New_York) with no offset; every comparison downstream is in UTC.
- the EOD `/full` endpoint's OHLC is split-adjusted (not dividend-adjusted),
  which is what a price chart wants.

Tickers: FMP uses the SEC's dash form for class shares (BRK-B), so
`normalize_ticker()` is only a trim/upper-case.
"""

import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.fundamentals.fmp_client import FMPClient, FMPError, fmp_symbol

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

PATH_BATCH_QUOTE = "/batch-quote"
PATH_EOD_FULL = "/historical-price-eod/full"
PATH_INTRADAY = "/historical-chart/{interval}"

# Symbols per batch-quote call; keeps the URL short.
QUOTE_BATCH = 200

DAILY = "1day"
# FMP's intraday intervals → calendar days fetched per request. FMP caps
# how many rows an intraday call returns, so long windows are chunked;
# the spans are conservative (1-min × 3 days is ~1,200 regular-session
# rows, ~2,900 with extended hours).
INTRADAY_CHUNK_DAYS = {
    "1min": 3,
    "5min": 15,
    "15min": 30,
    "30min": 60,
    "1hour": 120,
    "4hour": 365,
}
TIMEFRAMES = frozenset({DAILY, *INTRADAY_CHUNK_DAYS})


class MarketDataError(Exception):
    """Raised when a price request fails after the client's retries."""


_client: FMPClient | None = None


def _fmp() -> FMPClient:
    # One client per process, so every price call shares one rate bucket
    global _client
    if _client is None:
        _client = FMPClient()
    return _client


def _get(path: str, **params):
    try:
        data = _fmp().get(path, **params)
    except FMPError as exc:
        raise MarketDataError(str(exc)) from exc
    if isinstance(data, dict):
        for key in ("historical", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data or []


def normalize_ticker(ticker: str) -> str:
    """SEC form → FMP form (identical apart from case/whitespace)."""
    return fmp_symbol(ticker)


# ── quotes ─────────────────────────────────────────────────────────────

def get_quotes(symbols: list[str], skip_failed_batches: bool = False) -> dict[str, dict]:
    """Latest quote per symbol, keyed by FMP symbol.

    Symbols FMP doesn't know are absent from the result rather than errors.
    skip_failed_batches: for whole-universe callers (the daily sync) — a
    batch FMP rejects is logged and skipped instead of failing every other
    batch; raises only when no batch succeeded.
    """
    out: dict[str, dict] = {}
    unique = list(dict.fromkeys(symbols))
    failures = 0
    batches = range(0, len(unique), QUOTE_BATCH)
    for i in batches:
        batch = unique[i:i + QUOTE_BATCH]
        try:
            records = _get(PATH_BATCH_QUOTE, symbols=",".join(batch))
        except MarketDataError:
            if not skip_failed_batches:
                raise
            failures += 1
            log.warning("Quote batch %s..%s failed; skipping", batch[0], batch[-1], exc_info=True)
            if failures == len(batches):
                raise
            continue
        for record in records:
            if isinstance(record, dict) and record.get("symbol"):
                out[record["symbol"].upper()] = record
    return out


def quote_price(quote: dict | None):
    price = (quote or {}).get("price")
    return price if price else None


def quote_prev_close(quote: dict | None):
    prev = (quote or {}).get("previousClose")
    return prev if prev else None


def quote_time(quote: dict | None):
    """ISO UTC time of the quote (FMP sends epoch seconds), or None."""
    ts = (quote or {}).get("timestamp")
    if not ts:
        return None
    try:
        return _iso(datetime.fromtimestamp(int(ts), tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# ── bars ───────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _session_start(day: date) -> datetime:
    """Midnight New York on `day`, in UTC — a daily bar's stamp."""
    return datetime.combine(day, dtime(0, 0), tzinfo=EASTERN).astimezone(timezone.utc)


def _bar(t: datetime, row: dict) -> dict | None:
    try:
        return {
            "t": _iso(t),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": int(row.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _daily_bars(symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, dict]]:
    rows = _get(PATH_EOD_FULL, symbol=symbol,
                **{"from": start.astimezone(EASTERN).date().isoformat(),
                   "to": end.astimezone(EASTERN).date().isoformat()})
    out = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row["date"])[:10])
        except (KeyError, ValueError):
            continue
        out.append((_session_start(day), row))
    return out


def _intraday_bars(symbol: str, interval: str, start: datetime,
                   end: datetime) -> list[tuple[datetime, dict]]:
    span = timedelta(days=INTRADAY_CHUNK_DAYS[interval])
    first = start.astimezone(EASTERN).date()
    last = end.astimezone(EASTERN).date()
    path = PATH_INTRADAY.format(interval=interval)
    out = []
    chunk_start = first
    while chunk_start <= last:
        chunk_end = min(chunk_start + span - timedelta(days=1), last)
        rows = _get(path, symbol=symbol,
                    **{"from": chunk_start.isoformat(), "to": chunk_end.isoformat()})
        for row in rows:
            try:
                local = datetime.strptime(str(row["date"])[:19], "%Y-%m-%d %H:%M:%S")
            except (KeyError, ValueError):
                continue
            out.append((local.replace(tzinfo=EASTERN).astimezone(timezone.utc), row))
        chunk_start = chunk_end + timedelta(days=1)
    return out


def get_bars(symbol: str, timeframe: str, start: datetime,
             end: datetime | None = None) -> list[dict]:
    """OHLCV bars for one FMP symbol with start <= t <= end, oldest first.

    timeframe: "1day" or an FMP intraday interval ("1min", "15min",
    "1hour", …). start/end are datetimes (naive = UTC); end defaults to now.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {sorted(TIMEFRAMES)}")
    start = _utc(start)
    end = _utc(end) if end else datetime.now(timezone.utc)
    if end < start:
        return []

    if timeframe == DAILY:
        stamped = _daily_bars(symbol, start, end)
    else:
        stamped = _intraday_bars(symbol, timeframe, start, end)

    by_time: dict[datetime, dict] = {}
    for t, row in stamped:
        if start <= t <= end and t not in by_time:
            bar = _bar(t, row)
            if bar is not None:
                by_time[t] = bar
    return [by_time[t] for t in sorted(by_time)]


def compute_atr14(daily_bars: list[dict]) -> float | None:
    """ATR(14) from daily bars ({"h","l","c"} dicts, oldest→newest).

    Simple mean of the last 14 true ranges; needs at least 15 bars.
    """
    if not daily_bars or len(daily_bars) < 15:
        return None
    bars = daily_bars[-15:]
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        prev_close = prev["c"]
        tr = max(
            cur["h"] - cur["l"],
            abs(cur["h"] - prev_close),
            abs(cur["l"] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)
