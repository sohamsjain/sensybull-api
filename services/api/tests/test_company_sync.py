"""The FMP company universe sync, and symbol → company resolution."""

from datetime import datetime, timezone

import pytest

from app import db
from app.models.company import Company, CompanyTickerAlias
from app.models.filing_event import FilingEvent
from app.services import company_loader
from app.services.company_loader import is_common_stock, sync_companies
from app.services.company_resolver import find_company_by_symbol, resolve_event_company
from app.services.fundamentals.fmp_client import FMPError


def _row(symbol, name=None, volume=1_000, **extra):
    return {"symbol": symbol, "companyName": name or f"{symbol} Inc.", "volume": volume,
            "exchangeShortName": extra.pop("exchange", "NASDAQ"), "isEtf": False,
            "isFund": False, "isActivelyTrading": True, "marketCap": 10**9, **extra}


class FakeFMP:
    configured = True

    def __init__(self, rows, profiles=None, fail=False):
        self.rows = rows
        self.profiles = profiles or {}
        self.fail = fail
        self.profile_calls = []
        self.screener_calls = []

    def screener(self, **filters):
        self.screener_calls.append(filters)
        if self.fail:
            raise FMPError("down")
        ex = filters["exchange"]
        return [r for r in self.rows if r["exchangeShortName"] == ex]

    def profile(self, symbol):
        self.profile_calls.append(symbol)
        return self.profiles.get(symbol)


@pytest.fixture(autouse=True)
def small_universe(monkeypatch):
    monkeypatch.setattr(company_loader, "MIN_UNIVERSE", 1)


def _company(ticker, cik=None, name=None, listed=None):
    c = Company(name=name or ticker, ticker=ticker, cik=cik, listed=listed)
    db.session.add(c)
    db.session.commit()
    return c


class TestIsCommonStock:
    @pytest.mark.parametrize("row", [
        _row("AAPL", "Apple Inc."),
        _row("BRK-B", "Berkshire Hathaway Inc."),
        _row("ARCC", "Ares Capital Corporation"),  # a listed BDC is a listed stock
        _row("UNH", "UnitedHealth Group Incorporated"),
    ])
    def test_keeps_common_stock(self, row):
        assert is_common_stock(row)

    @pytest.mark.parametrize("row", [
        _row("SPY", "SPDR S&P 500 ETF Trust", isEtf=True),
        _row("VFIAX", "Vanguard 500 Index Fund", isFund=True),
        _row("OLDCO", "Old Co", isActivelyTrading=False),
        _row("ABCDW", "ABC Acquisition Corp. Warrant"),
        _row("ABCDU", "ABC Acquisition Corp. Units"),
        _row("ABCDR", "ABC Acquisition Corp. Rights"),
        _row("BAC-PL", "Bank of America Corporation Preferred Series L"),
        _row("JPM-PC", "JPMorgan Chase & Co."),
        _row("XYZ-WT", "XYZ Corp"),
        _row("T-NOTE", "AT&T Inc. 5.35% Notes due 2066"),
        _row("", "No Symbol"),
        _row("TOOLONGSYMBOL", "Too Long"),
    ])
    def test_drops_everything_else(self, row):
        assert not is_common_stock(row)


class TestSyncCompanies:
    def test_updates_existing_rows_and_takes_fmp_name(self, db_session):
        aapl = _company("AAPL", "0000320193", name="APPLE INC.")
        stats = sync_companies(FakeFMP([_row("AAPL", "Apple Inc.")]))
        assert stats["updated"] == 1 and stats["added"] == 0
        assert aapl.listed is True
        assert aapl.name == "Apple Inc."

    def test_takes_sector_industry_and_exchange_from_the_screener_row(self, db_session):
        aapl = _company("AAPL", "0000320193")
        aapl.market_cap = 3 * 10**12
        sync_companies(FakeFMP([
            _row("AAPL", sector="Technology", industry="Consumer Electronics"),
            _row("NEWCO", sector="Health Care", industry="Biotechnology", exchange="NYSE"),
        ], profiles={"NEWCO": {"cik": "1234567"}}))
        assert (aapl.sector, aapl.industry, aapl.exchange) == ("Technology", "Consumer Electronics", "NASDAQ")
        assert aapl.market_cap == 3 * 10**12  # sync-market-data owns an existing cap
        newco = Company.query.filter_by(ticker="NEWCO").one()
        assert newco.sector == "Healthcare"  # GICS spelling → FMP's
        assert newco.market_cap == 10**9  # seeded so its events land in a bucket

    def test_a_blank_or_unknown_sector_never_erases_one(self, db_session):
        aapl = _company("AAPL", "0000320193")
        aapl.sector = "Technology"
        sync_companies(FakeFMP([_row("AAPL", sector="")]))
        assert aapl.sector == "Technology"
        sync_companies(FakeFMP([_row("AAPL", sector="Shell Companies")]))
        assert aapl.sector == "Technology"

    def test_new_symbol_is_created_with_its_cik(self, db_session):
        fmp = FakeFMP([_row("NEWCO", "NewCo Holdings")], profiles={"NEWCO": {"cik": "1234567"}})
        stats = sync_companies(fmp)
        assert stats["added"] == 1
        c = Company.query.filter_by(ticker="NEWCO").one()
        assert c.cik == "0001234567" and c.listed is True
        assert fmp.profile_calls == ["NEWCO"]

    def test_renamed_ticker_moves_the_row_and_keeps_the_old_symbol(self, db_session):
        meta = _company("FB", "0001326801")
        fmp = FakeFMP([_row("META", "Meta Platforms, Inc.")],
                      profiles={"META": {"cik": "0001326801"}})
        stats = sync_companies(fmp)
        assert stats["renamed"] == 1
        assert meta.ticker == "META" and meta.listed is True
        assert Company.query.count() == 1
        assert find_company_by_symbol("FB").id == meta.id

    def test_second_share_class_is_an_alias_of_the_issuer(self, db_session):
        googl = _company("GOOGL", "0001652044")
        fmp = FakeFMP([_row("GOOG", "Alphabet Inc.", volume=9_000),
                       _row("GOOGL", "Alphabet Inc.", volume=5_000)],
                      profiles={"GOOG": {"cik": "1652044"}})
        stats = sync_companies(fmp)
        assert stats["share_classes"] == 1
        assert Company.query.count() == 1
        assert find_company_by_symbol("GOOG").id == googl.id
        # known aliases cost no profile call next run
        fmp.profile_calls.clear()
        sync_companies(fmp)
        assert fmp.profile_calls == []

    def test_new_issuer_with_two_classes_gets_one_row(self, db_session):
        fmp = FakeFMP([_row("BRK-A", "Berkshire Hathaway Inc.", volume=10),
                       _row("BRK-B", "Berkshire Hathaway Inc.", volume=5_000_000)],
                      profiles={"BRK-A": {"cik": "1067983"}, "BRK-B": {"cik": "1067983"}})
        sync_companies(fmp)
        assert [c.ticker for c in Company.query.all()] == ["BRK-B"]  # the traded line
        assert find_company_by_symbol("BRK.A").ticker == "BRK-B"

    def test_rows_outside_the_universe_are_delisted_not_deleted(self, db_session):
        _company("AAPL", "0000320193")
        otc = _company("OTCJUNK", "0000999999")
        stats = sync_companies(FakeFMP([_row("AAPL")]))
        assert stats["delisted"] == 1
        assert otc.listed is False
        assert db.session.get(Company, otc.id) is not None

    def test_incomplete_universe_never_delists(self, db_session, monkeypatch):
        monkeypatch.setattr(company_loader, "MIN_UNIVERSE", 50)
        _company("AAPL")
        other = _company("MSFT", listed=True)
        stats = sync_companies(FakeFMP([_row("AAPL")]))
        assert stats["complete"] is False
        assert other.listed is True

    def test_screener_failure_changes_nothing(self, db_session):
        c = _company("AAPL", listed=True)
        stats = sync_companies(FakeFMP([], fail=True))
        assert stats["universe"] == 0
        assert c.listed is True

    def test_delisted_primary_class_hands_over_to_the_live_one(self, db_session):
        googl = _company("GOOGL", "0001652044")
        db.session.add(CompanyTickerAlias(ticker="GOOG", company=googl, kind="share_class"))
        db.session.commit()
        sync_companies(FakeFMP([_row("GOOG", "Alphabet Inc.")]))
        assert googl.ticker == "GOOG" and googl.listed is True
        assert find_company_by_symbol("GOOGL").id == googl.id

    def test_feed_event_tickers_become_aliases(self, db_session):
        meta = _company("META", "0001326801")
        db.session.add(FilingEvent(edgar_id="e1", cik="1326801", company_name="Meta", ticker="FB", company_id=meta.id,
                                   filing_date=datetime(2021, 1, 1, tzinfo=timezone.utc)))
        db.session.commit()
        stats = sync_companies(FakeFMP([_row("META")]))
        assert stats["aliases"] == 1
        alias = CompanyTickerAlias.query.filter_by(ticker="FB").one()
        assert alias.company_id == meta.id and alias.kind == "former"

    def test_profile_budget_leaves_the_rest_for_next_run(self, db_session, monkeypatch):
        monkeypatch.setenv("COMPANY_PROFILE_LIMIT", "1")
        fmp = FakeFMP([_row("AAA", volume=2), _row("BBB", volume=1)],
                      profiles={"AAA": {"cik": "1"}, "BBB": {"cik": "2"}})
        stats = sync_companies(fmp)
        assert stats["added"] == 1 and stats["pending"] == 1

    def test_asks_for_all_share_classes_on_each_exchange(self, db_session):
        fmp = FakeFMP([_row("AAPL")])
        sync_companies(fmp)
        assert {c["exchange"] for c in fmp.screener_calls} == {"NYSE", "NASDAQ", "AMEX"}
        assert all(c["includeAllShareClasses"] and not c["isEtf"] and not c["isFund"]
                   for c in fmp.screener_calls)


class TestResolver:
    def test_class_share_spellings(self, db_session):
        brk = _company("BRK-B")
        assert find_company_by_symbol("BRK.B").id == brk.id
        assert find_company_by_symbol("brk-b").id == brk.id

    def test_live_ticker_beats_an_alias(self, db_session):
        old = _company("OLDCO")
        new = _company("FB")
        db.session.add(CompanyTickerAlias(ticker="FB", company=old, kind="former"))
        db.session.commit()
        assert find_company_by_symbol("FB").id == new.id

    def test_falls_back_to_the_symbol_a_stored_event_used(self, db_session):
        c = _company("NEWSYM")
        db.session.add(FilingEvent(edgar_id="e2", cik="1", company_name="X", ticker="OLDSYM", company_id=c.id))
        db.session.commit()
        assert find_company_by_symbol("OLDSYM").id == c.id
        assert find_company_by_symbol("OLDSYM", include_events=False) is None

    def test_event_resolution_prefers_cik(self, db_session):
        issuer = _company("META", "0001326801")
        _company("FB")  # the symbol now belongs to someone else
        assert resolve_event_company("FB", "1326801").id == issuer.id


class TestRoutes:
    def test_fundamentals_page_resolves_an_alias(self, client, db_session):
        googl = _company("GOOGL", "0001652044")
        db.session.add(CompanyTickerAlias(ticker="GOOG", company=googl, kind="share_class"))
        db.session.commit()
        resp = client.get("/api/v1/fundamentals/GOOG")
        assert resp.status_code in (200, 202)
        assert resp.get_json()["company"]["ticker"] == "GOOGL"

    def test_search_leaves_out_delisted_rows(self, client, db_session):
        _company("ACME", name="Acme Corp", listed=True)
        _company("ACMEQ", name="Acme Shell", listed=False)
        _company("ACMX", name="Acme New", listed=None)  # not judged yet
        resp = client.get("/api/v1/companies/search?q=acm")
        tickers = {r["ticker"] for r in resp.get_json()["results"]}
        assert tickers == {"ACME", "ACMX"}
