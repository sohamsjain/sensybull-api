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
