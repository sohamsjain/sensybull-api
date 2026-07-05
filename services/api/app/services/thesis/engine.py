# services/api/app/services/thesis/engine.py
"""
Thesis-break engine.

When a filing event is persisted for a company some user holds, evaluate
the event against each holder's thesis in two stages:

  1. TRIAGE — a cheap model over the pre-digested briefing decides whether
     the filing bears on the thesis at all.
  2. DEEP PASS — on any non-neutral triage, a larger model re-judges over
     the FULL filing text, the measured price reaction, position
     direction/size, and the structured thesis (per-assumption verdicts,
     confidence, verbatim citations). If the deep pass fails, the triage
     verdict stands — degraded, never dropped.

The final verdict drives:
  1. an escalation of the position's thesis_status (threatens→watch,
     breaks→broken; escalate-only),
  2. a real-time `thesis_alert` socket push, and
  3. a thesis-aware alert over the user's notification channels (email, etc.)
     — which is why these users are DEFERRED from the regular bulk dispatch
     in the subscriber: they get the enriched variant instead.

For a `neutral` verdict (or if the LLM is unavailable), the user falls back
to the regular tier-gated filing alert, so deferring them never drops an
alert they'd otherwise have received.

The same two-stage judgment also powers RETROACTIVE assessments: when a
thesis is created or edited, the engine backtests it against the company's
recent filing history. Retroactive rows are informational — they never move
thesis_status and never fire alerts.

Runs on its own thread pool so the Redis subscriber / socket fan-out path
is never blocked. Failure here must never affect event delivery.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import has_app_context

from app.services.alerts import thesis_format

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thesis")

# thesis_status severity ordering — the engine only ever escalates.
_STATUS_RANK = {"intact": 0, "watch": 1, "broken": 2}
# LLM verdict → the status it would imply
_IMPACT_TARGET = {"breaks": "broken", "threatens": "watch"}

# How many historical filings a new/edited thesis is backtested against.
RETRO_LIMIT = 10


def deferred_user_ids(company_id: str) -> set[str]:
    """Users the engine will handle for this company (held + thesis).

    The subscriber excludes these from the regular bulk dispatch so they
    receive the thesis-aware variant. Returns empty when the LLM is
    unconfigured (nothing will be assessed, so nobody is deferred).
    """
    from app.models.position import Position
    from app.services.thesis import llm

    if not company_id or not llm.is_configured():
        return set()
    rows = (
        Position.query
        .filter(Position.company_id == company_id)
        .filter(Position.thesis.isnot(None))
        .filter(Position.thesis != "")
        .with_entities(Position.user_id)
        .all()
    )
    return {r[0] for r in rows}


def trigger_thesis_assessments(app, event_id: str,
                               watchlist_user_ids: frozenset[str] = frozenset()) -> None:
    """Submit thesis assessment for a persisted event to the background pool."""
    _executor.submit(_run, app, event_id, frozenset(watchlist_user_ids))


def trigger_retroactive_assessments(app, position_id: str,
                                    limit: int = RETRO_LIMIT) -> None:
    """Backtest a new/edited thesis against the company's recent filings."""
    _executor.submit(_run_retroactive, app, position_id, limit)


def _with_app_context(app, fn, *args) -> None:
    ctx = None
    if not has_app_context():
        ctx = app.app_context()
        ctx.push()
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("thesis.engine: unhandled error in %s", fn.__name__)
    finally:
        if ctx is not None:
            ctx.pop()


def _run(app, event_id: str, watchlist_user_ids: frozenset[str]) -> None:
    _with_app_context(app, _assess_event, app, event_id, watchlist_user_ids)


def _run_retroactive(app, position_id: str, limit: int) -> None:
    _with_app_context(app, _assess_retroactive, position_id, limit)


def _assess_event(app, event_id: str, watchlist_user_ids: frozenset[str]) -> None:
    from app import db
    from app.models.filing_event import FilingEvent
    from app.models.position import Position

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

    deferred_users = {p.user_id for p in positions}
    handled: set[str] = set()
    payload = event.to_ws_payload()

    try:
        for position in positions:
            try:
                _assess_one(app, db, event, position, payload, watchlist_user_ids)
                handled.add(position.user_id)
            except Exception:  # noqa: BLE001
                log.exception("thesis.engine: failed position=%s event=%s",
                              position.id, event.id)
    finally:
        # Guarantee: any deferred user we didn't handle (crash / LLM outage)
        # still gets the regular alert they'd have received, if they're a
        # watchlist member.
        from app.services.alerts.dispatcher import trigger_alerts
        for uid in deferred_users - handled:
            if uid in watchlist_user_ids:
                trigger_alerts(app, event_id, {uid})


def _filing_text(event) -> str:
    """Assemble the full parsed filing text stored at ingest time.

    items_json rows carry the parsed text of each 8-K item (or equivalent);
    this is the source the briefing was compressed from, and what the deep
    pass reads instead of the summary-of-a-summary.
    """
    from app.services.thesis.llm import FILING_TEXT_CAP

    parts = []
    for item in (event.items_json or []):
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("number") or ""
        text = item.get("text") or ""
        if text:
            parts.append(f"[{title}]\n{text}" if title else text)
    return "\n\n".join(parts)[:FILING_TEXT_CAP]


def _price_summary(event) -> str | None:
    """Human-readable summary of measured price reactions, e.g.
    '5m: +1.2%, 1h: -3.4% (explosive)'. None if nothing measured yet."""
    from app.models.price_reaction import INTERVALS

    done = [r for r in event.price_reactions
            if r.status == "done" and r.pct_change is not None]
    if not done:
        return None
    done.sort(key=lambda r: INTERVALS.get(r.interval, 0))
    parts = []
    for r in done:
        s = f"{r.interval}: {r.pct_change:+.1f}%"
        if r.is_explosive:
            s += " (explosive)"
        parts.append(s)
    return ", ".join(parts)


def _judge(position, event, payload) -> dict | None:
    """Two-stage judgment. Returns the stored-verdict dict or None.

    Shape: {impact, rationale, model, stage, triage_impact, confidence,
    assumption_verdicts, citations} — triage-only verdicts carry None/[]
    for the deep-pass fields.
    """
    from app.services.thesis import llm

    triage = llm.assess_thesis(position.thesis, position.direction, payload)
    if triage is None:
        return None

    verdict = {
        **triage,
        "stage": "triage",
        "triage_impact": None,
        "confidence": None,
        "assumption_verdicts": [],
        "citations": [],
    }
    if triage["impact"] == "neutral":
        return verdict

    filing_text = _filing_text(event)
    deep = llm.assess_thesis_deep(
        position.thesis, position.thesis_structured, position.direction,
        position.shares, position.cost_basis, payload,
        filing_text, _price_summary(event),
    )
    if deep is None:
        # Deep pass unavailable — the triage verdict stands.
        return verdict
    return {**deep, "stage": "deep", "triage_impact": triage["impact"]}


def _store_assessment(db, event, position, verdict, prior_status: str,
                      new_status: str, retroactive: bool):
    from app.models.thesis_assessment import ThesisAssessment

    assessment = ThesisAssessment(
        position_id=position.id,
        filing_event_id=event.id,
        user_id=position.user_id,
        impact=verdict["impact"],
        rationale=verdict.get("rationale"),
        stage=verdict["stage"],
        triage_impact=verdict.get("triage_impact"),
        confidence=verdict.get("confidence"),
        assumption_verdicts_json=verdict.get("assumption_verdicts") or None,
        citations_json=verdict.get("citations") or None,
        retroactive=retroactive,
        thesis_version=position.thesis_version,
        prior_status=prior_status,
        new_status=new_status,
        model=verdict.get("model"),
    )
    db.session.add(assessment)
    return assessment


def _assess_one(app, db, event, position, payload, watchlist_user_ids) -> None:
    from app.models.thesis_assessment import ThesisAssessment
    from app.services.alerts.dispatcher import dispatch_thesis_alert, trigger_alerts

    # Idempotent per (position, event) — a re-delivered event was already
    # assessed and dispatched on the first pass.
    if ThesisAssessment.query.filter_by(
        position_id=position.id, filing_event_id=event.id,
    ).first():
        return

    verdict = _judge(position, event, payload)
    impact = verdict["impact"] if verdict else "neutral"

    # Only a real verdict produces a stored assessment + status change.
    if verdict is not None:
        prior_status = position.thesis_status
        new_status = _escalate(prior_status, impact)
        assessment = _store_assessment(db, event, position, verdict,
                                       prior_status, new_status, retroactive=False)
        if new_status != prior_status:
            from datetime import datetime, timezone
            position.thesis_status = new_status
            position.thesis_reviewed_at = datetime.now(timezone.utc)
        db.session.flush()
        position.last_assessment_id = assessment.id
        db.session.commit()
        log.info("thesis.engine: position=%s event=%s stage=%s impact=%s status=%s->%s",
                 position.id, event.id, verdict["stage"], impact,
                 prior_status, new_status)

    adict = {
        "impact": impact,
        "rationale": verdict.get("rationale") if verdict else None,
        "thesis_status": position.thesis_status,
        "stage": verdict.get("stage") if verdict else None,
        "confidence": verdict.get("confidence") if verdict else None,
        "assumption_verdicts": (verdict.get("assumption_verdicts") or []) if verdict else [],
        "citations": (verdict.get("citations") or []) if verdict else [],
    }

    if impact in thesis_format.NOTIFY_IMPACTS:
        _emit_socket(position, event, adict)
        # Enriched, thesis-aware alert — bypasses the tier gate.
        dispatch_thesis_alert(app, event.id, position.user_id, adict, bypass_tier=True)
    else:
        # neutral / no verdict → regular tier-gated alert if watchlisted.
        if position.user_id in watchlist_user_ids:
            trigger_alerts(app, event.id, {position.user_id})


def _assess_retroactive(position_id: str, limit: int) -> None:
    """Backtest the position's current thesis against recent filings.

    Informational only: rows are flagged retroactive, thesis_status never
    moves, and no alerts or sockets fire. Events that already carry a live
    assessment for this position are skipped (the unique constraint per
    (position, event) also guarantees this).
    """
    from app import db
    from app.models.filing_event import FilingEvent
    from app.models.position import Position
    from app.models.thesis_assessment import ThesisAssessment

    position = db.session.get(Position, position_id)
    if position is None or not position.thesis:
        return

    events = (
        FilingEvent.query
        .filter(FilingEvent.company_id == position.company_id)
        .order_by(FilingEvent.filing_date.desc())
        .limit(limit)
        .all()
    )
    for event in events:
        try:
            if ThesisAssessment.query.filter_by(
                position_id=position.id, filing_event_id=event.id,
            ).first():
                continue
            verdict = _judge(position, event, event.to_ws_payload())
            if verdict is None:
                continue
            _store_assessment(db, event, position, verdict,
                              prior_status=position.thesis_status,
                              new_status=position.thesis_status,
                              retroactive=True)
            db.session.commit()
        except Exception:  # noqa: BLE001
            db.session.rollback()
            log.exception("thesis.engine: retroactive failed position=%s event=%s",
                          position.id, event.id)


def _escalate(current: str, impact: str) -> str:
    """Return the new thesis_status — only ever moves toward 'broken'."""
    target = _IMPACT_TARGET.get(impact)
    if target is None:
        return current  # supports / neutral never change status
    if _STATUS_RANK.get(target, 0) > _STATUS_RANK.get(current, 0):
        return target
    return current


def _emit_socket(position, event, adict) -> None:
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
                "impact": adict["impact"],
                "rationale": adict["rationale"],
                "confidence": adict.get("confidence"),
                "stage": adict.get("stage"),
                "thesis_status": position.thesis_status,
                "filing_event_id": event.id,
                "headline": (event.briefing_json or {}).get("headline"),
            },
            room=f"user:{position.user_id}",
            namespace="/feed",
        )
    except Exception:  # noqa: BLE001 — alerting is best-effort
        log.exception("thesis.engine: failed to emit thesis_alert for position=%s", position.id)
