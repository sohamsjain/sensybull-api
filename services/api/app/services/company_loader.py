# services/api/app/services/company_loader.py
"""
Fetches the SEC company_tickers.json and upserts companies into the DB.

Two entry points:
- ensure_companies_loaded(app): called on app startup, loads if table is empty
- sync_companies(): idempotent upsert, safe to run daily via cron
"""

import json
import logging
import os
import urllib.request

from app import db
from app.models.company import Company

log = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _fetch_sec_tickers(user_agent: str) -> list[dict]:
    """Download the SEC company_tickers.json and return a list of dicts."""
    req = urllib.request.Request(
        SEC_TICKERS_URL,
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8", errors="replace"))
    return list(raw.values())


def sync_companies() -> tuple[int, int]:
    """Fetch SEC tickers and upsert into the Company table.

    Returns (added_count, total_sec_count). Safe to call repeatedly.
    """
    user_agent = os.environ.get("SEC_USER_AGENT") or ""
    if not user_agent:
        log.warning(
            "SEC_USER_AGENT not set — cannot fetch SEC tickers. "
            "Set it in .env (e.g. 'Your Name your@email.com')"
        )
        return 0, 0

    log.info("Syncing companies from SEC company_tickers.json ...")
    try:
        entries = _fetch_sec_tickers(user_agent)
    except Exception:
        log.exception("Failed to fetch SEC company tickers")
        return 0, 0

    # Build lookup of existing tickers and CIKs
    existing_tickers = {
        c.ticker for c in db.session.query(Company.ticker).filter(
            Company.ticker.isnot(None)).all()
    }
    existing_ciks = {
        c.cik for c in db.session.query(Company.cik).filter(
            Company.cik.isnot(None)).all()
    }

    new_companies = []
    seen_tickers = set()
    seen_ciks = set()

    for entry in entries:
        ticker = (entry.get("ticker") or "").upper().strip()
        cik = str(entry.get("cik_str", "")).zfill(10)
        name = entry.get("title", "").strip()

        if not ticker or not name:
            continue
        if ticker in seen_tickers or cik in seen_ciks:
            continue

        seen_tickers.add(ticker)
        seen_ciks.add(cik)

        # Skip if already in DB
        if ticker in existing_tickers or cik in existing_ciks:
            continue

        new_companies.append(Company(
            name=name,
            ticker=ticker,
            cik=cik,
        ))

    if new_companies:
        BATCH = 1000
        for i in range(0, len(new_companies), BATCH):
            db.session.add_all(new_companies[i:i + BATCH])
            db.session.flush()
        db.session.commit()
        log.info("Added %d new companies from SEC (total in dataset: %d)",
                 len(new_companies), len(entries))
    else:
        log.info("No new companies to add (all %d SEC entries already in DB)", len(entries))

    # Backfill company_id on orphaned filing events
    _backfill_filing_events()

    return len(new_companies), len(entries)


def ensure_companies_loaded(app) -> None:
    """On startup, sync companies if the table is empty or underpopulated."""
    with app.app_context():
        try:
            count = db.session.query(Company.id).count()
        except Exception:
            db.session.rollback()
            log.info("Company table not found — skipping SEC load (run migrations first)")
            return

        if count >= 5000:
            log.info("Company table has %d rows — skipping startup sync", count)
            return

        log.info("Company table has only %d rows — running sync", count)
        sync_companies()


def _backfill_filing_events() -> None:
    """Link orphaned FilingEvents to their Company rows by ticker."""
    from app.models.filing_event import FilingEvent

    orphans = (
        FilingEvent.query
        .filter(FilingEvent.company_id.is_(None))
        .filter(FilingEvent.ticker.isnot(None))
        .all()
    )
    if not orphans:
        return

    ticker_map = {
        c.ticker: c.id
        for c in Company.query.filter(Company.ticker.isnot(None)).all()
    }

    linked = 0
    for event in orphans:
        company_id = ticker_map.get(event.ticker)
        if company_id:
            event.company_id = company_id
            linked += 1

    if linked:
        db.session.commit()
        log.info("Backfilled company_id on %d/%d orphaned filing events", linked, len(orphans))
