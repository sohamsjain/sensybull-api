"""Discord webhook notification channel."""

import logging

import requests

from app.services.alerts.channels.base import NotificationChannel
from app.services.alerts import thesis_format

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}
TIER_COLORS = {1: 0xFF0000, 2: 0xFF8C00, 3: 0x00CC00}  # red, orange, green
# Thesis verdicts recolor the embed to match their severity.
IMPACT_COLORS = {'breaks': 0xDC2626, 'threatens': 0xEA580C, 'supports': 0x16A34A}


class DiscordChannel(NotificationChannel):
    """Delivers filing alerts via Discord webhook embeds."""

    @property
    def name(self) -> str:
        return 'discord'

    def send(self, user, event, app, assessment=None) -> None:
        from app.models.channel_config import ChannelConfig

        channel_config = ChannelConfig.query.filter_by(
            user_id=user.id, channel=self.name,
        ).first()
        if not channel_config:
            log.debug('DiscordChannel: skipped (no config) user=%s', user.id)
            return

        webhook_url = (channel_config.config_json or {}).get('webhook_url')
        if not webhook_url:
            log.debug('DiscordChannel: skipped (no webhook_url) user=%s', user.id)
            return

        cfg = app.config
        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')
        color = TIER_COLORS.get(event.max_tier, 0x00CC00)
        event_types = event.event_types_json or []
        frontend_url = cfg.get('FRONTEND_URL', '').rstrip('/')

        summary = briefing.get('summary', '')
        bullets = briefing.get('bullets', [])
        if bullets:
            summary = '\n'.join(f'• {b}' for b in bullets[:5])

        thesis_label = thesis_format.label(assessment)
        if thesis_label:
            color = IMPACT_COLORS.get(assessment.get('impact', ''), color)

        embed = {
            'title': f"{event.ticker or ''} — {briefing.get('headline', 'New SEC Filing')}",
            'description': summary[:2048] if summary else None,
            'color': color,
            'fields': [],
            'footer': {'text': f'{tier_label} Priority | Sensybull'},
        }

        if thesis_label:
            embed['fields'].append({
                'name': f"{thesis_format.emoji(assessment)} {thesis_label}",
                'value': (assessment.get('rationale') or 'Judged against your thesis.')[:1024],
                'inline': False,
            })

        if event_types:
            embed['fields'].append({
                'name': 'Event Types',
                'value': ', '.join(event_types),
                'inline': True,
            })

        if event.edgar_url:
            embed['fields'].append({
                'name': 'Links',
                'value': f"[EDGAR]({event.edgar_url}) | [Sensybull]({frontend_url}/watchlist)",
                'inline': True,
            })

        resp = requests.post(webhook_url, json={
            'embeds': [embed],
        }, timeout=10)
        resp.raise_for_status()

        log.info('DiscordChannel: sent alert user=%s event=%s', user.id, event.id)
