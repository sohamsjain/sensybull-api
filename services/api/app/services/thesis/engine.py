# services/api/app/services/thesis/engine.py
"""
Thesis-break engine.

When a filing event is persisted for a company some user holds, evaluate
the event against each holder's thesis (via Groq) and, when the thesis is
threatened or broken, escalate the position's thesis_status and push a
real-time `thesis_alert`.

Runs on its own thread pool so the Redis subscriber / socket fan-out path
is never blocked — mirrors app/services/alerts/dispatcher.py. Failure here
must never affect event delivery.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import has_app_context

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thesis")

# thesis_status severity ordering — the engine only ever escalates.
_STATUS_RANK = {"intact": 0, "watch": 1, "broken": 2}
# LLM verdict → the status it would imply
_IMPACT_TARGET = {"breaks": "broken", "threatens": "watch"}
_ALERTING_IMPACTS = {"threatens", "breaks"}


def trigger_thesis_assessments(app, event_id: str) -> None:
    """Submit thesis assessment for a persisted event to the background pool."""
    _executor.submit(_run, app, event_id)


def _run(app, event_id: str) -> None:
    ctx = None
    if not has_app_context():
        ctx = app.app_context()
        ctx.push()
    try:
        _assess_event(app, event_id)
    except Exception:  # noqa: BLE001
        log.exception("thesis.engine: unhandled error for event %s", event_id)
    finally:
        if ctx is not None:
            ctx.pop()


def _assess_event(app, event_id: str) -> None:
    from app import db
    from app.models.filing_event import FilingEvent
    from app.models.position import Position
    from app.models.thesis_assessment import ThesisAssessment
    from app.services.thesis import llm

    event = db.session.get(FilingEvent, event_id)
    if event is None or not event.company_id:
        return

    # Holders of this company who wrote down a thesis. A held position is
    # watched by definition — independent of watchlist membership.
    positions = (
        Position.query
        .filter(Position.company_id == event.company_id)
        .filter(Position.thesis.isnot(None))
        .filter(Position.thesis != "")
        .all()
    )
    if not positions:
        return

    if not llm.is_configured():
        log.debug("thesis.engine: Groq not configured — skipping %d positions", len(positions))
        return

    payload = event.to_ws_payload()

    for position in positions:
        # Idempotent per (position, event)
        existing = ThesisAssessment.query.filter_by(
            position_id=position.id, filing_event_id=event.id,
        ).first()
        if existing:
            continue

        verdict = llm.assess_thesis(position.thesis, position.direction, payload)
        if verdict is None:
            continue

        impact = verdict["impact"]
        prior_status = position.thesis_status
        new_status = _escalate(prior_status, impact)

        assessment = ThesisAssessment(
            position_id=position.id,
            filing_event_id=event.id,
            user_id=position.user_id,
            impact=impact,
            rationale=verdict.get("rationale"),
            prior_status=prior_status,
            new_status=new_status,
            model=verdict.get("model"),
        )
        db.session.add(assessment)

        if new_status != prior_status:
            from datetime import datetime, timezone
            position.thesis_status = new_status
            position.thesis_reviewed_at = datetime.now(timezone.utc)

        try:
            db.session.flush()  # assign assessment.id before we reference it
            position.last_assessment_id = assessment.id
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("thesis.engine: commit failed position=%s event=%s",
                          position.id, event.id)
            continue

        log.info(
            "thesis.engine: position=%s event=%s impact=%s status=%s->%s",
            position.id, event.id, impact, prior_status, new_status,
        )

        if impact in _ALERTING_IMPACTS:
            _emit_alert(position, event, assessment)


def _escalate(current: str, impact: str) -> str:
    """Return the new thesis_status — only ever moves toward 'broken'."""
    target = _IMPACT_TARGET.get(impact)
    if target is None:
        return current  # supports / neutral never change status
    if _STATUS_RANK.get(target, 0) > _STATUS_RANK.get(current, 0):
        return target
    return current


def _emit_alert(position, event, assessment) -> None:
    """Push a real-time thesis alert to the position owner's socket room."""
    try:
        from app.services.realtime.socketio_setup import socketio
        socketio.emit(
            "thesis_alert",
            {
                "position_id": position.id,
                "company_id": position.company_id,
                "ticker": event.ticker,
                "company_name": event.company_name,
                "impact": assessment.impact,
                "rationale": assessment.rationale,
                "thesis_status": position.thesis_status,
                "filing_event_id": event.id,
                "headline": (event.briefing_json or {}).get("headline"),
            },
            room=f"user:{position.user_id}",
            namespace="/feed",
        )
    except Exception:  # noqa: BLE001 — alerting is best-effort
        log.exception("thesis.engine: failed to emit thesis_alert for position=%s", position.id)
