from app.services.email.client import EmailMessage, MailClient


class ResendClient(MailClient):
    """Resend (resend.com) transactional backend."""

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError('RESEND_API_KEY is required for the resend provider')
        # Imported lazily so the dependency is only required when selected.
        import resend  # type: ignore

        resend.api_key = api_key
        self._resend = resend

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

        response = self._resend.Emails.send(payload)
        # Resend returns {'id': '...'} on success.
        return response.get('id') if isinstance(response, dict) else str(response)
