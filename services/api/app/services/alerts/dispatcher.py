"""Alert dispatcher — fans out filing events to notification channels.

Called from the Redis subscriber after a FilingEvent is persisted and
WebSocket delivery is complete. All work runs on a dedicated thread pool
so the real-time path is never blocked.

Entrypoint: trigger_alerts(...) — the bulk, tier-gated filing alert.
"""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import has_app_context

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='alerts')


def _with_app_context(app, fn, *args) -> None:
    ctx = None
    if not has_app_context():
        ctx = app.app_context()
        ctx.push()
    try:
        fn(app, *args)
    finally:
        if ctx is not None:
            # Executor threads are long-lived: release the thread-local
            # session (and its connection/open transaction) after each task,
            # or it lingers until the thread's next task. Only when we pushed
            # the context — inline callers keep their own session.
            from app import db
            db.session.remove()
            ctx.pop()


# ── Bulk alerts ──────────────────────────────────────────────────────────
def trigger_alerts(app, event_id: str, user_ids: set[str]) -> None:
    """Submit the tier-gated filing alert to the background thread pool.

    Args:
        app: Flask application instance.
        event_id: ID of the persisted FilingEvent.
        user_ids: Users whose watchlists contain the company.
    """
    targets = frozenset(user_ids)
    if not targets:
        return
    _executor.submit(_with_app_context, app, _dispatch_inner, event_id, targets)


def _dispatch(app, event_id: str, user_ids: frozenset[str]) -> None:
    """Run the bulk dispatch synchronously in an app context.

    Retained as the direct, testable entrypoint (the executor path submits
    _dispatch_inner via _with_app_context).
    """
    _with_app_context(app, _dispatch_inner, event_id, user_ids)


def _dispatch_inner(app, event_id: str, user_ids: frozenset[str]) -> None:
    from app import db
    from app.models.alert_preference import AlertPreference
    from app.models.company_read_state import CompanyReadState
    from app.models.filing_event import FilingEvent
    from app.models.user import User

    event = db.session.get(FilingEvent, event_id)
    if event is None:
        log.warning('Alert dispatcher: event %s not found — skipping', event_id)
        return

    # Users with alerts enabled whose tier threshold includes this event
    prefs = AlertPreference.query.filter(
        AlertPreference.user_id.in_(user_ids),
        AlertPreference.enabled.is_(True),
        AlertPreference.max_tier >= event.max_tier,
    ).all()
    if not prefs:
        log.debug('Alert dispatcher: no matching preferences for event %s', event_id)
        return

    # Users who muted this company get no alerts for it
    prefs = _drop_muted(event, prefs, CompanyReadState)
    if not prefs:
        log.debug('Alert dispatcher: all users muted company for event %s', event_id)
        return

    users = {u.id: u for u in User.query.filter(
        User.id.in_([p.user_id for p in prefs])).all()}

    for pref in prefs:
        user = users.get(pref.user_id)
        if user:
            _deliver_to_user(app, db, event, user, pref.channels_json or {})

    log.info('Alert dispatcher: processed event=%s prefs=%d', event_id, len(prefs))


# ── Shared helpers ───────────────────────────────────────────────────────
def _drop_muted(event, prefs, CompanyReadState):
    """Return prefs minus users who muted this event's company."""
    if not event.company_id:
        return prefs
    muted = {
        rs.user_id
        for rs in CompanyReadState.query.filter(
            CompanyReadState.company_id == event.company_id,
            CompanyReadState.user_id.in_([p.user_id for p in prefs]),
            CompanyReadState.muted.is_(True),
        )
    }
    return [p for p in prefs if p.user_id not in muted]


def _deliver_to_user(app, db, event, user, channels: dict) -> None:
    """Deliver to each of a user's enabled channels, with dedup + bookkeeping."""
    from app.models.notification import Notification
    from app.services.alerts.channels import get_channel

    for channel_name, enabled in channels.items():
        if not enabled:
            continue
        channel = get_channel(channel_name)
        if channel is None:
            log.debug('Alert dispatcher: unknown channel %r — skipping', channel_name)
            continue

        existing = Notification.query.filter_by(
            user_id=user.id, filing_event_id=event.id, channel=channel_name,
        ).first()
        if existing:
            log.debug('Alert dispatcher: dedup hit user=%s event=%s channel=%s',
                      user.id, event.id, channel_name)
            continue

        notification = Notification(
            user_id=user.id, filing_event_id=event.id,
            channel=channel_name, status='pending',
        )
        db.session.add(notification)
        db.session.commit()

        try:
            channel.send(user, event, app)
            notification.status = 'sent'
            notification.sent_at = datetime.now(timezone.utc)
            db.session.commit()
        except Exception:
            db.session.rollback()
            notification = Notification.query.filter_by(
                user_id=user.id, filing_event_id=event.id, channel=channel_name,
            ).first()
            if notification:
                notification.status = 'failed'
                notification.error_message = traceback.format_exc()[-500:]
                db.session.commit()
            log.exception('Alert dispatcher: channel %s failed for user=%s event=%s',
                          channel_name, user.id, event.id)
