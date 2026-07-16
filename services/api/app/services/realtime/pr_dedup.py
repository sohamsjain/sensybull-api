# services/api/app/services/realtime/pr_dedup.py
"""
Cross-source dedup between press-release events and SEC filings.

The same announcement can reach us three ways: on one or more newswires
(signal_type "PR") and again inside the follow-up 8-K (as an EX-99
exhibit). The ingest side stamps every event with content fingerprints;
this module decides, at persist time, whether an incoming event is a
duplicate of one already stored.

Match rule (keep in sync with services/ingest/press_release/fingerprint.py,
which computes the fingerprints — the two services share no code):
  - exact fingerprints equal, or
  - headline fingerprints equal, or
  - simhash Hamming distance <= SIMHASH_MATCH_DISTANCE,
and always only among events of the SAME company (ticker) within a
bounded time window.
"""

from datetime import datetime, timedelta, timezone

SIMHASH_MATCH_DISTANCE = 8

# An 8-K must follow its press release within the SEC's four-business-day
# deadline; five calendar days + weekend slack.
PR_TO_FILING_WINDOW_DAYS = 7

# The same release lands on a second wire within minutes.
CROSS_WIRE_WINDOW_DAYS = 2

# 8-K items that merely furnish/announce a press release. A matched 8-K is
# suppressed from the feed ONLY when all its items fall in this set —
# anything else (1.01 agreement, 5.02 departure, ...) is substance beyond
# the PR and the filing publishes normally. 9.01 never appears in items
# (ingest drops the exhibit list) but belongs here for safety.
WRAPPER_ITEMS = {"2.02", "7.01", "8.01", "9.01"}


def _hamming(simhash_a: str, simhash_b: str) -> int:
    try:
        a, b = int(simhash_a or "", 16), int(simhash_b or "", 16)
    except (ValueError, TypeError):
        return 65
    if a == 0 or b == 0:
        return 65  # zero means "no fingerprint", never a wildcard
    return bin(a ^ b).count("1")


def fingerprints_match(fp: dict, event) -> bool:
    """One ingest-side fingerprint dict vs a stored FilingEvent row."""
    if fp.get("exact") and fp["exact"] == event.content_fingerprint:
        return True
    if fp.get("headline") and fp["headline"] == event.headline_fingerprint:
        return True
    return _hamming(fp.get("simhash", ""), event.content_simhash or "") <= SIMHASH_MATCH_DISTANCE


def find_matching_event(ticker: str, fingerprints: list[dict],
                        window_days: int, signal_types: list[str]):
    """Most recent stored event for `ticker` whose fingerprints match any of
    `fingerprints`, within `window_days` of receipt. None when no match.

    Candidates are a handful of rows (one company, days-long window), so
    the fingerprint comparison happens in Python.
    """
    from app.models.filing_event import FilingEvent

    if not ticker or not fingerprints:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    candidates = (
        FilingEvent.query
        .filter(FilingEvent.ticker == ticker.upper())
        .filter(FilingEvent.signal_type.in_(signal_types))
        .filter(FilingEvent.created_at >= cutoff)
        .order_by(FilingEvent.created_at.desc())
        .limit(50)
        .all()
    )
    for event in candidates:
        for fp in fingerprints:
            if fingerprints_match(fp, event):
                return event
    return None


def is_wrapper_only(items: list[dict]) -> bool:
    """True when the 8-K's parsed items are non-empty and all fall in
    WRAPPER_ITEMS — i.e. the filing exists to furnish the press release."""
    numbers = {it.get("number", "") for it in items if isinstance(it, dict)}
    return bool(numbers) and numbers <= WRAPPER_ITEMS
