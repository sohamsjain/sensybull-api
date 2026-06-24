"""User-provided webhook notification channel."""

import hashlib
import hmac
import json
import logging

import requests

from app.services.alerts.channels.base import NotificationChannel

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class WebhookChannel(NotificationChannel):
    """Delivers filing alerts to a user-provided webhook URL."""

    @property
    def name(self) -> str:
        return 'webhook'

    def send(self, user, event, app) -> None:
        from app.models.channel_config import ChannelConfig

        channel_config = ChannelConfig.query.filter_by(
            user_id=user.id, channel=self.name,
        ).first()
        if not channel_config:
            log.debug('WebhookChannel: skipped (no config) user=%s', user.id)
            return

        config_data = channel_config.config_json or {}
        url = config_data.get('url')
        if not url:
            log.debug('WebhookChannel: skipped (no url) user=%s', user.id)
            return

        briefing = event.briefing_json or {}
        event_types = event.event_types_json or []

        payload = {
            'event_id': event.id,
            'ticker': event.ticker,
            'company_name': event.company_name,
            'headline': briefing.get('headline', ''),
            'summary': briefing.get('summary', ''),
            'max_tier': event.max_tier,
            'event_types': event_types,
            'edgar_url': event.edgar_url,
            'filing_date': event.filing_date.isoformat() if event.filing_date else None,
            'timestamp': event.created_at.isoformat() if event.created_at else None,
        }

        body = json.dumps(payload, default=str)
        headers = {'Content-Type': 'application/json'}

        secret = config_data.get('secret')
        if secret:
            signature = hmac.new(
                secret.encode('utf-8'),
                body.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            headers['X-Sensybull-Signature'] = signature

        resp = requests.post(url, data=body, headers=headers, timeout=10)
        resp.raise_for_status()

        log.info('WebhookChannel: sent alert url=%s event=%s', url, event.id)
