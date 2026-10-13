"""Alert dispatcher — fans out filing events to notification channels.

Called from the Redis subscriber after a FilingEvent is persisted and
WebSocket delivery is complete. All work runs on a dedicated thread pool
so the real-time path is never blocked.

Entrypoints:
  trigger_alerts(...)      — on publish: the tier-gated alert to readers who
                             follow the company, then saved-view alerts.
  trigger_view_alerts(...) — when a price reaction turns explosive: alerts
                             for views filtered on "moved the stock", which
                             can't match an event until its move is measured.

A reader hears about an event once per channel, however many reasons they
have to (Notification's unique user/event/channel key): following the
company and having a matching view sends one alert, not two.
"""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from flask import has_app_context
from sqlalchemy.exc import IntegrityError

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
    """Submit the filing's alerts to the background thread pool.

    Args:
        app: Flask application instance.
        event_id: ID of the persisted FilingEvent.
        user_ids: Users whose watchlists contain the company.

    Submitted even when nobody follows the company, as long as some saved
    view has alerts on: a view can match an event from any filer.
    Followed-company alerts go first, in the same task, so the two paths
    never race on one notification row.
    """
    if not user_ids and not _any_view_alerts():
        return
    _executor.submit(_with_app_context, app, _publish_inner, event_id, frozenset(user_ids))


def _any_view_alerts() -> bool:
    """Does any saved view have alerts on? Asked on the caller's thread,
    so an event nobody can be alerted about never costs a pool task."""
    from app.models.feed_view import FeedView
    try:
        return FeedView.query.filter(FeedView.alerts_enabled.is_(True)).first() is not None
    except Exception:
        log.exception('Alert dispatcher: view-alert check failed — dispatching anyway')
        return True


def _publish_inner(app, event_id: str, user_ids: frozenset[str]) -> None:
    if user_ids:
        _dispatch_inner(app, event_id, user_ids)
    _views_inner(app, event_id, False)


# A reaction measured later than this after the event doesn't alert a
# "moved the stock" view — "it moved on last Tuesday's 8-K" isn't news.
MOVE_ALERT_MAX_AGE = timedelta(days=2)


def trigger_view_alerts(app, event_id: str) -> None:
    """Alert the "moved the stock" views an event now matches (called by
    the price-reaction worker after it measures an explosive move)."""
    if not _any_view_alerts():
        return
    _executor.submit(_with_app_context, app, _views_inner, event_id, True)


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


# ── Saved-view alerts ────────────────────────────────────────────────────
def matching_views(event, *, moved_only: bool = False) -> list:
    """The alerting FeedViews this event matches, one per user (their
    first in view order — it names the alert).

    Each view is checked by running the feed's own SQL filter against this
    one event, so an alert can never disagree with what the view shows in
    the feed. Two differences, both because an alert is about *now*: the
    time window is ignored (a new event is always inside it), and a view
    that asks for a price move only matches once the move is measured —
    `moved_only` is that second pass.
    """
    from app.models.feed_view import FeedView
    from app.models.filing_event import FilingEvent
    from app.routes.events import _user_company_ids
    from app.services.feed_filters import FilterError, apply_filters, parse_filters

    views = (FeedView.query.filter(FeedView.alerts_enabled.is_(True))
             .order_by(FeedView.user_id, FeedView.position, FeedView.created_at).all())
    matched: dict[str, object] = {}
    for view in views:
        if view.user_id in matched:
            continue
        try:
            f = parse_filters(view.filters)
        except FilterError:
            # An option that has since disappeared (an industry no company
            # carries any more). The feed would 400 on it too.
            log.warning('Alert dispatcher: view %s has stale filters — skipped', view.id)
            continue
        if moved_only and not f.moved:
            continue
        f.window = None
        q = FilingEvent.query.filter(FilingEvent.id == event.id)
        if f.scope == 'mine':
            if not event.company_id or event.company_id not in _user_company_ids(view.user_id):
                continue
        if apply_filters(q, f).order_by(None).first() is not None:
            matched[view.user_id] = view
    return list(matched.values())


def _views_inner(app, event_id: str, moved_only: bool) -> None:
    from app import db
    from app.models.alert_preference import AlertPreference
    from app.models.company_read_state import CompanyReadState
    from app.models.filing_event import FilingEvent
    from app.models.user import User

    event = db.session.get(FilingEvent, event_id)
    if event is None:
        return
    if moved_only:
        received = event.created_at
        if received is not None and received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        if received is None or datetime.now(timezone.utc) - received > MOVE_ALERT_MAX_AGE:
            return

    views = matching_views(event, moved_only=moved_only)
    if not views:
        return

    by_user = {v.user_id: v for v in views}
    prefs = {p.user_id: p for p in AlertPreference.query.filter(
        AlertPreference.user_id.in_(by_user)).all()}
    # The alert switch in settings is the master switch: off means off,
    # whatever a view asks for. No preferences row yet means the defaults.
    targets = [uid for uid in by_user if prefs.get(uid) is None or prefs[uid].enabled]
    if event.company_id and targets:
        muted = {rs.user_id for rs in CompanyReadState.query.filter(
            CompanyReadState.company_id == event.company_id,
            CompanyReadState.user_id.in_(targets),
            CompanyReadState.muted.is_(True),
        )}
        targets = [uid for uid in targets if uid not in muted]
    if not targets:
        return

    users = {u.id: u for u in User.query.filter(User.id.in_(targets)).all()}
    for uid in targets:
        user = users.get(uid)
        if user is None:
            continue
        pref = prefs.get(uid)
        channels = (pref.channels_json if pref else None) or {'email': True}
        _deliver_to_user(app, db, event, user, channels, view=by_user[uid])

    log.info('Alert dispatcher: view alerts event=%s users=%d moved_only=%s',
             event_id, len(targets), moved_only)


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


def _deliver_to_user(app, db, event, user, channels: dict, view=None) -> None:
    """Deliver to each of a user's enabled channels, with dedup + bookkeeping.

    `view` is the saved view that matched, for view alerts — channels name
    it so the reader knows why this company reached them."""
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
            feed_view_id=view.id if view is not None else None,
        )
        db.session.add(notification)
        try:
            db.session.commit()
        except IntegrityError:
            # Another task got there first (the reaction pass racing the
            # publish pass) — it owns this delivery
            db.session.rollback()
            continue

        try:
            channel.send(user, event, app, view=view)
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
