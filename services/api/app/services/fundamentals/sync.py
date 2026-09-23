"""Fundamentals sync: FMP → fundamentals_period + company_fundamentals.

Three entry points:
- sync_company(company)         full fetch + rebuild for one company (~7 FMP calls)
- run_sync(...)                 cron: backfill never-synced companies, refresh
                                companies that reported since the last run,
                                and a slow rolling full refresh
- rebuild_snapshot(company)     recompute derived numbers from stored periods
                                (no FMP calls — used after the daily price sync)

Every function is safe to re-run; periods upsert on (company, type, end).
"""

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from app import db
from app.models.company import Company
from app.models.filing_event import FilingEvent
from app.models.fundamentals import (
    AMOUNT_COLUMNS, PER_SHARE_COLUMNS, SHARE_COLUMNS,
    CompanyFundamentals, FundamentalsPeriod,
)
from app.services.fundamentals import analysis, derive, mapper
from app.services.fundamentals.fmp_client import FMPClient, FMPError, fmp_symbol

log = logging.getLogger(__name__)

COMMIT_BATCH = 50
# Rolling refresh: re-fetch each company at least this often to pick up
# restatements and FMP corrections.
FULL_REFRESH_DAYS = 30
# Companies whose earnings date fell inside this window get refreshed.
CALENDAR_LOOKBACK_DAYS = 3
CALENDAR_LOOKAHEAD_DAYS = 1
# FMP publishes statements a little after the earnings date; retry a company
# for this long after its earnings date if no new period has appeared.
POST_EARNINGS_RETRY_DAYS = 10


def _now():
    return datetime.now(timezone.utc)


# ── One company ────────────────────────────────────────────────────────

def sync_company(company: Company, client: FMPClient | None = None, *, today: date | None = None) -> dict:
    """Fetch everything for one company and rebuild its snapshot.

    Returns {"periods": n, "has_fundamentals": bool, "error": str|None}.
    Never raises on FMP errors: the error is recorded on the snapshot so
    the cron moves on and the API shows "unavailable".
    """
    client = client or FMPClient()
    today = today or date.today()
    if not company.ticker:
        return {'periods': 0, 'has_fundamentals': False, 'error': 'no_ticker'}
    symbol = fmp_symbol(company.ticker)
    snap = _get_or_create_snapshot(company)
    fetched_at = _now()

    try:
        profile = client.profile(symbol)
        if not mapper.profile_is_operating_company(profile):
            snap.has_fundamentals = False
            snap.last_synced_at = fetched_at
            snap.sync_error = None if profile else 'no_profile'
            for col, val in mapper.map_profile(profile).items():
                setattr(snap, col, val)
            db.session.commit()
            return {'periods': 0, 'has_fundamentals': False, 'error': snap.sync_error}

        annual = mapper.merge_statements(
            client.income_statements(symbol, 'annual'),
            client.balance_sheets(symbol, 'annual'),
            client.cash_flows(symbol, 'annual'),
            'annual', fetched_at)
        quarters = mapper.merge_statements(
            client.income_statements(symbol, 'quarter'),
            client.balance_sheets(symbol, 'quarter'),
            client.cash_flows(symbol, 'quarter'),
            'quarter', fetched_at)
        dividends = _safe(client.dividends, symbol)
        eod = _safe(client.eod_light, symbol)
    except FMPError as exc:
        log.warning('FMP sync failed for %s: %s', company.ticker, exc)
        return _record_failure(company, str(exc), fetched_at)

    try:
        for col, val in mapper.map_profile(profile).items():
            setattr(snap, col, val)

        count = _upsert_periods(company, annual + quarters)

        refs = derive.price_references(eod, today)
        snap.price_ref_date = refs.get('ref_date')
        snap.price_1y_ago = _bounded(refs.get('1y'))
        snap.price_3y_ago = _bounded(refs.get('3y'))
        snap.price_5y_ago = _bounded(refs.get('5y'))
        snap.price_10y_ago = _bounded(refs.get('10y'))
        snap.high_52w = _bounded(refs.get('high_52w'))
        snap.low_52w = _bounded(refs.get('low_52w'))
        snap.dividends_ttm_ps = _bounded(derive.dividends_ttm_per_share(dividends, today))

        snap.has_fundamentals = any(p.get('revenue') is not None or p.get('net_income') is not None
                                    for p in annual + quarters)
        snap.sync_error = None
        snap.last_synced_at = fetched_at
        db.session.flush()
        rebuild_snapshot(company, snap=snap)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 — a bad row for one company must not end the run
        log.exception('storing fundamentals failed for %s', company.ticker)
        db.session.rollback()
        return _record_failure(company, f'store: {exc}', fetched_at)
    return {'periods': count, 'has_fundamentals': snap.has_fundamentals, 'error': None}


def _bounded(value):
    """Decimal safe for a Numeric(14, 4) column, else None."""
    from app.services.fundamentals.fields import to_decimal
    return to_decimal(value)


def _record_failure(company: Company, message: str, fetched_at: datetime) -> dict:
    """Mark the snapshot failed in its own transaction so the cron moves on."""
    try:
        snap = _get_or_create_snapshot(company)
        snap.sync_error = message[:300]
        snap.last_synced_at = fetched_at
        db.session.commit()
        has = bool(snap.has_fundamentals)
    except Exception:  # noqa: BLE001
        log.exception('could not record sync failure for %s', company.ticker)
        db.session.rollback()
        has = False
    return {'periods': 0, 'has_fundamentals': has, 'error': message}


def _safe(fn, *args):
    try:
        return fn(*args)
    except FMPError as exc:
        log.info('optional FMP call failed (%s): %s', getattr(fn, '__name__', fn), exc)
        return []


def _get_or_create_snapshot(company: Company) -> CompanyFundamentals:
    snap = CompanyFundamentals.query.filter_by(company_id=company.id).first()
    if snap is None:
        snap = CompanyFundamentals(company_id=company.id, has_fundamentals=False)
        db.session.add(snap)
        db.session.flush()
    return snap


def _upsert_periods(company: Company, period_dicts: list[dict]) -> int:
    existing = {
        (p.period_type, p.period_end): p
        for p in FundamentalsPeriod.query.filter_by(company_id=company.id).all()
    }
    columns = AMOUNT_COLUMNS + PER_SHARE_COLUMNS + SHARE_COLUMNS
    meta = ['fiscal_year', 'fiscal_period', 'filing_date', 'reported_currency',
            'source', 'source_fetched_at', 'quality_flags', 'raw']
    count = 0
    for pd in period_dicts:
        if not pd.get('period_end'):
            continue
        key = (pd['period_type'], pd['period_end'])
        row = existing.get(key)
        if row is None:
            row = FundamentalsPeriod(company_id=company.id, period_type=pd['period_type'],
                                     period_end=pd['period_end'])
            db.session.add(row)
            existing[key] = row
        for col in columns + meta:
            if col in pd:
                setattr(row, col, pd[col])
        count += 1
    return count


def rebuild_snapshot(company: Company, snap: CompanyFundamentals | None = None) -> CompanyFundamentals | None:
    """Recompute `derived` from stored periods + current price. No FMP calls."""
    snap = snap or CompanyFundamentals.query.filter_by(company_id=company.id).first()
    if snap is None:
        return None
    periods = FundamentalsPeriod.query.filter_by(company_id=company.id).all()
    annual = sorted((p.as_dict() for p in periods if p.period_type == 'annual'),
                    key=lambda p: p['period_end'], reverse=True)
    quarters = sorted((p.as_dict() for p in periods if p.period_type == 'quarter'),
                      key=lambda p: p['period_end'], reverse=True)
    price_refs = {
        '1y': snap.price_1y_ago, '3y': snap.price_3y_ago,
        '5y': snap.price_5y_ago, '10y': snap.price_10y_ago,
    }
    derived = derive.build_snapshot(
        annual, quarters,
        price=company.last_price,
        shares_outstanding=company.shares_outstanding,
        market_cap=company.market_cap,
        price_refs=price_refs,
        dividends_ttm_ps=snap.dividends_ttm_ps,
        high_52w=snap.high_52w, low_52w=snap.low_52w,
    )
    derived['analysis'] = analysis.build_analysis(annual, derived)
    snap.derived = derived
    ratios = derived['ratios']
    snap.pe_ttm = ratios.get('pe_ttm')
    snap.roce = ratios.get('roce')
    snap.roe = ratios.get('roe')
    snap.dividend_yield = ratios.get('dividend_yield')
    snap.latest_annual_end = annual[0]['period_end'] if annual else None
    snap.latest_quarter_end = quarters[0]['period_end'] if quarters else None
    snap.coverage_from = annual[-1]['period_end'].year if annual else None
    snap.fiscal_year_end_month = annual[0]['period_end'].month if annual else None
    return snap


# ── Cron ───────────────────────────────────────────────────────────────

def _priority_ids() -> set[str]:
    """Watchlisted companies and recent filers go first in any queue."""
    cutoff = _now() - timedelta(days=90)
    recent = db.session.query(FilingEvent.company_id).filter(
        FilingEvent.filing_date >= cutoff, FilingEvent.company_id.isnot(None)).distinct()
    ids = {row[0] for row in recent}
    watched = db.session.query(Company.id).filter(Company.watchlists.any())
    ids.update(row[0] for row in watched)
    return ids


def run_sync(*, client: FMPClient | None = None, limit: int | None = None,
             symbols: list[str] | None = None, full: bool = False,
             today: date | None = None) -> dict:
    """Cron entry point. Returns counts.

    Queue order: explicit symbols → never-synced (priority first, then by
    market cap) → companies that reported recently (earnings calendar) →
    rolling refresh (oldest last_synced_at first). `limit` caps FMP work
    per run (env FUNDAMENTALS_SYNC_LIMIT, default 6000 ≈ the whole
    universe on the Ultimate plan).
    """
    client = client or FMPClient()
    if not client.configured:
        log.warning('FMP_API_KEY not set — fundamentals sync skipped')
        return {'synced': 0, 'skipped': 'not_configured'}
    today = today or date.today()
    limit = limit or int(os.environ.get('FUNDAMENTALS_SYNC_LIMIT', '6000'))

    queue: list[Company] = []
    seen: set[str] = set()

    def push(companies):
        for c in companies:
            if c.id not in seen and c.ticker:
                seen.add(c.id)
                queue.append(c)

    if symbols:
        push(Company.query.filter(Company.ticker.in_([s.upper() for s in symbols])).all())
    else:
        synced_ids = db.session.query(CompanyFundamentals.company_id)
        never = (Company.query.filter(Company.ticker.isnot(None))
                 .filter(~Company.id.in_(synced_ids))
                 .order_by(Company.market_cap.desc().nullslast()).all())
        priority = _priority_ids()
        push([c for c in never if c.id in priority])
        # De-listed rows (outside the listed-stock universe) only when a
        # reader follows them or they filed recently — the priority set
        push([c for c in never if c.listed is not False])

        if not full:
            push(_recently_reported(client, today))
        cutoff = _now() - timedelta(days=0 if full else FULL_REFRESH_DAYS)
        stale = (Company.query.join(CompanyFundamentals)
                 .filter(CompanyFundamentals.last_synced_at <= cutoff)
                 .order_by(CompanyFundamentals.last_synced_at.asc()).all())
        push([c for c in stale if c.listed is not False or c.id in priority])

    queue = queue[:limit]
    started = time.monotonic()
    ok = errors = 0
    for i, company in enumerate(queue):
        result = sync_company(company, client, today=today)
        if result['error']:
            errors += 1
        else:
            ok += 1
        if (i + 1) % COMMIT_BATCH == 0:
            log.info('fundamentals sync: %d/%d done (%d errors, %d FMP calls)',
                     i + 1, len(queue), errors, client.calls)
    log.info('fundamentals sync complete: %d ok, %d errors, %d FMP calls in %.0fs',
             ok, errors, client.calls, time.monotonic() - started)
    return {'queued': len(queue), 'synced': ok, 'errors': errors, 'calls': client.calls}


def _recently_reported(client: FMPClient, today: date) -> list[Company]:
    """Companies whose earnings date fell in the recent window, plus those
    still waiting for FMP to publish the statements after their date."""
    start = today - timedelta(days=POST_EARNINGS_RETRY_DAYS)
    end = today + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
    try:
        rows = client.earnings_calendar(start, end)
    except FMPError as exc:
        log.warning('earnings calendar fetch failed: %s', exc)
        return []
    by_symbol: dict[str, date] = {}
    for row in rows:
        sym = (row.get('symbol') or '').upper()
        d = row.get('date')
        if not sym or not d:
            continue
        try:
            when = date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        if when > today:
            continue
        by_symbol[sym] = max(when, by_symbol.get(sym, when))
    if not by_symbol:
        return []
    tickers = {s.replace('.', '-') for s in by_symbol}
    companies = (Company.query.join(CompanyFundamentals)
                 .filter(Company.ticker.in_(tickers)).all())
    due = []
    for c in companies:
        snap = c.fundamentals
        reported = by_symbol.get(fmp_symbol(c.ticker))
        if not reported:
            continue
        # Skip once we already hold a period filed after the earnings date.
        if snap.last_synced_at and snap.last_synced_at.date() > reported \
                and snap.latest_quarter_end and (today - snap.latest_quarter_end).days < 120:
            continue
        due.append(c)
    return due


def rebuild_all_snapshots() -> int:
    """After the daily price sync: refresh price-dependent ratios everywhere."""
    snaps = CompanyFundamentals.query.filter_by(has_fundamentals=True).all()
    n = 0
    for i, snap in enumerate(snaps):
        rebuild_snapshot(snap.company, snap=snap)
        n += 1
        if (i + 1) % 500 == 0:
            db.session.commit()
    db.session.commit()
    return n
