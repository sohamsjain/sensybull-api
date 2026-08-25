"""Tests for the company quote endpoint (watchlist header price)."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.company import Company


@pytest.fixture(autouse=True)
def no_cache():
    """Isolate tests from any live Redis (CI runs one; a quote cached by one
    test must not leak into the next). The quote route imports the cache
    helpers at call time, so patching the module works."""
    with patch("app.services.market_data.cache.cache_get", return_value=None), \
         patch("app.services.market_data.cache.cache_set"):
        yield


def _snapshot(last=None, daily=None, prev=None):
    snap = {}
    if last is not None:
        snap["latestTrade"] = {"p": last, "t": "2026-08-06T18:22:03Z"}
    if daily is not None:
        snap["dailyBar"] = {"c": daily, "t": "2026-08-06T04:00:00Z"}
    if prev is not None:
        snap["prevDailyBar"] = {"c": prev, "t": "2026-08-05T04:00:00Z"}
    return snap


class TestCompanyQuote:
    def test_requires_auth(self, client, sample_company):
        resp = client.get(f"/api/v1/companies/{sample_company.id}/quote")
        assert resp.status_code == 401

    def test_no_ticker_is_422(self, client, auth_headers, db_session):
        company = Company(name="Private Holdings LLC", cik="0009999999")
        db_session.session.add(company)
        db_session.session.commit()

        resp = client.get(f"/api/v1/companies/{company.id}/quote", headers=auth_headers)
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "no_ticker"

    def test_returns_price_and_day_change(self, client, auth_headers, sample_company):
        snapshots = {"AAPL": _snapshot(last=214.32, daily=213.90, prev=216.18)}
        with patch("app.services.market_data.alpaca.get_snapshots",
                   return_value=snapshots):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/quote",
                headers=auth_headers)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ticker"] == "AAPL"
        assert data["price"] == 214.32
        assert data["prev_close"] == 216.18
        assert data["change"] == pytest.approx(-1.86)
        assert data["change_pct"] == pytest.approx(-0.86, abs=0.01)
        assert data["as_of"] == "2026-08-06T18:22:03Z"
        assert data["stale"] is False

    def test_falls_back_to_daily_bar_when_no_trade(
            self, client, auth_headers, sample_company):
        """Thin IEX names can have no trade today — use the daily bar."""
        snapshots = {"AAPL": _snapshot(daily=213.90, prev=216.18)}
        with patch("app.services.market_data.alpaca.get_snapshots",
                   return_value=snapshots):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/quote",
                headers=auth_headers)

        data = resp.get_json()
        assert data["price"] == 213.90
        assert data["as_of"] == "2026-08-06T04:00:00Z"

    def test_missing_prev_close_leaves_change_null(
            self, client, auth_headers, sample_company):
        """A freshly listed symbol has no previous session to compare to."""
        snapshots = {"AAPL": _snapshot(last=214.32)}
        with patch("app.services.market_data.alpaca.get_snapshots",
                   return_value=snapshots):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/quote",
                headers=auth_headers)

        data = resp.get_json()
        assert data["price"] == 214.32
        assert data["change"] is None
        assert data["change_pct"] is None

    def test_alpaca_down_falls_back_to_synced_price(
            self, client, auth_headers, sample_company, db_session):
        from app.services.market_data.alpaca import AlpacaError

        sample_company.last_price = Decimal("210.5000")
        sample_company.price_updated_at = datetime(2026, 8, 5, 21, 0, tzinfo=timezone.utc)
        db_session.session.commit()

        with patch("app.services.market_data.alpaca.get_snapshots",
                   side_effect=AlpacaError("down")):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/quote",
                headers=auth_headers)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["price"] == 210.5
        assert data["stale"] is True
        assert data["change_pct"] is None

    def test_alpaca_down_without_synced_price_is_503(
            self, client, auth_headers, sample_company):
        from app.services.market_data.alpaca import AlpacaError

        with patch("app.services.market_data.alpaca.get_snapshots",
                   side_effect=AlpacaError("down")):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/quote",
                headers=auth_headers)

        assert resp.status_code == 503

    def test_unknown_symbol_is_503(self, client, auth_headers, sample_company):
        """Alpaca omits symbols it doesn't know rather than erroring."""
        with patch("app.services.market_data.alpaca.get_snapshots",
                   return_value={}):
            resp = client.get(
                f"/api/v1/companies/{sample_company.id}/quote",
                headers=auth_headers)

        assert resp.status_code == 503


def test_snapshot_price_prefers_latest_trade():
    from app.services.market_data import alpaca

    assert alpaca.snapshot_price(_snapshot(last=10, daily=11, prev=12)) == 10
    assert alpaca.snapshot_price(_snapshot(daily=11, prev=12)) == 11
    assert alpaca.snapshot_price(_snapshot(prev=12)) == 12
    assert alpaca.snapshot_price({}) is None


class TestCompanyQuotesBatch:
    """GET /companies/quotes — one request for a screenful of prices."""

    def test_requires_auth(self, client, sample_company):
        resp = client.get(f"/api/v1/companies/quotes?ids={sample_company.id}")
        assert resp.status_code == 401

    def test_returns_a_quote_per_company(
            self, client, auth_headers, sample_company, sample_company_2):
        snapshots = {
            "AAPL": _snapshot(last=214.32, prev=216.18),
            "TSLA": _snapshot(last=402.10, prev=390.00),
        }
        with patch("app.services.market_data.alpaca.get_snapshots",
                   return_value=snapshots) as get_snapshots:
            resp = client.get(
                f"/api/v1/companies/quotes?ids={sample_company.id},{sample_company_2.id}",
                headers=auth_headers)

        assert resp.status_code == 200
        quotes = resp.get_json()["quotes"]
        assert quotes[str(sample_company.id)]["price"] == 214.32
        assert quotes[str(sample_company_2.id)]["change_pct"] == pytest.approx(3.1, abs=0.01)
        # Every miss folds into a single upstream call
        assert get_snapshots.call_count == 1

    def test_empty_ids_is_an_empty_map(self, client, auth_headers):
        resp = client.get("/api/v1/companies/quotes", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == {"quotes": {}}

    def test_omits_companies_without_a_price(
            self, client, auth_headers, sample_company, db_session):
        """No ticker, unknown to Alpaca, and never synced — all just absent."""
        tickerless = Company(name="Private Holdings LLC", cik="0009999999")
        db_session.session.add(tickerless)
        db_session.session.commit()

        with patch("app.services.market_data.alpaca.get_snapshots", return_value={}):
            resp = client.get(
                f"/api/v1/companies/quotes?ids={sample_company.id},{tickerless.id}",
                headers=auth_headers)

        assert resp.status_code == 200
        assert resp.get_json()["quotes"] == {}

    def test_alpaca_down_falls_back_to_synced_prices(
            self, client, auth_headers, sample_company, db_session):
        from app.services.market_data.alpaca import AlpacaError

        sample_company.last_price = Decimal("210.5000")
        sample_company.price_updated_at = datetime(2026, 8, 5, 21, 0, tzinfo=timezone.utc)
        db_session.session.commit()

        with patch("app.services.market_data.alpaca.get_snapshots",
                   side_effect=AlpacaError("down")):
            resp = client.get(
                f"/api/v1/companies/quotes?ids={sample_company.id}",
                headers=auth_headers)

        quote = resp.get_json()["quotes"][str(sample_company.id)]
        assert quote["price"] == 210.5
        assert quote["stale"] is True

    def test_malformed_id_does_not_error(self, client, auth_headers):
        resp = client.get("/api/v1/companies/quotes?ids=not-a-uuid", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["quotes"] == {}

    def test_does_not_shadow_the_company_route(
            self, client, auth_headers, sample_company):
        """`/quotes` must not be swallowed by `/<company_id>`."""
        resp = client.get(f"/api/v1/companies/{sample_company.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["company"]["ticker"] == "AAPL"
