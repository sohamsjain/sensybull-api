"""Tests for the thesis-break engine, alert routing, and channel formatting.

The Groq call (app.services.thesis.llm.assess_thesis) and the alert
dispatcher entrypoints are mocked — these tests exercise the engine's
persistence, status-escalation, idempotency, and the deferral/enrichment
routing, not the LLM or real delivery.
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


def _run(app, event_id, verdict, configured=True, watchlist=None):
    """Invoke the engine with a mocked LLM, socket, and dispatcher."""
    with patch("app.services.thesis.llm.is_configured", return_value=configured), \
         patch("app.services.thesis.llm.assess_thesis", return_value=verdict) as m_assess, \
         patch("app.services.realtime.socketio_setup.socketio") as m_sock, \
         patch("app.services.alerts.dispatcher.dispatch_thesis_alert") as m_thesis, \
         patch("app.services.alerts.dispatcher.trigger_alerts") as m_regular:
        engine._assess_event(app, event_id, frozenset(watchlist or set()))
    return {"assess": m_assess, "sock": m_sock, "thesis": m_thesis, "regular": m_regular}


class TestVerdicts:
    def test_breaks_sets_status_and_dispatches_enriched(self, app, db_session, held_position, sample_event):
        m = _run(app, sample_event.id,
                 {"impact": "breaks", "rationale": "Guidance cut kills the margin story.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "broken"

        a = ThesisAssessment.query.filter_by(position_id=held_position.id).one()
        assert a.impact == "breaks" and a.new_status == "broken"
        # enriched channel dispatch, bypassing the tier gate
        m["thesis"].assert_called_once()
        assert m["thesis"].call_args.kwargs.get("bypass_tier") is True
        assert m["thesis"].call_args.args[2] == held_position.user_id  # user_id
        m["sock"].emit.assert_called_once()
        m["regular"].assert_not_called()

    def test_threatens_sets_watch_and_enriched(self, app, db_session, held_position, sample_event):
        m = _run(app, sample_event.id, {"impact": "threatens", "rationale": "Debt raise.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "watch"
        m["thesis"].assert_called_once()

    def test_supports_enriched_but_status_unchanged(self, app, db_session, held_position, sample_event):
        m = _run(app, sample_event.id, {"impact": "supports", "rationale": "Buyback.", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "intact"          # no escalation
        assert ThesisAssessment.query.filter_by(position_id=held_position.id).count() == 1
        m["thesis"].assert_called_once()                        # but still notifies
        m["sock"].emit.assert_called_once()

    def test_neutral_falls_back_to_regular_when_watchlisted(self, app, db_session, held_position, sample_event, sample_user):
        m = _run(app, sample_event.id, {"impact": "neutral", "rationale": "Routine.", "model": "x"},
                 watchlist={sample_user.id})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "intact"
        m["thesis"].assert_not_called()
        m["sock"].emit.assert_not_called()
        m["regular"].assert_called_once()                       # regular tier-gated alert

    def test_neutral_no_regular_when_not_watchlisted(self, app, db_session, held_position, sample_event):
        m = _run(app, sample_event.id, {"impact": "neutral", "rationale": "x", "model": "x"}, watchlist=set())
        m["thesis"].assert_not_called()
        m["regular"].assert_not_called()


class TestEscalateOnly:
    def test_threatens_does_not_downgrade_broken(self, app, db_session, held_position, sample_event):
        held_position.thesis_status = "broken"
        db_session.session.commit()
        _run(app, sample_event.id, {"impact": "threatens", "rationale": "x", "model": "x"})
        db_session.session.refresh(held_position)
        assert held_position.thesis_status == "broken"


class TestGuards:
    def test_idempotent_per_event(self, app, db_session, held_position, sample_event):
        v = {"impact": "breaks", "rationale": "x", "model": "x"}
        m1 = _run(app, sample_event.id, v)
        m2 = _run(app, sample_event.id, v)
        assert m1["assess"].called and not m2["assess"].called
        assert ThesisAssessment.query.filter_by(position_id=held_position.id).count() == 1

    def test_position_without_thesis_ignored(self, app, db_session, sample_user, sample_company, sample_event):
        pos = Position(user_id=sample_user.id, company_id=sample_company.id, thesis=None)
        db_session.session.add(pos)
        db_session.session.commit()
        m = _run(app, sample_event.id, {"impact": "breaks", "rationale": "x", "model": "x"})
        assert not m["assess"].called
        assert ThesisAssessment.query.count() == 0

    def test_llm_failure_falls_back_to_regular(self, app, db_session, held_position, sample_event, sample_user):
        # verdict None (LLM unavailable) → no assessment row, but the deferred
        # user must still get their regular alert if watchlisted.
        m = _run(app, sample_event.id, None, watchlist={sample_user.id})
        assert ThesisAssessment.query.count() == 0
        m["thesis"].assert_not_called()
        m["regular"].assert_called_once()


class TestDeferredUserIds:
    def test_deferred_ids_are_thesis_holders(self, app, db_session, held_position, sample_company):
        with patch("app.services.thesis.llm.is_configured", return_value=True):
            assert engine.deferred_user_ids(sample_company.id) == {held_position.user_id}

    def test_deferred_empty_when_unconfigured(self, app, db_session, held_position, sample_company):
        with patch("app.services.thesis.llm.is_configured", return_value=False):
            assert engine.deferred_user_ids(sample_company.id) == set()


class TestAssessmentEndpoints:
    def test_position_and_recent_feeds(self, app, client, auth_headers, db_session, held_position, sample_event):
        _run(app, sample_event.id, {"impact": "breaks", "rationale": "boom", "model": "x"})
        by_pos = client.get(f"/api/v1/positions/{held_position.id}/assessments",
                            headers=auth_headers).get_json()["assessments"]
        assert len(by_pos) == 1 and by_pos[0]["rationale"] == "boom"
        recent = client.get("/api/v1/positions/assessments?impact=breaks",
                            headers=auth_headers).get_json()["assessments"]
        assert len(recent) == 1
