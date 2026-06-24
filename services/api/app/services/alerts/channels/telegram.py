"""Telegram Bot notification channel."""

import logging

import requests

from app.services.alerts.channels.base import NotificationChannel

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class TelegramChannel(NotificationChannel):
    """Delivers filing alerts via Telegram Bot API."""

    @property
    def name(self) -> str:
        return 'telegram'

    def send(self, user, event, app) -> None:
        from app.models.channel_config import ChannelConfig

        cfg = app.config
        bot_token = cfg.get('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            log.debug('TelegramChannel: skipped (no TELEGRAM_BOT_TOKEN) user=%s', user.id)
            return

        channel_config = ChannelConfig.query.filter_by(
            user_id=user.id, channel=self.name,
        ).first()
        if not channel_config or not channel_config.verified:
            log.debug('TelegramChannel: skipped (no config or not verified) user=%s', user.id)
            return

        chat_id = (channel_config.config_json or {}).get('chat_id')
        if not chat_id:
            log.debug('TelegramChannel: skipped (no chat_id) user=%s', user.id)
            return

        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')
        frontend_url = cfg.get('FRONTEND_URL', '').rstrip('/')
        event_types = event.event_types_json or []

        bullets = briefing.get('bullets', [])
        bullet_text = '\n'.join(f'• {b}' for b in bullets[:5]) if bullets else ''

        text = (
            f"*{event.ticker or event.company_name}* — {briefing.get('headline', 'New SEC Filing')}\n"
            f"Priority: {tier_label}\n"
        )
        if event_types:
            text += f"Types: {', '.join(event_types)}\n"
        if bullet_text:
            text += f"\n{bullet_text}\n"
        if event.edgar_url:
            text += f"\n[View on EDGAR]({event.edgar_url})"
        text += f"\n[Open in Sensybull]({frontend_url}/chats)"

        url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
        resp = requests.post(url, json={
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True,
        }, timeout=10)
        resp.raise_for_status()

        log.info('TelegramChannel: sent alert chat_id=%s event=%s', chat_id, event.id)
