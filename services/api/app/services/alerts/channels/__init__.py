"""Channel registry — single place to register all notification channels."""

from app.services.alerts.channels.base import NotificationChannel
from app.services.alerts.channels.email import EmailChannel
from app.services.alerts.channels.push import PushChannel
from app.services.alerts.channels.sms import SmsChannel
from app.services.alerts.channels.telegram import TelegramChannel
from app.services.alerts.channels.discord import DiscordChannel
from app.services.alerts.channels.slack import SlackChannel
from app.services.alerts.channels.whatsapp import WhatsAppChannel
from app.services.alerts.channels.webhook import WebhookChannel

_CHANNELS: dict[str, NotificationChannel] = {}


def _register(channel: NotificationChannel) -> None:
    _CHANNELS[channel.name] = channel


# Register built-in channels
_register(EmailChannel())
_register(PushChannel())

# Register additional channels
_register(SmsChannel())
_register(TelegramChannel())
_register(DiscordChannel())
_register(SlackChannel())
_register(WhatsAppChannel())
_register(WebhookChannel())


def get_channel(name: str) -> NotificationChannel | None:
    return _CHANNELS.get(name)


def all_channel_names() -> list[str]:
    return list(_CHANNELS.keys())
