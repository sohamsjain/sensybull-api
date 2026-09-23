"""Tests for the FMP market-data module: FMP shapes → the bars/quotes the API serves."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.services.fundamentals.fmp_client import FMPError
from app.services.market_data import prices


class FakeFMP:
    """Stands in for FMPClient.get: routes by path, records every call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, **params):
        self.calls.append((path, params))
        handler = self.responses.get(path, [])
        return handler(params) if callable(handler) else handler


@pytest.fixture
def fmp():
    def install(responses):
        fake = FakeFMP(responses)
        patcher = patch.object(prices, "_fmp", return_value=fake)
        patcher.start()
        installed.append(patcher)
        return fake

    installed = []
    yield install
    for patcher in installed:
        patcher.stop()


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestDailyBars:
    def test_stamps_each_session_at_new_york_midnight(self, fmp):
        fmp({"/historical-price-eod/full": [
            # FMP answers newest first; winter and summer offsets differ
            {"date": "2026-06-02", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 20},
            {"date": "2026-01-05", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        ]})
        bars = prices.get_bars("AAPL", "1day", _utc(2026, 1, 1), _utc(2026, 6, 30))

        assert [b["t"] for b in bars] == ["2026-01-05T05:00:00Z", "2026-06-02T04:00:00Z"]
        assert bars[1] == {"t": "2026-06-02T04:00:00Z", "o": 2.0, "h": 3.0, "l": 1.0,
                           "c": 2.5, "v": 20}

    def test_requests_eastern_dates(self, fmp):
        fake = fmp({})
        # 02:00Z on the 2nd is still the 1st in New York
        prices.get_bars("AAPL", "1day", _utc(2026, 3, 2, 2), _utc(2026, 6, 2, 2))
        path, params = fake.calls[0]
        assert path == "/historical-price-eod/full"
        assert params == {"symbol": "AAPL", "from": "2026-03-01", "to": "2026-06-01"}

    def test_end_is_inclusive_and_bounds_the_window(self, fmp):
        fmp({"/historical-price-eod/full": [
            {"date": d, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for d in ("2026-03-02", "2026-02-27", "2026-02-26")
        ]})
        # The web pages backwards with end = its earliest bar's `t`
        bars = prices.get_bars("AAPL", "1day", _utc(2026, 2, 27), _utc(2026, 2, 27, 5))
        assert [b["t"] for b in bars] == ["2026-02-27T05:00:00Z"]

    def test_malformed_rows_are_skipped(self, fmp):
        fmp({"/historical-price-eod/full": [
            {"date": "2026-06-02", "open": None, "high": 3, "low": 1, "close": 2},
            {"date": "not-a-date", "open": 1, "high": 1, "low": 1, "close": 1},
            {"date": "2026-06-01", "open": 1, "high": 1, "low": 1, "close": 1},
        ]})
        bars = prices.get_bars("AAPL", "1day", _utc(2026, 5, 1), _utc(2026, 6, 30))
        assert [b["t"] for b in bars] == ["2026-06-01T04:00:00Z"]
        assert bars[0]["v"] == 0


class TestIntradayBars:
    def test_eastern_wall_time_becomes_utc(self, fmp):
        fmp({"/historical-chart/1min": [
            {"date": "2026-06-01 09:31:00", "open": 1, "high": 1, "low": 1, "close": 101, "volume": 5},
            {"date": "2026-06-01 09:30:00", "open": 1, "high": 1, "low": 1, "close": 100, "volume": 5},
            {"date": "2026-01-05 09:30:00", "open": 1, "high": 1, "low": 1, "close": 90, "volume": 5},
        ]})
        bars = prices.get_bars("AAPL", "1min", _utc(2026, 1, 5), _utc(2026, 6, 2))
        assert [(b["t"], b["c"]) for b in bars] == [
            ("2026-01-05T14:30:00Z", 90.0),   # EST, UTC-5
            ("2026-06-01T13:30:00Z", 100.0),  # EDT, UTC-4
            ("2026-06-01T13:31:00Z", 101.0),
        ]

    def test_long_windows_are_chunked_without_gaps(self, fmp):
        fake = fmp({})
        prices.get_bars("AAPL", "1min", _utc(2026, 6, 1, 12), _utc(2026, 6, 8, 23))
        ranges = [(p["from"], p["to"]) for _, p in fake.calls]
        assert ranges == [("2026-06-01", "2026-06-03"), ("2026-06-04", "2026-06-06"),
                          ("2026-06-07", "2026-06-08")]
        assert all(path == "/historical-chart/1min" for path, _ in fake.calls)

    def test_filters_to_the_exact_window_and_dedupes(self, fmp):
        row = {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        fmp({"/historical-chart/1min": lambda params: [
            {**row, "date": "2026-06-01 11:59:00"},
            {**row, "date": "2026-06-01 12:00:00"},
            {**row, "date": "2026-06-01 12:00:00"},
            {**row, "date": "2026-06-01 12:02:00"},
        ]})
        bars = prices.get_bars("AAPL", "1min", _utc(2026, 6, 1, 16), _utc(2026, 6, 1, 16, 1))
        assert [b["t"] for b in bars] == ["2026-06-01T16:00:00Z"]

    def test_unknown_timeframe_is_rejected(self):
        with pytest.raises(ValueError):
            prices.get_bars("AAPL", "1Day", _utc(2026, 6, 1))

    def test_inverted_window_is_empty_without_a_call(self, fmp):
        fake = fmp({})
        assert prices.get_bars("AAPL", "1min", _utc(2026, 6, 2), _utc(2026, 6, 1)) == []
        assert fake.calls == []


class TestQuotes:
    def test_batches_and_keys_by_symbol(self, fmp):
        fake = fmp({"/batch-quote": lambda params: [
            {"symbol": s, "price": 1.0} for s in params["symbols"].split(",")
        ]})
        symbols = [f"T{i}" for i in range(prices.QUOTE_BATCH + 5)] + ["T0"]
        quotes = prices.get_quotes(symbols)

        assert len(quotes) == prices.QUOTE_BATCH + 5
        assert len(fake.calls) == 2  # duplicates collapse, then batch

    def test_unknown_symbols_are_absent(self, fmp):
        fmp({"/batch-quote": []})
        assert prices.get_quotes(["NOPE"]) == {}

    def test_sync_mode_skips_a_rejected_batch(self, fmp):
        def answer(params):
            if "BAD" in params["symbols"]:
                raise FMPError("HTTP 400")
            return [{"symbol": s, "price": 1.0} for s in params["symbols"].split(",")]

        fmp({"/batch-quote": answer})
        symbols = ["BAD"] + [f"T{i}" for i in range(prices.QUOTE_BATCH)]
        quotes = prices.get_quotes(symbols, skip_failed_batches=True)
        assert set(quotes) == {"T199"}  # the first batch held BAD and was skipped
        with pytest.raises(prices.MarketDataError):
            prices.get_quotes(symbols)

    def test_sync_mode_still_raises_when_every_batch_fails(self, fmp):
        def boom(params):
            raise FMPError("HTTP 500")

        fmp({"/batch-quote": boom})
        with pytest.raises(prices.MarketDataError):
            prices.get_quotes(["AAPL"], skip_failed_batches=True)

    def test_fmp_errors_surface_as_market_data_errors(self, fmp):
        def boom(params):
            raise FMPError("HTTP 500")

        fmp({"/batch-quote": boom})
        with pytest.raises(prices.MarketDataError):
            prices.get_quotes(["AAPL"])
