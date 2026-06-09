"""Tests for filing event endpoints."""

from app.models.filing_event import FilingEvent


class TestGetEvents:
    def test_events_requires_auth(self, client):
        resp = client.get("/api/v1/events/")
        assert resp.status_code == 401

    def test_events_empty_watchlist(self, client, auth_headers):
        """User with no watchlist gets empty results."""
        resp = client.get("/api/v1/events/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["events"] == []
        assert resp.get_json()["total"] == 0

    def test_events_returns_watchlist_events(
        self, client, auth_headers, sample_watchlist, sample_event
    ):
        resp = client.get("/api/v1/events/", headers=auth_headers)
        data = resp.get_json()
        assert data["total"] == 1
        assert data["events"][0]["edgar_id"] == "test-edgar-001"
        assert data["events"][0]["ticker"] == "AAPL"

    def test_events_excludes_unwatched_companies(
        self, client, auth_headers, sample_watchlist, sample_company_2, db_session
    ):
        """Events for companies NOT in the user's watchlist should be excluded."""
        event = FilingEvent(
            edgar_id="other-company-event",
            signal_type="8-K",
            company_id=sample_company_2.id,
            cik=sample_company_2.cik,
            ticker=sample_company_2.ticker,
            company_name=sample_company_2.name,
            max_tier=1,
        )
        db_session.session.add(event)
        db_session.session.commit()

        resp = client.get("/api/v1/events/", headers=auth_headers)
        data = resp.get_json()
        assert data["total"] == 0  # TSLA not in watchlist

    def test_events_filter_by_tier(
        self, client, auth_headers, sample_watchlist, sample_company, db_session
    ):
        # Add a tier 3 event
        event = FilingEvent(
            edgar_id="tier3-event",
            signal_type="8-K",
            company_id=sample_company.id,
            cik=sample_company.cik,
            ticker=sample_company.ticker,
            company_name=sample_company.name,
            max_tier=3,
        )
        db_session.session.add(event)
        db_session.session.commit()

        # max_tier=1 should only return tier 1
        resp = client.get("/api/v1/events/?max_tier=1", headers=auth_headers)
        assert resp.get_json()["total"] == 0  # no tier 1 events yet

        # max_tier=3 should return all
        resp = client.get("/api/v1/events/?max_tier=3", headers=auth_headers)
        assert resp.get_json()["total"] == 1

    def test_events_filter_by_event_type(
        self, client, auth_headers, sample_watchlist, sample_event
    ):
        resp = client.get("/api/v1/events/?event_type=Acquisition", headers=auth_headers)
        assert resp.get_json()["total"] == 1

        resp = client.get("/api/v1/events/?event_type=Bankruptcy", headers=auth_headers)
        assert resp.get_json()["total"] == 0


class TestGetAllEvents:
    def test_all_events_no_auth_required(self, client, sample_event):
        resp = client.get("/api/v1/events/all")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 1

    def test_all_events_pagination(self, client, sample_company, db_session):
        for i in range(5):
            db_session.session.add(FilingEvent(
                edgar_id=f"page-test-{i}",
                signal_type="8-K",
                company_id=sample_company.id,
                cik=sample_company.cik,
                ticker=sample_company.ticker,
                company_name=sample_company.name,
                max_tier=2,
            ))
        db_session.session.commit()

        resp = client.get("/api/v1/events/all?page=1&per_page=2")
        data = resp.get_json()
        assert len(data["events"]) == 2
        assert data["total"] == 5


class TestGetEventTypes:
    def test_returns_event_types(self, client):
        resp = client.get("/api/v1/events/types")
        assert resp.status_code == 200
        types = resp.get_json()["event_types"]
        assert "Acquisition" in types
        assert "M&A / Merger" in types
        assert len(types) > 30


class TestGetEventDetail:
    def test_get_event_by_id(
        self, client, auth_headers, sample_watchlist, sample_event
    ):
        resp = client.get(f"/api/v1/events/{sample_event.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["event"]["edgar_id"] == "test-edgar-001"

    def test_get_event_access_denied(
        self, client, auth_headers, sample_company_2, db_session
    ):
        """Accessing an event for a company not in watchlist returns 403."""
        event = FilingEvent(
            edgar_id="forbidden-event",
            signal_type="8-K",
            company_id=sample_company_2.id,
            cik=sample_company_2.cik,
            ticker=sample_company_2.ticker,
            company_name=sample_company_2.name,
            max_tier=1,
        )
        db_session.session.add(event)
        db_session.session.commit()

        resp = client.get(f"/api/v1/events/{event.id}", headers=auth_headers)
        assert resp.status_code == 403


class TestCatalysts:
    def test_catalysts_endpoint(self, client, sample_event, sample_company, db_session):
        from datetime import date, timedelta
        from app.models.catalyst import Catalyst

        future = date.today() + timedelta(days=30)
        cat = Catalyst(
            filing_event_id=sample_event.id,
            event_description="Annual meeting",
            catalyst_date=future,
            ticker="AAPL",
            company_name="Apple Inc.",
        )
        db_session.session.add(cat)
        db_session.session.commit()

        resp = client.get("/api/v1/events/catalysts")
        assert resp.status_code == 200
        catalysts = resp.get_json()["catalysts"]
        assert len(catalysts) >= 1
        assert catalysts[0]["event"] == "Annual meeting"
