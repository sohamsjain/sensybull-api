"""Tests for authentication endpoints."""


def _cookie_from(resp, name):
    """Return the value of a Set-Cookie named `name`, or None."""
    for c in resp.headers.getlist("Set-Cookie"):
        if c.startswith(name + "="):
            return c.split("=", 1)[1].split(";", 1)[0]
    return None


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
        # Refresh token is delivered as an httpOnly cookie, not in the body.
        assert "refresh_token" not in data
        assert _cookie_from(resp, "refresh_token_cookie")
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
        assert "refresh_token" not in data
        assert _cookie_from(resp, "refresh_token_cookie")

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
        # The refresh token now lives in an httpOnly cookie; a bearer header
        # is still an accepted token location (bypasses CSRF, as in a browser
        # the cookie + CSRF header path is used instead).
        refresh_token = _cookie_from(login, "refresh_token_cookie")

        resp = client.post("/api/v1/auth/refresh", headers={
            "Authorization": f"Bearer {refresh_token}",
        })
        assert resp.status_code == 200
        assert resp.get_json()["access_token"]

    def test_refresh_with_access_token_fails(self, client, auth_headers):
        resp = client.post("/api/v1/auth/refresh", headers=auth_headers)
        assert resp.status_code == 422  # JWT type mismatch


class TestLogout:
    def test_logout_revokes_refresh_token(self, client, sample_user):
        login = client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "testpass123",
        })
        refresh_token = _cookie_from(login, "refresh_token_cookie")
        auth = {"Authorization": f"Bearer {refresh_token}"}

        # Refresh works before logout.
        assert client.post("/api/v1/auth/refresh", headers=auth).status_code == 200

        out = client.post("/api/v1/auth/logout", headers=auth)
        assert out.status_code == 200

        # After logout the same refresh token is blocklisted → rejected.
        resp = client.post("/api/v1/auth/refresh", headers=auth)
        assert resp.status_code == 401


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
