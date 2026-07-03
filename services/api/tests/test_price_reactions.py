"""Tests for price-reaction scheduling (subscriber) and measurement (worker)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.models.filing_event import FilingEvent
from app.models.price_reaction import INTERVALS, PriceReaction
from app.services.market_data import reaction_worker
from app.services.realtime.subscriber import _handle_event


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_filing_json(filing_date, **overrides):
    base = {
        "edgar_id": "test-pr-001",
        "signal_type": "8-K",
        "cik": "0000320193",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "filing_date": _iso(filing_date),
        "max_tier": 1,
        "items": [],
        "exhibits": [],
        "briefing": {"headline": "Test", "summary": "Test"},
        "event_types": ["Acquisition"],
    }
    base.update(overrides)
    return json.dumps(base)


def _make_event(db_session, company, filing_date, edgar_id="evt-pr-1"):
    event = FilingEvent(
        edgar_id=edgar_id,
        company_id=company.id,
        cik=company.cik,
        ticker=company.ticker,
        company_name=company.name,
        filing_date=filing_date,
        max_tier=1,
    )
    db_session.session.add(event)
    db_session.session.commit()
    return event


def _add_rows(db_session, event, intervals=None):
    t0 = event.filing_date
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    for interval in (intervals or INTERVALS):
        db_session.session.add(PriceReaction(
            filing_event_id=event.id,
            ticker=event.ticker,
            interval=interval,
            measure_at=t0 + timedelta(seconds=INTERVALS[interval]),
        ))
    db_session.session.commit()


def _minute_bars(t0, prices):
    """One bar per minute starting at t0, closes from `prices`."""
    return [
        {"t": _iso(t0 + timedelta(minutes=i)), "o": p, "h": p, "l": p, "c": p, "v": 1000}
        for i, p in enumerate(prices)
    ]


def _daily_bars(start_day, prices):
    return [
        {"t": _iso(start_day + timedelta(days=i)), "o": p, "h": p + 1, "l": p - 1,
         "c": p, "v": 10000}
        for i, p in enumerate(prices)
    ]


class TestSubscriberScheduling:
    def test_creates_six_pending_rows(self, app, db_session, sample_company):
        filing_date = datetime.now(timezone.utc) - timedelta(hours=1)
        _handle_event(app, MagicMock(), _make_filing_json(filing_date))

        event = FilingEvent.query.filter_by(edgar_id="test-pr-001").first()
        rows = PriceReaction.query.filter_by(filing_event_id=event.id).all()
        assert len(rows) == 6
        assert {r.interval for r in rows} == set(INTERVALS)
        assert all(r.status == "pending" for r in rows)

        by_interval = {r.interval: r for r in rows}
        for interval, seconds in INTERVALS.items():
            measure_at = by_interval[interval].measure_at
            if measure_at.tzinfo is None:
                measure_at = measure_at.replace(tzinfo=timezone.utc)
            expected = event.filing_date.replace(tzinfo=timezone.utc) + timedelta(seconds=seconds)
            assert abs((measure_at - expected).total_seconds()) < 1

    def test_no_rows_without_ticker(self, app, db_session):
        filing_date = datetime.now(timezone.utc)
        _handle_event(app, MagicMock(), _make_filing_json(
            filing_date, ticker="", edgar_id="test-pr-002"))
        assert PriceReaction.query.count() == 0


class TestWorkerTick:
    def _run_tick(self, app, bars_by_call):
        """Run one tick with alpaca.get_bars returning canned data.

        bars_by_call: {("1Day"|"1Min"): bars list} — keyed by timeframe.
        """
        sio = MagicMock()

        def fake_get_bars(symbols, timeframe, start, end=None, **kwargs):
            return {symbols[0]: bars_by_call.get(timeframe, [])}

        with patch.object(reaction_worker, "CALL_DELAY", 0), \
             patch.object(reaction_worker.alpaca, "get_bars", side_effect=fake_get_bars):
            done = reaction_worker.tick(app, sio)
        return done, sio

    def test_measures_intraday_and_daily(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=10)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event)

        # Minute bars: 100.0 at t0, rising 1.0/minute afterwards
        minute = _minute_bars(t0, [100.0 + i for i in range(90)])
        # Daily bars: 20 days before t0 at 100, then 8 days after t0 rising
        daily = (
            _daily_bars(t0 - timedelta(days=20), [100.0] * 20)
            + _daily_bars(t0 + timedelta(days=1), [110.0, 111.0, 112.0, 113.0, 114.0,
                                                   115.0, 116.0, 117.0])
        )
        done, sio = self._run_tick(app, {"1Min": minute, "1Day": daily})

        assert done == 6
        rows = {r.interval: r for r in PriceReaction.query.all()}
        assert all(r.status == "done" for r in rows.values())
        assert float(rows["5m"].baseline_price) == 100.0
        # +5m → first bar at/after t0+5min closes at 105
        assert float(rows["5m"].measured_price) == 105.0
        assert rows["5m"].pct_change == 5.0
        # 1d → first daily bar after t0's date; 1w → fifth
        assert float(rows["1d"].measured_price) == 110.0
        assert float(rows["1w"].measured_price) == 114.0

        # ATR from constant pre-t0 dailies (h-l = 2) → explosive iff |Δ| >= 4
        assert rows["5m"].is_explosive is True   # Δ = 5 >= 4
        assert float(rows["1w"].measured_price) - 100.0 >= 4
        assert rows["1w"].is_explosive is True

    def test_not_explosive_below_2x_atr(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=10)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(t0, [100.0, 100.5, 101.0, 101.0, 101.0, 101.0, 101.0])
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 20)  # ATR = 2
        self._run_tick(app, {"1Min": minute, "1Day": daily})

        row = PriceReaction.query.first()
        assert row.status == "done"
        assert row.pct_change == 1.0  # 100 → 101
        assert row.is_explosive is False  # Δ = 1 < 2×ATR = 4

    def test_baseline_falls_back_to_prev_daily_close(self, app, db_session, sample_company):
        """After-hours filing with no minute prints before t0."""
        t0 = datetime.now(timezone.utc) - timedelta(days=10)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        # No minute bars before t0; first print 2h after (next open)
        minute = _minute_bars(t0 + timedelta(hours=2), [95.0, 95.0])
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 19)
        self._run_tick(app, {"1Min": minute, "1Day": daily})

        row = PriceReaction.query.first()
        assert row.status == "done"
        assert float(row.baseline_price) == 100.0  # prev daily close
        assert float(row.measured_price) == 95.0
        assert row.pct_change == -5.0
        # measured_at records the actual (gap) print time
        measured_at = row.measured_at.replace(tzinfo=timezone.utc)
        assert measured_at >= t0 + timedelta(hours=2) - timedelta(seconds=1)

    def test_unknown_symbol_skipped_when_stale(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=30)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m", "1d"])

        self._run_tick(app, {})  # Alpaca knows nothing about this symbol

        rows = PriceReaction.query.all()
        assert all(r.status == "skipped" for r in rows)
        assert all(r.error == "no_prints" for r in rows)

    def test_recent_no_prints_stays_pending(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(minutes=30)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        self._run_tick(app, {})

        row = PriceReaction.query.first()
        assert row.status == "pending"
        assert row.attempts == 1

    def test_alpaca_error_increments_attempts_then_fails(self, app, db_session,
                                                         sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=1)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        def boom(*args, **kwargs):
            raise reaction_worker.alpaca.AlpacaError("rate limited")

        with patch.object(reaction_worker, "CALL_DELAY", 0), \
             patch.object(reaction_worker.alpaca, "get_bars", side_effect=boom):
            for _ in range(reaction_worker.MAX_ATTEMPTS):
                reaction_worker.tick(app, MagicMock())

        row = PriceReaction.query.first()
        assert row.attempts == reaction_worker.MAX_ATTEMPTS
        assert row.status == "failed"

    def test_emits_price_reaction_socket_event(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=10)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(t0, [100.0] * 10)
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 20)
        done, sio = self._run_tick(app, {"1Min": minute, "1Day": daily})

        assert done == 1
        emits = [c for c in sio.emit.call_args_list if c.args[0] == "price_reaction"]
        assert len(emits) == 2  # public + ticker room
        payload = emits[0].args[1]
        assert payload["filing_event_id"] == event.id
        assert "5m" in payload["price_reactions"]

    def test_payload_shape_after_measurement(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=10)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(t0, [100.0, 100.0, 100.0, 100.0, 100.0, 108.0])
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 20)
        self._run_tick(app, {"1Min": minute, "1Day": daily})

        payload = FilingEvent.query.get(event.id).to_ws_payload()
        assert payload["explosive"] is True
        reaction = payload["price_reactions"]["5m"]
        assert reaction["pct"] == 8.0
        assert reaction["price"] == 108.0
        assert reaction["explosive"] is True
        assert reaction["measured_at"] is not None


class TestBackfill:
    def test_backfill_creates_missing_rows(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=2)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])  # one exists already

        with app.app_context():
            created, touched = reaction_worker.backfill_reactions(days=7)
        assert created == 5
        assert touched == 1
        assert PriceReaction.query.count() == 6

    def test_backfill_ignores_old_events(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=30)
        _make_event(db_session, sample_company, t0)

        with app.app_context():
            created, _ = reaction_worker.backfill_reactions(days=7)
        assert created == 0
