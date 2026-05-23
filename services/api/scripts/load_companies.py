"""
Load companies from SEC EDGAR into the database using edgartools.

Usage:
    python scripts/load_companies.py                  # Initial load / full sync
    python scripts/load_companies.py --ticker AAPL    # Load or update a single company
    python scripts/load_companies.py --limit 100      # Load first N companies (useful for testing)
    python scripts/load_companies.py --exchange NYSE   # Load companies from a specific exchange
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from edgar import set_identity, Company as EdgarCompany
from edgar.reference.tickers import get_company_tickers, get_companies_by_exchange

edgar_identity = os.environ.get('EDGAR_IDENTITY')
if not edgar_identity:
    print("Error: EDGAR_IDENTITY environment variable is not set.")
    print("Set it to your name and email, e.g.: EDGAR_IDENTITY='Your Name your@email.com'")
    sys.exit(1)
set_identity(edgar_identity)

from app import create_app, db
from app.models.company import Company


def get_company_detail(cik: int, ticker: str) -> dict | None:
    """Fetch detailed company info from EDGAR by CIK."""
    try:
        ec = EdgarCompany(cik)
        return {
            'name': ec.name,
            'ticker': ticker or ec.get_ticker(),
            'cik': str(ec.cik),
            'sic': str(ec.sic) if ec.sic else None,
            'state_of_incorporation': ec.data.state_of_incorporation,
        }
    except Exception as e:
        print(f"  [WARN] Failed to fetch details for CIK {cik} ({ticker}): {e}")
        return None


def upsert_company(data: dict) -> str:
    """Insert or update a company. Returns 'created', 'updated', or 'skipped'.

    Matches on ticker first (unique per share class), then CIK.
    """
    existing = None

    with db.session.no_autoflush:
        if data['ticker']:
            existing = db.session.query(Company).filter_by(ticker=data['ticker']).first()
        if not existing and data['cik']:
            existing = db.session.query(Company).filter_by(cik=data['cik']).first()

    if existing:
        changed = False
        for field in ('name', 'cik', 'sic', 'state_of_incorporation'):
            new_val = data.get(field)
            if new_val and getattr(existing, field) != new_val:
                setattr(existing, field, new_val)
                changed = True
        return 'updated' if changed else 'skipped'
    else:
        company = Company(
            name=data['name'],
            ticker=data['ticker'],
            cik=data['cik'],
            sic=data['sic'],
            state_of_incorporation=data['state_of_incorporation'],
        )
        db.session.add(company)
        return 'created'


def load_single(ticker: str):
    """Load or update a single company by ticker."""
    print(f"Fetching {ticker} from EDGAR...")
    data = get_company_detail(0, ticker)
    if not data:
        # Retry using ticker string directly
        try:
            ec = EdgarCompany(ticker)
            data = {
                'name': ec.name,
                'ticker': ticker,
                'cik': str(ec.cik),
                'sic': str(ec.sic) if ec.sic else None,
                'state_of_incorporation': ec.data.state_of_incorporation,
            }
        except Exception as e:
            print(f"Failed to load {ticker}: {e}")
            return

    action = upsert_company(data)
    db.session.commit()
    print(f"  {ticker}: {action}")


def load_bulk(limit: int | None = None, exchange: str | None = None):
    """Load companies in bulk from the EDGAR company tickers dataset."""
    print("Fetching company tickers list from EDGAR...")

    if exchange:
        tickers_df = get_companies_by_exchange(exchange)
        print(f"Found {len(tickers_df)} companies on {exchange}")
    else:
        tickers_df = get_company_tickers()
        print(f"Found {len(tickers_df)} total companies")

    dupes = tickers_df[tickers_df['ticker'].duplicated(keep=False)]
    if len(dupes) > 0:
        print(f"Dropping {len(tickers_df) - len(tickers_df.drop_duplicates(subset='ticker', keep='first'))} "
              f"duplicate tickers (keeping first occurrence)")
    tickers_df = tickers_df.drop_duplicates(subset='ticker', keep='first')

    if limit:
        tickers_df = tickers_df.head(limit)
        print(f"Processing first {limit} companies")

    stats = {'created': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
    total = len(tickers_df)

    for i, row in enumerate(tickers_df.itertuples(), 1):
        cik = row.cik
        ticker = row.ticker
        company_name = row.company

        if i % 50 == 0 or i == total:
            print(f"  Progress: {i}/{total} "
                  f"(created={stats['created']}, updated={stats['updated']}, "
                  f"skipped={stats['skipped']}, failed={stats['failed']})")

        # First try a fast upsert with just the ticker list data
        basic_data = {
            'name': company_name,
            'ticker': ticker,
            'cik': str(cik),
            'sic': None,
            'state_of_incorporation': None,
        }

        existing = None
        with db.session.no_autoflush:
            if basic_data['ticker']:
                existing = db.session.query(Company).filter_by(ticker=basic_data['ticker']).first()
            if not existing and basic_data['cik']:
                existing = db.session.query(Company).filter_by(cik=basic_data['cik']).first()

        if existing and existing.sic:
            # Already has detailed data, just update name/ticker if needed
            action = upsert_company(basic_data)
        else:
            # New company or missing detail — fetch full info from EDGAR
            detailed = get_company_detail(cik, ticker)
            if detailed:
                action = upsert_company(detailed)
            else:
                # Fall back to basic data from tickers list
                action = upsert_company(basic_data)
                if action == 'created':
                    stats['failed'] += 1
                    stats['created'] -= 1  # will be re-added below, net out

        stats[action] += 1

        # Commit in batches
        if i % 50 == 0:
            db.session.commit()

    db.session.commit()

    print(f"\nDone! Created={stats['created']}, Updated={stats['updated']}, "
          f"Skipped={stats['skipped']}, Failed detail fetch={stats['failed']}")


def main():
    parser = argparse.ArgumentParser(description='Load companies from SEC EDGAR')
    parser.add_argument('--ticker', type=str, help='Load a single company by ticker')
    parser.add_argument('--limit', type=int, help='Limit number of companies to load')
    parser.add_argument('--exchange', type=str, help='Filter by exchange (e.g. NYSE, Nasdaq)')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.ticker:
            load_single(args.ticker.upper())
        else:
            load_bulk(limit=args.limit, exchange=args.exchange)


if __name__ == '__main__':
    main()
