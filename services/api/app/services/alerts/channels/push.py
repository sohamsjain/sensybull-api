"""Browser Web Push notification channel (VAPID)."""

import json
import logging

from app.services.alerts.channels.base import NotificationChannel

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'


def _alert_copy(event) -> tuple[str, str]:
    """Title/body shared by Web Push and Expo push."""
    briefing = event.briefing_json or {}
    tier_label = TIER_LABELS.get(event.max_tier, 'Low')
    title = f"{event.ticker or event.company_name}: {briefing.get('headline', 'New SEC filing')}"
    body = (briefing.get('summary') or '')[:180] \
        or f'{tier_label} priority {event.signal_type} filing'
    return title, body


class PushChannel(NotificationChannel):
    """Delivers filing alerts to the user's devices.

    Two transports behind one channel toggle:
    - Web Push (VAPID) to subscribed browsers — requires VAPID_PRIVATE_KEY /
      VAPID_PUBLIC_KEY in config.
    - Expo push to registered mobile devices (sensybull-app) — no credentials
      needed server-side; Expo routes to FCM/APNs.

    Expired registrations (push service 404/410, Expo DeviceNotRegistered)
    are pruned on the fly. The notification only counts as failed when no
    browser and no device got it.
    """

    @property
    def name(self) -> str:
        return 'push'

    def send(self, user, event, app) -> None:
        web_sent, web_failures = self._send_web(user, event, app)
        expo_sent, expo_failures = self._send_expo(user, event, app)

        failures = web_failures + expo_failures
        if failures and (web_sent + expo_sent) == 0:
            raise failures[0]
        log.info('PushChannel: delivered to %d browser(s) + %d device(s) user=%s event=%s',
                 web_sent, expo_sent, user.id, event.id)

    # ── Web Push (browsers) ──────────────────────────────────────────

    def _send_web(self, user, event, app) -> tuple[int, list]:
        cfg = app.config
        private_key = cfg.get('VAPID_PRIVATE_KEY')
        if not private_key:
            log.debug('PushChannel: web push skipped (no VAPID_PRIVATE_KEY) user=%s', user.id)
            return 0, []

        from pywebpush import webpush, WebPushException
        from app import db
        from app.models.push_subscription import PushSubscription

        subscriptions = PushSubscription.query.filter_by(user_id=user.id).all()
        if not subscriptions:
            return 0, []

        title, body = _alert_copy(event)
        payload = json.dumps({
            'title': title,
            'body': body,
            'url': f"{cfg.get('FRONTEND_URL', '').rstrip('/')}/watchlist",
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

        return len(subscriptions) - len(failures), failures

    # ── Expo push (mobile devices) ───────────────────────────────────

    def _send_expo(self, user, event, app) -> tuple[int, list]:
        import requests
        from app import db
        from app.models.device_token import DeviceToken

        devices = DeviceToken.query.filter_by(user_id=user.id).all()
        if not devices:
            return 0, []

        title, body = _alert_copy(event)
        messages = [{
            'to': d.token,
            'title': title,
            'body': body,
            'sound': 'default',
            'priority': 'high',
            # The app routes notification taps to the event permalink screen
            'data': {'event_id': event.id, 'company_id': event.company_id},
        } for d in devices]

        try:
            resp = requests.post(EXPO_PUSH_URL, json=messages, timeout=10, headers={
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            })
            resp.raise_for_status()
            tickets = (resp.json() or {}).get('data') or []
        except Exception as exc:
            log.warning('PushChannel: Expo push request failed user=%s: %s', user.id, exc)
            return 0, [exc]

        sent, failures = 0, []
        for device, ticket in zip(devices, tickets):
            if ticket.get('status') == 'ok':
                sent += 1
                continue
            details = ticket.get('details') or {}
            if details.get('error') == 'DeviceNotRegistered':
                # App uninstalled or token rotated — drop the dead registration
                db.session.delete(device)
                db.session.commit()
                log.info('PushChannel: pruned dead Expo token user=%s', user.id)
            else:
                failures.append(RuntimeError(
                    f"Expo push rejected: {ticket.get('message', 'unknown error')}"))
                log.warning('PushChannel: Expo delivery failed user=%s error=%s',
                            user.id, details.get('error') or ticket.get('message'))
        return sent, failures
