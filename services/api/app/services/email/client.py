from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EmailMessage:
    """A single transactional email ready to be handed to a backend."""
    to: str
    subject: str
    html: str
    text: str
    from_address: str
    from_name: str
    reply_to: Optional[str] = None
    headers: dict = field(default_factory=dict)

    @property
    def from_header(self) -> str:
        """RFC 5322 'Name <addr>' header value."""
        if self.from_name:
            return f'{self.from_name} <{self.from_address}>'
        return self.from_address


class MailClient(ABC):
    """Provider-agnostic transactional mail interface."""

    @abstractmethod
    def send(self, message: EmailMessage) -> str:
        """Send the message synchronously. Returns the provider message id.

        Raises on failure - callers are expected to wrap in their own
        try/except (the high-level sender catches and logs).
        """
        ...
