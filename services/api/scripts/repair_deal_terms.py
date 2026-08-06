"""
Repair deal_terms values that were persisted as stringified containers.

Before the ingest-side fix, `_parse_briefing` coerced every LLM deal_terms
value with a bare str(), so when the model answered with a nested object
(e.g. {"deal_value": {"$sum": "11500000000"}}) the Python repr was stored
verbatim and rendered in the UI as literal "{'$sum': '11500000000'}".

This walks the affected rows and rewrites those values in place, unwrapping
single-scalar containers and dropping anything ambiguous. Both storage
sites are repaired: filing_event.briefing_json["deal_terms"] and the
denormalized copy in event_type.attributes.

Usage:
    python scripts/repair_deal_terms.py --dry-run   # report only, no writes
    python scripts/repair_deal_terms.py             # apply

Unwrap rules mirror _coerce_deal_terms/_scalar_term in
services/ingest/briefing.py — keep the two in sync.
"""

import argparse
import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from app import create_app, db
from app.models.event_type import EventType
from app.models.filing_event import FilingEvent

# A stored value is suspect when it looks like a repr rather than a figure.
_CONTAINER_PREFIXES = ("{", "[", "(")


def _scalar_term(value, _depth: int = 0) -> str:
    """Return value as a display string, or "" if it isn't scalar-shaped."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if _depth >= 2:
        return ""
    if isinstance(value, dict):
        inner = list(value.values())
    elif isinstance(value, (list, tuple)):
        inner = list(value)
    else:
        return ""
    usable = [v for v in inner if v or v == 0]
    if len(usable) != 1:
        return ""
    return _scalar_term(usable[0], _depth + 1)


def repair_value(stored: str) -> str | None:
    """Return the repaired string for a stored deal-terms value.

    None means "leave this row alone" — either it was already fine or the
    repr held several values and picking one would be a guess.
    """
    if not isinstance(stored, str):
        return None
    text = stored.strip()
    if not text.startswith(_CONTAINER_PREFIXES):
        return None
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None      # not a repr after all; don't touch it
    repaired = _scalar_term(parsed)
    return repaired or None


def repair_terms(terms: dict) -> tuple[dict, list[str]]:
    """Repair a deal_terms dict. Returns (new_terms, changed_keys)."""
    out = dict(terms)
    changed = []
    for key, value in terms.items():
        repaired = repair_value(value)
        if repaired is not None and repaired != value:
            out[key] = repaired
            changed.append(key)
    return out, changed


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
            terms = briefing.get("deal_terms")
            if not isinstance(terms, dict):
                continue
            new_terms, changed = repair_terms(terms)
            if not changed:
                continue
            events_fixed += 1
            for key in changed:
                print(f"  event {event.id} ({event.ticker or '—'}) "
                      f"{key}: {terms[key]!r} → {new_terms[key]!r}")
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
            attrs = row.attributes or {}
            if not isinstance(attrs, dict):
                continue
            new_attrs, changed = repair_terms(attrs)
            if not changed:
                continue
            attrs_fixed += 1
            if not args.dry_run:
                row.attributes = new_attrs

        if args.dry_run:
            print(f"\nDry run: {events_fixed} filing_event row(s) and "
                  f"{attrs_fixed} event_type row(s) would be repaired.")
            db.session.rollback()
        else:
            db.session.commit()
            print(f"\nRepaired {events_fixed} filing_event row(s) and "
                  f"{attrs_fixed} event_type row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
