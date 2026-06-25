"""
worker.py — the analysis worker loop.

A standalone process (separate from the web server). For each persisted
FilingEvent it runs ``analyze_event`` (fundamentals + playbook + LLM + thesis)
and then fans the event out to users — the "single combined message". If
analysis fails or times out, it still fans out the instant briefing alone so an
alert is never silently dropped.

Delivery to web-connected clients works because the emitter is configured with
the Redis ``message_queue`` (the same one the web SocketIO server uses).

Run with::  python -m app.services.analysis.worker   (cwd = services/api)
"""
import logging
import os

log = logging.getLogger(__name__)

# How long to wait on a single dequeue before looping (lets the process exit /
# heartbeat between jobs).
_BRPOP_TIMEOUT = 10


def _make_emitter():
    """A write-only SocketIO bound to the Redis message queue for cross-process emit."""
    from flask_socketio import SocketIO

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        log.warning("worker: REDIS_URL not set — WebSocket fan-out will be local-only")
        return SocketIO()
    return SocketIO(message_queue=redis_url)


def _process(app, emitter, event_id: str) -> None:
    """Analyze one event then fan it out (combined message), with a safe fallback."""
    from app import db
    from app.models.event_analysis import ANALYSIS_FAILED, ANALYSIS_DONE
    from app.models.filing_event import FilingEvent
    from app.services.analysis.engine import analyze_event
    from app.services.realtime.dispatch import fan_out

    with app.app_context():
        try:
            analyze_event(event_id)  # commits with status 'done'
        except Exception:
            db.session.rollback()
            log.exception("worker: analysis failed for %s — falling back to briefing", event_id)
            ev = db.session.get(FilingEvent, event_id)
            if ev is not None and ev.analysis_status != ANALYSIS_DONE:
                ev.analysis_status = ANALYSIS_FAILED
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        # Always deliver exactly once — enriched if done, briefing-only if failed.
        try:
            fan_out(emitter, event_id)
        except Exception:
            log.exception("worker: fan-out failed for %s", event_id)


def _sweep_pending(app, emitter) -> None:
    """On startup, process recent events left 'pending' (queue lost across restarts).

    Bounded to the last 24h as a safety net so a misconfiguration can never cause
    a mass re-fan-out of historical filings (those are backfilled to 'skipped')."""
    from datetime import datetime, timedelta, timezone
    from app.models.filing_event import FilingEvent

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with app.app_context():
        pending = (FilingEvent.query
                   .filter(FilingEvent.analysis_status == "pending")
                   .filter(FilingEvent.created_at >= cutoff)
                   .order_by(FilingEvent.created_at.asc())
                   .limit(100).all())
        ids = [e.id for e in pending]
    if ids:
        log.info("worker: sweeping %d pending event(s) from a prior run", len(ids))
    for event_id in ids:
        _process(app, emitter, event_id)


def run(app=None) -> None:
    from app.services.analysis.queue import ANALYSIS_QUEUE, _redis

    if app is None:
        # This process must not also consume filing:new — only the web service
        # runs the subscriber.
        os.environ["RUN_SUBSCRIBER"] = "false"
        from app import create_app
        app = create_app()

    emitter = _make_emitter()
    log.info("analysis worker starting (queue=%s)", ANALYSIS_QUEUE)

    _sweep_pending(app, emitter)

    client = _redis()
    while True:
        try:
            item = client.brpop(ANALYSIS_QUEUE, timeout=_BRPOP_TIMEOUT)
            if item is None:
                continue  # idle tick
            _, event_id = item
            _process(app, emitter, event_id)
        except Exception:
            import time
            log.exception("analysis worker: loop error — retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    run()
