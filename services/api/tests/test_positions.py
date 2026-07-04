"""Tests for the /positions endpoints (holdings + investment thesis)."""


def _open_position(client, auth_headers, company_id, **extra):
    payload = {"company_id": company_id}
    payload.update(extra)
    return client.post("/api/v1/positions/", json=payload, headers=auth_headers)


class TestCreatePosition:
    def test_open_position_minimal(self, client, auth_headers, sample_company):
        resp = _open_position(client, auth_headers, sample_company.id)
        assert resp.status_code == 201
        data = resp.get_json()["position"]
        assert data["company_id"] == sample_company.id
        assert data["direction"] == "long"          # server default
        assert data["thesis_status"] == "intact"    # server default
        assert data["company"]["ticker"] == "AAPL"  # nested company

    def test_open_position_with_thesis(self, client, auth_headers, sample_company):
        resp = _open_position(
            client, auth_headers, sample_company.id,
            direction="long", shares="100", cost_basis="150.25",
            thesis="Services margin expansion outweighs hardware slowdown.",
        )
        assert resp.status_code == 201
        data = resp.get_json()["position"]
        assert data["thesis"].startswith("Services margin")
        assert data["shares"] == "100.0000"
        assert data["cost_basis"] == "150.2500"

    def test_reopening_same_company_updates_not_duplicates(self, client, auth_headers, sample_company):
        first = _open_position(client, auth_headers, sample_company.id, thesis="v1")
        assert first.status_code == 201
        second = _open_position(client, auth_headers, sample_company.id, thesis="v2")
        assert second.status_code == 200               # updated, not created
        assert second.get_json()["position"]["thesis"] == "v2"

        listing = client.get("/api/v1/positions/", headers=auth_headers).get_json()
        assert len(listing["positions"]) == 1          # no duplicate row

    def test_unknown_company_404(self, client, auth_headers):
        resp = _open_position(client, auth_headers, "does-not-exist")
        assert resp.status_code == 404

    def test_bad_direction_rejected(self, client, auth_headers, sample_company):
        resp = _open_position(client, auth_headers, sample_company.id, direction="sideways")
        assert resp.status_code == 400

    def test_requires_auth(self, client, sample_company):
        resp = client.post("/api/v1/positions/", json={"company_id": sample_company.id})
        assert resp.status_code == 401


class TestListPositions:
    def test_list_empty(self, client, auth_headers):
        resp = client.get("/api/v1/positions/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["positions"] == []

    def test_list_filters_by_thesis_status(self, client, auth_headers, sample_company, sample_company_2):
        _open_position(client, auth_headers, sample_company.id)
        pos2 = _open_position(client, auth_headers, sample_company_2.id).get_json()["position"]
        # flip one to broken
        client.put(f"/api/v1/positions/{pos2['id']}",
                   json={"thesis_status": "broken"}, headers=auth_headers)

        broken = client.get("/api/v1/positions/?thesis_status=broken",
                            headers=auth_headers).get_json()["positions"]
        assert len(broken) == 1
        assert broken[0]["id"] == pos2["id"]


class TestUpdatePosition:
    def test_update_thesis_status_stamps_review(self, client, auth_headers, sample_company):
        pos = _open_position(client, auth_headers, sample_company.id).get_json()["position"]
        resp = client.put(f"/api/v1/positions/{pos['id']}",
                          json={"thesis_status": "watch"}, headers=auth_headers)
        assert resp.status_code == 200
        updated = resp.get_json()["position"]
        assert updated["thesis_status"] == "watch"
        assert updated["thesis_reviewed_at"] is not None

    def test_cannot_touch_other_users_position(self, client, auth_headers, sample_company, db_session):
        from app.models.user import User
        other = User(name="Other", email="other@example.com")
        other.set_password("x")
        db_session.session.add(other)
        db_session.session.commit()
        from app.models.position import Position
        pos = Position(user_id=other.id, company_id=sample_company.id)
        db_session.session.add(pos)
        db_session.session.commit()

        resp = client.put(f"/api/v1/positions/{pos.id}",
                          json={"thesis": "hijack"}, headers=auth_headers)
        assert resp.status_code == 403


class TestDeletePosition:
    def test_delete_position(self, client, auth_headers, sample_company):
        pos = _open_position(client, auth_headers, sample_company.id).get_json()["position"]
        resp = client.delete(f"/api/v1/positions/{pos['id']}", headers=auth_headers)
        assert resp.status_code == 200
        listing = client.get("/api/v1/positions/", headers=auth_headers).get_json()
        assert listing["positions"] == []
