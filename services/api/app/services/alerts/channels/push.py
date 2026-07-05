"""Browser Web Push notification channel (VAPID)."""

import json
import logging

from app.services.alerts.channels.base import NotificationChannel
from app.services.alerts import thesis_format

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class PushChannel(NotificationChannel):
    """Delivers filing alerts to the user's subscribed browsers via Web Push.

    Requires VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY in config. Expired
    subscriptions (push service responds 404/410) are pruned on the fly.
    """

    @property
    def name(self) -> str:
        return 'push'

    def send(self, user, event, app, assessment=None) -> None:
        from pywebpush import webpush, WebPushException
        from app import db
        from app.models.push_subscription import PushSubscription

        cfg = app.config
        private_key = cfg.get('VAPID_PRIVATE_KEY')
        if not private_key:
            log.debug('PushChannel: skipped (no VAPID_PRIVATE_KEY) user=%s', user.id)
            return

        subscriptions = PushSubscription.query.filter_by(user_id=user.id).all()
        if not subscriptions:
            log.debug('PushChannel: user %s has push enabled but no subscriptions', user.id)
            return

        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')
        thesis_label = thesis_format.label(assessment)
        # A thesis verdict leads the title; the rationale becomes the body.
        title = (
            f"{thesis_format.emoji(assessment)} {thesis_label} — {event.ticker or event.company_name}"
            if thesis_label
            else f"{event.ticker or event.company_name}: {briefing.get('headline', 'New SEC filing')}"
        )
        body = (
            (assessment or {}).get('rationale')
            if thesis_label
            else None
        ) or briefing.get('investor_takeaway') \
            or (briefing.get('summary') or '')[:180] \
            or f'{tier_label} priority {event.signal_type} filing'
        payload = json.dumps({
            'title': title,
            'body': body,
            'url': f"{cfg.get('FRONTEND_URL', '').rstrip('/')}/positions"
                   if thesis_label
                   else f"{cfg.get('FRONTEND_URL', '').rstrip('/')}/watchlist",
            'tag': event.id,
        })
        claims_sub = cfg.get('VAPID_SUBJECT') or f"mailto:{cfg.get('SUPPORT_EMAIL', 'support@sensybull.com')}"

        failures = []
        for sub in subscriptions:
            try:
                webpush(
                    subscription_info=sub.to_subscription_info(),
                    data=payload,
                    vapid_private_key=private_key,
                    vapid_claims={'sub': claims_sub},
                )
            except WebPushException as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status in (404, 410):
                    # Browser unregistered — drop the dead subscription
                    db.session.delete(sub)
                    db.session.commit()
                    log.info('PushChannel: pruned expired subscription user=%s', user.id)
                else:
                    failures.append(exc)
                    log.warning('PushChannel: delivery failed user=%s status=%s', user.id, status)

        # Only mark the notification failed if no browser got it
        if failures and len(failures) == len(subscriptions):
            raise failures[0]
        log.info('PushChannel: sent alert to %d subscription(s) user=%s event=%s',
                 len(subscriptions) - len(failures), user.id, event.id)
