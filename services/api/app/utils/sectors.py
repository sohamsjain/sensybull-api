"""Map SEC SIC codes to their top-level division (a coarse "sector").

EDGAR gives us a 4-digit SIC code per company; the division ranges are fixed
by the SIC standard, so a range table is enough for share pages and SEO
metadata — no external data needed.
"""

_SIC_DIVISIONS = [
    (100, 999, 'Agriculture, Forestry & Fishing'),
    (1000, 1499, 'Mining'),
    (1500, 1799, 'Construction'),
    (2000, 3999, 'Manufacturing'),
    (4000, 4999, 'Transportation & Utilities'),
    (5000, 5199, 'Wholesale Trade'),
    (5200, 5999, 'Retail Trade'),
    (6000, 6799, 'Finance, Insurance & Real Estate'),
    (7000, 8999, 'Services'),
    (9100, 9999, 'Public Administration'),
]


def sic_to_sector(sic) -> str | None:
    """Return the SIC division name for a (string) SIC code, or None."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    for low, high, name in _SIC_DIVISIONS:
        if low <= code <= high:
            return name
    return None
