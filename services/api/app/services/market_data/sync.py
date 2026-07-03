# services/api/app/services/market_data/sync.py
"""
Daily market-data sync: EDGAR shares outstanding + Alpaca last price
→ Company.market_cap. Run via `flask sync-market-data` (cron), after
sync-companies so newly listed companies are present.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app import db
from app.models.company import Company
from app.models.filing_event import FilingEvent
from app.services.market_data import alpaca, edgar_facts

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000
# Cap on per-company companyfacts fallback calls per run (~2 min at 8 req/s)
FALLBACK_LIMIT = 1000


def _snapshot_price(snap: dict):
    """Best available last price from an Alpaca snapshot."""
    for path in (("latestTrade", "p"), ("dailyBar", "c"), ("prevDailyBar", "c")):
        node = snap.get(path[0]) or {}
        price = node.get(path[1])
        if price:
            return price
    return None


def _sync_shares() -> int:
    """Frames bulk fetch + targeted companyfacts fallback. Returns update count."""
    shares_by_cik = edgar_facts.fetch_shares_by_cik()
    updated = 0

    if shares_by_cik:
        companies = Company.query.filter(Company.cik.isnot(None)).all()
        for i, company in enumerate(companies):
            hit = shares_by_cik.get(company.cik.zfill(10))
            if not hit:
                continue
            company.shares_outstanding, company.shares_as_of = hit
            updated += 1
            if (i + 1) % COMMIT_BATCH == 0:
                db.session.commit()
        db.session.commit()
        log.info("Shares outstanding updated for %d companies via frames", updated)

    # Fallback: companies users actually see (watchlisted or recently filed)
    # that the frames data missed.
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_filer_ids = db.session.query(FilingEvent.company_id).filter(
        FilingEvent.filing_date >= recent_cutoff,
        FilingEvent.company_id.isnot(None),
    ).distinct()
    missing = (
        Company.query
        .filter(Company.cik.isnot(None))
        .filter(Company.shares_outstanding.is_(None))
        .filter(db.or_(
            Company.watchlists.any(),
            Company.id.in_(recent_filer_ids),
        ))
        .limit(FALLBACK_LIMIT)
        .all()
    )
    fallback_hits = 0
    for company in missing:
        result = edgar_facts.fetch_company_shares(company.cik)
        time.sleep(edgar_facts.FALLBACK_DELAY)
        if not result:
            continue
        company.shares_outstanding, company.shares_as_of = result
        fallback_hits += 1
    if fallback_hits:
        db.session.commit()
        log.info("Shares outstanding backfilled for %d/%d companies via companyfacts",
                 fallback_hits, len(missing))
    return updated + fallback_hits


def _sync_prices() -> int:
    """Alpaca snapshots → last_price + market_cap. Returns update count."""
    companies = Company.query.filter(Company.ticker.isnot(None)).all()
    by_symbol = {alpaca.normalize_ticker(c.ticker): c for c in companies}

    try:
        snapshots = alpaca.get_snapshots(list(by_symbol.keys()))
    except alpaca.AlpacaError:
        log.exception("Alpaca snapshot fetch failed — prices not updated")
        return 0

    now = datetime.now(timezone.utc)
    updated = 0
    for i, (symbol, company) in enumerate(by_symbol.items()):
        snap = snapshots.get(symbol)
        price = _snapshot_price(snap) if snap else None
        if price is None:
            continue
        company.last_price = Decimal(str(price))
        company.price_updated_at = now
        if company.shares_outstanding:
            company.market_cap = int(company.shares_outstanding * float(price))
        updated += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Prices updated for %d/%d tickered companies", updated, len(by_symbol))
    return updated


def sync_market_data() -> tuple[int, int]:
    """Returns (companies with shares updated, companies with price updated)."""
    shares_updated = _sync_shares()
    prices_updated = _sync_prices()
    return shares_updated, prices_updated
