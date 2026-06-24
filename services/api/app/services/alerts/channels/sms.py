"""SMS notification channel via Twilio."""

import logging

from app.services.alerts.channels.base import NotificationChannel

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class SmsChannel(NotificationChannel):
    """Delivers filing alerts as SMS messages via Twilio."""

    @property
    def name(self) -> str:
        return 'sms'

    def send(self, user, event, app) -> None:
        from twilio.rest import Client
        from app.models.channel_config import ChannelConfig

        cfg = app.config
        account_sid = cfg.get('TWILIO_ACCOUNT_SID')
        auth_token = cfg.get('TWILIO_AUTH_TOKEN')
        from_number = cfg.get('TWILIO_PHONE_NUMBER')

        if not account_sid or not auth_token or not from_number:
            log.debug('SmsChannel: skipped (Twilio not configured) user=%s', user.id)
            return

        channel_config = ChannelConfig.query.filter_by(
            user_id=user.id, channel=self.name,
        ).first()
        if not channel_config or not channel_config.verified:
            log.debug('SmsChannel: skipped (no config or not verified) user=%s', user.id)
            return

        phone = (channel_config.config_json or {}).get('phone')
        if not phone:
            log.debug('SmsChannel: skipped (no phone number) user=%s', user.id)
            return

        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')
        frontend_url = cfg.get('FRONTEND_URL', '').rstrip('/')

        body = (
            f"[Sensybull] {tier_label} Priority\n"
            f"{event.ticker or event.company_name}: "
            f"{briefing.get('headline', 'New SEC Filing')}\n"
            f"{frontend_url}/chats"
        )

        client = Client(account_sid, auth_token)
        client.messages.create(
            body=body,
            from_=from_number,
            to=phone,
        )
        log.info('SmsChannel: sent alert to=%s event=%s', phone, event.id)
