# services/api/app/services/market_data/alpaca.py
"""
Thin client for the Alpaca Market Data API (https://data.alpaca.markets).

Free-tier friendly: uses the feed named by ALPACA_FEED (default "iex"),
batches multi-symbol requests, and honors Retry-After on 429s.

All symbols passed to Alpaca must go through normalize_ticker() — SEC
class-share tickers use a dash ("BRK-B") while Alpaca uses a dot ("BRK.B").
The database always stores the SEC form.
"""

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

SNAPSHOT_BATCH = 500
BARS_SYMBOL_BATCH = 200


class AlpacaError(Exception):
    """Raised when an Alpaca request fails after retries."""


def _base_url() -> str:
    return os.environ.get("ALPACA_DATA_BASE_URL") or "https://data.alpaca.markets"


def _feed() -> str:
    return os.environ.get("ALPACA_FEED") or "iex"


def _headers() -> dict:
    key = os.environ.get("ALPACA_API_KEY_ID")
    secret = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise AlpacaError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def normalize_ticker(ticker: str) -> str:
    """SEC form → Alpaca form (BRK-B → BRK.B)."""
    return ticker.strip().upper().replace("-", ".")


def denormalize_ticker(symbol: str) -> str:
    """Alpaca form → SEC form (BRK.B → BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def _get(path: str, params: dict) -> dict:
    url = f"{_base_url()}{path}"
    last_status = None
    for attempt in (1, 2):
        resp = requests.get(url, params=params, headers=_headers(), timeout=30)
        last_status = resp.status_code
        if resp.status_code == 429 and attempt == 1:
            wait = resp.headers.get("Retry-After")
            try:
                wait = float(wait) if wait else 3.0
            except ValueError:
                wait = 3.0
            log.warning("Alpaca 429 on %s — retrying in %.1fs", path, wait)
            time.sleep(min(wait, 30.0))
            continue
        if resp.status_code != 200:
            raise AlpacaError(f"Alpaca {path} returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    raise AlpacaError(f"Alpaca {path} rate-limited (status {last_status})")


def get_snapshots(symbols: list[str]) -> dict[str, dict]:
    """Latest trade/quote/daily bar/prev daily bar per symbol.

    Symbols are expected in Alpaca form (see normalize_ticker). Returns a
    dict keyed by Alpaca symbol; symbols unknown to Alpaca are absent.
    """
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), SNAPSHOT_BATCH):
        batch = symbols[i:i + SNAPSHOT_BATCH]
        data = _get("/v2/stocks/snapshots", {
            "symbols": ",".join(batch),
            "feed": _feed(),
        })
        # Multi-symbol snapshots respond as {symbol: snapshot, ...}
        for sym, snap in (data or {}).items():
            if isinstance(snap, dict):
                out[sym] = snap
    return out


def snapshot_price(snap: dict):
    """Best available last price from an Alpaca snapshot.

    The IEX feed is sparse: a thinly traded symbol can have no trade today,
    so fall back through today's daily bar to the previous close.
    """
    for node_key, field in (("latestTrade", "p"), ("dailyBar", "c"), ("prevDailyBar", "c")):
        node = snap.get(node_key) or {}
        price = node.get(field)
        if price:
            return price
    return None


def snapshot_time(snap: dict):
    """Timestamp matching the price snapshot_price() would pick."""
    for node_key, field in (("latestTrade", "p"), ("dailyBar", "c"), ("prevDailyBar", "c")):
        node = snap.get(node_key) or {}
        if node.get(field):
            return node.get("t")
    return None


def get_bars(
    symbols: list[str],
    timeframe: str,
    start: str,
    end: str | None = None,
    limit: int = 10000,
    adjustment: str = "split",
) -> dict[str, list[dict]]:
    """Historical OHLCV bars keyed by Alpaca symbol.

    timeframe: e.g. "1Min", "15Min", "1Hour", "1Day".
    start/end: RFC-3339 timestamps. Follows next_page_token to exhaustion.
    """
    out: dict[str, list[dict]] = {}
    for i in range(0, len(symbols), BARS_SYMBOL_BATCH):
        batch = symbols[i:i + BARS_SYMBOL_BATCH]
        params = {
            "symbols": ",".join(batch),
            "timeframe": timeframe,
            "start": start,
            "limit": limit,
            "adjustment": adjustment,
            "feed": _feed(),
        }
        if end:
            params["end"] = end
        page_token = None
        while True:
            if page_token:
                params["page_token"] = page_token
            data = _get("/v2/stocks/bars", params)
            for sym, bars in (data.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars or [])
            page_token = data.get("next_page_token")
            if not page_token:
                break
    return out


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
