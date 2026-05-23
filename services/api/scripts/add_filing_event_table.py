"""
Idempotently creates the filing_event table.

    python scripts/add_filing_event_table.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app, db
from app.models.filing_event import FilingEvent  # noqa: F401 – registers the table


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        print("[ok] filing_event table created (or already exists)")


if __name__ == "__main__":
    main()
