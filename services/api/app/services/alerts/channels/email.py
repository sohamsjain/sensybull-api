import logging

from app.services.alerts.channels.base import NotificationChannel
from app.services.alerts import thesis_format
from app.services.email.renderer import render
from app.services.email.resend_client import EmailMessage

log = logging.getLogger(__name__)

TIER_LABELS = {1: 'High', 2: 'Medium', 3: 'Low'}


class EmailChannel(NotificationChannel):
    """Sends filing alert emails via the existing Resend infrastructure."""

    @property
    def name(self) -> str:
        return 'email'

    def send(self, user, event, app, assessment=None) -> None:
        client = app.extensions.get('mail')
        if client is None:
            log.debug('EmailChannel: skipped (no RESEND_API_KEY) user=%s', user.id)
            return

        cfg = app.config
        briefing = event.briefing_json or {}
        tier_label = TIER_LABELS.get(event.max_tier, 'Low')

        context = {
            'app_name': cfg.get('APP_NAME', 'Sensybull'),
            'frontend_url': cfg.get('FRONTEND_URL', ''),
            'support_email': cfg.get('SUPPORT_EMAIL', ''),
            'user_name': user.name,
            'ticker': event.ticker or '',
            'company_name': event.company_name or '',
            'headline': briefing.get('headline', 'New SEC Filing'),
            'summary': briefing.get('summary', ''),
            'summary_bullets': briefing.get('bullets', []),
            'event_types': event.event_types_json or [],
            'max_tier': event.max_tier,
            'tier_label': tier_label,
            'filing_date': event.filing_date,
            'edgar_url': event.edgar_url or '',
            'event_url': f"{cfg.get('FRONTEND_URL', '').rstrip('/')}/events/{event.id}",
            # Thesis banner (rendered only when a verdict is present)
            'thesis_label': thesis_format.label(assessment),
            'thesis_emoji': thesis_format.emoji(assessment),
            'thesis_color': thesis_format.color(assessment),
            'thesis_rationale': (assessment or {}).get('rationale'),
        }

        html, text = render('filing_alert', context)

        prefix = cfg.get('ALERT_EMAIL_SUBJECT_PREFIX', '[Sensybull]')
        thesis_subject = thesis_format.subject_prefix(assessment)
        if thesis_subject:
            subject = f"{prefix} {thesis_subject}: {event.company_name or event.ticker} — {briefing.get('headline', 'New Filing')}"
        else:
            subject = f"{prefix} {tier_label} Priority: {event.company_name or event.ticker} — {briefing.get('headline', 'New Filing')}"

        message = EmailMessage(
            to=user.email,
            subject=subject,
            html=html,
            text=text,
            from_address=cfg['MAIL_FROM_ADDRESS'],
            from_name=cfg['MAIL_FROM_NAME'],
            reply_to=cfg.get('MAIL_REPLY_TO'),
        )

        client.send(message)
        log.info('EmailChannel: sent alert to=%s event=%s', user.email, event.id)
