"""Tests for the market-data sync (EDGAR shares outstanding + Alpaca prices)."""

from datetime import date
from unittest.mock import patch

from app.services.market_data import alpaca, edgar_facts
from app.services.market_data.sync import sync_market_data


class TestTickerNormalization:
    def test_sec_dash_to_alpaca_dot(self):
        assert alpaca.normalize_ticker("BRK-B") == "BRK.B"
        assert alpaca.normalize_ticker("aapl") == "AAPL"

    def test_roundtrip(self):
        assert alpaca.denormalize_ticker(alpaca.normalize_ticker("BRK-B")) == "BRK-B"


class TestComputeAtr14:
    def test_needs_15_bars(self):
        bars = [{"h": 11, "l": 9, "c": 10}] * 14
        assert alpaca.compute_atr14(bars) is None

    def test_known_series(self):
        # Constant bars: TR = high - low = 2 every day → ATR = 2
        bars = [{"h": 11.0, "l": 9.0, "c": 10.0}] * 20
        assert alpaca.compute_atr14(bars) == 2.0

    def test_gap_dominates_range(self):
        # Prev close 10, next bar gaps to h=20 l=19 c=19.5:
        # TR = max(1, |20-10|, |19-10|) = 10 for that bar
        bars = [{"h": 11.0, "l": 9.0, "c": 10.0}] * 15
        bars.append({"h": 20.0, "l": 19.0, "c": 19.5})
        atr = alpaca.compute_atr14(bars[-15:])
        assert atr == (13 * 2.0 + 10.0) / 14


class TestRecentQuarters:
    def test_walks_back_across_year_boundary(self):
        quarters = edgar_facts._recent_quarters(date(2026, 1, 15))
        assert quarters == [(2026, 1), (2025, 4), (2025, 3)]


class TestFramesParsing:
    def test_keys_by_padded_cik_first_hit_wins(self, monkeypatch):
        frames = [
            {"data": [{"cik": 320193, "val": 15000000000, "end": "2026-03-31"}]},
            {"data": [
                {"cik": 320193, "val": 14000000000, "end": "2025-12-31"},
                {"cik": 1318605, "val": 3200000000, "end": "2025-12-31"},
            ]},
            {"data": []},
        ]
        monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
        monkeypatch.setattr(edgar_facts, "FALLBACK_DELAY", 0)
        with patch.object(edgar_facts, "_get_json", side_effect=frames):
            result = edgar_facts.fetch_shares_by_cik()

        # Most recent quarter wins for AAPL; TSLA from the older frame
        assert result["0000320193"] == (15000000000, date(2026, 3, 31))
        assert result["0001318605"] == (3200000000, date(2025, 12, 31))


class TestSyncMarketData:
    def test_market_cap_arithmetic(self, db_session, sample_company, sample_company_2):
        shares = {
            "0000320193": (1000, date(2026, 3, 31)),   # AAPL
            "0001318605": (500, date(2026, 3, 31)),    # TSLA
        }
        snapshots = {
            "AAPL": {"latestTrade": {"p": 200.5}},
            "TSLA": {"dailyBar": {"c": 100.0}},  # no latest trade → daily close
        }
        with patch.object(edgar_facts, "fetch_shares_by_cik", return_value=shares), \
             patch("app.services.market_data.sync.alpaca.get_snapshots",
                   return_value=snapshots):
            shares_updated, prices_updated = sync_market_data()

        assert shares_updated == 2
        assert prices_updated == 2
        db_session.session.refresh(sample_company)
        db_session.session.refresh(sample_company_2)
        assert sample_company.shares_outstanding == 1000
        assert sample_company.market_cap == int(1000 * 200.5)
        assert float(sample_company_2.last_price) == 100.0
        assert sample_company_2.market_cap == 500 * 100

    def test_alpaca_failure_leaves_prices_untouched(self, db_session, sample_company):
        with patch.object(edgar_facts, "fetch_shares_by_cik", return_value={}), \
             patch("app.services.market_data.sync.alpaca.get_snapshots",
                   side_effect=alpaca.AlpacaError("down")):
            _, prices_updated = sync_market_data()
        assert prices_updated == 0
        assert sample_company.last_price is None


class TestCompanyPayloads:
    def test_company_schema_exposes_market_cap(self, client, auth_headers,
                                                db_session, sample_company):
        sample_company.market_cap = 3_000_000_000_000
        sample_company.last_price = 200
        db_session.session.commit()

        resp = client.get(f"/api/v1/companies/{sample_company.id}", headers=auth_headers)
        assert resp.status_code == 200
        company = resp.get_json()["company"]
        assert company["market_cap"] == 3_000_000_000_000
        assert company["last_price"] == 200.0

    def test_event_payload_includes_market_cap(self, db_session, sample_company,
                                               sample_event):
        sample_company.market_cap = 42
        db_session.session.commit()
        assert sample_event.to_ws_payload()["market_cap"] == 42
