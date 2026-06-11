"""Channel registry — single place to register all notification channels."""

from app.services.alerts.channels.base import NotificationChannel
from app.services.alerts.channels.email import EmailChannel
from app.services.alerts.channels.push import PushChannel

_CHANNELS: dict[str, NotificationChannel] = {}


def _register(channel: NotificationChannel) -> None:
    _CHANNELS[channel.name] = channel


# Register built-in channels
_register(EmailChannel())
_register(PushChannel())


def get_channel(name: str) -> NotificationChannel | None:
    return _CHANNELS.get(name)


def all_channel_names() -> list[str]:
    return list(_CHANNELS.keys())
