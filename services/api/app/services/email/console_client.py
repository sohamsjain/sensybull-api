import sys
import uuid

from app.services.email.client import EmailMessage, MailClient


class ConsoleClient(MailClient):
    """Dev backend - prints rendered email to stdout. No network calls."""

    def send(self, message: EmailMessage) -> str:
        msg_id = f'console-{uuid.uuid4()}'
        out = sys.stdout
        out.write('\n' + '=' * 72 + '\n')
        out.write(f'[email:console] id={msg_id}\n')
        out.write(f'From:    {message.from_header}\n')
        out.write(f'To:      {message.to}\n')
        if message.reply_to:
            out.write(f'Reply-To:{message.reply_to}\n')
        out.write(f'Subject: {message.subject}\n')
        out.write('-' * 72 + '\n')
        out.write(message.text)
        out.write('\n' + '=' * 72 + '\n')
        out.flush()
        return msg_id
