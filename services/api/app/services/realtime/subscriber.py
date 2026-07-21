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
import os
import threading
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.services.realtime import pr_dedup

log = logging.getLogger(__name__)


def _suppress_wrapper_8ks() -> bool:
    """Escape hatch: PR_SUPPRESS_8K=0 keeps backfill but publishes every
    8-K normally (use while tuning the fingerprint match on real data)."""
    return os.environ.get("PR_SUPPRESS_8K", "1").strip().lower() not in ("0", "false", "no")


def _watchlist_user_ids(company) -> set:
    from app.models.watchlist import Watchlist

    if not company:
        return set()
    watchlists = Watchlist.query.filter(
        Watchlist.companies.any(id=company.id)
    ).all()
    return {wl.user_id for wl in watchlists}


def _emit_to_rooms(socketio, event_name: str, payload: dict, user_ids: set) -> None:
    for uid in user_ids:
        socketio.emit(event_name, payload, room=f"user:{uid}", namespace="/feed")
    socketio.emit(event_name, payload, room="public", namespace="/feed")


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

        signal_type = data.get("signal_type", "8-K")

        # ── Cross-source dedup (press releases ↔ SEC filings) ────────────
        if signal_type == "PR" and ticker:
            own_fp = {
                "exact": data.get("content_fingerprint", ""),
                "headline": data.get("headline_fingerprint", ""),
                "simhash": data.get("content_simhash", ""),
            }
            # Same release on a second wire (ingest's fingerprint file is
            # ephemeral — the DB is the durable backstop)
            dup = pr_dedup.find_matching_event(
                ticker, [own_fp], pr_dedup.CROSS_WIRE_WINDOW_DAYS, ["PR"])
            if dup:
                log.info("Subscriber: pr_dropped_cross_wire_dup edgar_id=%s of=%s",
                         edgar_id, dup.edgar_id)
                return
            # The SEC filing for this announcement already published — the
            # wire copy adds nothing.
            filing = pr_dedup.find_matching_event(
                ticker, [own_fp], pr_dedup.PR_TO_FILING_WINDOW_DAYS, ["8-K", "8-K/A"])
            if filing:
                log.info("Subscriber: pr_dropped_matching_8k edgar_id=%s filing=%s",
                         edgar_id, filing.edgar_id)
                return

        if signal_type in ("8-K", "8-K/A") and ticker:
            # Redelivery of a filing that already backfilled a PR event
            if FilingEvent.query.filter_by(related_edgar_id=edgar_id).first():
                log.debug("Subscriber: 8-K already backfilled a PR edgar_id=%s — skipped",
                          edgar_id)
                return
            exhibit_fps = data.get("exhibit_fingerprints") or []
            pr_event = pr_dedup.find_matching_event(
                ticker, exhibit_fps, pr_dedup.PR_TO_FILING_WINDOW_DAYS, ["PR"])
            if pr_event:
                # Backfill the PR event with the authoritative source — but
                # never overwrite an earlier filing's link (an 8-K/A that
                # refiles the same release would otherwise clobber it)
                if not pr_event.related_edgar_id:
                    pr_event.related_edgar_id = edgar_id
                    pr_event.related_filing_url = data.get("edgar_url") or None
                    pr_event.related_accession_number = data.get("accession_number") or None
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        log.exception("Subscriber: PR backfill failed for %s", pr_event.id)
                    else:
                        update_payload = pr_event.to_ws_payload()
                        _emit_to_rooms(socketio, "filing_event_update", update_payload,
                                       _watchlist_user_ids(pr_event.company))
                        log.info("Subscriber: backfilled PR %s with filing %s",
                                 pr_event.edgar_id, edgar_id)
                # Suppress the duplicate feed item only when the filing is a
                # pure press-release wrapper — any substantive item beyond
                # the PR means the 8-K still publishes.
                if (_suppress_wrapper_8ks()
                        and pr_dedup.is_wrapper_only(data.get("items", []))):
                    log.info("Subscriber: 8k_suppressed_pr_dup edgar_id=%s pr=%s",
                             edgar_id, pr_event.edgar_id)
                    return

        # ── End cross-source dedup ────────────────────────────────────────

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

        # Late-lifecycle filings (Form 15/25 after a delisting closes) arrive
        # without a ticker because SEC's ticker file drops deregistered
        # companies. Our company table never deletes tickers, so the CIK
        # match still knows it — backfill so the event keeps its ticker,
        # logo, and price reactions.
        if not ticker and company is not None and company.ticker:
            ticker = company.ticker
            log.info("Subscriber: backfilled ticker=%s from company match (cik=%s)",
                     ticker, cik)

        max_tier = data.get("max_tier", 3)
        items    = data.get("items", [])
        if not isinstance(max_tier, int):
            max_tier = min((it.get("tier", 3) for it in items), default=3)

        raw_event_types = data.get("event_types", [])
        briefing_data = data.get("briefing") or {}
        deal_terms = briefing_data.get("deal_terms") or {}

        event = FilingEvent(
            edgar_id=edgar_id,
            signal_type=signal_type,
            source=data.get("source") or "edgar",
            issuer_name=data.get("issuer_name") or None,
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
            content_fingerprint=data.get("content_fingerprint") or None,
            headline_fingerprint=data.get("headline_fingerprint") or None,
            content_simhash=data.get("content_simhash") or None,
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

        _schedule_price_reactions(db, event)

        payload = event.to_ws_payload()

        # Fan-out: every user who has this company in a watchlist, plus the
        # public room (unauthenticated direct-feed clients, client.html)
        user_ids = _watchlist_user_ids(company)
        _emit_to_rooms(socketio, "filing_event", payload, user_ids)

        # Dispatch alerts (async — does not block the subscriber)
        from app.services.alerts.dispatcher import trigger_alerts
        trigger_alerts(app, event.id, user_ids)

        log.info(
            "Subscriber: stored + emitted edgar_id=%s ticker=%s tier=%d users=%d",
            edgar_id, ticker or "—", max_tier, len(user_ids),
        )


def _schedule_price_reactions(db, event) -> None:
    """Queue the six interval price measurements for a freshly stored event.

    Rows are the durable work queue for the reaction worker (see
    app/services/market_data/reaction_worker.py). The unique constraint on
    (filing_event_id, interval) makes this idempotent; failure here must
    never block event delivery.
    """
    from app.models.price_reaction import INTERVALS, PriceReaction

    if not event.ticker or not event.filing_date:
        return
    try:
        for interval, seconds in INTERVALS.items():
            db.session.add(PriceReaction(
                filing_event_id=event.id,
                ticker=event.ticker,
                interval=interval,
                measure_at=event.filing_date + timedelta(seconds=seconds),
            ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    except Exception:
        db.session.rollback()
        log.exception("Failed to schedule price reactions for event %s", event.id)


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
