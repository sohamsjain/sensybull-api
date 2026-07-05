"""Alert dispatcher — fans out filing events to notification channels.

Called from the Redis subscriber after a FilingEvent is persisted and
WebSocket delivery is complete. All work runs on a dedicated thread pool
so the real-time path is never blocked.

Two entrypoints:
- trigger_alerts(...)        — the bulk, tier-gated filing alert (regular).
- dispatch_thesis_alert(...) — a single user's thesis-aware alert, fired by
  the thesis engine once a filing has been judged against their thesis.
  Bypasses the tier gate (a thesis break matters at any tier) but still
  respects the user's enabled flag, channel choices, and per-company mute.
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
            ctx.pop()


# ── Bulk regular alerts ──────────────────────────────────────────────────
def trigger_alerts(app, event_id: str, user_ids: set[str],
                   exclude_user_ids: frozenset[str] = frozenset()) -> None:
    """Submit the tier-gated filing alert to the background thread pool.

    Args:
        app: Flask application instance.
        event_id: ID of the persisted FilingEvent.
        user_ids: Users whose watchlists contain the company.
        exclude_user_ids: Users to skip here because their alert is deferred
            to the thesis engine (they hold this company with a thesis, so
            they'll get the thesis-aware variant instead).
    """
    targets = frozenset(user_ids) - frozenset(exclude_user_ids)
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


# ── Per-user thesis-aware alert ──────────────────────────────────────────
def dispatch_thesis_alert(app, event_id: str, user_id: str, assessment: dict,
                          bypass_tier: bool = True) -> None:
    """Submit a single user's thesis-aware filing alert.

    Fired by the thesis engine after a filing is judged against the user's
    thesis. `assessment` is a plain dict (see thesis_format) carried across
    the thread boundary.
    """
    _executor.submit(_with_app_context, app, _dispatch_thesis,
                     event_id, user_id, assessment, bypass_tier)


def _dispatch_thesis(app, event_id: str, user_id: str, assessment: dict,
                     bypass_tier: bool) -> None:
    from app import db
    from app.models.alert_preference import AlertPreference
    from app.models.company_read_state import CompanyReadState
    from app.models.filing_event import FilingEvent
    from app.models.user import User

    event = db.session.get(FilingEvent, event_id)
    if event is None:
        return

    pref = AlertPreference.query.filter_by(user_id=user_id).first()
    if pref is None or not pref.enabled:
        return  # user turned alerts off entirely
    if not bypass_tier and pref.max_tier < event.max_tier:
        return

    if _drop_muted(event, [pref], CompanyReadState) == []:
        return  # muted this company

    user = db.session.get(User, user_id)
    if user is None:
        return

    _deliver_to_user(app, db, event, user, pref.channels_json or {}, assessment)
    log.info('Alert dispatcher: thesis alert user=%s event=%s impact=%s',
             user_id, event_id, assessment.get('impact'))


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


def _deliver_to_user(app, db, event, user, channels: dict, assessment: dict | None = None) -> None:
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
            channel.send(user, event, app, assessment=assessment)
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
