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
from datetime import datetime

from sqlalchemy.exc import IntegrityError

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
        from app.models.event_type import EventType
        from app.models.catalyst import Catalyst

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

        # Resolve company — create if missing so we never get orphan events
        company = None
        if ticker:
            company = Company.query.filter_by(ticker=ticker).first()
        if company is None and cik:
            company = Company.query.filter_by(cik=cik).first()
        if company is None and cik:
            company = Company.query.filter_by(cik=cik.zfill(10)).first()
        if company is None and ticker:
            company = Company(
                name=data.get("company_name", ticker),
                ticker=ticker,
                cik=cik.zfill(10) if cik else None,
            )
            db.session.add(company)
            db.session.flush()
            log.info("Subscriber: auto-created company ticker=%s cik=%s", ticker, cik)

        max_tier = data.get("max_tier", 3)
        items    = data.get("items", [])
        if not isinstance(max_tier, int):
            max_tier = min((it.get("tier", 3) for it in items), default=3)

        raw_event_types = data.get("event_types", [])
        briefing_data = data.get("briefing") or {}
        deal_terms = briefing_data.get("deal_terms") or {}

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
            event_types_json=raw_event_types,
        )

        for type_name in raw_event_types:
            event.event_types.append(EventType(
                type_name=type_name,
                attributes=deal_terms if deal_terms else None,
            ))

        # Persist catalysts from briefing
        catalysts_data = briefing_data.get("catalysts") or []
        for cat in catalysts_data:
            if not isinstance(cat, dict) or not cat.get("event"):
                continue
            catalyst_date = None
            if cat.get("date"):
                try:
                    catalyst_date = datetime.strptime(cat["date"], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass
            event.catalysts.append(Catalyst(
                event_description=cat["event"],
                catalyst_date=catalyst_date,
                ticker=ticker,
                company_name=data.get("company_name", ""),
            ))

        try:
            db.session.add(event)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            log.debug("Subscriber: duplicate edgar_id=%s — skipped (race)", edgar_id)
            return
        except Exception:
            db.session.rollback()
            log.exception("Subscriber: DB commit failed for edgar_id=%s", edgar_id)
            return

        # Hand off to the analysis worker, which enriches the event with
        # second-order analysis + a thesis update and then fans it out (the
        # "single combined message"). The DB's analysis_status='pending' is the
        # durable record, so a dropped queue entry is recovered by the worker's
        # startup sweep. When analysis is disabled, fan out immediately (legacy).
        from app.services.analysis.queue import analysis_enabled, enqueue

        if analysis_enabled():
            enqueue(event.id)
            log.info(
                "Subscriber: stored + queued edgar_id=%s ticker=%s tier=%d",
                edgar_id, ticker or "—", max_tier,
            )
        else:
            from app.services.realtime.dispatch import fan_out
            user_ids = fan_out(socketio, event.id)
            log.info(
                "Subscriber: stored + emitted (analysis off) edgar_id=%s ticker=%s tier=%d users=%d",
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
                client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_keepalive=True,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
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
