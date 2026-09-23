"""Tests for the market-data sync (FMP quotes + FMP shares outstanding)."""

from datetime import date
from unittest.mock import patch

from app.services.market_data import prices, sync as market_sync
from app.services.market_data.sync import sync_market_data


class TestTickerNormalization:
    def test_fmp_keeps_the_sec_dash(self):
        assert prices.normalize_ticker("BRK-B") == "BRK-B"
        assert prices.normalize_ticker(" aapl ") == "AAPL"


class TestComputeAtr14:
    def test_needs_15_bars(self):
        bars = [{"h": 11, "l": 9, "c": 10}] * 14
        assert prices.compute_atr14(bars) is None

    def test_known_series(self):
        # Constant bars: TR = high - low = 2 every day → ATR = 2
        bars = [{"h": 11.0, "l": 9.0, "c": 10.0}] * 20
        assert prices.compute_atr14(bars) == 2.0

    def test_gap_dominates_range(self):
        # Prev close 10, next bar gaps to h=20 l=19 c=19.5:
        # TR = max(1, |20-10|, |19-10|) = 10 for that bar
        bars = [{"h": 11.0, "l": 9.0, "c": 10.0}] * 15
        bars.append({"h": 20.0, "l": 19.0, "c": 19.5})
        atr = prices.compute_atr14(bars[-15:])
        assert atr == (13 * 2.0 + 10.0) / 14


class _FakeFMP:
    """shares_float_page() over a fixed list of rows, paged like FMP."""

    def __init__(self, rows):
        self.rows = rows
        self.pages = []

    def shares_float_page(self, page, limit):
        self.pages.append(page)
        return self.rows[page * limit:(page + 1) * limit]


def _run(quotes, share_rows, monkeypatch, page_size=None):
    fake = _FakeFMP(share_rows)
    if page_size:
        monkeypatch.setattr(market_sync, "SHARES_PAGE_SIZE", page_size)
    with patch("app.services.market_data.sync.prices._fmp", return_value=fake), \
         patch("app.services.market_data.sync.prices.get_quotes", return_value=quotes):
        result = sync_market_data()
    return result, fake


class TestSyncMarketData:
    def test_fmp_market_cap_wins_over_shares_times_price(
        self, db_session, sample_company, sample_company_2, monkeypatch,
    ):
        quotes = {
            # FMP's cap is taken as is
            "AAPL": {"symbol": "AAPL", "price": 200.5, "marketCap": 3_000_000},
            # no cap on the quote → shares × price
            "TSLA": {"symbol": "TSLA", "price": 100.0},
        }
        shares = [
            {"symbol": "AAPL", "date": "2026-06-30", "outstandingShares": 1000},
            {"symbol": "TSLA", "date": "2026-06-30", "outstandingShares": 500},
        ]
        (shares_updated, prices_updated), _ = _run(quotes, shares, monkeypatch)

        assert shares_updated == 2
        assert prices_updated == 2
        db_session.session.refresh(sample_company)
        db_session.session.refresh(sample_company_2)
        assert sample_company.shares_outstanding == 1000
        assert sample_company.shares_as_of == date(2026, 6, 30)
        assert sample_company.market_cap == 3_000_000
        assert float(sample_company_2.last_price) == 100.0
        assert sample_company_2.market_cap == 500 * 100

    def test_pages_through_shares_float(self, db_session, sample_company, monkeypatch):
        rows = [{"symbol": f"X{i}", "outstandingShares": 1} for i in range(4)]
        rows.append({"symbol": "AAPL", "date": "2026-06-30", "outstandingShares": 7})
        (shares_updated, _), fake = _run({}, rows, monkeypatch, page_size=2)
        assert fake.pages == [0, 1, 2]  # the short third page ends it
        assert shares_updated == 1
        assert sample_company.shares_outstanding == 7

    def test_never_regresses_to_older_share_count(self, db_session, sample_company, monkeypatch):
        sample_company.shares_outstanding = 900
        sample_company.shares_as_of = date(2026, 9, 1)
        db_session.session.commit()
        rows = [{"symbol": "AAPL", "date": "2026-06-30", "outstandingShares": 1000}]
        _run({}, rows, monkeypatch)
        assert sample_company.shares_outstanding == 900

    def test_fmp_failure_leaves_prices_untouched(self, db_session, sample_company):
        fake = _FakeFMP([])
        with patch("app.services.market_data.sync.prices._fmp", return_value=fake), \
             patch("app.services.market_data.sync.prices.get_quotes",
                   side_effect=prices.MarketDataError("down")):
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
