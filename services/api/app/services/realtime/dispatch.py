"""
dispatch.py — shared WebSocket fan-out + alert trigger for a persisted event.

Extracted from the Redis subscriber so the same delivery logic can be invoked
from the analysis worker (which fans out *after* attaching second-order
analysis). Must be called inside an app context.

When emitting from a separate process (the worker), the ``socketio`` instance
must be configured with the Redis ``message_queue`` so emits reach clients
connected to the web server (see ``create_app`` and ``analysis/worker.py``).
"""
import logging

from flask import current_app

log = logging.getLogger(__name__)


def fan_out(socketio, event_id: str) -> set:
    """Emit ``filing_event`` to watchers + public room and trigger alerts.

    Returns the set of user IDs the event was personalized to.
    """
    from app import db
    from app.models.filing_event import FilingEvent
    from app.models.watchlist import Watchlist

    event = db.session.get(FilingEvent, event_id)
    if event is None:
        log.warning("dispatch: event %s not found — nothing to fan out", event_id)
        return set()

    payload = event.to_ws_payload()

    if event.company_id:
        watchlists = Watchlist.query.filter(
            Watchlist.companies.any(id=event.company_id)
        ).all()
        user_ids = {wl.user_id for wl in watchlists}
    else:
        user_ids = set()

    for uid in user_ids:
        socketio.emit("filing_event", payload, room=f"user:{uid}", namespace="/feed")
    # Public room for unauthenticated direct-feed clients (backward compat).
    socketio.emit("filing_event", payload, room="public", namespace="/feed")

    from app.services.alerts.dispatcher import trigger_alerts
    trigger_alerts(current_app._get_current_object(), event_id, user_ids)

    log.info("dispatch: fanned out event=%s ticker=%s users=%d status=%s",
             event_id, event.ticker or "—", len(user_ids), event.analysis_status)
    return user_ids
