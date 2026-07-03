# services/api/app/services/market_data/reaction_worker.py
"""
Price-reaction worker.

Drains the price_reaction table (the durable work queue written by the
Redis subscriber): every 60s it claims rows whose measure_at has passed,
measures the price move since the filing via Alpaca bars, flags explosive
moves (|move| >= 2 x ATR14), and pushes updates to connected clients as
`price_reaction` socket events.

Measurement semantics (after-hours friendly):
- baseline  = close of the last 1-min bar at/before filing_date
              (fallback: last daily close before the filing date)
- 5m..1h    = close of the first 1-min bar at/after measure_at — an
              after-hours filing's "+5m" resolves to the next print,
              and measured_at records when that actually was
- 1d / 1w   = close of the 1st / 5th daily bar after the filing date

Runs as a daemon thread in the API process (same pattern as the Redis
subscriber); pending rows survive restarts because state lives in Postgres.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.market_data import alpaca
from app.services.market_data.cache import cache_get, cache_set

log = logging.getLogger(__name__)

TICK_SECONDS = 60
CLAIM_LIMIT = 200
MAX_ATTEMPTS = 5
# Give up on a measurement this long after it came due with no prints at all
STALE_AFTER = timedelta(days=4)
# Politeness delay between Alpaca calls (free tier: 200 req/min)
CALL_DELAY = 0.35

ATR_CACHE_TTL = 24 * 3600


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite strips tzinfo; stored values are UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_bar_time(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _throttled_bars(symbol: str, timeframe: str, start: datetime, end: datetime | None):
    time.sleep(CALL_DELAY)
    end_iso = end.isoformat() if end else None
    bars = alpaca.get_bars([symbol], timeframe, start.isoformat(), end=end_iso)
    return bars.get(symbol) or []


def _resolve_atr(company, symbol: str, daily_bars: list[dict], now: datetime):
    """ATR(14) for the ticker: Redis cache → fresh Company column → compute."""
    cached = cache_get(f"atr:{symbol}")
    if cached is not None:
        return float(cached)
    if company is not None and company.atr_14 is not None:
        updated = _aware(company.atr_updated_at)
        if updated and now - updated < timedelta(seconds=ATR_CACHE_TTL):
            return float(company.atr_14)

    atr = alpaca.compute_atr14(daily_bars)
    if atr is None:
        return None
    if company is not None:
        company.atr_14 = Decimal(str(round(atr, 4)))
        company.atr_updated_at = now
    cache_set(f"atr:{symbol}", atr, ATR_CACHE_TTL)
    return atr


def _measure_intraday(minute_bars: list[dict], measure_at: datetime):
    """First 1-min bar at/after measure_at → (price, bar time), else None."""
    for bar in minute_bars:
        bar_time = _parse_bar_time(bar["t"])
        if bar_time >= measure_at:
            return bar["c"], bar_time
    return None


def _measure_daily(daily_bars_after: list[dict], interval: str):
    """1st (1d) / 5th (1w) daily bar after the filing date → (price, bar time)."""
    index = 0 if interval == "1d" else 4
    if len(daily_bars_after) > index:
        bar = daily_bars_after[index]
        return bar["c"], _parse_bar_time(bar["t"])
    return None


def _finish_row(row, price, measured_at, baseline, atr):
    row.measured_price = Decimal(str(price))
    row.measured_at = measured_at
    row.pct_change = round((float(price) - baseline) / baseline * 100.0, 4)
    if atr:
        row.atr_14 = Decimal(str(round(atr, 4)))
        row.is_explosive = abs(float(price) - baseline) >= 2.0 * atr
    row.status = "done"
    row.error = None


def _process_event_rows(db, event, rows, company, now):
    """Measure all due rows for one event. Returns True if any row completed."""
    from app.models.price_reaction import STATUS_SKIPPED

    symbol = alpaca.normalize_ticker(event.ticker)
    t0 = _aware(event.filing_date)

    # One daily-bars window serves ATR (bars before t0) and 1d/1w
    # measurements (bars after t0's date).
    daily_bars = _throttled_bars(symbol, "1Day", t0 - timedelta(days=60), None)
    pre_t0 = [b for b in daily_bars if _parse_bar_time(b["t"]).date() <= t0.date()]
    post_t0 = [b for b in daily_bars if _parse_bar_time(b["t"]).date() > t0.date()]

    atr = _resolve_atr(company, symbol, pre_t0, now)

    intraday_rows = [r for r in rows if r.interval in ("5m", "15m", "30m", "1h")]
    minute_bars = []
    if intraday_rows:
        window_end = min(
            max(_aware(r.measure_at) for r in intraday_rows) + timedelta(hours=24),
            now,
        )
        minute_bars = _throttled_bars(symbol, "1Min", t0 - timedelta(hours=4), window_end)

    # Baseline: last 1-min close at/before t0, else last daily close before t0
    baseline = baseline_at = None
    for bar in minute_bars:
        bar_time = _parse_bar_time(bar["t"])
        if bar_time <= t0:
            baseline, baseline_at = bar["c"], bar_time
        else:
            break
    if baseline is None and pre_t0:
        prev = [b for b in pre_t0 if _parse_bar_time(b["t"]).date() < t0.date()]
        if prev:
            baseline, baseline_at = prev[-1]["c"], _parse_bar_time(prev[-1]["t"])

    completed = False
    for row in rows:
        measure_at = _aware(row.measure_at)
        overdue = now - measure_at

        if baseline is None:
            # No prints at all around the filing (unknown/illiquid symbol)
            if overdue > STALE_AFTER:
                row.status = STATUS_SKIPPED
                row.error = "no_prints"
            else:
                row.attempts += 1
            continue

        row.baseline_price = Decimal(str(baseline))
        row.baseline_at = baseline_at

        if row.interval in ("1d", "1w"):
            result = _measure_daily(post_t0, row.interval)
            # Holiday-shortened weeks: settle for the latest available bar
            # once well overdue rather than leaving the row stuck
            if result is None and overdue > STALE_AFTER and post_t0:
                bar = post_t0[-1]
                result = bar["c"], _parse_bar_time(bar["t"])
        else:
            result = _measure_intraday(minute_bars, measure_at)

        if result is None:
            if overdue > STALE_AFTER:
                row.status = STATUS_SKIPPED
                row.error = "no_prints"
            # else: leave pending — prints may still arrive (AH gap, next open)
            continue

        _finish_row(row, result[0], result[1], float(baseline), atr)
        completed = True

    db.session.commit()
    return completed


def _emit_reactions(socketio, event):
    payload = event.to_ws_payload()
    update = {
        "filing_event_id": event.id,
        "ticker": event.ticker,
        "price_reactions": payload["price_reactions"],
        "explosive": payload["explosive"],
    }
    socketio.emit("price_reaction", update, room="public", namespace="/feed")
    if event.ticker:
        socketio.emit("price_reaction", update, room=f"ticker:{event.ticker}",
                      namespace="/feed")


def tick(app, socketio) -> int:
    """One worker pass. Returns the number of rows that completed."""
    with app.app_context():
        from app import db
        from app.models.company import Company
        from app.models.price_reaction import PriceReaction, STATUS_FAILED, STATUS_PENDING

        now = datetime.now(timezone.utc)
        q = (
            PriceReaction.query
            .filter(PriceReaction.status == STATUS_PENDING)
            .filter(PriceReaction.measure_at <= now)
            .order_by(PriceReaction.measure_at)
            .limit(CLAIM_LIMIT)
        )
        if db.session.get_bind().dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        rows = q.all()
        if not rows:
            return 0

        by_event: dict[str, list] = {}
        for row in rows:
            by_event.setdefault(row.filing_event_id, []).append(row)

        done_count = 0
        for event_id, event_rows in by_event.items():
            event = event_rows[0].filing_event
            if event is None or not event.ticker or not event.filing_date:
                for row in event_rows:
                    row.status = "skipped"
                    row.error = "event_missing"
                db.session.commit()
                continue

            company = Company.query.filter_by(ticker=event.ticker).first()
            try:
                if _process_event_rows(db, event, event_rows, company, now):
                    done_count += sum(1 for r in event_rows if r.status == "done")
                    _emit_reactions(socketio, event)
            except alpaca.AlpacaError as exc:
                db.session.rollback()
                for row in event_rows:
                    row.attempts += 1
                    if row.attempts >= MAX_ATTEMPTS:
                        row.status = STATUS_FAILED
                        row.error = str(exc)[:200]
                db.session.commit()
                log.warning("Alpaca error for %s: %s", event.ticker, exc)
            except Exception:
                db.session.rollback()
                log.exception("Reaction worker failed on event %s", event_id)

        return done_count


def backfill_reactions(days: int = 7) -> tuple[int, int]:
    """Create missing PriceReaction rows for recent events (CLI helper)."""
    from app import db
    from app.models.filing_event import FilingEvent
    from app.models.price_reaction import INTERVALS, PriceReaction

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = (
        FilingEvent.query
        .filter(FilingEvent.filing_date >= cutoff)
        .filter(FilingEvent.ticker.isnot(None))
        .all()
    )
    created = touched = 0
    for event in events:
        existing = {r.interval for r in event.price_reactions}
        missing = [i for i in INTERVALS if i not in existing]
        if not missing:
            continue
        for interval in missing:
            db.session.add(PriceReaction(
                filing_event_id=event.id,
                ticker=event.ticker,
                interval=interval,
                measure_at=_aware(event.filing_date) + timedelta(seconds=INTERVALS[interval]),
            ))
            created += 1
        touched += 1
    db.session.commit()
    return created, touched


def start_reaction_worker(app, socketio) -> threading.Thread | None:
    """Spawn the worker daemon thread. Safe to call multiple times — only
    the first call per process spawns a thread (mirrors start_subscriber)."""
    _lock = getattr(start_reaction_worker, "_lock", None)
    if _lock is None:
        start_reaction_worker._lock = threading.Lock()
        start_reaction_worker._started = False

    with start_reaction_worker._lock:
        if getattr(start_reaction_worker, "_started", False):
            return None
        start_reaction_worker._started = True

    def _run():
        log.info("Price-reaction worker starting (tick=%ds)", TICK_SECONDS)
        while True:
            try:
                done = tick(app, socketio)
                if done:
                    log.info("Reaction worker: completed %d measurements", done)
            except Exception:
                log.exception("Reaction worker: unhandled error in tick")
            time.sleep(TICK_SECONDS)

    t = threading.Thread(target=_run, daemon=True, name="price-reaction-worker")
    t.start()
    log.info("Price-reaction worker thread started")
    return t
