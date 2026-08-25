"""Tests for the company bars endpoint (price chart data)."""

from unittest.mock import patch

import pytest

from app.models.company import Company
from app.routes import companies as companies_module


@pytest.fixture(autouse=True)
def no_cache():
    """Isolate tests from any live Redis (CI runs one; a bars response
    cached by one test must not leak into the next). The bars route imports
    the cache helpers at call time, so patching the module works."""
    with patch("app.services.market_data.cache.cache_get", return_value=None), \
         patch("app.services.market_data.cache.cache_set"):
        yield


class TestCompanyBars:
    def test_requires_auth(self, client, sample_company):
        resp = client.get(f"/api/v1/companies/{sample_company.id}/bars")
        assert resp.status_code == 401

    def test_no_ticker_is_422(self, client, auth_headers, db_session):
        company = Company(name="Private Holdings LLC", cik="0009999999")
        db_session.session.add(company)
        db_session.session.commit()

        resp = client.get(f"/api/v1/companies/{company.id}/bars", headers=auth_headers)
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "no_ticker"

    def test_invalid_params_rejected(self, client, auth_headers, sample_company):
        resp = client.get(
            f"/api/v1/companies/{sample_company.id}/bars?timeframe=3Sec",
            headers=auth_headers)
        assert resp.status_code == 400

        resp = client.get(
            f"/api/v1/companies/{sample_company.id}/bars?lookback=99Y",
            headers=auth_headers)
        assert resp.status_code == 400

    def test_returns_bars(self, client, auth_headers, sample_company):
        bars = {"AAPL": [
            {"t": "2026-06-01T04:00:00Z", "o": 100, "h": 105, "l": 99, "c": 104,
             "v": 1000, "n": 10, "vw": 102},
        ]}
        with patch("app.services.market_data.alpaca.get_bars", return_value=bars):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/bars",
                headers=auth_headers)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ticker"] == "AAPL"
        assert data["timeframe"] == "1D"
        assert data["bars"] == [
            {"t": "2026-06-01T04:00:00Z", "o": 100, "h": 105, "l": 99, "c": 104, "v": 1000},
        ]

    def test_alpaca_down_is_503(self, client, auth_headers, sample_company):
        from app.services.market_data.alpaca import AlpacaError
        with patch("app.services.market_data.alpaca.get_bars",
                   side_effect=AlpacaError("down")):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/bars",
                headers=auth_headers)
        assert resp.status_code == 503


def test_timeframe_whitelist_matches_alpaca_names():
    assert companies_module.BAR_TIMEFRAMES == {
        "1D": "1Day", "1H": "1Hour", "15Min": "15Min"}


class TestBarsPaging:
    """`end` walks the chart backwards through history."""

    def test_end_date_narrows_the_window(self, client, auth_headers, sample_company):
        captured = {}

        def fake_get_bars(symbols, timeframe, start, end=None, **kwargs):
            captured["start"] = start
            captured["end"] = end
            return {symbols[0]: []}

        with patch("app.services.market_data.alpaca.get_bars", side_effect=fake_get_bars):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/bars"
                "?timeframe=1D&lookback=3M&end=2026-03-01",
                headers=auth_headers)

        assert resp.status_code == 200
        # A date-only `end` is read as that day's 00:00 UTC, so the page holds
        # strictly earlier sessions than the caller's earliest bar.
        assert captured["end"] == "2026-03-01T00:00:00+00:00"
        assert captured["start"].startswith("2025-11-28")
        assert resp.get_json()["end"] == "2026-03-01T00:00:00+00:00"

    def test_end_accepts_a_timestamp(self, client, auth_headers, sample_company):
        captured = {}

        def fake_get_bars(symbols, timeframe, start, end=None, **kwargs):
            captured["end"] = end
            return {symbols[0]: []}

        with patch("app.services.market_data.alpaca.get_bars", side_effect=fake_get_bars):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/bars?end=2026-03-01T04:00:00Z",
                headers=auth_headers)

        assert resp.status_code == 200
        assert captured["end"] == "2026-03-01T04:00:00+00:00"

    def test_unparseable_end_is_400(self, client, auth_headers, sample_company):
        resp = client.get(
            f"/api/v1/companies/{sample_company.id}/bars?end=last-tuesday",
            headers=auth_headers)
        assert resp.status_code == 400

    def test_no_end_leaves_the_window_open(self, client, auth_headers, sample_company):
        captured = {}

        def fake_get_bars(symbols, timeframe, start, end=None, **kwargs):
            captured["end"] = end
            return {symbols[0]: []}

        with patch("app.services.market_data.alpaca.get_bars", side_effect=fake_get_bars):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/bars", headers=auth_headers)

        assert resp.status_code == 200
        assert captured["end"] is None
        assert resp.get_json()["end"] is None

    def test_closed_windows_cache_for_a_day(self, client, auth_headers, sample_company):
        with patch("app.services.market_data.alpaca.get_bars", return_value={"AAPL": []}), \
             patch("app.services.market_data.cache.cache_set") as cache_set:
            client.get(
                f"/api/v1/companies/{sample_company.id}/bars?end=2026-03-01",
                headers=auth_headers)
            historical_ttl = cache_set.call_args[0][2]

            client.get(
                f"/api/v1/companies/{sample_company.id}/bars", headers=auth_headers)
            live_ttl = cache_set.call_args[0][2]

        assert historical_ttl == companies_module.BARS_HISTORY_CACHE_SECONDS
        assert live_ttl == companies_module.BARS_CACHE_SECONDS

    def test_pages_cache_separately(self, client, auth_headers, sample_company):
        keys = []
        with patch("app.services.market_data.alpaca.get_bars", return_value={"AAPL": []}), \
             patch("app.services.market_data.cache.cache_set",
                   side_effect=lambda k, v, t: keys.append(k)):
            client.get(f"/api/v1/companies/{sample_company.id}/bars",
                       headers=auth_headers)
            client.get(f"/api/v1/companies/{sample_company.id}/bars?end=2026-03-01",
                       headers=auth_headers)

        assert len(set(keys)) == 2
