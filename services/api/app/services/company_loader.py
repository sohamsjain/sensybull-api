# services/api/app/services/company_loader.py
"""
Fetches the SEC company_tickers.json and bulk-loads companies into the DB.

Called once during app startup if the Company table is empty.
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


def ensure_companies_loaded(app) -> None:
    """If the Company table is empty, fetch from SEC and bulk-insert."""
    with app.app_context():
        count = db.session.query(Company.id).limit(1).count()
        if count > 0:
            log.info("Company table already populated — skipping SEC load")
            return

        user_agent = os.environ.get("SEC_USER_AGENT") or ""
        if not user_agent:
            log.warning(
                "SEC_USER_AGENT not set — cannot fetch SEC tickers. "
                "Set it in .env (e.g. 'Your Name your@email.com')"
            )
            return

        log.info("Company table empty — fetching SEC company_tickers.json ...")
        try:
            entries = _fetch_sec_tickers(user_agent)
        except Exception:
            log.exception("Failed to fetch SEC company tickers")
            return

        # Build lookup of existing tickers to avoid duplicates on partial loads
        companies = []
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

            companies.append(Company(
                name=name,
                ticker=ticker,
                cik=cik,
            ))

        if not companies:
            log.warning("SEC tickers JSON returned no valid entries")
            return

        # Bulk insert in batches
        BATCH = 1000
        for i in range(0, len(companies), BATCH):
            db.session.add_all(companies[i:i + BATCH])
            db.session.flush()

        db.session.commit()
        log.info("Loaded %d companies from SEC into the database", len(companies))

        # Backfill company_id on existing FilingEvents that have a ticker but no company_id
        _backfill_filing_events()


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

    # Build ticker -> company_id map
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
