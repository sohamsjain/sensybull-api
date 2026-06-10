from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    """Abstract base for all notification delivery channels.

    To add a new channel (e.g. Discord, Slack, SMS):
    1. Subclass this and implement `name` and `send()`.
    2. Register the instance in channels/__init__.py.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel identifier — must match keys in AlertPreference.channels_json."""
        ...

    @abstractmethod
    def send(self, user, event, app) -> None:
        """Deliver a notification. Called from a worker thread.

        Args:
            user: User model instance.
            event: FilingEvent model instance.
            app: Flask application (for config and extensions).

        Raises on failure — the dispatcher will catch and record the error.
        """
        ...
