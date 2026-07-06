"""Public share surface for "Track on Sensybull" links.

GET  /api/v1/share/<symbol>  — company info + ready-to-paste link/HTML/markdown
POST /api/v1/share/events    — funnel analytics (anonymous or authed)

Both endpoints are public (share pages must render for logged-out visitors
and crawlers) and rate-limited. Responses deliberately expose no internal
IDs — companies are addressed by ticker only.
"""

from html import escape

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app import db, limiter
from app.models.company import Company
from app.services.share_analytics import ALLOWED_EVENTS, record_share_event
from app.utils.sectors import sic_to_sector
from app.utils.tickers import normalize_symbol

share_bp = Blueprint('share', __name__)


def share_base_url() -> str:
    """Public site origin for share links (env SHARE_BASE_URL > FRONTEND_URL)."""
    base = (current_app.config.get('SHARE_BASE_URL')
            or current_app.config['FRONTEND_URL'])
    return base.rstrip('/')


def find_company_by_symbol(symbol: str) -> Company | None:
    # ilike without wildcards = case-insensitive equality
    return Company.query.filter(Company.ticker.ilike(symbol)).first()


def build_share_payload(company: Company) -> dict:
    symbol = company.ticker.upper()
    url = f'{share_base_url()}/add/{symbol}'
    label = f'Track {company.name} on Sensybull'
    md_label = label.replace('[', r'\[').replace(']', r'\]')
    return {
        'symbol': symbol,
        'company': {
            'name': company.name,
            'ticker': symbol,
            'sector': sic_to_sector(company.sic),
            'market_cap': company.market_cap,
        },
        'url': url,
        'html': f'<a href="{url}">{escape(label)}</a>',
        'markdown': f'[{md_label}]({url})',
    }


@share_bp.route('/<raw_symbol>', methods=['GET'])
@limiter.limit('120 per minute')
def get_share_info(raw_symbol):
    symbol = normalize_symbol(raw_symbol)
    if not symbol:
        return jsonify({'error': 'invalid_symbol'}), 400
    company = find_company_by_symbol(symbol)
    if not company:
        return jsonify({'error': 'unknown_ticker'}), 404
    return jsonify(build_share_payload(company))


@share_bp.route('/events', methods=['POST'])
@limiter.limit('60 per minute')
def record_event():
    """Record one funnel step. Accepts anonymous callers; a valid JWT (if
    present) attaches the user. Always returns 202 for allowed events —
    analytics must never block the UX."""
    data = request.get_json(silent=True) or {}

    event = data.get('event')
    if event not in ALLOWED_EVENTS:
        return jsonify({'error': 'unknown_event'}), 400

    user_id = None
    try:
        verify_jwt_in_request(optional=True)
        user_id = get_jwt_identity()
    except Exception:
        pass  # expired/garbled token on a public endpoint — treat as anonymous

    record_share_event(
        event,
        symbol=normalize_symbol(data.get('symbol')),
        attribution=data if isinstance(data, dict) else {},
        referrer=data.get('referrer') or request.referrer,
        user_id=user_id,
        logged_in=bool(user_id) or bool(data.get('logged_in')),
    )
    db.session.commit()
    return jsonify({'status': 'recorded'}), 202
