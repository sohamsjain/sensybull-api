"""
form4.py — Form 4 (insider transaction) parsing and qualification.

Form 4s are structured XML, so no LLM is involved: the briefing is built
programmatically. "Less noise, more signal" means almost every Form 4 is
dropped — only meaningful open-market BUYS (transaction code P) by an
officer, director, or 10% owner survive:

- single insider buys >= FORM4_MIN_BUY_USD           → tier-2 event
- 2+ distinct insiders buy within a rolling window   → tier-1 cluster event

Sales, option exercises, grants, and 10b5-1 plan trades never emit events.
"""

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from models import Briefing

log = logging.getLogger(__name__)

MIN_BUY_USD = float(os.environ.get("FORM4_MIN_BUY_USD", "100000"))
CLUSTER_WINDOW_DAYS = int(os.environ.get("FORM4_CLUSTER_WINDOW_DAYS", "7"))
CLUSTER_MIN_INSIDERS = int(os.environ.get("FORM4_CLUSTER_MIN_INSIDERS", "2"))


@dataclass
class Form4Transaction:
    code: str          # "P" = open-market purchase, "S" = sale, ...
    shares: float
    price: float       # 0.0 when only footnoted
    date: str          # YYYY-MM-DD
    acquired: bool     # (A) vs (D)


@dataclass
class Form4:
    issuer_cik: str        # zero-padded 10 digits
    issuer_name: str
    issuer_ticker: str
    owner_name: str
    owner_cik: str
    is_director: bool
    is_officer: bool
    is_ten_pct: bool
    officer_title: str
    aff_10b5_1: bool       # trade made under a Rule 10b5-1 plan
    shares_after: float | None = None
    transactions: list[Form4Transaction] = field(default_factory=list)


def _text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _flag(el: ET.Element | None, path: str) -> bool:
    return _text(el, path) in ("1", "true")


def _num(el: ET.Element | None, path: str) -> float:
    raw = _text(el, path)
    try:
        return float(raw)
    except ValueError:
        return 0.0


def parse_form4_xml(xml_text: str) -> Form4 | None:
    """Parse an ownershipDocument XML into a Form4. None on failure."""
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    if root.tag != "ownershipDocument":
        return None

    issuer = root.find("issuer")
    owner = root.find("reportingOwner")
    owner_id = owner.find("reportingOwnerId") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None

    ticker = _text(issuer, "issuerTradingSymbol").upper()
    if ticker in ("NONE", "N/A", "NA"):
        ticker = ""

    transactions: list[Form4Transaction] = []
    shares_after: float | None = None
    table = root.find("nonDerivativeTable")
    if table is not None:
        for txn in table.findall("nonDerivativeTransaction"):
            amounts = txn.find("transactionAmounts")
            code = _text(txn, "transactionCoding/transactionCode").upper()
            acquired = _text(amounts, "transactionAcquiredDisposedCode/value") == "A"
            shares = _num(amounts, "transactionShares/value")
            price = _num(amounts, "transactionPricePerShare/value")
            date = _text(txn, "transactionDate/value")
            transactions.append(Form4Transaction(
                code=code, shares=shares, price=price, date=date, acquired=acquired,
            ))
            post = _num(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
            if post:
                shares_after = post

    return Form4(
        issuer_cik=_text(issuer, "issuerCik").zfill(10),
        issuer_name=_text(issuer, "issuerName"),
        issuer_ticker=ticker,
        owner_name=_text(owner_id, "rptOwnerName"),
        owner_cik=_text(owner_id, "rptOwnerCik").zfill(10),
        is_director=_flag(rel, "isDirector"),
        is_officer=_flag(rel, "isOfficer"),
        is_ten_pct=_flag(rel, "isTenPercentOwner"),
        officer_title=_text(rel, "officerTitle"),
        aff_10b5_1=_flag(root, "aff10b5One"),
        shares_after=shares_after,
        transactions=transactions,
    )


def qualifying_buy_value(f4: Form4, min_usd: float = MIN_BUY_USD) -> float:
    """Dollar value of qualifying open-market buys; 0.0 if the filing is noise.

    Noise: 10b5-1 plan trades (pre-scheduled, no signal), filers who are not
    officers/directors/10% owners, footnote-only prices, and totals below
    the minimum.
    """
    if f4.aff_10b5_1:
        return 0.0
    if not (f4.is_director or f4.is_officer or f4.is_ten_pct):
        return 0.0
    total = sum(
        t.shares * t.price
        for t in f4.transactions
        if t.code == "P" and t.acquired and t.price > 0
    )
    return total if total >= min_usd else 0.0


def owner_role(f4: Form4) -> str:
    parts: list[str] = []
    if f4.is_officer:
        parts.append(f4.officer_title or "Officer")
    if f4.is_director:
        parts.append("Director")
    if not parts and f4.is_ten_pct:
        parts.append("10% owner")
    return " & ".join(parts) or "Insider"


def _fmt_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value / 1_000:.0f}k"


def build_form4_briefing(f4: Form4, value: float, window_buys: list[dict]) -> Briefing:
    """Programmatic briefing — no LLM. window_buys includes this filing's buy."""
    buys = [t for t in f4.transactions if t.code == "P" and t.acquired and t.price > 0]
    total_shares = sum(t.shares for t in buys)
    avg_price = value / total_shares if total_shares else 0.0
    role = owner_role(f4)

    distinct_owners = {b["owner_cik"] for b in window_buys}
    is_cluster = len(distinct_owners) >= CLUSTER_MIN_INSIDERS

    if is_cluster:
        window_total = sum(b["value"] for b in window_buys)
        headline = (
            f"Insider cluster: {len(distinct_owners)} insiders buy "
            f"{_fmt_usd(window_total)} in {CLUSTER_WINDOW_DAYS} days; "
            f"{role} {f4.owner_name} latest with {_fmt_usd(value)}"
        )
        takeaway = (
            f"{len(distinct_owners)} insiders buying {_fmt_usd(window_total)} of "
            f"stock in the open market within {CLUSTER_WINDOW_DAYS} days is a "
            "strong conviction signal."
        )
        significance = "High"
    else:
        headline = f"{role} {f4.owner_name} buys {_fmt_usd(value)} at ${avg_price:,.2f}"
        takeaway = (
            f"Open-market insider purchase of {_fmt_usd(value)} — "
            "insiders buy for one reason."
        )
        significance = "Medium"

    summary = (
        f"{role} {f4.owner_name} purchased {total_shares:,.0f} shares of "
        f"{f4.issuer_name} at an average of ${avg_price:,.2f} "
        f"({_fmt_usd(value)}) in the open market"
    )
    if f4.shares_after:
        summary += f", bringing their holding to {f4.shares_after:,.0f} shares"
    summary += "."
    if is_cluster and len(window_buys) > 1:
        others = len(distinct_owners) - 1
        summary += (
            f" {others} other insider{'s' if others != 1 else ''} also bought "
            f"within the past {CLUSTER_WINDOW_DAYS} days."
        )

    deal_terms = {
        "share_count": f"{total_shares:,.0f}",
        "price_per_share": f"${avg_price:,.2f}",
        "deal_value": _fmt_usd(value),
    }

    return Briefing(
        headline=headline,
        summary=summary,
        primary_event_type="Insider Buying",
        deal_terms=deal_terms,
        significance=significance,
        sentiment="Positive",
        investor_takeaway=takeaway,
        catalysts=[],
        event_types=["Insider Buying"],
    )
