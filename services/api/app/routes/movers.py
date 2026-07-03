# services/api/app/routes/movers.py
"""
GET /movers — event-driven top gainers/losers.

Ranks companies that filed a material event in the last 7 days by today's
price change (Alpaca snapshots), so every mover comes with its "reason":
the most recent material filing. Response is Redis-cached for 2 minutes;
a stale copy (1h) is served if Alpaca is unavailable.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from app import db
from app.models.filing_event import FilingEvent
from app.services.market_data import alpaca
from app.services.market_data.cache import cache_get, cache_set

log = logging.getLogger(__name__)

movers_bp = Blueprint("movers", __name__)

CACHE_TTL = 120
STALE_TTL = 3600
LOOKBACK_DAYS = 7


def _event_summary(event: FilingEvent) -> dict:
    briefing = event.briefing_json or {}
    return {
        "id": event.id,
        "headline": briefing.get("headline"),
        "significance": briefing.get("significance"),
        "primary_event_type": briefing.get("primary_event_type"),
        "filing_date": FilingEvent._utc_iso(event.filing_date),
    }


def _pick_events(tickers: list[str], cutoff: datetime) -> dict[str, FilingEvent]:
    """Most recent event per ticker in the window, preferring tier <= 2."""
    if not tickers:
        return {}
    events = (
        FilingEvent.query
        .filter(FilingEvent.ticker.in_(tickers))
        .filter(FilingEvent.filing_date >= cutoff)
        .order_by(FilingEvent.filing_date.desc())
        .all()
    )
    best: dict[str, FilingEvent] = {}
    for event in events:  # newest first
        current = best.get(event.ticker)
        if current is None:
            best[event.ticker] = event
        elif current.max_tier > 2 and event.max_tier <= 2:
            best[event.ticker] = event
    return best


def _snapshot_change(snap: dict):
    """(price, change_pct vs previous close) from a snapshot, or None."""
    prev_close = (snap.get("prevDailyBar") or {}).get("c")
    price = (
        (snap.get("latestTrade") or {}).get("p")
        or (snap.get("dailyBar") or {}).get("c")
        or prev_close
    )
    if not prev_close or not price:
        return None
    return price, (price - prev_close) / prev_close * 100.0


@movers_bp.route("/", methods=["GET"])
def get_movers():
    limit = min(max(request.args.get("limit", 10, type=int), 1), 25)
    cache_key = f"movers:v1:{limit}"
    stale_key = f"{cache_key}:stale"

    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ticker_rows = (
        db.session.query(FilingEvent.ticker)
        .filter(FilingEvent.filing_date >= cutoff)
        .filter(FilingEvent.ticker.isnot(None))
        .distinct()
        .all()
    )
    tickers = [t for (t,) in ticker_rows]
    if not tickers:
        empty = {"as_of": datetime.now(timezone.utc).isoformat(), "gainers": [], "losers": []}
        return jsonify(empty)

    symbol_to_ticker = {alpaca.normalize_ticker(t): t for t in tickers}
    try:
        snapshots = alpaca.get_snapshots(list(symbol_to_ticker.keys()))
    except alpaca.AlpacaError:
        log.warning("Alpaca snapshots failed for movers — trying stale cache")
        stale = cache_get(stale_key)
        if stale:
            return jsonify(stale)
        return jsonify({"error": "Market data temporarily unavailable"}), 503

    changes = []
    for symbol, snap in snapshots.items():
        ticker = symbol_to_ticker.get(symbol)
        result = _snapshot_change(snap) if ticker else None
        if result:
            changes.append((ticker, result[0], result[1]))

    changes.sort(key=lambda c: c[2], reverse=True)
    gainer_rows = [c for c in changes if c[2] > 0][:limit]
    loser_rows = [c for c in reversed(changes) if c[2] < 0][:limit]

    winning = [c[0] for c in gainer_rows + loser_rows]
    events = _pick_events(winning, cutoff)

    def _serialize(rows):
        out = []
        for ticker, price, change_pct in rows:
            event = events.get(ticker)
            out.append({
                "ticker": ticker,
                "company_name": event.company_name if event else None,
                "company_id": event.company_id if event else None,
                "price": round(float(price), 4),
                "change_pct": round(change_pct, 2),
                "event": _event_summary(event) if event else None,
            })
        return out

    response = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "gainers": _serialize(gainer_rows),
        "losers": _serialize(loser_rows),
    }
    cache_set(cache_key, response, CACHE_TTL)
    cache_set(stale_key, response, STALE_TTL)
    return jsonify(response)
