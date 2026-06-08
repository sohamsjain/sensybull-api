"""Tests for watchlist CRUD endpoints."""


class TestListWatchlists:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/watchlists/")
        assert resp.status_code == 401

    def test_empty_watchlists(self, client, auth_headers):
        resp = client.get("/api/v1/watchlists/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["watchlists"] == []

    def test_lists_user_watchlists(self, client, auth_headers, sample_watchlist):
        resp = client.get("/api/v1/watchlists/", headers=auth_headers)
        data = resp.get_json()
        assert len(data["watchlists"]) == 1
        assert data["watchlists"][0]["name"] == "Tech"


class TestCreateWatchlist:
    def test_create_watchlist(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists/", headers=auth_headers, json={
            "name": "Biotech",
            "description": "Biotech companies",
        })
        assert resp.status_code == 201
        assert resp.get_json()["watchlist"]["name"] == "Biotech"

    def test_create_watchlist_missing_name(self, client, auth_headers):
        resp = client.post("/api/v1/watchlists/", headers=auth_headers, json={
            "description": "No name",
        })
        assert resp.status_code == 400


class TestGetWatchlist:
    def test_get_watchlist(self, client, auth_headers, sample_watchlist):
        resp = client.get(f"/api/v1/watchlists/{sample_watchlist.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["watchlist"]["name"] == "Tech"

    def test_get_other_users_watchlist(self, client, db_session, auth_headers):
        """Cannot access another user's watchlist."""
        from app.models.user import User
        from app.models.watchlist import Watchlist

        other = User(name="Other", email="other@example.com")
        other.set_password("pass")
        db_session.session.add(other)
        db_session.session.flush()

        wl = Watchlist(name="Private", user_id=other.id)
        db_session.session.add(wl)
        db_session.session.commit()

        resp = client.get(f"/api/v1/watchlists/{wl.id}", headers=auth_headers)
        assert resp.status_code == 403


class TestUpdateWatchlist:
    def test_update_watchlist(self, client, auth_headers, sample_watchlist):
        resp = client.put(f"/api/v1/watchlists/{sample_watchlist.id}", headers=auth_headers, json={
            "name": "Updated Tech",
        })
        assert resp.status_code == 200
        assert resp.get_json()["watchlist"]["name"] == "Updated Tech"


class TestDeleteWatchlist:
    def test_delete_watchlist(self, client, auth_headers, sample_watchlist):
        resp = client.delete(f"/api/v1/watchlists/{sample_watchlist.id}", headers=auth_headers)
        assert resp.status_code == 200

        # Verify it's gone
        resp2 = client.get(f"/api/v1/watchlists/{sample_watchlist.id}", headers=auth_headers)
        assert resp2.status_code == 404


class TestWatchlistCompanies:
    def test_add_company(self, client, auth_headers, sample_watchlist, sample_company_2):
        resp = client.post(
            f"/api/v1/watchlists/{sample_watchlist.id}/companies",
            headers=auth_headers,
            json={"company_id": sample_company_2.id},
        )
        assert resp.status_code == 200

    def test_add_duplicate_company(self, client, auth_headers, sample_watchlist, sample_company):
        """Adding a company that's already in the watchlist returns 409."""
        resp = client.post(
            f"/api/v1/watchlists/{sample_watchlist.id}/companies",
            headers=auth_headers,
            json={"company_id": sample_company.id},
        )
        assert resp.status_code == 409

    def test_remove_company(self, client, auth_headers, sample_watchlist, sample_company):
        resp = client.delete(
            f"/api/v1/watchlists/{sample_watchlist.id}/companies/{sample_company.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_remove_nonexistent_company(
        self, client, auth_headers, sample_watchlist, sample_company_2
    ):
        resp = client.delete(
            f"/api/v1/watchlists/{sample_watchlist.id}/companies/{sample_company_2.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404
