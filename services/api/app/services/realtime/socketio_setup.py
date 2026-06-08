# services/api/app/services/realtime/socketio_setup.py
"""
Flask-SocketIO instance and authenticated namespace.

Import `socketio` from here anywhere inside the api service.
"""

import logging
import os

from flask import request
from flask_jwt_extended import decode_token
from flask_socketio import SocketIO, emit, join_room, leave_room

log = logging.getLogger(__name__)

socketio = SocketIO(
    async_mode=os.environ.get("SOCKETIO_ASYNC_MODE", "eventlet"),
    logger=False,
    engineio_logger=False,
)


@socketio.on("connect", namespace="/feed")
def on_connect(auth):
    """
    Authenticate on connect. Clients send:
        { token: "<JWT access token>" }   ← via auth dict (Socket.IO v5)

    On success: join the user's personal room and the public room.
    On failure: disconnect immediately.
    """
    token = (auth or {}).get("token", "")
    if not token:
        # Unauthenticated — join public room only (backward compat with client.html)
        join_room("public")
        log.debug("WS: unauthenticated client joined public room sid=%s", request.sid)
        return True

    try:
        decoded = decode_token(token)
        user_id = decoded["sub"]
        join_room(f"user:{user_id}")
        join_room("public")
        log.info("WS: user=%s connected sid=%s", user_id, request.sid)
        emit("connected", {"status": "ok", "user_id": user_id})

        # Send missed events (last 100 Tier 1-2 events for user's watchlist)
        _replay_missed(user_id)
    except Exception:
        log.warning("WS: invalid JWT on connect sid=%s", request.sid)
        return False  # reject connection


@socketio.on("disconnect", namespace="/feed")
def on_disconnect():
    log.debug("WS: client disconnected sid=%s", request.sid)


@socketio.on("subscribe_ticker", namespace="/feed")
def on_subscribe_ticker(data):
    """
    Allow clients to subscribe to a specific ticker room.
    Authenticated clients only.
    """
    ticker = (data or {}).get("ticker", "").upper()
    if ticker:
        join_room(f"ticker:{ticker}")
        emit("subscribed", {"ticker": ticker})


def _replay_missed(user_id: str) -> None:
    """
    Emit the last 50 Tier 1 + Tier 2 events for the user's watchlist companies.
    Called immediately after a client authenticates so reconnects don't miss events.
    """
    try:
        from app.models.filing_event import FilingEvent
        from app.models.watchlist import Watchlist

        watchlists = Watchlist.query.filter_by(user_id=user_id).all()
        company_ids = {c.id for wl in watchlists for c in wl.companies}
        if not company_ids:
            return

        recent = (
            FilingEvent.query
            .filter(FilingEvent.company_id.in_(company_ids))
            .filter(FilingEvent.max_tier <= 2)
            .order_by(FilingEvent.filing_date.desc())
            .limit(50)
            .all()
        )
        for ev in reversed(recent):  # chronological order
            emit("filing_event", ev.to_ws_payload(), namespace="/feed")
        if recent:
            log.debug("WS: replayed %d events to user=%s", len(recent), user_id)
    except Exception:
        log.exception("WS: replay failed for user=%s", user_id)
