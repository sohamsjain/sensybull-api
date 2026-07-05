"""Tests for the two-stage thesis judgment, thesis versioning, retroactive
backtests, the drafting assistant, the scorecard, and the analyst endpoint.

All Groq calls are mocked — these exercise routing, persistence, and
validation, not the LLM.
"""
from unittest.mock import patch

import pytest

from app.models.position import Position
from app.models.price_reaction import PriceReaction
from app.models.thesis_assessment import ThesisAssessment
from app.models.thesis_version import ThesisVersion
from app.services.thesis import engine, llm


STRUCTURED = {
    "core_claim": "Services margin expansion outweighs hardware slowdown.",
    "assumptions": ["Services revenue grows >12% YoY", "No major App Store regulation"],
    "kill_criteria": ["Services growth below 5% for two quarters"],
    "horizon": "12 months",
}

TRIAGE_BREAKS = {"impact": "breaks", "rationale": "Guidance cut.", "model": "scout"}
DEEP_THREATENS = {
    "impact": "threatens",
    "confidence": 0.8,
    "rationale": "Margin pressure is real but services growth holds.",
    "assumption_verdicts": [
        {"index": 1, "impact": "threatens", "rationale": "Growth slowing."},
        {"index": 2, "impact": "neutral", "rationale": "No regulatory news."},
    ],
    "citations": ["Test"],
    "model": "70b",
}


@pytest.fixture
def held_position(db_session, sample_user, sample_company):
    pos = Position(
        user_id=sample_user.id,
        company_id=sample_company.id,
        direction="long",
        thesis="Services margin expansion outweighs hardware slowdown.",
        thesis_structured=STRUCTURED,
        thesis_version=1,
        thesis_status="intact",
    )
    db_session.session.add(pos)
    db_session.session.commit()
    return pos


def _run(app, event_id, triage, deep, watchlist=None):
    with patch("app.services.thesis.llm.is_configured", return_value=True), \
         patch("app.services.thesis.llm.assess_thesis", return_value=triage), \
         patch("app.services.thesis.llm.assess_thesis_deep", return_value=deep) as m_deep, \
         patch("app.services.realtime.socketio_setup.socketio") as m_sock, \
         patch("app.services.alerts.dispatcher.dispatch_thesis_alert") as m_thesis, \
         patch("app.services.alerts.dispatcher.trigger_alerts") as m_regular:
        engine._assess_event(app, event_id, frozenset(watchlist or set()))
    return {"deep": m_deep, "sock": m_sock, "thesis": m_thesis, "regular": m_regular}


class TestTwoStage:
    def test_deep_verdict_overrides_triage(self, app, db_session, held_position, sample_event):
        m = _run(app, sample_event.id, TRIAGE_BREAKS, DEEP_THREATENS)
        db_session.session.refresh(held_position)
        # deep said threatens → watch, not the triage "breaks" → broken
        assert held_position.thesis_status == "watch"

        a = ThesisAssessment.query.filter_by(position_id=held_position.id).one()
        assert a.stage == "deep"
        assert a.impact == "threatens"
        assert a.triage_impact == "breaks"
        assert a.confidence == 0.8
        assert len(a.assumption_verdicts_json) == 2
        assert a.citations_json == ["Test"]
        assert a.thesis_version == 1
        m["thesis"].assert_called_once()
        adict = m["thesis"].call_args.args[3]
        assert adict["confidence"] == 0.8 and adict["stage"] == "deep"

    def test_deep_receives_filing_text_and_position_size(self, app, db_session, held_position, sample_event):
        held_position.shares = 100
        db_session.session.commit()
        m = _run(app, sample_event.id, TRIAGE_BREAKS, DEEP_THREATENS)
        kwargs_or_args = m["deep"].call_args
        # signature: (thesis, structured, direction, shares, cost_basis, event, filing_text, price_summary)
        args = kwargs_or_args.args
        assert args[1] == STRUCTURED
        assert float(args[3]) == 100
        assert "Test" in args[6]  # items_json text made it into filing_text

    def test_deep_failure_falls_back_to_triage_verdict(self, app, db_session, held_position, sample_event):
        _run(app, sample_event.id, TRIAGE_BREAKS, None)
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "broken"  # triage verdict stands
        a = ThesisAssessment.query.filter_by(position_id=held_position.id).one()
        assert a.stage == "triage" and a.impact == "breaks"
        assert a.triage_impact is None and a.confidence is None

    def test_neutral_triage_skips_deep_pass(self, app, db_session, held_position, sample_event):
        m = _run(app, sample_event.id,
                 {"impact": "neutral", "rationale": "Routine.", "model": "scout"},
                 DEEP_THREATENS)
        m["deep"].assert_not_called()
        a = ThesisAssessment.query.filter_by(position_id=held_position.id).one()
        assert a.stage == "triage" and a.impact == "neutral"


class TestRetroactive:
    def test_backtest_rows_are_informational(self, app, db_session, held_position, sample_event):
        with patch("app.services.thesis.llm.assess_thesis", return_value=TRIAGE_BREAKS), \
             patch("app.services.thesis.llm.assess_thesis_deep", return_value=None), \
             patch("app.services.alerts.dispatcher.dispatch_thesis_alert") as m_thesis, \
             patch("app.services.realtime.socketio_setup.socketio") as m_sock:
            engine._assess_retroactive(held_position.id, limit=10)

        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "intact"   # never escalates
        a = ThesisAssessment.query.filter_by(position_id=held_position.id).one()
        assert a.retroactive is True
        assert a.impact == "breaks"
        assert a.prior_status == a.new_status == "intact"
        m_thesis.assert_not_called()
        m_sock.emit.assert_not_called()

    def test_backtest_skips_already_assessed_events(self, app, db_session, held_position, sample_event):
        _run(app, sample_event.id, TRIAGE_BREAKS, None)  # live assessment exists
        with patch("app.services.thesis.llm.assess_thesis", return_value=TRIAGE_BREAKS) as m, \
             patch("app.services.thesis.llm.assess_thesis_deep", return_value=None):
            engine._assess_retroactive(held_position.id, limit=10)
        assert not m.called
        assert ThesisAssessment.query.filter_by(position_id=held_position.id).count() == 1


class TestThesisVersioning:
    def _create(self, client, auth_headers, company_id, **extra):
        with patch("app.services.thesis.llm.is_configured", return_value=True), \
             patch("app.services.thesis.engine.trigger_retroactive_assessments") as m_retro:
            resp = client.post("/api/v1/positions/", headers=auth_headers,
                               json={"company_id": company_id, **extra})
        return resp, m_retro

    def test_create_with_structured_thesis_snapshots_v1(self, client, auth_headers, db_session, sample_company):
        resp, m_retro = self._create(client, auth_headers, sample_company.id,
                                     thesis_structured=STRUCTURED, thesis_source="assist")
        assert resp.status_code == 201
        pos = resp.get_json()["position"]
        assert pos["thesis_version"] == 1
        assert pos["thesis_structured"]["core_claim"] == STRUCTURED["core_claim"]
        # free-text derived from the structure
        assert "Services margin expansion" in pos["thesis"]
        assert "Assumptions:" in pos["thesis"]

        v = ThesisVersion.query.filter_by(position_id=pos["id"]).one()
        assert v.version == 1 and v.source == "assist"
        m_retro.assert_called_once()

    def test_thesis_edit_bumps_version_and_resets_status(self, client, auth_headers, db_session, held_position, sample_event):
        # a live broken status + a retroactive row from the old thesis
        held_position.thesis_status = "broken"
        db_session.session.add(ThesisAssessment(
            position_id=held_position.id, filing_event_id=sample_event.id,
            user_id=held_position.user_id, impact="breaks", retroactive=True,
        ))
        db_session.session.commit()

        with patch("app.services.thesis.llm.is_configured", return_value=True), \
             patch("app.services.thesis.engine.trigger_retroactive_assessments") as m_retro:
            resp = client.put(f"/api/v1/positions/{held_position.id}", headers=auth_headers,
                              json={"thesis": "New story: buybacks + AI capex."})
        assert resp.status_code == 200
        pos = resp.get_json()["position"]
        assert pos["thesis_version"] == 2
        assert pos["thesis_status"] == "intact"          # rewritten thesis starts fresh
        assert ThesisVersion.query.filter_by(position_id=held_position.id, version=2).count() == 1
        # stale backtest of the old thesis purged; fresh one queued
        assert ThesisAssessment.query.filter_by(
            position_id=held_position.id, retroactive=True).count() == 0
        m_retro.assert_called_once()

    def test_non_thesis_update_does_not_version(self, client, auth_headers, db_session, held_position):
        with patch("app.services.thesis.engine.trigger_retroactive_assessments") as m_retro:
            resp = client.put(f"/api/v1/positions/{held_position.id}", headers=auth_headers,
                              json={"shares": "250"})
        assert resp.status_code == 200
        assert resp.get_json()["position"]["thesis_version"] == 1
        assert ThesisVersion.query.count() == 0  # fixture didn't snapshot; no new row
        m_retro.assert_not_called()

    def test_versions_endpoint(self, client, auth_headers, db_session, held_position):
        db_session.session.add(ThesisVersion(position_id=held_position.id, version=1,
                                             thesis="old", source="user"))
        db_session.session.commit()
        resp = client.get(f"/api/v1/positions/{held_position.id}/versions", headers=auth_headers)
        versions = resp.get_json()["versions"]
        assert len(versions) == 1 and versions[0]["version"] == 1


class TestDraftThesis:
    def test_returns_structured_draft(self, client, auth_headers, sample_company):
        draft = {**STRUCTURED, "model": "70b"}
        with patch("app.services.thesis.llm.draft_thesis", return_value=draft) as m:
            resp = client.post("/api/v1/positions/draft-thesis", headers=auth_headers,
                               json={"raw_text": "I think services will carry them",
                                     "company_id": sample_company.id})
        assert resp.status_code == 200
        body = resp.get_json()["draft"]
        assert body["core_claim"] == STRUCTURED["core_claim"]
        assert "model" not in body
        assert m.call_args.kwargs["ticker"] == "AAPL"

    def test_503_when_unavailable(self, client, auth_headers):
        with patch("app.services.thesis.llm.draft_thesis", return_value=None):
            resp = client.post("/api/v1/positions/draft-thesis", headers=auth_headers,
                               json={"raw_text": "notes"})
        assert resp.status_code == 503

    def test_validation(self, client, auth_headers):
        resp = client.post("/api/v1/positions/draft-thesis", headers=auth_headers, json={})
        assert resp.status_code == 400


class TestScorecard:
    def test_verdicts_joined_to_price_moves(self, client, auth_headers, db_session, held_position, sample_event):
        db_session.session.add(ThesisAssessment(
            position_id=held_position.id, filing_event_id=sample_event.id,
            user_id=held_position.user_id, impact="breaks",
        ))
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for interval, pct in (("1d", -8.0), ("1w", -14.0)):
            db_session.session.add(PriceReaction(
                filing_event_id=sample_event.id, ticker="AAPL", interval=interval,
                measure_at=now, status="done", pct_change=pct,
            ))
        db_session.session.commit()

        resp = client.get("/api/v1/positions/scorecard", headers=auth_headers)
        sc = resp.get_json()["scorecard"]
        assert sc["assessed_filings"] == 1
        assert sc["verdict_counts"] == {"breaks": 1}
        assert sc["avg_move_after_verdict"]["breaks"] == {"1d": -8.0, "1w": -14.0}
        assert sc["position_status_counts"] == {"intact": 1}

    def test_retroactive_rows_excluded(self, client, auth_headers, db_session, held_position, sample_event):
        db_session.session.add(ThesisAssessment(
            position_id=held_position.id, filing_event_id=sample_event.id,
            user_id=held_position.user_id, impact="breaks", retroactive=True,
        ))
        db_session.session.commit()
        resp = client.get("/api/v1/positions/scorecard", headers=auth_headers)
        assert resp.get_json()["scorecard"]["assessed_filings"] == 0


class TestRecentAssessmentsFeed:
    def test_excludes_retroactive_by_default(self, client, auth_headers, db_session, held_position, sample_event):
        db_session.session.add(ThesisAssessment(
            position_id=held_position.id, filing_event_id=sample_event.id,
            user_id=held_position.user_id, impact="breaks", retroactive=True,
        ))
        db_session.session.commit()
        assert client.get("/api/v1/positions/assessments",
                          headers=auth_headers).get_json()["assessments"] == []
        included = client.get("/api/v1/positions/assessments?include_retroactive=1",
                              headers=auth_headers).get_json()["assessments"]
        assert len(included) == 1 and included[0]["retroactive"] is True


class TestAnalystEndpoint:
    def test_reply(self, client, auth_headers, held_position):
        result = {"reply": "The margin story is intact.", "model": "70b",
                  "tools_used": ["list_recent_filings"]}
        with patch("app.services.thesis.analyst.run_analyst", return_value=result):
            resp = client.post(f"/api/v1/positions/{held_position.id}/analyst",
                               headers=auth_headers,
                               json={"messages": [{"role": "user", "content": "Stress my thesis"}]})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["reply"].startswith("The margin story")
        assert body["tools_used"] == ["list_recent_filings"]

    def test_503_when_unavailable(self, client, auth_headers, held_position):
        with patch("app.services.thesis.analyst.run_analyst", return_value=None):
            resp = client.post(f"/api/v1/positions/{held_position.id}/analyst",
                               headers=auth_headers,
                               json={"messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 503

    def test_last_message_must_be_user(self, client, auth_headers, held_position):
        resp = client.post(f"/api/v1/positions/{held_position.id}/analyst",
                           headers=auth_headers,
                           json={"messages": [{"role": "assistant", "content": "hi"}]})
        assert resp.status_code == 400

    def test_other_users_position_denied(self, client, db_session, held_position):
        from app.models.user import User
        other = User(name="Other", email="other@example.com")
        other.set_password("testpass123")
        db_session.session.add(other)
        db_session.session.commit()
        login = client.post("/api/v1/auth/login",
                            json={"email": "other@example.com", "password": "testpass123"})
        headers = {"Authorization": f"Bearer {login.get_json()['access_token']}"}
        resp = client.post(f"/api/v1/positions/{held_position.id}/analyst", headers=headers,
                           json={"messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 403


class TestLlmValidation:
    """Pure unit tests over the deep-pass output cleaning."""

    def test_citations_must_be_verbatim(self):
        text = "Revenue guidance was reduced to $80B for fiscal 2027."
        cleaned = llm._clean_citations(
            ["guidance was reduced", "completely fabricated quote"], text)
        assert cleaned == ["guidance was reduced"]

    def test_assumption_verdicts_validated(self):
        cleaned = llm._clean_assumption_verdicts([
            {"index": 1, "impact": "threatens", "rationale": "x"},
            {"index": 9, "impact": "breaks", "rationale": "out of range"},
            {"index": 2, "impact": "banana", "rationale": "bad impact"},
            "not a dict",
        ], n_assumptions=2)
        assert cleaned == [{"index": 1, "impact": "threatens", "rationale": "x"}]

    def test_filing_text_assembled_from_items(self, app, db_session, sample_event):
        text = engine._filing_text(sample_event)
        assert "[Material Agreement]" in text and "Test" in text


class TestCatalystCompanyFilter:
    def test_company_id_filter(self, client, db_session, sample_event, sample_company, sample_company_2):
        from datetime import date, timedelta
        from app.models.catalyst import Catalyst
        # give the fixture catalyst a future date so it clears the cutoff
        cat = Catalyst.query.first()
        cat.catalyst_date = date.today() + timedelta(days=30)
        db_session.session.commit()

        mine = client.get(f"/api/v1/events/catalysts?company_id={sample_company.id}").get_json()
        assert len(mine["catalysts"]) == 1
        other = client.get(f"/api/v1/events/catalysts?company_id={sample_company_2.id}").get_json()
        assert other["catalysts"] == []
