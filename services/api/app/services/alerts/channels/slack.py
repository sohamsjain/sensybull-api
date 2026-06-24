"""Slack Incoming Webhook notification channel."""

import logging

import requests

from app.services.alerts.channels.base import NotificationChannel

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class SlackChannel(NotificationChannel):
    """Delivers filing alerts via Slack Incoming Webhooks using Block Kit."""

    @property
    def name(self) -> str:
        return 'slack'

    def send(self, user, event, app) -> None:
        from app.models.channel_config import ChannelConfig

        channel_config = ChannelConfig.query.filter_by(
            user_id=user.id, channel=self.name,
        ).first()
        if not channel_config:
            log.debug('SlackChannel: skipped (no config) user=%s', user.id)
            return

        webhook_url = (channel_config.config_json or {}).get('webhook_url')
        if not webhook_url:
            log.debug('SlackChannel: skipped (no webhook_url) user=%s', user.id)
            return

        cfg = app.config
        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')
        event_types = event.event_types_json or []
        frontend_url = cfg.get('FRONTEND_URL', '').rstrip('/')

        summary = briefing.get('summary', '')
        bullets = briefing.get('bullets', [])
        if bullets:
            summary = '\n'.join(f'• {b}' for b in bullets[:5])

        blocks = [
            {
                'type': 'header',
                'text': {
                    'type': 'plain_text',
                    'text': f"{event.ticker or event.company_name}: {briefing.get('headline', 'New SEC Filing')}",
                },
            },
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': summary[:3000] if summary else '_No summary available._',
                },
            },
            {
                'type': 'context',
                'elements': [
                    {
                        'type': 'mrkdwn',
                        'text': f"*Priority:* {tier_label}",
                    },
                ],
            },
        ]

        if event_types:
            blocks[2]['elements'].append({
                'type': 'mrkdwn',
                'text': f"*Types:* {', '.join(event_types)}",
            })

        # Action buttons
        actions = {'type': 'actions', 'elements': []}
        if event.edgar_url:
            actions['elements'].append({
                'type': 'button',
                'text': {'type': 'plain_text', 'text': 'View on EDGAR'},
                'url': event.edgar_url,
            })
        actions['elements'].append({
            'type': 'button',
            'text': {'type': 'plain_text', 'text': 'Open in Sensybull'},
            'url': f'{frontend_url}/chats',
        })
        blocks.append(actions)

        resp = requests.post(webhook_url, json={'blocks': blocks}, timeout=10)
        resp.raise_for_status()

        log.info('SlackChannel: sent alert user=%s event=%s', user.id, event.id)
