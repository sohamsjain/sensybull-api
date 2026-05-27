# services/api/app/services/realtime/subscriber.py
"""
Redis pub/sub subscriber.

Runs in a background daemon thread (started once in create_app).
Receives FilingEvent JSON from the `filing:new` channel, persists the
event, and fans it out to connected WebSocket clients whose watchlists
contain the relevant company.
"""

import json
import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _parse_filing_date(iso: str):
    """Parse an ISO-8601 timestamp string to a timezone-aware datetime.
    Returns None on failure."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _handle_event(app, socketio, raw_message: str) -> None:
    """Process a single Redis message within an app context."""
    with app.app_context():
        from app import db
        from app.models.company import Company
        from app.models.filing_event import FilingEvent
        from app.models.watchlist import Watchlist

        try:
            data = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            log.warning("Subscriber: invalid JSON in filing:new — skipped")
            return

        edgar_id = data.get("edgar_id", "")
        ticker   = data.get("ticker", "").upper() or None
        cik      = data.get("cik", "")

        # Idempotency: skip if already stored
        if FilingEvent.query.filter_by(edgar_id=edgar_id).first():
            log.debug("Subscriber: duplicate edgar_id=%s — skipped", edgar_id)
            return

        # Resolve company_id (best-effort — NULL if not yet in companies table)
        company = None
        if ticker:
            company = Company.query.filter_by(ticker=ticker).first()
        if company is None and cik:
            company = Company.query.filter_by(cik=cik).first()
        if company is None and cik:
            # Also try zero-padded CIK
            company = Company.query.filter_by(cik=cik.zfill(10)).first()

        max_tier = data.get("max_tier", 3)
        items    = data.get("items", [])
        if not isinstance(max_tier, int):
            max_tier = min((it.get("tier", 3) for it in items), default=3)

        event = FilingEvent(
            edgar_id=edgar_id,
            signal_type=data.get("signal_type", "8-K"),
            company_id=company.id if company else None,
            cik=cik,
            ticker=ticker,
            company_name=data.get("company_name", ""),
            filing_date=_parse_filing_date(data.get("filing_date")),
            edgar_url=data.get("edgar_url") or None,
            accession_number=data.get("accession_number") or None,
            max_tier=max_tier,
            items_json=items,
            exhibits_json=data.get("exhibits", []),
            briefing_json=data.get("briefing"),
            event_types_json=data.get("event_types", []),
        )

        try:
            db.session.add(event)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Subscriber: DB commit failed for edgar_id=%s", edgar_id)
            return

        payload = event.to_ws_payload()

        # Fan-out: find every user who has this company in a watchlist
        if company:
            watchlists = Watchlist.query.filter(
                Watchlist.companies.any(id=company.id)
            ).all()
            user_ids = {wl.user_id for wl in watchlists}
        else:
            # Company not in DB — no personalized delivery yet.
            # Still emit to a public "unfiltered" room for direct-feed clients.
            user_ids = set()

        for uid in user_ids:
            socketio.emit(
                "filing_event",
                payload,
                room=f"user:{uid}",
                namespace="/feed",
            )
            log.debug("Emitted filing_event to user:%s (tier=%d)", uid, max_tier)

        # Also emit to the public room (for unauthenticated direct-feed clients
        # and the existing client.html, keeping backward compat)
        socketio.emit("filing_event", payload, room="public", namespace="/feed")

        log.info(
            "Subscriber: stored + emitted edgar_id=%s ticker=%s tier=%d users=%d",
            edgar_id, ticker or "—", max_tier, len(user_ids),
        )


def start_subscriber(app, socketio) -> threading.Thread:
    """
    Spawn a daemon thread that subscribes to Redis `filing:new` forever.
    Call once from create_app. Safe to call multiple times — only the
    first call per process spawns a thread.
    """
    import os
    import redis

    _lock = getattr(start_subscriber, "_lock", None)
    if _lock is None:
        start_subscriber._lock = threading.Lock()
        start_subscriber._started = False

    with start_subscriber._lock:
        if getattr(start_subscriber, "_started", False):
            return None
        start_subscriber._started = True

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    def _run():
        log.info("Redis subscriber starting (url=%s)", redis_url)
        while True:
            try:
                client = redis.from_url(redis_url, decode_responses=True)
                pubsub = client.pubsub()
                pubsub.subscribe("filing:new")
                log.info("Redis subscriber connected — listening on filing:new")
                for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        _handle_event(app, socketio, message["data"])
                    except Exception:
                        log.exception("Subscriber: unhandled error processing message")
            except Exception:
                import time
                log.exception("Subscriber: connection lost — reconnecting in 5s")
                time.sleep(5)

    t = threading.Thread(target=_run, daemon=True, name="redis-subscriber")
    t.start()
    log.info("Redis subscriber thread started")
    return t
