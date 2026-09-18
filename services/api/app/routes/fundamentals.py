"""Public fundamentals surface (screener-style company pages).

GET /api/v1/fundamentals/<symbol>            everything the page renders
GET /api/v1/fundamentals/<symbol>/documents  SEC filings + recent briefings

Public: the company page must render for signed-out readers and crawlers.
Rate-limited and Redis-cached; responses carry Cache-Control so Next's
fetch cache and any CDN can hold them. A company that has never been
synced triggers an on-demand backfill and answers 202 `building` until it
lands, so the long tail of tickers never waits twice.
"""

import logging
import threading

from flask import Blueprint, current_app, jsonify, request

from app import db, limiter
from app.models.company import Company
from app.models.filing_event import FilingEvent
from app.models.fundamentals import CompanyFundamentals, FundamentalsPeriod
from app.services.fundamentals import payload as P
from app.services.fundamentals.edgar_docs import EdgarDocsError, fetch_documents
from app.services.market_data.cache import cache_get, cache_set
from app.utils.tickers import normalize_symbol

log = logging.getLogger(__name__)

fundamentals_bp = Blueprint('fundamentals', __name__)

PAGE_CACHE_SECONDS = 3600
DOCS_CACHE_SECONDS = 6 * 3600
BUILDING_CACHE_SECONDS = 30
RECENT_EVENTS = 10

# In-process guard so two requests for the same unsynced ticker start one
# backfill. Multi-instance deployments additionally key on Redis below.
_building: set[str] = set()
_building_lock = threading.Lock()


def _cache_control(resp, seconds: int):
    resp.headers['Cache-Control'] = f'public, s-maxage={seconds}, max-age=60, stale-while-revalidate=86400'
    return resp


def _find_company(raw_symbol: str):
    symbol = normalize_symbol(raw_symbol)
    if not symbol:
        return None, (jsonify({'error': 'invalid_symbol'}), 400)
    company = Company.query.filter(Company.ticker.ilike(symbol)).first()
    if not company:
        return None, (jsonify({'error': 'unknown_ticker'}), 404)
    return company, None


def _snapshot_version(snap: CompanyFundamentals | None, company: Company) -> str:
    synced = snap.last_synced_at.isoformat() if snap and snap.last_synced_at else '0'
    priced = company.price_updated_at.isoformat() if company.price_updated_at else '0'
    return f'{synced}|{priced}'


def _start_backfill(company: Company) -> bool:
    """Kick off sync_company() in the background; True if started."""
    if not current_app.config.get('FUNDAMENTALS_ON_DEMAND', True):
        return False
    import os
    if not os.environ.get('FMP_API_KEY'):
        return False
    key = company.id
    with _building_lock:
        if key in _building:
            return True
        _building.add(key)
    redis_key = f'fundamentals:building:{company.id}'
    if cache_get(redis_key):
        with _building_lock:
            _building.discard(key)
        return True
    cache_set(redis_key, True, 300)

    app = current_app._get_current_object()
    company_id = company.id

    def run():
        with app.app_context():
            try:
                from app.services.fundamentals.sync import sync_company
                c = db.session.get(Company, company_id)
                if c is not None:
                    sync_company(c)
            except Exception:
                log.exception('on-demand fundamentals backfill failed for %s', company_id)
                db.session.rollback()
            finally:
                db.session.remove()
                with _building_lock:
                    _building.discard(key)

    threading.Thread(target=run, name=f'fundamentals-{company.ticker}', daemon=True).start()
    return True


@fundamentals_bp.route('/<raw_symbol>', methods=['GET'])
@limiter.limit('120 per minute')
def get_fundamentals(raw_symbol):
    company, err = _find_company(raw_symbol)
    if err:
        return err
    snap = CompanyFundamentals.query.filter_by(company_id=company.id).first()

    if snap is None or (snap.last_synced_at is None):
        started = _start_backfill(company)
        body = P.build_payload(company, snap, [], status='building' if started else 'unavailable')
        resp = jsonify(body)
        resp.status_code = 202 if started else 200
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    cache_key = f'fundamentals:page:{company.id}:{_snapshot_version(snap, company)}'
    cached = cache_get(cache_key)
    if cached:
        return _cache_control(jsonify(cached), PAGE_CACHE_SECONDS)

    if not snap.has_fundamentals:
        status = 'unavailable' if snap.sync_error else 'empty'
        body = P.build_payload(company, snap, [], status=status)
        return _cache_control(jsonify(body), BUILDING_CACHE_SECONDS)

    periods = FundamentalsPeriod.query.filter_by(company_id=company.id).all()
    body = P.build_payload(company, snap, periods, status='ready')
    cache_set(cache_key, body, PAGE_CACHE_SECONDS)
    return _cache_control(jsonify(body), PAGE_CACHE_SECONDS)


@fundamentals_bp.route('/<raw_symbol>/documents', methods=['GET'])
@limiter.limit('120 per minute')
def get_documents(raw_symbol):
    company, err = _find_company(raw_symbol)
    if err:
        return err

    cache_key = f'fundamentals:docs:{company.id}'
    body = cache_get(cache_key)
    if body is None:
        filings = {'annual': [], 'quarterly': [], 'proxy': [], 'recent_8k_count': 0}
        filings_error = None
        if company.cik:
            try:
                filings = fetch_documents(company.cik)
            except (EdgarDocsError, Exception) as exc:  # network errors included
                log.warning('EDGAR documents fetch failed for %s: %s', company.ticker, exc)
                filings_error = 'edgar_unavailable'
        events = (FilingEvent.query.filter_by(company_id=company.id)
                  .order_by(FilingEvent.filing_date.desc()).limit(RECENT_EVENTS).all())
        body = {
            'symbol': company.ticker,
            'cik': company.cik,
            'edgar_url': (f'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany'
                          f'&CIK={company.cik}&type=&dateb=&owner=include&count=40')
            if company.cik else None,
            'filings': filings,
            'filings_error': filings_error,
            'updates': [_brief_event(e) for e in events],
        }
        if not filings_error:
            cache_set(cache_key, body, DOCS_CACHE_SECONDS)
    resp = jsonify(body)
    return _cache_control(resp, DOCS_CACHE_SECONDS if not body.get('filings_error') else 60)


def _brief_event(e: FilingEvent) -> dict:
    briefing = e.briefing_json or {}
    return {
        'id': e.id,
        'signal_type': e.signal_type,
        'source': e.source or 'edgar',
        'filing_date': e.filing_date.isoformat() if e.filing_date else None,
        'headline': briefing.get('headline'),
        'important': bool(e.important),
        'url': e.edgar_url,
        'event_types': [et.type_name for et in e.event_types] if e.event_types else (e.event_types_json or []),
    }


@fundamentals_bp.route('/<raw_symbol>/refresh', methods=['POST'])
@limiter.limit('10 per hour')
def refresh_fundamentals(raw_symbol):
    """Force a re-sync (tail companies, or after a restatement). Rate-limited hard."""
    company, err = _find_company(raw_symbol)
    if err:
        return err
    if request.headers.get('X-Refresh-Token') != current_app.config.get('FUNDAMENTALS_REFRESH_TOKEN', object()):
        return jsonify({'error': 'forbidden'}), 403
    started = _start_backfill(company)
    return jsonify({'status': 'building' if started else 'unavailable'}), 202 if started else 503
