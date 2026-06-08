"""Tests for authentication endpoints."""


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "name": "New User",
            "email": "new@example.com",
            "password": "securepass123",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["access_token"]
        assert data["refresh_token"]
        assert data["user"]["email"] == "new@example.com"

    def test_register_duplicate_email(self, client, sample_user):
        resp = client.post("/api/v1/auth/register", json={
            "name": "Dup User",
            "email": "test@example.com",
            "password": "securepass123",
        })
        assert resp.status_code == 409

    def test_register_missing_fields(self, client):
        resp = client.post("/api/v1/auth/register", json={"email": "x@x.com"})
        assert resp.status_code == 400


class TestLogin:
    def test_login_success(self, client, sample_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "testpass123",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["access_token"]
        assert data["refresh_token"]

    def test_login_wrong_password(self, client, sample_user):
        resp = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "wrongpass",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/v1/auth/login", json={
            "email": "nobody@example.com",
            "password": "whatever",
        })
        assert resp.status_code == 401


class TestRefresh:
    def test_refresh_token(self, client, sample_user):
        login = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "testpass123",
        })
        refresh_token = login.get_json()["refresh_token"]

        resp = client.post("/api/v1/auth/refresh", headers={
            "Authorization": f"Bearer {refresh_token}",
        })
        assert resp.status_code == 200
        assert resp.get_json()["access_token"]

    def test_refresh_with_access_token_fails(self, client, auth_headers):
        resp = client.post("/api/v1/auth/refresh", headers=auth_headers)
        assert resp.status_code == 422  # JWT type mismatch


class TestMe:
    def test_get_current_user(self, client, auth_headers, sample_user):
        resp = client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["user"]["email"] == "test@example.com"

    def test_get_current_user_no_auth(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401


class TestChangePassword:
    def test_change_password_success(self, client, auth_headers):
        resp = client.post("/api/v1/auth/change-password", headers=auth_headers, json={
            "current_password": "testpass123",
            "new_password": "newpass456",
        })
        assert resp.status_code == 200

        # Old password should no longer work
        resp2 = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "testpass123",
        })
        assert resp2.status_code == 401

        # New password should work
        resp3 = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "newpass456",
        })
        assert resp3.status_code == 200

    def test_change_password_wrong_current(self, client, auth_headers):
        resp = client.post("/api/v1/auth/change-password", headers=auth_headers, json={
            "current_password": "wrongpass",
            "new_password": "newpass456",
        })
        assert resp.status_code == 401
