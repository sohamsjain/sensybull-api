# services/api/app/services/market_data/reaction_worker.py
"""
Price-reaction worker.

Drains the price_reaction table (the durable work queue written by the
Redis subscriber): every 60s it claims rows whose measure_at has passed,
measures the price move since the filing via FMP bars, flags explosive
moves (|move| >= 2 x ATR14), and pushes updates to connected clients as
`price_reaction` socket events.

Measurement semantics. FMP's 1-min bars cover the regular session only
(09:30-16:00 ET), so a filing's session decides what can be measured:
- baseline  = close of the last 1-min bar at/before filing_date
              (fallback: the last daily close before it)
- filed during the session:
    5m..1h  = close of the first 1-min bar at/after measure_at; an interval
              that lands after that session's close is skipped
              ("after_close") rather than read off the next morning
- filed outside it (evening, pre-market, weekend, holiday):
    open    = the next session's opening print. The four intraday rows
              would all resolve to that same bar, so the 5m row becomes
              "open" and 15m-1h are skipped ("off_hours")
- 1d / 1w   = close of the 1st / 5th daily bar after the filing's
              (Eastern) date

Runs as a daemon thread in the API process (same pattern as the Redis
subscriber); pending rows survive restarts because state lives in Postgres.
"""

import logging
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal

from app.models.price_reaction import (
    INTERVALS, INTRADAY_INTERVALS, OPEN_INTERVAL, STATUS_PENDING, STATUS_SKIPPED,
)
from app.services.market_data import prices
from app.services.market_data.cache import cache_get, cache_set

log = logging.getLogger(__name__)

TICK_SECONDS = 60
CLAIM_LIMIT = 200
MAX_ATTEMPTS = 5
# Give up on a measurement this long after it came due with no prints at all
STALE_AFTER = timedelta(days=4)
# Politeness delay between calls; the FMP client's token bucket is the
# real limit, this just spreads a burst of due rows across the tick.
CALL_DELAY = 0.05

ATR_CACHE_TTL = 24 * 3600

SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)
# Measure the open a few minutes after the bell, once its bar has landed
OPEN_SETTLE = timedelta(minutes=5)
# How far back to look for the last print before an off-hours filing:
# far enough to cross a long weekend
OFF_HOURS_LOOKBACK = timedelta(days=4)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite strips tzinfo; stored values are UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_bar_time(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _throttled_bars(symbol: str, timeframe: str, start: datetime, end: datetime | None):
    time.sleep(CALL_DELAY)
    return prices.get_bars(symbol, timeframe, start, end)


def _resolve_atr(company, symbol: str, daily_bars: list[dict], now: datetime):
    """ATR(14) for the ticker: Redis cache → fresh Company column → compute."""
    cached = cache_get(f"atr:{symbol}")
    if cached is not None:
        return float(cached)
    if company is not None and company.atr_14 is not None:
        updated = _aware(company.atr_updated_at)
        if updated and now - updated < timedelta(seconds=ATR_CACHE_TTL):
            return float(company.atr_14)

    atr = prices.compute_atr14(daily_bars)
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


def _eastern(dt: datetime) -> datetime:
    return dt.astimezone(prices.EASTERN)


def _in_regular_hours(t: datetime) -> bool:
    """Weekday 09:30-16:00 ET by the clock (holidays are caught from the bars)."""
    e = _eastern(t)
    return e.weekday() < 5 and SESSION_OPEN <= e.time() < SESSION_CLOSE


def _next_open(t: datetime) -> datetime:
    """09:30 ET of the next weekday session starting after t.

    Holidays aren't known here; an "open" row due on one simply waits for
    the next session's bars.
    """
    e = _eastern(t)
    day = e.date()
    if e.weekday() >= 5 or e.time() >= SESSION_OPEN:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, SESSION_OPEN, tzinfo=prices.EASTERN)


def _defer_to_open(event, open_at: datetime) -> None:
    """Off-hours filing: keep one intraday reaction, the next open.

    Works on every pending row of the event, not just the claimed ones, so
    15m-1h are settled before they come due.
    """
    has_open = any(r.interval == OPEN_INTERVAL for r in event.price_reactions)
    for row in event.price_reactions:
        if row.status != STATUS_PENDING:
            continue
        if row.interval == "5m" and not has_open:
            row.interval = OPEN_INTERVAL
            row.measure_at = open_at + OPEN_SETTLE
        elif row.interval in INTRADAY_INTERVALS:
            row.status = STATUS_SKIPPED
            row.error = "off_hours"


def _measure_open(minute_bars: list[dict], open_at: datetime):
    """Opening print of the first session at/after open_at → (price, bar time)."""
    for bar in minute_bars:
        bar_time = _parse_bar_time(bar["t"])
        if bar_time >= open_at:
            return bar["o"], bar_time
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
    # The company's ticker is FMP's symbol; an older event may carry the one
    # the SEC used that day
    symbol = prices.normalize_ticker(company.ticker if company is not None and company.ticker
                                     else event.ticker)
    t0 = _aware(event.filing_date)
    t0_day = _eastern(t0).date()
    in_hours = _in_regular_hours(t0)

    def still_due(rs):
        return [r for r in rs if r.status == STATUS_PENDING and _aware(r.measure_at) <= now]

    if not in_hours and any(r.interval in INTRADAY_INTERVALS for r in rows):
        _defer_to_open(event, _next_open(t0))
    elif in_hours:
        # Filed during the session: an interval that lands after the close
        # would read the next morning's print under a "+1h" label
        close_at = datetime.combine(t0_day, SESSION_CLOSE, tzinfo=prices.EASTERN)
        for row in rows:
            if row.interval in INTRADAY_INTERVALS and _aware(row.measure_at) >= close_at:
                row.status = STATUS_SKIPPED
                row.error = "after_close"
    rows = still_due(rows)
    if not rows:
        db.session.commit()
        return False

    # One daily-bars window serves ATR (bars before t0) and 1d/1w
    # measurements (bars after t0's Eastern date).
    daily_bars = _throttled_bars(symbol, prices.DAILY, t0 - timedelta(days=60), None)
    pre_t0 = [b for b in daily_bars if _eastern(_parse_bar_time(b["t"])).date() <= t0_day]
    post_t0 = [b for b in daily_bars if _eastern(_parse_bar_time(b["t"])).date() > t0_day]

    atr = _resolve_atr(company, symbol, pre_t0, now)

    minute_rows = [r for r in rows if r.interval in INTRADAY_INTERVALS or r.interval == OPEN_INTERVAL]
    minute_bars = []
    if minute_rows:
        window_end = min(
            max(_aware(r.measure_at) for r in minute_rows) + timedelta(hours=24),
            now,
        )
        lookback = timedelta(hours=4) if in_hours else OFF_HOURS_LOOKBACK
        minute_bars = _throttled_bars(symbol, "1min", t0 - lookback, window_end)

    # Weekday hours on a market holiday: no session that day, but a later
    # one has printed. Same treatment as any other off-hours filing.
    if in_hours and any(r.interval in INTRADAY_INTERVALS for r in rows):
        days = {_eastern(_parse_bar_time(b["t"])).date() for b in minute_bars}
        later = sorted(d for d in days if d > t0_day)
        if t0_day not in days and later:
            in_hours = False
            _defer_to_open(event, datetime.combine(later[0], SESSION_OPEN,
                                                   tzinfo=prices.EASTERN))
            rows = still_due(rows)

    # Baseline: last 1-min close at/before t0, else the last daily close
    # before it (today's too, when filed after the close)
    baseline = baseline_at = None
    for bar in minute_bars:
        bar_time = _parse_bar_time(bar["t"])
        if bar_time <= t0:
            baseline, baseline_at = bar["c"], bar_time
        else:
            break
    if baseline is None and pre_t0:
        closed_by_t0 = _eastern(t0).time() >= SESSION_CLOSE
        prev = [b for b in pre_t0
                if _eastern(_parse_bar_time(b["t"])).date() < t0_day
                or (closed_by_t0 and _eastern(_parse_bar_time(b["t"])).date() == t0_day)]
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
        elif row.interval == OPEN_INTERVAL:
            result = _measure_open(minute_bars, measure_at - OPEN_SETTLE)
        else:
            result = _measure_intraday(minute_bars, measure_at)
            # Early-close days end before 16:00: the first print after
            # measure_at is the next session's, so the same rule applies
            if result is not None and _eastern(result[1]).date() != t0_day:
                row.status = STATUS_SKIPPED
                row.error = "after_close"
                continue

        if result is None:
            if overdue > STALE_AFTER:
                row.status = STATUS_SKIPPED
                row.error = "no_prints"
            # else: leave pending — the bar may not have landed yet
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
        "price_reaction_intervals": payload["price_reaction_intervals"],
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

            company = event.company or Company.query.filter_by(ticker=event.ticker).first()
            try:
                if _process_event_rows(db, event, event_rows, company, now):
                    done_count += sum(1 for r in event_rows if r.status == "done")
                    _emit_reactions(socketio, event)
            except prices.MarketDataError as exc:
                db.session.rollback()
                for row in event_rows:
                    row.attempts += 1
                    if row.attempts >= MAX_ATTEMPTS:
                        row.status = STATUS_FAILED
                        row.error = str(exc)[:200]
                db.session.commit()
                log.warning("Market data error for %s: %s", event.ticker, exc)
            except Exception:
                db.session.rollback()
                log.exception("Reaction worker failed on event %s", event_id)

        return done_count


def backfill_reactions(days: int = 7) -> tuple[int, int]:
    """Create missing PriceReaction rows for recent events (CLI helper)."""
    from app import db
    from app.models.filing_event import FilingEvent
    from app.models.price_reaction import PriceReaction

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
        if OPEN_INTERVAL in existing:  # an off-hours event's 5m became "open"
            existing.add("5m")
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


def reset_intraday_reactions(days: int = 30) -> int:
    """Re-queue recent events' intraday rows for measurement (CLI helper).

    For rows measured under older rules (every after-hours filing's 5m-1h
    read the same next-open print): puts 5m-1h and "open" back to pending
    as scheduled, so the worker re-measures them under the current ones.
    Returns the number of rows reset.
    """
    from app import db
    from app.models.filing_event import FilingEvent

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = (
        FilingEvent.query
        .filter(FilingEvent.filing_date >= cutoff)
        .filter(FilingEvent.ticker.isnot(None))
        .all()
    )
    reset = 0
    for event in events:
        t0 = _aware(event.filing_date)
        has_5m = any(r.interval == "5m" for r in event.price_reactions)
        for row in event.price_reactions:
            if row.interval == OPEN_INTERVAL and not has_5m:
                row.interval = "5m"
            elif row.interval not in INTRADAY_INTERVALS:
                continue
            row.measure_at = t0 + timedelta(seconds=INTERVALS[row.interval])
            row.status = STATUS_PENDING
            row.attempts = 0
            row.error = None
            row.baseline_price = row.baseline_at = None
            row.measured_price = row.measured_at = None
            row.pct_change = None
            row.is_explosive = False
            reset += 1
    db.session.commit()
    return reset


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
