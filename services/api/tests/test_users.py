"""Tests for the users blueprint, focused on access control."""


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {resp.get_json()['access_token']}"}


class TestListUsersAccessControl:
    def test_non_admin_cannot_list_users(self, client, sample_user):
        headers = _login(client, "test@example.com", "testpass123")
        resp = client.get("/api/v1/users/", headers=headers)
        assert resp.status_code == 403

    def test_unauthenticated_cannot_list_users(self, client):
        resp = client.get("/api/v1/users/")
        assert resp.status_code == 401

    def test_admin_can_list_users(self, client, db_session):
        from app.models.user import User

        admin = User(name="Admin", email="admin@example.com", is_admin=True)
        admin.set_password("adminpass123")
        db_session.session.add(admin)
        db_session.session.commit()

        headers = _login(client, "admin@example.com", "adminpass123")
        resp = client.get("/api/v1/users/", headers=headers)
        assert resp.status_code == 200
        assert "users" in resp.get_json()
