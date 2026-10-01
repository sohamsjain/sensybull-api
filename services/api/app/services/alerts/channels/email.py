import logging

from app.services.alerts.channels.base import NotificationChannel
from app.services.email.renderer import render
from app.services.email.resend_client import EmailMessage

log = logging.getLogger(__name__)


def _event_price(event):
    """Best-effort last trade price for the event's ticker, for display
    next to the ticker in the alert. Mirrors the companies.py quote proxy
    (same Redis cache key, same FMP-then-last_price fallback) so this
    never issues an extra FMP call beyond what the feed already pays for.
    """
    if not event.ticker:
        return None

    from app.routes.companies import QUOTE_CACHE_SECONDS, _quote_payload
    from app.services.market_data import prices
    from app.services.market_data.cache import cache_get, cache_set

    cache_key = f'quote:{event.ticker}'
    cached = cache_get(cache_key)
    if cached:
        return cached.get('price')

    company = event.company
    if company is None:
        return None

    symbol = prices.normalize_ticker(event.ticker)
    try:
        fmp_quote = prices.get_quotes([symbol]).get(symbol)
    except prices.MarketDataError:
        fmp_quote = None

    quote = _quote_payload(company, fmp_quote)
    if quote is None:
        return None
    if not quote['stale']:
        cache_set(cache_key, quote, QUOTE_CACHE_SECONDS)
    return quote.get('price')


class EmailChannel(NotificationChannel):
    """Sends filing alert emails via the existing Resend infrastructure."""

    @property
    def name(self) -> str:
        return 'email'

    def send(self, user, event, app) -> None:
        client = app.extensions.get('mail')
        if client is None:
            log.debug('EmailChannel: skipped (no RESEND_API_KEY) user=%s', user.id)
            return

        cfg = app.config
        briefing = event.briefing_json or {}
        headline = briefing.get('headline', 'New SEC Filing')

        try:
            price = _event_price(event)
        except Exception:
            log.exception('EmailChannel: price lookup failed for event=%s', event.id)
            price = None

        context = {
            'app_name': cfg.get('APP_NAME', 'Sensybull'),
            'frontend_url': cfg.get('FRONTEND_URL', ''),
            'support_email': cfg.get('SUPPORT_EMAIL', ''),
            'user_name': user.name,
            'ticker': event.ticker or '',
            'company_name': event.company_name or '',
            'price': price,
            'important': event.important,
            'headline': headline,
            'summary': briefing.get('summary', ''),
            'summary_bullets': briefing.get('bullets', []),
            'edgar_url': event.edgar_url or '',
            'event_url': f"{cfg.get('FRONTEND_URL', '').rstrip('/')}/e/{event.id}",
        }

        html, text = render('filing_alert', context)

        subject = f"{event.company_name or event.ticker}: {headline}"

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
