"""Resend (resend.com) transactional email backend."""

import logging
from dataclasses import dataclass, field
from typing import Optional

import resend

log = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """A single transactional email ready to send."""
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


class ResendClient:
    """Sends transactional emails via Resend."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError('RESEND_API_KEY is required')
        resend.api_key = api_key

    def send(self, message: EmailMessage) -> str:
        payload = {
            'from': message.from_header,
            'to': [message.to],
            'subject': message.subject,
            'html': message.html,
            'text': message.text,
        }
        if message.reply_to:
            payload['reply_to'] = message.reply_to
        if message.headers:
            payload['headers'] = message.headers

        response = resend.Emails.send(payload)
        return response.get('id') if isinstance(response, dict) else str(response)
