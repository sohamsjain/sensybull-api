"""Benzinga Logo API sync — stores brand-mark URLs on Company rows.

The API key must stay server-side (it travels as a query param), so the
frontend only ever sees the signed image URLs we store here. We request
square icon "marks" in dark-theme variants first — they're designed for
dark UIs and avoid the white-box-in-a-circle problem entirely.

Docs: https://docs.benzinga.com (Logo API v2, /api/v2/logos/search)
"""

import logging

import requests

log = logging.getLogger(__name__)

BENZINGA_LOGOS_URL = 'https://api.benzinga.com/api/v2/logos/search'

# Square icon marks for dark UIs first, wordmark logos as a last resort
FIELD_PRIORITY = [
    'mark_vector_dark',
    'mark_dark',
    'mark_vector_light',
    'mark_light',
    'logo_dark',
]
BATCH_SIZE = 100


def _pick_url(fields: dict) -> str | None:
    for field in FIELD_PRIORITY:
        if fields.get(field):
            return fields[field]
    return None


def sync_logos(api_key: str, only_watchlisted: bool = True) -> tuple[int, int]:
    """Fetch logo marks for companies and store the best URL per company.

    Args:
        api_key: Benzinga API token.
        only_watchlisted: Limit to companies on at least one watchlist
            (the set users actually see; keeps API usage tiny). Pass False
            for a full-universe sync.

    Returns:
        (companies_updated, companies_considered)
    """
    from app import db
    from app.models.company import Company

    q = Company.query.filter(Company.ticker.isnot(None))
    if only_watchlisted:
        q = q.filter(Company.watchlists.any())
    companies = q.all()

    by_ticker = {c.ticker.upper(): c for c in companies}
    tickers = sorted(by_ticker)
    updated = 0

    seen: set[str] = set()

    for i in range(0, len(tickers), BATCH_SIZE):
        chunk = tickers[i:i + BATCH_SIZE]
        try:
            resp = requests.get(BENZINGA_LOGOS_URL, params={
                'token': api_key,
                'search_keys': ','.join(chunk),
                'search_keys_type': 'symbol',
                'fields': ','.join(FIELD_PRIORITY),
                'scale': '192x192',
                'pagesize': '1000',
            }, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            log.exception('Logo sync: batch %d-%d failed — skipping', i, i + len(chunk))
            continue

        body = resp.json()
        entries = body.get('data', body) if isinstance(body, dict) else body
        for entry in entries or []:
            raw = entry.get('files') or entry.get('fields') or {}
            url = _pick_url(raw)
            if not url:
                continue
            for security in entry.get('securities') or []:
                ticker = (security.get('symbol') or '').upper()
                if ticker in seen:
                    continue
                company = by_ticker.get(ticker)
                if company and company.logo_url != url:
                    company.logo_url = url
                    updated += 1
                if company:
                    seen.add(ticker)
        db.session.commit()

    log.info('Logo sync: updated %d of %d companies', updated, len(tickers))
    return updated, len(tickers)
