"""
service.py — cached access to a company's fundamentals snapshot.

Wraps the companyfacts fetch + extract behind the ``company_fundamentals``
cache table so the analysis worker doesn't re-download the (large) companyfacts
JSON on every filing. Refreshes only when the cached row is older than
``max_age_days`` (companyfacts only changes when a new 10-Q/10-K is filed).
"""
import logging
from datetime import datetime, timedelta, timezone

from app.services.fundamentals.client import fetch_company_facts
from app.services.fundamentals.extract import build_snapshot

log = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 7


def get_company_snapshot(cik: str, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                         force_refresh: bool = False) -> dict | None:
    """Return a FundamentalsSnapshot dict for ``cik``, using the cache.

    Returns ``None`` when no XBRL data exists for the company (e.g. a foreign
    private issuer) and nothing is cached. Must be called inside an app context.
    """
    from app import db
    from app.models.company_fundamentals import CompanyFundamentals

    if not cik:
        return None
    padded = str(cik).strip().lstrip("0").zfill(10)

    row = CompanyFundamentals.query.filter_by(cik=padded).first()
    if row and not force_refresh:
        age = datetime.now(timezone.utc) - _aware(row.fetched_at)
        if age < timedelta(days=max_age_days):
            return row.snapshot_json

    facts = fetch_company_facts(padded)
    if facts is None:
        # Fetch failed / no XBRL — serve stale cache if we have one.
        return row.snapshot_json if row else None

    snapshot = build_snapshot(facts)
    try:
        if row is None:
            row = CompanyFundamentals(cik=padded)
            db.session.add(row)
        row.snapshot_json = snapshot
        row.as_of_period = snapshot.get("as_of")
        row.fetched_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("fundamentals: failed to cache snapshot for CIK %s", padded)
    return snapshot


def _aware(dt: datetime) -> datetime:
    """SQLite reads datetimes back naive; treat them as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
