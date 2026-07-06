"""Recording for the "Track on Sensybull" share funnel.

Best-effort by design: analytics must never fail a user-facing request, so
`record_share_event` swallows its own errors and uses a nested transaction
that can't clobber the caller's session state.
"""

import logging
import re

from flask import request as flask_request

from app import db
from app.models.share_event import ShareEvent

log = logging.getLogger(__name__)

# The funnel steps we accept from clients (plus the two the API records
# itself on /watchlists/track). Anything else is dropped.
ALLOWED_EVENTS = {
    'link_opened',
    'button_clicked',
    'auth_started',
    'auth_completed',
    'watchlist_added',
    'already_in_watchlist',
    'failed',
}

_ATTRIBUTION_FIELDS = ('ref', 'utm_source', 'utm_medium', 'utm_campaign')

# Order matters: Edge ships "Chrome/" in its UA, Chrome ships "Safari/".
_BROWSERS = [
    ('edg/', 'Edge'),
    ('opr/', 'Opera'),
    ('samsungbrowser', 'Samsung Internet'),
    ('firefox/', 'Firefox'),
    ('chrome/', 'Chrome'),
    ('safari/', 'Safari'),
]

_CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]')


def _clean(value, max_len: int) -> str | None:
    """Coerce untrusted input to a bounded, control-char-free string."""
    if not isinstance(value, str):
        return None
    value = _CONTROL_CHARS.sub('', value).strip()
    return value[:max_len] or None


def _device_from_ua(ua: str) -> str:
    ua = ua.lower()
    if not ua:
        return 'unknown'
    if any(bot in ua for bot in ('bot', 'crawler', 'spider', 'preview', 'facebookexternalhit')):
        return 'bot'
    if 'ipad' in ua or ('android' in ua and 'mobile' not in ua):
        return 'tablet'
    if 'mobi' in ua or 'iphone' in ua or 'android' in ua:
        return 'mobile'
    return 'desktop'


def _browser_from_ua(ua: str) -> str | None:
    ua = ua.lower()
    for marker, name in _BROWSERS:
        if marker in ua:
            return name
    return None


def _country_from_headers() -> str | None:
    # Set by common edge proxies (Cloudflare / Vercel / Render's CDN).
    for header in ('CF-IPCountry', 'X-Vercel-IP-Country', 'X-Country-Code'):
        value = flask_request.headers.get(header)
        if value and value.upper() != 'XX':
            return value.upper()[:8]
    return None


def record_share_event(event: str, *, symbol: str | None = None,
                       attribution: dict | None = None,
                       referrer: str | None = None,
                       user_id: str | None = None,
                       logged_in: bool = False) -> bool:
    """Persist one funnel event. Returns False (and logs) instead of raising."""
    if event not in ALLOWED_EVENTS:
        return False

    attribution = attribution or {}
    ua = flask_request.user_agent.string or ''
    row = ShareEvent(
        event=event,
        symbol=_clean(symbol, 10),
        referrer=_clean(referrer, 255),
        device=_device_from_ua(ua),
        browser=_browser_from_ua(ua),
        country=_country_from_headers(),
        logged_in=bool(logged_in),
        user_id=user_id,
        **{f: _clean(attribution.get(f), 64) for f in _ATTRIBUTION_FIELDS},
    )
    try:
        with db.session.begin_nested():
            db.session.add(row)
        return True
    except Exception:
        log.warning('share analytics write failed', exc_info=True)
        return False
