"""Alert dispatcher — fans out filing events to notification channels.

Called from the Redis subscriber after a FilingEvent is persisted and
WebSocket delivery is complete. All work runs on a dedicated thread pool
so the real-time path is never blocked.
"""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import has_app_context

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='alerts')


def trigger_alerts(app, event_id: str, user_ids: set[str]) -> None:
    """Submit alert dispatch to the background thread pool.

    Args:
        app: Flask application instance.
        event_id: ID of the persisted FilingEvent (not the ORM object —
                  avoids DetachedInstanceError across threads).
        user_ids: Set of user IDs whose watchlists contain the company.
    """
    if not user_ids:
        return
    _executor.submit(_dispatch, app, event_id, frozenset(user_ids))


def _dispatch(app, event_id: str, user_ids: frozenset[str]) -> None:
    """Query matching alert preferences and deliver to each enabled channel."""
    ctx = None
    if not has_app_context():
        ctx = app.app_context()
        ctx.push()
    try:
        _dispatch_inner(app, event_id, user_ids)
    finally:
        if ctx is not None:
            ctx.pop()


def _dispatch_inner(app, event_id: str, user_ids: frozenset[str]) -> None:
    from app import db
    from app.models.alert_preference import AlertPreference
    from app.models.company_read_state import CompanyReadState
    from app.models.filing_event import FilingEvent
    from app.models.notification import Notification
    from app.models.user import User
    from app.services.alerts.channels import get_channel

    event = db.session.get(FilingEvent, event_id)
    if event is None:
        log.warning('Alert dispatcher: event %s not found — skipping', event_id)
        return

    # Find users who have alerts enabled and whose tier threshold includes this event
    prefs = AlertPreference.query.filter(
        AlertPreference.user_id.in_(user_ids),
        AlertPreference.enabled.is_(True),
        AlertPreference.max_tier >= event.max_tier,
    ).all()

    if not prefs:
        log.debug('Alert dispatcher: no matching preferences for event %s', event_id)
        return

    # Users who muted this company's chat get no alerts for it
    if event.company_id:
        muted_user_ids = {
            rs.user_id
            for rs in CompanyReadState.query.filter(
                CompanyReadState.company_id == event.company_id,
                CompanyReadState.user_id.in_([p.user_id for p in prefs]),
                CompanyReadState.muted.is_(True),
            )
        }
        if muted_user_ids:
            prefs = [p for p in prefs if p.user_id not in muted_user_ids]
            if not prefs:
                log.debug('Alert dispatcher: all users muted company for event %s', event_id)
                return

    # Build user lookup
    pref_user_ids = [p.user_id for p in prefs]
    users = {u.id: u for u in User.query.filter(User.id.in_(pref_user_ids)).all()}

    for pref in prefs:
        user = users.get(pref.user_id)
        if not user:
            continue

        channels = pref.channels_json or {}
        for channel_name, enabled in channels.items():
            if not enabled:
                continue

            channel = get_channel(channel_name)
            if channel is None:
                log.debug('Alert dispatcher: unknown channel %r — skipping', channel_name)
                continue

            # Check dedup before creating
            existing = Notification.query.filter_by(
                user_id=user.id,
                filing_event_id=event.id,
                channel=channel_name,
            ).first()
            if existing:
                log.debug(
                    'Alert dispatcher: dedup hit user=%s event=%s channel=%s',
                    user.id, event.id, channel_name,
                )
                continue

            # Create notification record with pending status and commit
            notification = Notification(
                user_id=user.id,
                filing_event_id=event.id,
                channel=channel_name,
                status='pending',
            )
            db.session.add(notification)
            db.session.commit()

            # Attempt delivery
            try:
                channel.send(user, event, app)
                notification.status = 'sent'
                notification.sent_at = datetime.now(timezone.utc)
                db.session.commit()
            except Exception:
                db.session.rollback()
                # Re-fetch after rollback to update status
                notification = Notification.query.filter_by(
                    user_id=user.id,
                    filing_event_id=event.id,
                    channel=channel_name,
                ).first()
                if notification:
                    notification.status = 'failed'
                    notification.error_message = traceback.format_exc()[-500:]
                    db.session.commit()
                log.exception(
                    'Alert dispatcher: channel %s failed for user=%s event=%s',
                    channel_name, user.id, event.id,
                )

    log.info(
        'Alert dispatcher: processed event=%s prefs=%d',
        event_id, len(prefs),
    )
