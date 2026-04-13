from app.services.email.client import MailClient
from app.services.email.console_client import ConsoleClient


def get_mail_client(config) -> MailClient:
    """Build a MailClient from a Flask config-like mapping.

    Defaults to ConsoleClient when MAIL_PROVIDER is unset/unknown so the
    app boots without email credentials during local dev.
    """
    provider = (config.get('MAIL_PROVIDER') or 'console').lower()

    if provider == 'resend':
        from app.services.email.resend_client import ResendClient
        return ResendClient(api_key=config.get('RESEND_API_KEY'))

    if provider == 'smtp':
        from app.services.email.smtp_client import SmtpClient
        return SmtpClient(
            host=config.get('SMTP_HOST'),
            port=int(config.get('SMTP_PORT') or 587),
            username=config.get('SMTP_USER'),
            password=config.get('SMTP_PASS'),
            use_tls=bool(config.get('SMTP_USE_TLS', True)),
        )

    return ConsoleClient()
