"""Feed filters (server-side), facet counts and saved views."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.company import Company
from app.models.event_type import EventType
from app.models.filing_event import FilingEvent
from app.models.price_reaction import PriceReaction
from app.models.watchlist import Watchlist
from app.services.feed_filters import cap_bucket, normalize_sector, parse_filters, FilterError

NOW = datetime.now(timezone.utc)


def _company(db_session, ticker, sector=None, cap=None):
    c = Company(name=f"{ticker} Inc.", ticker=ticker, cik=str(abs(hash(ticker)) % 10**9),
                sector=sector, market_cap=cap)
    db_session.session.add(c)
    db_session.session.commit()
    return c


def _event(db_session, company, eid, *, types=("Other",), significance="Medium", sentiment="Neutral",
           signal_type="8-K", age_days=0, headline=None, tier=3, move=None):
    when = NOW - timedelta(days=age_days)
    e = FilingEvent(
        edgar_id=eid, signal_type=signal_type, company_id=company.id, cik=company.cik,
        ticker=company.ticker, company_name=company.name, max_tier=tier,
        filing_date=when, created_at=when,
        briefing_json={"headline": headline or f"{company.ticker} news {eid}",
                       "significance": significance, "sentiment": sentiment,
                       "primary_event_type": types[0]},
    )
    for t in types:
        e.event_types.append(EventType(type_name=t))
    if move is not None:
        e.price_reactions.append(PriceReaction(
            ticker=company.ticker, interval="1d", measure_at=when, status="done",
            pct_change=move, is_explosive=abs(move) >= 5))
    db_session.session.add(e)
    db_session.session.commit()
    return e


@pytest.fixture
def market(db_session):
    """A small cross-section of the market with one event each."""
    bio = _company(db_session, "BIO", "Healthcare", 800_000_000)           # small
    big = _company(db_session, "BIG", "Technology", 3_000_000_000_000)     # mega
    bank = _company(db_session, "BNK", "Financial Services", 50_000_000_000)  # large
    otc = _company(db_session, "OTC", None, None)
    return {
        "bio": _event(db_session, bio, "e-bio", types=("Regulatory / Clinical",),
                      significance="High", sentiment="Positive", signal_type="PR", move=12.0),
        "big": _event(db_session, big, "e-big", types=("Earnings",), sentiment="Negative",
                      age_days=3, move=-6.0),
        "bank": _event(db_session, bank, "e-bank", types=("Acquisition", "Debt / Financing"),
                       significance="High", age_days=20, headline="Bank agrees to buy rival"),
        "otc": _event(db_session, otc, "e-otc", types=("Bankruptcy",), significance=None, tier=1,
                      age_days=40, move=1.0),
    }


def _ids(resp):
    assert resp.status_code == 200, resp.get_json()
    return {e["edgar_id"] for e in resp.get_json()["events"]}


class TestHelpers:
    @pytest.mark.parametrize("raw,expected", [
        ("Technology", "Technology"), ("health care", "Healthcare"),
        ("Consumer Discretionary", "Consumer Cyclical"), ("", None), ("Crypto", None),
    ])
    def test_normalize_sector(self, raw, expected):
        assert normalize_sector(raw) == expected

    @pytest.mark.parametrize("cap,bucket", [
        (None, None), (0, None), (250e6, "micro"), (300e6, "small"), (1.99e9, "small"),
        (2e9, "mid"), (10e9, "large"), (199e9, "large"), (200e9, "mega"),
    ])
    def test_cap_bucket_bounds(self, cap, bucket):
        assert cap_bucket(cap) == bucket

    def test_parse_rejects_unknown_values(self):
        for bad in ({"sector": "Crypto"}, {"cap": "huge"}, {"since": "2y"},
                    {"moved": "sideways"}, {"source": "tv"}, {"event_type": "Gossip"}):
            with pytest.raises(FilterError):
                parse_filters(bad)

    def test_to_dict_is_canonical(self):
        f = parse_filters({"sector": "health care,Technology", "cap": "SMALL",
                           "event_type": "earnings", "important": "1", "q": "  fda   approval "})
        assert f.to_dict() == {
            "scope": "all", "important": True, "event_type": ["Earnings"],
            "sector": ["Healthcare", "Technology"], "cap": ["small"], "q": "fda approval",
        }


class TestAllEventsFilters:
    def test_no_filters_returns_everything_newest_first(self, client, market):
        data = client.get("/api/v1/events/all").get_json()
        assert [e["edgar_id"] for e in data["events"]] == ["e-bio", "e-big", "e-bank", "e-otc"]
        assert data["has_more"] is False

    def test_important_matches_the_payload_flag(self, client, market):
        ids = _ids(client.get("/api/v1/events/all?important=1"))
        # significance High, or tier 1 when the briefing has no significance
        assert ids == {"e-bio", "e-bank", "e-otc"}
        for e in client.get("/api/v1/events/all?important=1").get_json()["events"]:
            assert e["important"] is True

    def test_sector_multi_select(self, client, market):
        assert _ids(client.get("/api/v1/events/all?sector=Healthcare,Technology")) == {"e-bio", "e-big"}

    def test_cap_buckets(self, client, market):
        assert _ids(client.get("/api/v1/events/all?cap=mega,small")) == {"e-big", "e-bio"}
        assert _ids(client.get("/api/v1/events/all?cap=micro")) == set()  # unknown cap is no bucket

    def test_event_type_multi_matches_any(self, client, market):
        assert _ids(client.get("/api/v1/events/all?event_type=Earnings,Debt%20%2F%20Financing")) \
            == {"e-big", "e-bank"}

    def test_source(self, client, market):
        assert _ids(client.get("/api/v1/events/all?source=pr")) == {"e-bio"}
        assert "e-bio" not in _ids(client.get("/api/v1/events/all?source=sec"))

    def test_sentiment(self, client, market):
        assert _ids(client.get("/api/v1/events/all?sentiment=negative")) == {"e-big"}

    def test_moved_needs_an_explosive_reaction(self, client, market):
        assert _ids(client.get("/api/v1/events/all?moved=any")) == {"e-bio", "e-big"}
        assert _ids(client.get("/api/v1/events/all?moved=up")) == {"e-bio"}
        assert _ids(client.get("/api/v1/events/all?moved=down")) == {"e-big"}

    def test_since_window(self, client, market):
        assert _ids(client.get("/api/v1/events/all?since=7d")) == {"e-bio", "e-big"}
        assert _ids(client.get("/api/v1/events/all?since=30d")) == {"e-bio", "e-big", "e-bank"}

    def test_search_covers_ticker_name_and_headline(self, client, market):
        assert _ids(client.get("/api/v1/events/all?q=rival")) == {"e-bank"}
        assert _ids(client.get("/api/v1/events/all?q=bio")) == {"e-bio"}
        # LIKE wildcards are literal
        assert _ids(client.get("/api/v1/events/all?q=%25")) == set()

    def test_filters_combine(self, client, market):
        assert _ids(client.get("/api/v1/events/all?important=1&cap=large,mega&since=30d")) == {"e-bank"}

    def test_pagination_counts_matches_not_rows(self, client, market):
        data = client.get("/api/v1/events/all?important=1&per_page=2").get_json()
        assert data["total"] == 3 and len(data["events"]) == 2 and data["has_more"] is True

    def test_unknown_value_is_a_400(self, client, market):
        resp = client.get("/api/v1/events/all?sector=Crypto")
        assert resp.status_code == 400
        assert "sector" in resp.get_json()["error"]

    def test_payload_carries_sector_and_industry(self, client, market):
        event = next(e for e in client.get("/api/v1/events/all").get_json()["events"]
                     if e["edgar_id"] == "e-bio")
        assert event["sector"] == "Healthcare"
        assert "industry" in event


class TestMineFilters:
    def test_filters_apply_inside_the_watchlist(self, client, auth_headers, sample_user, market, db_session):
        wl = Watchlist(name="Mine", user_id=sample_user.id)
        wl.companies.extend([market["bio"].company, market["big"].company])
        db_session.session.add(wl)
        db_session.session.commit()
        assert _ids(client.get("/api/v1/events/", headers=auth_headers)) == {"e-bio", "e-big"}
        assert _ids(client.get("/api/v1/events/?sector=Technology", headers=auth_headers)) == {"e-big"}
        assert _ids(client.get("/api/v1/events/?sector=Financial%20Services",
                               headers=auth_headers)) == set()


class TestFacets:
    def test_counts_hold_every_other_filter(self, client, market):
        data = client.get("/api/v1/events/facets?sector=Healthcare").get_json()
        assert data["total"] == 1
        # the sector facet ignores the sector filter, so the other options
        # say what adding them would bring in
        assert data["sector"] == {"Healthcare": 1, "Technology": 1, "Financial Services": 1}
        # every other facet is narrowed by it
        assert data["cap"] == {"small": 1}
        assert data["source"] == {"pr": 1}
        assert data["event_type"] == {"Regulatory / Clinical": 1}
        assert data["important"] == 1
        assert data["moved"] == {"any": 1, "up": 1, "down": 0}

    def test_unfiltered(self, client, market):
        data = client.get("/api/v1/events/facets").get_json()
        assert data["total"] == 4
        assert data["cap"] == {"small": 1, "mega": 1, "large": 1}
        assert data["source"] == {"pr": 1, "sec": 3}
        assert data["sentiment"] == {"Positive": 1, "Negative": 1, "Neutral": 2}
        assert data["event_type"]["Acquisition"] == 1
        assert data["important"] == 3

    def test_mine_needs_a_token(self, client, market):
        assert client.get("/api/v1/events/facets?scope=mine").status_code == 401

    def test_mine_counts_only_followed(self, client, auth_headers, sample_user, market, db_session):
        wl = Watchlist(name="Mine", user_id=sample_user.id)
        wl.companies.append(market["bank"].company)
        db_session.session.add(wl)
        db_session.session.commit()
        data = client.get("/api/v1/events/facets?scope=mine", headers=auth_headers).get_json()
        assert data["total"] == 1
        assert data["sector"] == {"Financial Services": 1}

    def test_options_list(self, client):
        data = client.get("/api/v1/events/filters").get_json()
        assert "Healthcare" in data["sector"]
        assert [c["key"] for c in data["cap"]] == ["mega", "large", "mid", "small", "micro"]
        assert "Other" not in data["event_type"]


class TestSavedViews:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/feed/views").status_code == 401

    def test_crud(self, client, auth_headers):
        resp = client.post("/api/v1/feed/views", headers=auth_headers, json={
            "name": "  Small-cap   biotech ", "filters": {"sector": ["Healthcare"], "cap": "small,micro",
                                                          "important": True}})
        assert resp.status_code == 201
        view = resp.get_json()["view"]
        assert view["name"] == "Small-cap biotech"
        assert view["filters"] == {"scope": "all", "important": True, "sector": ["Healthcare"],
                                   "cap": ["small", "micro"]}

        resp = client.put(f"/api/v1/feed/views/{view['id']}", headers=auth_headers,
                          json={"filters": {"scope": "mine", "moved": "up"}})
        assert resp.get_json()["view"]["filters"] == {"scope": "mine", "moved": "up"}

        views = client.get("/api/v1/feed/views", headers=auth_headers).get_json()["views"]
        assert [v["id"] for v in views] == [view["id"]]

        assert client.delete(f"/api/v1/feed/views/{view['id']}", headers=auth_headers).status_code == 200
        assert client.get("/api/v1/feed/views", headers=auth_headers).get_json()["views"] == []

    def test_rejects_bad_filters_and_duplicate_names(self, client, auth_headers):
        bad = client.post("/api/v1/feed/views", headers=auth_headers,
                          json={"name": "x", "filters": {"sector": "Crypto"}})
        assert bad.status_code == 400
        assert client.post("/api/v1/feed/views", headers=auth_headers,
                           json={"name": "", "filters": {}}).status_code == 400
        client.post("/api/v1/feed/views", headers=auth_headers, json={"name": "Energy", "filters": {}})
        dup = client.post("/api/v1/feed/views", headers=auth_headers, json={"name": "energy", "filters": {}})
        assert dup.status_code == 409

    def test_cannot_touch_someone_elses_view(self, client, auth_headers, db_session):
        from app.models.feed_view import FeedView
        from app.models.user import User
        other = User(name="Other", email="other@example.com")
        other.set_password("x" * 12)
        db_session.session.add(other)
        db_session.session.commit()
        view = FeedView(user_id=other.id, name="Theirs", filters={"scope": "all"})
        db_session.session.add(view)
        db_session.session.commit()
        assert client.put(f"/api/v1/feed/views/{view.id}", headers=auth_headers,
                          json={"name": "Mine"}).status_code == 404
        assert client.delete(f"/api/v1/feed/views/{view.id}", headers=auth_headers).status_code == 404
