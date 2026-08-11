"""
Normalize the case of deal_terms already persisted in the database.

Events stored before `normalize_deal_terms` was wired into the Redis
subscriber carry the model's raw casing — "definitive agreement signed",
"stock", "spac merger" — which the UI renders verbatim. This rewrites those
rows in place using the same normalizer the subscriber now applies, so old
and new events read alike. Both storage sites are covered:
filing_event.briefing_json["deal_terms"] and the denormalized copy in
event_type.attributes.

Idempotent: normalizing an already-normalized value is a no-op, so a
re-run reports zero changes.

Usage:
    python scripts/normalize_deal_terms_case.py --dry-run   # report only
    python scripts/normalize_deal_terms_case.py            # apply
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from app import create_app, db
from app.models.event_type import EventType
from app.models.filing_event import FilingEvent
from app.utils.deal_terms import normalize_deal_terms


def changed_entries(before: dict, after: dict) -> list:
    """Describe what normalization altered, as (key, old, new) triples.

    A renamed key ("Deal Value" → "deal_value") is reported against its old
    value so the printed diff stays readable.
    """
    out = []
    for (old_key, old_value), (new_key, new_value) in zip(
        before.items(), after.items()
    ):
        if old_key != new_key or old_value != new_value:
            out.append((old_key if old_key == new_key else f"{old_key}→{new_key}",
                        old_value, new_value))
    return out


def normalize_row(terms: object) -> tuple:
    """Return (new_terms, changes) for one stored deal_terms dict."""
    if not isinstance(terms, dict) or not terms:
        return terms, []
    new_terms = normalize_deal_terms(terms)
    if new_terms == terms:
        return terms, []
    # Dropped or reordered entries mean the stored dict was malformed in a
    # way this script shouldn't paper over silently — report the whole diff.
    if len(new_terms) != len(terms):
        return new_terms, [("<entries>", terms, new_terms)]
    return new_terms, changed_entries(terms, new_terms)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        events_fixed = 0
        attrs_fixed = 0

        events = (
            db.session.query(FilingEvent)
            .filter(FilingEvent.briefing_json.isnot(None))
            .all()
        )
        for event in events:
            briefing = event.briefing_json or {}
            if not isinstance(briefing, dict):
                continue
            new_terms, changes = normalize_row(briefing.get("deal_terms"))
            if not changes:
                continue
            events_fixed += 1
            for key, old, new in changes:
                print(f"  event {event.id} ({event.ticker or '—'}) "
                      f"{key}: {old!r} → {new!r}")
            if not args.dry_run:
                # Reassign the whole dict: SQLAlchemy's JSON column does not
                # track in-place mutation of the decoded value.
                event.briefing_json = {**briefing, "deal_terms": new_terms}

        rows = (
            db.session.query(EventType)
            .filter(EventType.attributes.isnot(None))
            .all()
        )
        for row in rows:
            new_attrs, changes = normalize_row(row.attributes)
            if not changes:
                continue
            attrs_fixed += 1
            if not args.dry_run:
                row.attributes = new_attrs

        if args.dry_run:
            print(f"\nDry run: {events_fixed} filing_event row(s) and "
                  f"{attrs_fixed} event_type row(s) would be normalized.")
            db.session.rollback()
        else:
            db.session.commit()
            print(f"\nNormalized {events_fixed} filing_event row(s) and "
                  f"{attrs_fixed} event_type row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
