"""Tests for price-reaction scheduling (subscriber) and measurement (worker)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.models.filing_event import FilingEvent
from app.models.price_reaction import INTERVALS, PriceReaction
from app.services.market_data import reaction_worker
from app.services.realtime.subscriber import _handle_event


ET = ZoneInfo("America/New_York")
# A Tuesday. Wednesday 2026-06-03 is the next session.
TUE = (2026, 6, 2)
WED = (2026, 6, 3)


def _et(day, hour, minute=0):
    """A fixed Eastern wall time, so a test's session doesn't depend on when it runs."""
    return datetime(*day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


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
        """Run one tick with prices.get_bars returning canned data.

        bars_by_call: {("1day"|"1min"): bars list} — keyed by timeframe.
        """
        sio = MagicMock()

        def fake_get_bars(symbol, timeframe, start, end=None):
            return bars_by_call.get(timeframe, [])

        with patch.object(reaction_worker, "CALL_DELAY", 0), \
             patch.object(reaction_worker.prices, "get_bars", side_effect=fake_get_bars):
            done = reaction_worker.tick(app, sio)
        return done, sio

    def test_measures_intraday_and_daily(self, app, db_session, sample_company):
        t0 = _et(TUE, 11)
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
        done, sio = self._run_tick(app, {"1min": minute, "1day": daily})

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
        t0 = _et(TUE, 11)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(t0, [100.0, 100.5, 101.0, 101.0, 101.0, 101.0, 101.0])
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 20)  # ATR = 2
        self._run_tick(app, {"1min": minute, "1day": daily})

        row = PriceReaction.query.first()
        assert row.status == "done"
        assert row.pct_change == 1.0  # 100 → 101
        assert row.is_explosive is False  # Δ = 1 < 2×ATR = 4

    def test_baseline_falls_back_to_prev_daily_close(self, app, db_session, sample_company):
        """Pre-market filing with no minute prints before it."""
        t0 = _et(TUE, 8)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(_et(TUE, 9, 30), [95.0, 95.0])
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 19)
        self._run_tick(app, {"1min": minute, "1day": daily})

        row = PriceReaction.query.first()
        assert row.interval == "open"
        assert row.status == "done"
        assert float(row.baseline_price) == 100.0  # prev daily close
        assert float(row.measured_price) == 95.0
        assert row.pct_change == -5.0

    def test_unknown_symbol_skipped_when_stale(self, app, db_session, sample_company):
        t0 = _et(TUE, 11)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m", "1d"])

        self._run_tick(app, {})  # FMP knows nothing about this symbol

        rows = PriceReaction.query.all()
        assert all(r.status == "skipped" for r in rows)
        assert all(r.error == "no_prints" for r in rows)

    def test_recent_no_prints_stays_pending(self, app, db_session, sample_company):
        t0 = _et(TUE, 11)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        # A fixed mid-session filing, still inside the staleness window
        with patch.object(reaction_worker, "STALE_AFTER", timedelta(days=36500)):
            self._run_tick(app, {})

        row = PriceReaction.query.first()
        assert row.status == "pending"
        assert row.attempts == 1

    def test_market_data_error_increments_attempts_then_fails(self, app, db_session,
                                                              sample_company):
        t0 = _et(TUE, 11)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        def boom(*args, **kwargs):
            raise reaction_worker.prices.MarketDataError("rate limited")

        with patch.object(reaction_worker, "CALL_DELAY", 0), \
             patch.object(reaction_worker.prices, "get_bars", side_effect=boom):
            for _ in range(reaction_worker.MAX_ATTEMPTS):
                reaction_worker.tick(app, MagicMock())

        row = PriceReaction.query.first()
        assert row.attempts == reaction_worker.MAX_ATTEMPTS
        assert row.status == "failed"

    def test_emits_price_reaction_socket_event(self, app, db_session, sample_company):
        t0 = _et(TUE, 11)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(t0, [100.0] * 10)
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 20)
        done, sio = self._run_tick(app, {"1min": minute, "1day": daily})

        assert done == 1
        emits = [c for c in sio.emit.call_args_list if c.args[0] == "price_reaction"]
        assert len(emits) == 2  # public + ticker room
        payload = emits[0].args[1]
        assert payload["filing_event_id"] == event.id
        assert "5m" in payload["price_reactions"]
        assert payload["price_reaction_intervals"] == ["5m"]

    def test_payload_shape_after_measurement(self, app, db_session, sample_company):
        t0 = _et(TUE, 11)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])

        minute = _minute_bars(t0, [100.0, 100.0, 100.0, 100.0, 100.0, 108.0])
        daily = _daily_bars(t0 - timedelta(days=20), [100.0] * 20)
        self._run_tick(app, {"1min": minute, "1day": daily})

        payload = FilingEvent.query.get(event.id).to_ws_payload()
        assert payload["explosive"] is True
        reaction = payload["price_reactions"]["5m"]
        assert reaction["pct"] == 8.0
        assert reaction["price"] == 108.0
        assert reaction["explosive"] is True
        assert reaction["measured_at"] is not None


class TestSessions:
    """Which intraday reactions a filing gets depends on when it was filed."""

    _run_tick = TestWorkerTick._run_tick

    def _rows(self):
        return {r.interval: r for r in PriceReaction.query.all()}

    def test_after_close_filing_gets_one_reaction_at_the_open(
            self, app, db_session, sample_company):
        t0 = _et(TUE, 17, 5)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event)

        minute = (_minute_bars(_et(TUE, 15, 58), [99.0, 100.0])
                  + [{"t": _iso(_et(WED, 9, 30)), "o": 108.0, "h": 109.0, "l": 107.0,
                      "c": 107.5, "v": 1000}]
                  + _minute_bars(_et(WED, 9, 31), [107.0] * 60))
        daily = (_daily_bars(_et(TUE, 0) - timedelta(days=20), [100.0] * 20)
                 + _daily_bars(_et(WED, 0), [107.0] * 8))
        self._run_tick(app, {"1min": minute, "1day": daily})

        rows = self._rows()
        assert "5m" not in rows
        assert {i: rows[i].error for i in ("15m", "30m", "1h")} == {
            "15m": "off_hours", "30m": "off_hours", "1h": "off_hours"}
        opened = rows["open"]
        assert opened.status == "done"
        assert float(opened.baseline_price) == 100.0     # Tuesday's last print
        assert float(opened.measured_price) == 108.0     # Wednesday's opening print
        assert opened.measured_at.replace(tzinfo=timezone.utc) == _et(WED, 9, 30)
        assert opened.pct_change == 8.0
        assert rows["1d"].status == "done"

        payload = FilingEvent.query.get(event.id).to_ws_payload()
        assert set(payload["price_reactions"]) == {"open", "1d", "1w"}
        assert payload["price_reaction_intervals"] == ["open", "1d", "1w"]

    def test_off_hours_rows_settle_before_the_open_without_fetching(
            self, app, db_session, sample_company):
        """Filed last night, market not open yet: nothing to measure, and no
        polling FMP every minute until it is."""
        now = datetime.now(timezone.utc)
        t0 = now - timedelta(minutes=10)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m", "15m", "30m", "1h"])

        with patch.object(reaction_worker, "_in_regular_hours", return_value=False), \
             patch.object(reaction_worker, "_next_open", return_value=now + timedelta(hours=8)), \
             patch.object(reaction_worker, "CALL_DELAY", 0), \
             patch.object(reaction_worker.prices, "get_bars") as get_bars:
            reaction_worker.tick(app, MagicMock())

        get_bars.assert_not_called()
        rows = self._rows()
        assert rows["open"].status == "pending"
        due = rows["open"].measure_at.replace(tzinfo=timezone.utc)
        assert due == now + timedelta(hours=8) + reaction_worker.OPEN_SETTLE
        assert all(rows[i].status == "skipped" for i in ("15m", "30m", "1h"))

    def test_intervals_past_the_close_are_skipped(self, app, db_session, sample_company):
        t0 = _et(TUE, 15, 40)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m", "15m", "30m", "1h"])

        minute = (_minute_bars(_et(TUE, 15, 30), [100.0 + i for i in range(30)])
                  + _minute_bars(_et(WED, 9, 30), [90.0] * 60))
        daily = _daily_bars(_et(TUE, 0) - timedelta(days=20), [100.0] * 20)
        self._run_tick(app, {"1min": minute, "1day": daily})

        rows = self._rows()
        assert rows["5m"].status == "done"
        assert rows["15m"].status == "done"      # 15:55, still in session
        assert (rows["30m"].status, rows["30m"].error) == ("skipped", "after_close")
        assert (rows["1h"].status, rows["1h"].error) == ("skipped", "after_close")
        payload = FilingEvent.query.get(event.id).to_ws_payload()
        assert payload["price_reaction_intervals"] == ["5m", "15m"]

    def test_past_close_rows_are_skipped_without_fetching(
            self, app, db_session, sample_company):
        t0 = _et(TUE, 15, 40)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["30m", "1h"])  # 16:10, 16:40

        with patch.object(reaction_worker, "CALL_DELAY", 0), \
             patch.object(reaction_worker.prices, "get_bars") as get_bars:
            reaction_worker.tick(app, MagicMock())

        get_bars.assert_not_called()
        assert {r.error for r in PriceReaction.query.all()} == {"after_close"}

    def test_weekday_holiday_is_treated_as_off_hours(self, app, db_session, sample_company):
        t0 = _et(TUE, 11)  # a Tuesday with no session
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m", "15m"])

        minute = [{"t": _iso(_et(WED, 9, 30)), "o": 102.0, "h": 102.0, "l": 102.0,
                   "c": 102.0, "v": 1}]
        daily = _daily_bars(_et(TUE, 0) - timedelta(days=20), [100.0] * 19)
        self._run_tick(app, {"1min": minute, "1day": daily})

        rows = self._rows()
        assert rows["open"].status == "done"
        assert float(rows["open"].measured_price) == 102.0
        assert rows["15m"].error == "off_hours"

    def test_next_open_skips_weekends(self):
        fri_evening = datetime(2026, 6, 5, 18, 0, tzinfo=ET)
        assert reaction_worker._next_open(fri_evening) == datetime(2026, 6, 8, 9, 30, tzinfo=ET)
        tue_early = datetime(2026, 6, 2, 7, 0, tzinfo=ET)
        assert reaction_worker._next_open(tue_early) == datetime(2026, 6, 2, 9, 30, tzinfo=ET)
        tue_midday = datetime(2026, 6, 2, 11, 0, tzinfo=ET)
        assert reaction_worker._next_open(tue_midday) == datetime(2026, 6, 3, 9, 30, tzinfo=ET)

    def test_late_evening_filing_uses_the_next_session_for_1d(
            self, app, db_session, sample_company):
        """21:00 ET is already Wednesday in UTC; +1d is still Wednesday's close."""
        t0 = _et(TUE, 21)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["1d"])

        daily = (_daily_bars(_et(TUE, 0) - timedelta(days=20), [100.0] * 21)  # through Tue
                 + _daily_bars(_et(WED, 0), [104.0, 105.0]))
        self._run_tick(app, {"1day": daily})

        row = PriceReaction.query.first()
        assert float(row.measured_price) == 104.0


class TestResetReactions:
    def test_reset_requeues_intraday_rows(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=2)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m", "15m", "1d"])
        rows = {r.interval: r for r in PriceReaction.query.all()}
        rows["5m"].interval = "open"
        rows["5m"].status = "done"
        rows["5m"].pct_change = 3.0
        rows["15m"].status = "skipped"
        rows["15m"].error = "off_hours"
        rows["1d"].status = "done"
        db_session.session.commit()

        with app.app_context():
            assert reaction_worker.reset_intraday_reactions(days=7) == 2

        rows = {r.interval: r for r in PriceReaction.query.all()}
        assert set(rows) == {"5m", "15m", "1d"}
        assert rows["5m"].status == rows["15m"].status == "pending"
        assert rows["5m"].pct_change is None and rows["15m"].error is None
        assert rows["1d"].status == "done"  # daily rows are left alone


class TestBackfill:
    def test_backfill_counts_open_as_the_5m_row(self, app, db_session, sample_company):
        t0 = datetime.now(timezone.utc) - timedelta(days=2)
        event = _make_event(db_session, sample_company, t0)
        _add_rows(db_session, event, intervals=["5m"])
        row = PriceReaction.query.first()
        row.interval = "open"
        db_session.session.commit()

        with app.app_context():
            created, _ = reaction_worker.backfill_reactions(days=7)
        assert created == 5
        assert "5m" not in {r.interval for r in PriceReaction.query.all()}

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
