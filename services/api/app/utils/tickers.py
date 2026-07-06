"""Ticker symbol validation shared by the public share surface and watchlists.

Accepts plain US tickers (MU, NVDA) plus class/unit suffixes (BRK.B, BF-B).
Deliberately strict: this validates *format* before any DB lookup so the
public endpoints never feed arbitrary strings into queries or share markup.
"""

import re

# 1-6 alphanumerics, optional . or - suffix of 1-4 alphanumerics (max 10 total,
# matching the Company.ticker column width).
TICKER_RE = re.compile(r'^[A-Z0-9]{1,6}([.\-][A-Z0-9]{1,4})?$')


def normalize_symbol(raw) -> str | None:
    """Uppercase and validate a ticker symbol. Returns None if malformed."""
    if not isinstance(raw, str):
        return None
    symbol = raw.strip().upper()
    if not TICKER_RE.match(symbol):
        return None
    return symbol
