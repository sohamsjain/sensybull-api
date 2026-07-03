# services/api/app/services/market_data/sync.py
"""
Daily market-data sync: Alpaca last price + EDGAR shares outstanding
→ Company.market_cap. Run via `flask sync-market-data` (cron), after
sync-companies so newly listed companies are present.

Order matters: prices come first so the per-company EDGAR fallback can
target exactly the companies that are tradable (have a price) but lack a
share count — the set the market-cap filter actually needs. The fallback
budget is a daily quota; because results persist, coverage converges to
complete within a few cron runs even when the bulk frames API is sparse.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app import db
from app.models.company import Company
from app.models.filing_event import FilingEvent
from app.services.market_data import alpaca, edgar_facts

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000


def _fallback_limit() -> int:
    """Per-run cap on companyfacts calls (~0.125s each; 2500 ≈ 5 min)."""
    try:
        return int(os.environ.get("EDGAR_FALLBACK_LIMIT", "2500"))
    except ValueError:
        return 2500


def _snapshot_price(snap: dict):
    """Best available last price from an Alpaca snapshot."""
    for path in (("latestTrade", "p"), ("dailyBar", "c"), ("prevDailyBar", "c")):
        node = snap.get(path[0]) or {}
        price = node.get(path[1])
        if price:
            return price
    return None


def _sync_prices() -> int:
    """Alpaca snapshots → last_price. Returns update count."""
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
        updated += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Prices updated for %d/%d tickered companies", updated, len(by_symbol))
    return updated


def _sync_shares() -> int:
    """EDGAR shares outstanding: bulk frames + targeted companyfacts backfill."""
    frames_hits = 0
    shares_by_cik = edgar_facts.fetch_shares_by_cik()
    if shares_by_cik:
        companies = Company.query.filter(Company.cik.isnot(None)).all()
        for i, company in enumerate(companies):
            hit = shares_by_cik.get(company.cik.zfill(10))
            if not hit:
                continue
            # Never regress to an older value than we already hold
            if company.shares_as_of and hit[1] and hit[1] < company.shares_as_of:
                continue
            company.shares_outstanding, company.shares_as_of = hit
            frames_hits += 1
            if (i + 1) % COMMIT_BATCH == 0:
                db.session.commit()
        db.session.commit()
        log.info("Shares outstanding updated for %d companies via frames", frames_hits)

    # Authoritative per-company backfill for everything the frames missed,
    # prioritized: watchlisted, then recent filers, then any priced company.
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_filer_ids = db.session.query(FilingEvent.company_id).filter(
        FilingEvent.filing_date >= recent_cutoff,
        FilingEvent.company_id.isnot(None),
    ).distinct()

    missing_base = (
        Company.query
        .filter(Company.cik.isnot(None))
        .filter(Company.shares_outstanding.is_(None))
    )
    prioritized = (
        missing_base
        .filter(db.or_(
            Company.watchlists.any(),
            Company.id.in_(recent_filer_ids),
        ))
        .all()
    )
    priced = (
        missing_base
        .filter(Company.last_price.isnot(None))
        .all()
    )
    seen_ids = {c.id for c in prioritized}
    queue = prioritized + [c for c in priced if c.id not in seen_ids]
    queue = queue[:_fallback_limit()]

    fallback_hits = 0
    for i, company in enumerate(queue):
        result = edgar_facts.fetch_company_shares(company.cik)
        time.sleep(edgar_facts.FALLBACK_DELAY)
        if not result:
            continue
        company.shares_outstanding, company.shares_as_of = result
        fallback_hits += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Shares outstanding backfilled for %d/%d companies via companyfacts",
             fallback_hits, len(queue))
    return frames_hits + fallback_hits


def _recompute_market_caps() -> int:
    """market_cap = shares × last price for every company with both."""
    companies = (
        Company.query
        .filter(Company.shares_outstanding.isnot(None))
        .filter(Company.last_price.isnot(None))
        .all()
    )
    updated = 0
    for i, company in enumerate(companies):
        cap = int(company.shares_outstanding * float(company.last_price))
        if company.market_cap != cap:
            company.market_cap = cap
            updated += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Market cap computed for %d companies (%d changed)",
             len(companies), updated)
    return len(companies)


def sync_market_data() -> tuple[int, int]:
    """Returns (companies with shares updated, companies with price updated)."""
    prices_updated = _sync_prices()
    shares_updated = _sync_shares()
    caps = _recompute_market_caps()
    log.info("Market data sync complete: prices=%d shares=%d caps=%d",
             prices_updated, shares_updated, caps)
    return shares_updated, prices_updated
