"""Public discovery surface: what the web app lists in its sitemap.

GET /api/v1/discovery/sitemap — the indexable URLs the API knows about:
every company with a fundamentals page, and the recent events that carry
verified evidence quotes (the only permalinks worth indexing — a briefing
without evidence is LLM prose over a public filing, which search engines
rightly treat as thin).

The web app's `sitemap.ts` turns this into /sitemap.xml. Public, rate
limited and Redis-cached, because crawlers hit sitemaps on their schedule,
not ours.
"""

import sqlalchemy as sa
from flask import Blueprint, jsonify

from app import db, limiter
from app.models.company import Company
from app.models.filing_event import FilingEvent
from app.models.fundamentals import CompanyFundamentals
from app.services.market_data.cache import cache_get, cache_set

discovery_bp = Blueprint('discovery', __name__)

SITEMAP_CACHE_KEY = 'discovery:sitemap'
SITEMAP_CACHE_SECONDS = 6 * 3600

# A sitemap file holds at most 50,000 URLs; the web app adds a handful of
# static pages on top, so the two lists together stay under that.
MAX_COMPANIES = 20_000
MAX_EVENTS = 25_000

# Matches a non-empty evidence array in the stored JSON text. Both Postgres
# `json` output and SQLAlchemy's SQLite serialisation write `"key": [` with
# the same spacing, and an empty list is `[]`, so `[{` means "has a quote".
_HAS_EVIDENCE = '%"evidence": [{%'


def _iso(dt):
    return dt.isoformat() if dt else None


def build_sitemap_payload() -> dict:
    companies = (
        db.session.query(Company.ticker, CompanyFundamentals.last_synced_at)
        .join(CompanyFundamentals, CompanyFundamentals.company_id == Company.id)
        .filter(CompanyFundamentals.has_fundamentals.is_(True))
        .filter(Company.ticker.isnot(None))
        .order_by(Company.market_cap.desc().nullslast())
        .limit(MAX_COMPANIES)
        .all()
    )
    events = (
        db.session.query(FilingEvent.id, FilingEvent.created_at)
        .filter(sa.cast(FilingEvent.briefing_json, sa.Text).like(_HAS_EVIDENCE))
        .order_by(FilingEvent.created_at.desc())
        .limit(MAX_EVENTS)
        .all()
    )
    return {
        'companies': [
            {'symbol': ticker.upper(), 'lastmod': _iso(synced)}
            for ticker, synced in companies
        ],
        'events': [
            {'id': event_id, 'lastmod': _iso(created)}
            for event_id, created in events
        ],
    }


@discovery_bp.route('/sitemap', methods=['GET'])
@limiter.limit('30 per minute')
def get_sitemap():
    payload = cache_get(SITEMAP_CACHE_KEY)
    if payload is None:
        payload = build_sitemap_payload()
        cache_set(SITEMAP_CACHE_KEY, payload, SITEMAP_CACHE_SECONDS)
    resp = jsonify(payload)
    resp.headers['Cache-Control'] = f'public, max-age={SITEMAP_CACHE_SECONDS}'
    return resp
