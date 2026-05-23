import smtplib
import ssl
import uuid
from email.message import EmailMessage as MimeMessage

from app.services.email.client import EmailMessage, MailClient


class SmtpClient(MailClient):
    """Generic SMTP backend - works with Google Workspace, Zoho, M365,
    AWS SES SMTP, MailHog, etc.
    """

    def __init__(self, host: str, port: int = 587, username: str | None = None,
                 password: str | None = None, use_tls: bool = True):
        if not host:
            raise ValueError('SMTP_HOST is required for the smtp provider')
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls

    def _build_mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime['From'] = message.from_header
        mime['To'] = message.to
        mime['Subject'] = message.subject
        if message.reply_to:
            mime['Reply-To'] = message.reply_to
        for k, v in (message.headers or {}).items():
            mime[k] = v
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype='html')
        return mime

    def send(self, message: EmailMessage) -> str:
        mime = self._build_mime(message)
        msg_id = f'<{uuid.uuid4()}@{self.host}>'
        mime['Message-ID'] = msg_id

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.ehlo()
            if self.use_tls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(mime)

        return msg_id
