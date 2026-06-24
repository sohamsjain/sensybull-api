"""WhatsApp notification channel via Twilio."""

import logging

from app.services.alerts.channels.base import NotificationChannel

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class WhatsAppChannel(NotificationChannel):
    """Delivers filing alerts as WhatsApp messages via Twilio."""

    @property
    def name(self) -> str:
        return 'whatsapp'

    def send(self, user, event, app) -> None:
        from twilio.rest import Client
        from app.models.channel_config import ChannelConfig

        cfg = app.config
        account_sid = cfg.get('TWILIO_ACCOUNT_SID')
        auth_token = cfg.get('TWILIO_AUTH_TOKEN')
        wa_number = cfg.get('TWILIO_WHATSAPP_NUMBER')

        if not account_sid or not auth_token or not wa_number:
            log.debug('WhatsAppChannel: skipped (Twilio WhatsApp not configured) user=%s', user.id)
            return

        channel_config = ChannelConfig.query.filter_by(
            user_id=user.id, channel=self.name,
        ).first()
        if not channel_config or not channel_config.verified:
            log.debug('WhatsAppChannel: skipped (no config or not verified) user=%s', user.id)
            return

        phone = (channel_config.config_json or {}).get('phone')
        if not phone:
            log.debug('WhatsAppChannel: skipped (no phone number) user=%s', user.id)
            return

        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')
        frontend_url = cfg.get('FRONTEND_URL', '').rstrip('/')
        event_types = event.event_types_json or []

        bullets = briefing.get('bullets', [])
        bullet_text = '\n'.join(f'• {b}' for b in bullets[:5]) if bullets else ''

        body = (
            f"*{event.ticker or event.company_name}* — "
            f"{briefing.get('headline', 'New SEC Filing')}\n"
            f"Priority: {tier_label}\n"
        )
        if event_types:
            body += f"Types: {', '.join(event_types)}\n"
        if bullet_text:
            body += f"\n{bullet_text}\n"
        if event.edgar_url:
            body += f"\nEDGAR: {event.edgar_url}"
        body += f"\n{frontend_url}/chats"

        client = Client(account_sid, auth_token)
        client.messages.create(
            body=body,
            from_=f'whatsapp:{wa_number}',
            to=f'whatsapp:{phone}',
        )
        log.info('WhatsAppChannel: sent alert to=%s event=%s', phone, event.id)
