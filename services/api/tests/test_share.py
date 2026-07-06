"""Tests for the shareable "Track on Sensybull" surface:

- GET  /api/v1/share/<symbol>   (public share info)
- POST /api/v1/share/events     (public funnel analytics)
- POST /api/v1/watchlists/track (authed, idempotent add-by-ticker)
"""

from app.models.share_event import ShareEvent
from app.models.watchlist import Watchlist
from app.utils.sectors import sic_to_sector
from app.utils.tickers import normalize_symbol


# ── Ticker validation ────────────────────────────────────────────────────

class TestNormalizeSymbol:
    def test_valid_symbols(self):
        assert normalize_symbol("mu") == "MU"
        assert normalize_symbol(" NVDA ") == "NVDA"
        assert normalize_symbol("BRK.B") == "BRK.B"
        assert normalize_symbol("bf-b") == "BF-B"

    def test_invalid_symbols(self):
        assert normalize_symbol("") is None
        assert normalize_symbol(None) is None
        assert normalize_symbol(123) is None
        assert normalize_symbol("TOOLONGSYM") is None
        assert normalize_symbol("<script>") is None
        assert normalize_symbol("MU'; DROP TABLE company;--") is None
        assert normalize_symbol("A B") is None


class TestSicToSector:
    def test_known_divisions(self):
        assert sic_to_sector("3674") == "Manufacturing"
        assert sic_to_sector("6022") == "Finance, Insurance & Real Estate"
        assert sic_to_sector("7372") == "Services"

    def test_unknown(self):
        assert sic_to_sector(None) is None
        assert sic_to_sector("") is None
        assert sic_to_sector("0042") is None
        assert sic_to_sector("not-a-code") is None


# ── GET /share/<symbol> ──────────────────────────────────────────────────

class TestShareInfo:
    def test_share_info_public_no_auth(self, client, sample_company):
        resp = client.get("/api/v1/share/AAPL")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["symbol"] == "AAPL"
        assert data["company"]["name"] == "Apple Inc."
        assert data["url"].endswith("/add/AAPL")
        assert data["url"] in data["html"]
        assert data["markdown"] == f"[Track Apple Inc. on Sensybull]({data['url']})"

    def test_case_insensitive_lookup(self, client, sample_company):
        resp = client.get("/api/v1/share/aapl")
        assert resp.status_code == 200
        assert resp.get_json()["symbol"] == "AAPL"

    def test_no_internal_ids_exposed(self, client, sample_company):
        data = client.get("/api/v1/share/AAPL").get_json()
        body = str(data)
        assert sample_company.id not in body
        assert "id" not in data["company"]

    def test_unknown_ticker_404(self, client):
        resp = client.get("/api/v1/share/ZZZZ")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "unknown_ticker"

    def test_invalid_symbol_400(self, client):
        resp = client.get("/api/v1/share/%3Cscript%3E")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_symbol"

    def test_html_snippet_escapes_company_name(self, client, db_session):
        from app.models.company import Company
        company = Company(name='Ba<d> & "Co"', ticker="BAD")
        db_session.session.add(company)
        db_session.session.commit()
        data = client.get("/api/v1/share/BAD").get_json()
        assert "<d>" not in data["html"]
        assert "Ba&lt;d&gt;" in data["html"]


# ── POST /share/events ───────────────────────────────────────────────────

class TestShareEvents:
    def test_record_anonymous_event(self, client, db_session):
        resp = client.post(
            "/api/v1/share/events",
            json={"event": "link_opened", "symbol": "mu", "ref": "substack",
                  "utm_source": "newsletter"},
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) "
                                   "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"},
        )
        assert resp.status_code == 202
        row = ShareEvent.query.one()
        assert row.event == "link_opened"
        assert row.symbol == "MU"
        assert row.ref == "substack"
        assert row.utm_source == "newsletter"
        assert row.device == "mobile"
        assert row.logged_in is False
        assert row.user_id is None

    def test_record_authed_event_attaches_user(self, client, auth_headers, sample_user):
        resp = client.post("/api/v1/share/events",
                           json={"event": "auth_completed", "symbol": "MU"},
                           headers=auth_headers)
        assert resp.status_code == 202
        row = ShareEvent.query.one()
        assert row.user_id == sample_user.id
        assert row.logged_in is True

    def test_unknown_event_rejected(self, client):
        resp = client.post("/api/v1/share/events", json={"event": "evil_event"})
        assert resp.status_code == 400
        assert ShareEvent.query.count() == 0

    def test_attribution_is_sanitized_and_bounded(self, client):
        resp = client.post("/api/v1/share/events", json={
            "event": "link_opened",
            "symbol": "MU",
            "ref": "x" * 500 + "\x00\x1f",
            "referrer": "https://evil.example/" + "y" * 500,
        })
        assert resp.status_code == 202
        row = ShareEvent.query.one()
        assert len(row.ref) == 64
        assert "\x00" not in row.ref
        assert len(row.referrer) == 255

    def test_bad_symbol_dropped_not_fatal(self, client):
        resp = client.post("/api/v1/share/events",
                           json={"event": "link_opened", "symbol": "<script>"})
        assert resp.status_code == 202
        assert ShareEvent.query.one().symbol is None


# ── POST /watchlists/track ───────────────────────────────────────────────

class TestTrackCompany:
    def test_requires_auth(self, client, sample_company):
        resp = client.post("/api/v1/watchlists/track", json={"symbol": "AAPL"})
        assert resp.status_code == 401

    def test_track_creates_default_watchlist_and_adds(self, client, auth_headers,
                                                      sample_user, sample_company):
        resp = client.post("/api/v1/watchlists/track",
                           json={"symbol": "aapl"}, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "added"
        assert data["company"]["ticker"] == "AAPL"

        wl = Watchlist.query.filter_by(user_id=sample_user.id).one()
        assert wl.name == "My Watchlist"
        assert data["watchlist_id"] == wl.id
        assert sample_company in wl.companies

    def test_track_is_idempotent(self, client, auth_headers, sample_user, sample_company):
        first = client.post("/api/v1/watchlists/track",
                            json={"symbol": "AAPL"}, headers=auth_headers)
        second = client.post("/api/v1/watchlists/track",
                             json={"symbol": "AAPL"}, headers=auth_headers)
        assert first.get_json()["status"] == "added"
        assert second.status_code == 200
        assert second.get_json()["status"] == "already_tracking"

        wl = Watchlist.query.filter_by(user_id=sample_user.id).one()
        assert len(wl.companies) == 1

    def test_track_uses_existing_first_watchlist(self, client, auth_headers,
                                                 sample_watchlist, sample_company_2):
        resp = client.post("/api/v1/watchlists/track",
                           json={"symbol": "TSLA"}, headers=auth_headers)
        data = resp.get_json()
        assert data["status"] == "added"
        assert data["watchlist_id"] == sample_watchlist.id
        assert Watchlist.query.count() == 1

    def test_already_in_watchlist_via_regular_add(self, client, auth_headers,
                                                  sample_watchlist):
        # sample_watchlist already contains AAPL (added through the classic flow)
        resp = client.post("/api/v1/watchlists/track",
                           json={"symbol": "AAPL"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "already_tracking"

    def test_unknown_ticker(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists/track",
                           json={"symbol": "ZZZZ"}, headers=auth_headers)
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "unknown_ticker"

    def test_invalid_symbol(self, client, auth_headers):
        for bad in ("", None, "DROP TABLE", "<x>"):
            resp = client.post("/api/v1/watchlists/track",
                               json={"symbol": bad}, headers=auth_headers)
            assert resp.status_code == 400
            assert resp.get_json()["error"] == "invalid_symbol"

    def test_track_records_funnel_analytics(self, client, auth_headers,
                                            sample_user, sample_company):
        client.post("/api/v1/watchlists/track",
                    json={"symbol": "AAPL",
                          "attribution": {"ref": "reddit", "utm_source": "post"}},
                    headers=auth_headers)
        row = ShareEvent.query.filter_by(event="watchlist_added").one()
        assert row.symbol == "AAPL"
        assert row.ref == "reddit"
        assert row.utm_source == "post"
        assert row.user_id == sample_user.id
        assert row.logged_in is True

        client.post("/api/v1/watchlists/track",
                    json={"symbol": "AAPL"}, headers=auth_headers)
        assert ShareEvent.query.filter_by(event="already_in_watchlist").count() == 1

    def test_existing_watchlist_endpoints_still_work(self, client, auth_headers,
                                                     sample_watchlist, sample_company_2):
        # The classic add/remove flow must be unaffected by the new route.
        resp = client.post(f"/api/v1/watchlists/{sample_watchlist.id}/companies",
                           json={"company_id": sample_company_2.id},
                           headers=auth_headers)
        assert resp.status_code == 200
        resp = client.delete(
            f"/api/v1/watchlists/{sample_watchlist.id}/companies/{sample_company_2.id}",
            headers=auth_headers)
        assert resp.status_code == 200
