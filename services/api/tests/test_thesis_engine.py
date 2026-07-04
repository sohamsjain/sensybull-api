"""Tests for the thesis-break engine and assessment endpoints.

The Groq call (app.services.thesis.llm.assess_thesis) is mocked — these
tests exercise the engine's persistence, status-escalation, idempotency,
and alerting logic, not the LLM itself.
"""
from unittest.mock import patch

import pytest

from app.models.position import Position
from app.models.thesis_assessment import ThesisAssessment
from app.services.thesis import engine


@pytest.fixture
def held_position(db_session, sample_user, sample_company):
    """A long position with a thesis on the sample_event's company (AAPL)."""
    pos = Position(
        user_id=sample_user.id,
        company_id=sample_company.id,
        direction="long",
        thesis="Services margin expansion outweighs hardware slowdown.",
        thesis_status="intact",
    )
    db_session.session.add(pos)
    db_session.session.commit()
    return pos


def _run(app, event_id, verdict, configured=True):
    """Invoke the engine synchronously with a mocked LLM + captured socket."""
    with patch("app.services.thesis.llm.is_configured", return_value=configured), \
         patch("app.services.thesis.llm.assess_thesis", return_value=verdict) as m_assess, \
         patch("app.services.realtime.socketio_setup.socketio") as m_sock:
        engine._assess_event(app, event_id)
    return m_assess, m_sock


class TestEngineVerdicts:
    def test_breaks_sets_status_broken_and_alerts(self, app, db_session, held_position, sample_event):
        _, m_sock = _run(app, sample_event.id,
                         {"impact": "breaks", "rationale": "Guidance cut kills the margin story.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "broken"
        assert held_position.thesis_reviewed_at is not None

        a = ThesisAssessment.query.filter_by(position_id=held_position.id).one()
        assert a.impact == "breaks"
        assert a.prior_status == "intact"
        assert a.new_status == "broken"
        assert held_position.last_assessment_id == a.id
        # alert emitted to the owner's room
        m_sock.emit.assert_called_once()
        assert m_sock.emit.call_args.args[0] == "thesis_alert"
        assert m_sock.emit.call_args.kwargs["room"] == f"user:{held_position.user_id}"

    def test_threatens_sets_status_watch(self, app, db_session, held_position, sample_event):
        _, m_sock = _run(app, sample_event.id,
                         {"impact": "threatens", "rationale": "Debt raise pressures the story.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "watch"
        m_sock.emit.assert_called_once()

    def test_supports_records_but_no_status_change_or_alert(self, app, db_session, held_position, sample_event):
        _, m_sock = _run(app, sample_event.id,
                         {"impact": "supports", "rationale": "Buyback reinforces capital return.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "intact"
        assert ThesisAssessment.query.filter_by(position_id=held_position.id).count() == 1
        m_sock.emit.assert_not_called()

    def test_neutral_no_status_change(self, app, db_session, held_position, sample_event):
        _run(app, sample_event.id, {"impact": "neutral", "rationale": "Routine.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "intact"


class TestEscalateOnly:
    def test_threatens_does_not_downgrade_a_broken_thesis(self, app, db_session, held_position, sample_event):
        held_position.thesis_status = "broken"
        db_session.session.commit()
        _run(app, sample_event.id, {"impact": "threatens", "rationale": "x", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "broken"  # stays broken


class TestGuards:
    def test_idempotent_per_event(self, app, db_session, held_position, sample_event):
        v = {"impact": "breaks", "rationale": "x", "model": "x"}
        m1, _ = _run(app, sample_event.id, v)
        m2, _ = _run(app, sample_event.id, v)
        assert m1.called and not m2.called  # second run skips the LLM entirely
        assert ThesisAssessment.query.filter_by(position_id=held_position.id).count() == 1

    def test_position_without_thesis_is_ignored(self, app, db_session, sample_user, sample_company, sample_event):
        pos = Position(user_id=sample_user.id, company_id=sample_company.id, thesis=None)
        db_session.session.add(pos)
        db_session.session.commit()
        m_assess, _ = _run(app, sample_event.id, {"impact": "breaks", "rationale": "x", "model": "x"})
        assert not m_assess.called
        assert ThesisAssessment.query.count() == 0

    def test_no_op_when_llm_unconfigured(self, app, db_session, held_position, sample_event):
        m_assess, _ = _run(app, sample_event.id, {"impact": "breaks"}, configured=False)
        assert not m_assess.called
        assert ThesisAssessment.query.count() == 0

    def test_llm_failure_records_nothing(self, app, db_session, held_position, sample_event):
        _run(app, sample_event.id, None)  # assess_thesis returned None
        assert ThesisAssessment.query.count() == 0
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "intact"


class TestAssessmentEndpoints:
    def test_position_and_recent_assessment_feeds(self, app, client, auth_headers,
                                                  db_session, held_position, sample_event):
        _run(app, sample_event.id, {"impact": "breaks", "rationale": "boom", "model": "x"})

        by_pos = client.get(f"/api/v1/positions/{held_position.id}/assessments",
                            headers=auth_headers).get_json()["assessments"]
        assert len(by_pos) == 1
        assert by_pos[0]["impact"] == "breaks"
        assert by_pos[0]["rationale"] == "boom"

        recent = client.get("/api/v1/positions/assessments?impact=breaks",
                            headers=auth_headers).get_json()["assessments"]
        assert len(recent) == 1
        assert recent[0]["filing_event_id"] == sample_event.id
