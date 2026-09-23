# services/api/app/services/market_data/sync.py
"""
Daily market-data sync, all from FMP: last price and market cap from batch
quotes, shares outstanding from `/shares-float-all`. Run via
`flask sync-market-data` (cron), after sync-companies so newly listed
companies are present.

Market cap is FMP's own figure from the quote (the number every FMP-backed
screen shows, and multi-class issuers are counted whole); shares × price
is only the fallback for a quote that carries none. EDGAR share counts
were dropped in Sept 2026 along with the SEC company list.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from app import db
from app.models.company import Company
from app.services.fundamentals import fields as F
from app.services.fundamentals.fmp_client import FMPError
from app.services.market_data import prices

log = logging.getLogger(__name__)

COMMIT_BATCH = 1000
# /shares-float-all page size and a runaway guard (~10k symbols a page
# at most matter to us; FMP covers many more exchanges).
SHARES_PAGE_SIZE = 5000
SHARES_MAX_PAGES = 40


def _sync_prices() -> tuple[int, set[str]]:
    """FMP batch quotes → last_price and market_cap.

    Returns (price updates, ids of companies whose cap came from FMP).
    """
    companies = Company.query.filter(Company.ticker.isnot(None)).all()
    by_symbol = {prices.normalize_ticker(c.ticker): c for c in companies}

    try:
        quotes = prices.get_quotes(list(by_symbol.keys()), skip_failed_batches=True)
    except prices.MarketDataError:
        log.exception("FMP quote fetch failed — prices not updated")
        return 0, set()

    now = datetime.now(timezone.utc)
    updated = 0
    capped: set[str] = set()
    for i, (symbol, company) in enumerate(by_symbol.items()):
        quote = quotes.get(symbol)
        price = prices.quote_price(quote)
        if price is None:
            continue
        company.last_price = Decimal(str(price))
        company.price_updated_at = now
        cap = F.to_int(F.pick(quote, F.PROFILE_FIELDS["market_cap"]))
        if cap and cap > 0:
            company.market_cap = cap
            capped.add(company.id)
        updated += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Prices updated for %d/%d tickered companies (%d with an FMP market cap)",
             updated, len(by_symbol), len(capped))
    return updated, capped


def _fetch_shares() -> dict[str, tuple[int, object]]:
    """symbol → (outstanding shares, as-of date) from every page FMP has."""
    client = prices._fmp()
    out: dict[str, tuple[int, object]] = {}
    for page in range(SHARES_MAX_PAGES):
        try:
            rows = client.shares_float_page(page, SHARES_PAGE_SIZE)
        except FMPError:
            log.exception("FMP shares-float page %d failed — stopping there", page)
            break
        if not rows:
            break
        for row in rows:
            symbol = (row.get("symbol") or "").upper() if isinstance(row, dict) else ""
            shares = F.to_int(row.get("outstandingShares")) if symbol else None
            if shares and shares > 0:
                out[symbol] = (shares, F.to_date(row.get("date")))
        if len(rows) < SHARES_PAGE_SIZE:
            break
    return out


def _sync_shares() -> int:
    """FMP outstanding shares → shares_outstanding / shares_as_of."""
    shares = _fetch_shares()
    if not shares:
        log.warning("No share counts from FMP — shares not updated")
        return 0
    updated = 0
    companies = Company.query.filter(Company.ticker.isnot(None)).all()
    for i, company in enumerate(companies):
        hit = shares.get(prices.normalize_ticker(company.ticker))
        if not hit:
            continue
        # Never regress to an older value than we already hold
        if company.shares_as_of and hit[1] and hit[1] < company.shares_as_of:
            continue
        company.shares_outstanding, company.shares_as_of = hit
        updated += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Shares outstanding updated for %d companies from FMP", updated)
    return updated


def _recompute_market_caps(fmp_capped: set[str]) -> int:
    """market_cap = shares × last price where the quote carried no cap."""
    companies = (
        Company.query
        .filter(Company.shares_outstanding.isnot(None))
        .filter(Company.last_price.isnot(None))
        .all()
    )
    companies = [c for c in companies if c.id not in fmp_capped]
    updated = 0
    for i, company in enumerate(companies):
        cap = int(company.shares_outstanding * float(company.last_price))
        if company.market_cap != cap:
            company.market_cap = cap
            updated += 1
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()
    log.info("Market cap computed from shares × price for %d companies (%d changed)",
             len(companies), updated)
    return len(companies)


def sync_market_data() -> tuple[int, int]:
    """Returns (companies with shares updated, companies with price updated)."""
    prices_updated, fmp_capped = _sync_prices()
    shares_updated = _sync_shares()
    caps = _recompute_market_caps(fmp_capped)
    log.info("Market data sync complete: prices=%d shares=%d caps=%d",
             prices_updated, shares_updated, caps)
    return shares_updated, prices_updated
