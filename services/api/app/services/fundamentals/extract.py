"""
extract.py — normalize raw SEC companyfacts JSON into a FundamentalsSnapshot.

The companyfacts payload nests every reported XBRL concept under
``facts.<taxonomy>.<Concept>.units.<unit>`` as a list of period observations.
For each metric we take the **latest reported value**, using a fallback list of
concept tags (companies tag the "same" line item differently), and record what
was missing so the LLM and UI can be honest about gaps.

Pure functions, no I/O — easy to unit-test against a fixture payload.
"""
from datetime import date

# Balance-sheet (instant) metrics: ordered fallback concept lists.
_INSTANT_CONCEPTS = {
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
}
# Debt is assembled from components (long-term + current) to avoid double counts.
_LONG_TERM_DEBT = ["LongTermDebtNoncurrent"]
_CURRENT_DEBT = ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]
_TOTAL_DEBT_FALLBACK = ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"]

# Income-statement (duration) metrics — prefer full-year periods.
_ANNUAL_CONCEPTS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestAndDebtExpense",
    ],
}

_SHARES_CONCEPTS = [
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
]


def _entries(facts: dict, taxonomy: str, concept: str, unit: str):
    try:
        return facts["facts"][taxonomy][concept]["units"][unit]
    except (KeyError, TypeError):
        return None


def _period_days(start, end):
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        return (e - s).days
    except (TypeError, ValueError):
        return None


def _latest_instant(facts: dict, concepts):
    """Latest point-in-time value for the first present concept. -> (val, end)."""
    for concept in concepts:
        for tax in ("us-gaap", "ifrs-full"):
            entries = _entries(facts, tax, concept, "USD")
            if not entries:
                continue
            best = None  # (end, filed, val)
            for e in entries:
                end, val = e.get("end"), e.get("val")
                if not end or val is None:
                    continue
                key = (end, e.get("filed") or "")
                if best is None or key > best[:2]:
                    best = (end, e.get("filed") or "", val)
            if best is not None:
                return best[2], best[0]
    return None, None


def _latest_annual(facts: dict, concepts):
    """Latest full-year (≥350-day) value, falling back to latest of any period."""
    for concept in concepts:
        for tax in ("us-gaap", "ifrs-full"):
            entries = _entries(facts, tax, concept, "USD")
            if not entries:
                continue
            rows = []  # (end, filed, val, days)
            for e in entries:
                end, val = e.get("end"), e.get("val")
                if not end or val is None:
                    continue
                rows.append((end, e.get("filed") or "", val, _period_days(e.get("start"), end)))
            if not rows:
                continue
            annual = [r for r in rows if r[3] is not None and r[3] >= 350]
            pool = annual or rows
            pool.sort(key=lambda r: (r[0], r[1]))
            return pool[-1][2], pool[-1][0]
    return None, None


def _latest_shares(facts: dict):
    for tax, concept in _SHARES_CONCEPTS:
        entries = _entries(facts, tax, concept, "shares")
        if not entries:
            continue
        best = None
        for e in entries:
            end, val = e.get("end"), e.get("val")
            if not end or val is None:
                continue
            key = (end, e.get("filed") or "")
            if best is None or key > best[:2]:
                best = (end, e.get("filed") or "", val)
        if best is not None:
            return best[2], best[0]
    return None, None


def build_snapshot(facts: dict | None) -> dict:
    """Normalize companyfacts JSON into a FundamentalsSnapshot dict.

    Shape::

        {
          "entity_name": str | None,
          "as_of": "YYYY-MM-DD" | None,   # latest balance-sheet period end used
          "currency": "USD",
          "metrics": { <metric>: float | None, ... },
          "missing": [<metric>, ...],     # metrics we couldn't find
        }
    """
    if not facts or not isinstance(facts, dict):
        return {"entity_name": None, "as_of": None, "currency": "USD",
                "metrics": {}, "missing": ["all"]}

    metrics: dict[str, float | None] = {}
    as_of_candidates: list[str] = []

    for name, concepts in _INSTANT_CONCEPTS.items():
        val, end = _latest_instant(facts, concepts)
        metrics[name] = val
        if end:
            as_of_candidates.append(end)

    # Debt: components first, total fallback otherwise.
    lt, lt_end = _latest_instant(facts, _LONG_TERM_DEBT)
    cur, cur_end = _latest_instant(facts, _CURRENT_DEBT)
    metrics["long_term_debt"] = lt
    metrics["current_debt"] = cur
    if lt is not None or cur is not None:
        metrics["total_debt"] = (lt or 0) + (cur or 0)
    else:
        metrics["total_debt"], tot_end = _latest_instant(facts, _TOTAL_DEBT_FALLBACK)
        if tot_end:
            as_of_candidates.append(tot_end)
    for e in (lt_end, cur_end):
        if e:
            as_of_candidates.append(e)

    for name, concepts in _ANNUAL_CONCEPTS.items():
        val, _ = _latest_annual(facts, concepts)
        metrics[name] = val

    shares, _ = _latest_shares(facts)
    metrics["shares_outstanding"] = shares

    missing = [k for k, v in metrics.items() if v is None]
    as_of = max(as_of_candidates) if as_of_candidates else None

    return {
        "entity_name": facts.get("entityName"),
        "as_of": as_of,
        "currency": "USD",
        "metrics": metrics,
        "missing": missing,
    }
