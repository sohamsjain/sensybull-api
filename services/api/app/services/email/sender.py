"""High-level email sending API.

Use the `send_*` helpers from route handlers. They render the template,
build an EmailMessage, and dispatch via a thread pool so the request is
never blocked by mail delivery. All failures are logged and swallowed -
auth flows must succeed even if the mail provider is temporarily down.
"""
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urljoin

from flask import current_app

from app.services.email.client import EmailMessage
from app.services.email.renderer import render

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mailer')


def _deliver(app, message: EmailMessage) -> None:
    with app.app_context():
        try:
            app.extensions['mail'].send(message)
        except Exception:
            app.logger.exception(
                'email send failed to=%s subject=%r', message.to, message.subject
            )


def _send_async(message: EmailMessage) -> None:
    app = current_app._get_current_object()
    _executor.submit(_deliver, app, message)


def _base_context() -> dict:
    cfg = current_app.config
    return {
        'app_name': cfg.get('APP_NAME', 'Sensybull'),
        'frontend_url': cfg.get('FRONTEND_URL', ''),
        'support_email': cfg.get('SUPPORT_EMAIL', ''),
    }


def _build_link(path: str, **params) -> str:
    base = current_app.config.get('FRONTEND_URL', '').rstrip('/') + '/'
    query = '?' + urlencode(params) if params else ''
    return urljoin(base, path.lstrip('/')) + query


def _build_message(to: str, subject: str, template: str, context: dict) -> EmailMessage:
    cfg = current_app.config
    html, text = render(template, context)
    return EmailMessage(
        to=to,
        subject=subject,
        html=html,
        text=text,
        from_address=cfg['MAIL_FROM_ADDRESS'],
        from_name=cfg['MAIL_FROM_NAME'],
        reply_to=cfg.get('MAIL_REPLY_TO'),
    )


def send_verification(user, raw_token: str) -> None:
    ctx = _base_context()
    ctx.update({
        'user_name': user.name,
        'verify_url': _build_link('verify-email', token=raw_token),
        'expires_hours': current_app.config.get('EMAIL_VERIFY_TOKEN_HOURS', 24),
    })
    msg = _build_message(
        to=user.email,
        subject=f"Verify your email for {ctx['app_name']}",
        template='verify_email',
        context=ctx,
    )
    _send_async(msg)


def send_welcome(user) -> None:
    ctx = _base_context()
    ctx.update({
        'user_name': user.name,
        'dashboard_url': _build_link(''),
    })
    msg = _build_message(
        to=user.email,
        subject=f"Welcome to {ctx['app_name']}",
        template='welcome',
        context=ctx,
    )
    _send_async(msg)


def send_password_reset(user, raw_token: str) -> None:
    ctx = _base_context()
    ctx.update({
        'user_name': user.name,
        'reset_url': _build_link('reset-password', token=raw_token),
        'expires_hours': current_app.config.get('PASSWORD_RESET_TOKEN_HOURS', 1),
    })
    msg = _build_message(
        to=user.email,
        subject=f"Reset your {ctx['app_name']} password",
        template='password_reset',
        context=ctx,
    )
    _send_async(msg)


def send_password_changed(user) -> None:
    ctx = _base_context()
    ctx.update({'user_name': user.name})
    msg = _build_message(
        to=user.email,
        subject=f"Your {ctx['app_name']} password was changed",
        template='password_changed',
        context=ctx,
    )
    _send_async(msg)
