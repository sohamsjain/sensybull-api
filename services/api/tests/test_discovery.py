"""Discovery surface: sitemap data, API catalog, API-host robots.txt."""

from app.models.filing_event import FilingEvent
from app.models.fundamentals import CompanyFundamentals


def _event(db_session, company, edgar_id, briefing):
    event = FilingEvent(
        edgar_id=edgar_id,
        signal_type="8-K",
        company_id=company.id,
        cik=company.cik,
        ticker=company.ticker,
        company_name=company.name,
        max_tier=1,
        briefing_json=briefing,
    )
    db_session.session.add(event)
    db_session.session.commit()
    return event


class TestSitemap:
    def test_lists_companies_with_fundamentals_only(
        self, client, db_session, sample_company, sample_company_2
    ):
        db_session.session.add(CompanyFundamentals(
            company_id=sample_company.id, has_fundamentals=True))
        db_session.session.add(CompanyFundamentals(
            company_id=sample_company_2.id, has_fundamentals=False))
        db_session.session.commit()

        data = client.get("/api/v1/discovery/sitemap").get_json()
        assert [c["symbol"] for c in data["companies"]] == ["AAPL"]

    def test_lists_only_events_with_evidence(self, client, db_session, sample_company):
        quoted = _event(db_session, sample_company, "e-1", {
            "headline": "Apple buys a thing",
            "evidence": [{"quote": "Apple entered into an agreement", "url": None}],
        })
        _event(db_session, sample_company, "e-2", {"headline": "No quotes", "evidence": []})
        _event(db_session, sample_company, "e-3", {"headline": "Facts only"})
        _event(db_session, sample_company, "e-4", None)

        data = client.get("/api/v1/discovery/sitemap").get_json()
        assert [e["id"] for e in data["events"]] == [quoted.id]
        assert data["events"][0]["lastmod"]

    def test_is_public_and_cacheable(self, client):
        resp = client.get("/api/v1/discovery/sitemap")
        assert resp.status_code == 200
        assert "max-age" in resp.headers["Cache-Control"]


class TestApiCatalog:
    def test_linkset_points_at_spec_docs_and_health(self, client):
        resp = client.get("/.well-known/api-catalog")
        assert resp.status_code == 200
        assert resp.mimetype == "application/linkset+json"
        assert 'rel="api-catalog"' in resp.headers["Link"]
        entry = resp.get_json()["linkset"][0]
        assert entry["anchor"].endswith("/api/v1")
        assert entry["service-desc"][0]["href"].endswith("/docs/openapi.json")
        assert entry["service-doc"][0]["href"].endswith("/docs")
        assert entry["status"][0]["href"].endswith("/health")

    def test_head_is_supported(self, client):
        # RFC 9727 §2: the well-known URI must answer HEAD as well as GET
        resp = client.head("/.well-known/api-catalog")
        assert resp.status_code == 200
        assert "api-catalog" in resp.headers["Link"]

    def test_catalogued_routes_exist(self, app, client):
        """Every link in the catalog resolves (health may 503 without Redis)."""
        entry = client.get("/.well-known/api-catalog").get_json()["linkset"][0]
        for rel in ("service-desc", "service-doc", "status"):
            path = "/" + entry[rel][0]["href"].split("/", 3)[3]
            assert client.get(path).status_code in (200, 503), path


class TestOpenApiSpec:
    def test_every_public_read_route_is_documented(self, client):
        paths = client.get("/docs/openapi.json").get_json()["paths"]
        for path in (
            "/api/v1/events/all/{event_id}",
            "/api/v1/events/filters",
            "/api/v1/events/facets",
            "/api/v1/companies/search",
            "/api/v1/fundamentals/{symbol}",
            "/api/v1/fundamentals/{symbol}/documents",
            "/api/v1/discovery/sitemap",
        ):
            assert path in paths, path


def test_api_host_robots_keeps_crawlers_off_the_json(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.get_data(as_text=True)
    assert "Allow: /docs" in body
    assert "Disallow: /\n" in body
