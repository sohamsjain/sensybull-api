"""
playbooks.py — event-type-specific, deterministic financial analysis.

Each playbook takes a FundamentalsSnapshot (``metrics`` dict) plus the filing's
extracted ``deal_terms`` and computes second-order ratios *in Python* — the LLM
is never asked to do arithmetic, only to interpret the numbers we hand it. New
event types are supported by adding a registry entry; anything unmapped falls
back to ``GENERIC`` (a leverage/profitability/liquidity snapshot).

The result carries:
- ``ratios``  — machine-readable {name: number | None}
- ``lines``   — human-readable strings for the UI and the LLM prompt
- ``focus``   — a prompt fragment telling the LLM what to reason about
"""
import re
from dataclasses import dataclass, field

# ── number parsing from free-text deal terms ─────────────────────────────────
_SCALE = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}


def parse_money(value) -> float | None:
    """Parse '$200M', '1.5 billion', '$3,000,000' → float USD. None if unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).lower().replace(",", "")
    m = re.search(r"\$?\s*([0-9]*\.?[0-9]+)\s*(trillion|billion|million|thousand|bn|mm|[kmbt])?", s)
    if not m:
        return None
    num = float(m.group(1))
    scale = _SCALE.get(m.group(2), 1.0) if m.group(2) else 1.0
    return num * scale


def parse_count(value) -> float | None:
    """Parse a share count like '3,000,000 shares' or '3M shares' → float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_money(value)  # same numeric grammar, no '$'


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


# ── display formatting ───────────────────────────────────────────────────────
def _money(v) -> str:
    if v is None:
        return "n/a"
    av = abs(v)
    for thresh, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if av >= thresh:
            return f"${v / thresh:.2f}{suffix}"
    return f"${v:.0f}"


def _x(v) -> str:
    return "n/a" if v is None else f"{v:.2f}x"


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


@dataclass
class PlaybookResult:
    playbook: str
    ratios: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)
    focus: str = ""

    def to_metrics(self, snapshot: dict) -> dict:
        return {
            "playbook": self.playbook,
            "ratios": self.ratios,
            "lines": self.lines,
            "snapshot": snapshot,
        }


# ── individual playbooks ─────────────────────────────────────────────────────
def _debt(m: dict, deal: dict) -> PlaybookResult:
    debt, equity = m.get("total_debt"), m.get("equity")
    ebit, interest, cash = m.get("operating_income"), m.get("interest_expense"), m.get("cash")
    new_debt = parse_money(deal.get("deal_value"))

    de = _div(debt, equity)
    proforma_debt = (debt + new_debt) if (debt is not None and new_debt is not None) else None
    proforma_de = _div(proforma_debt, equity)
    coverage = _div(ebit, interest)
    r = {
        "debt_to_equity": de,
        "new_debt_usd": new_debt,
        "new_debt_pct_of_existing_debt": _div(new_debt, debt),
        "proforma_debt_to_equity": proforma_de,
        "interest_coverage": coverage,
        "cash_to_new_debt": _div(cash, new_debt),
    }
    lines = [
        f"Existing total debt: {_money(debt)}; equity: {_money(equity)} → D/E {_x(de)}",
    ]
    if new_debt is not None:
        lines.append(
            f"New debt ~{_money(new_debt)} = {_pct(r['new_debt_pct_of_existing_debt'])} of existing "
            f"debt → pro-forma D/E {_x(proforma_de)}")
        lines.append(f"Cash on hand {_money(cash)} covers {_x(r['cash_to_new_debt'])} of the new debt")
    lines.append(f"Interest coverage (EBIT/interest): {_x(coverage)}")
    focus = ("Assess the leverage impact of this financing: how much it raises debt and D/E, "
             "whether operating income comfortably services interest (coverage), and the liquidity "
             "cushion. Flag refinancing/solvency risk only if the numbers warrant it.")
    return PlaybookResult("debt_financing", r, lines, focus)


def _acquisition(m: dict, deal: dict) -> PlaybookResult:
    assets, equity, cash = m.get("assets"), m.get("equity"), m.get("cash")
    deal_value = parse_money(deal.get("deal_value"))
    r = {
        "deal_value_usd": deal_value,
        "deal_vs_assets": _div(deal_value, assets),
        "deal_vs_equity": _div(deal_value, equity),
        "deal_vs_cash": _div(deal_value, cash),
        "cash_funded_feasible": (None if (deal_value is None or cash is None) else deal_value <= cash),
    }
    lines = [f"Deal value: {_money(deal_value)} vs assets {_money(assets)} "
             f"({_pct(r['deal_vs_assets'])}), equity {_money(equity)} ({_pct(r['deal_vs_equity'])})"]
    lines.append(f"Cash on hand {_money(cash)} = {_x(r['deal_vs_cash'])} the deal "
                 f"({'could be cash-funded' if r['cash_funded_feasible'] else 'likely needs debt/equity'})")
    focus = ("Gauge the scale of this deal against the balance sheet and whether it is plausibly "
             "cash-funded or will require new debt/equity (and the leverage that implies).")
    return PlaybookResult("acquisition", r, lines, focus)


def _dilution(m: dict, deal: dict) -> PlaybookResult:
    shares = m.get("shares_outstanding")
    equity = m.get("equity")
    offering_value = parse_money(deal.get("deal_value"))
    new_shares = parse_count(deal.get("share_count"))
    r = {
        "offering_value_usd": offering_value,
        "new_shares": new_shares,
        "dilution_pct": _div(new_shares, shares),
        "offering_vs_equity": _div(offering_value, equity),
        "shares_outstanding": shares,
    }
    lines = []
    if new_shares is not None and shares:
        lines.append(f"New shares ~{new_shares:,.0f} on {shares:,.0f} outstanding "
                     f"→ ~{_pct(r['dilution_pct'])} dilution")
    if offering_value is not None:
        lines.append(f"Capital raised ~{_money(offering_value)} = {_pct(r['offering_vs_equity'])} of equity")
    if not lines:
        lines.append(f"Shares outstanding: {shares:,.0f}" if shares else "Shares outstanding: n/a")
    focus = ("Quantify shareholder dilution (new shares vs. shares outstanding) and the capital "
             "raised relative to the company's size. Note if the offering is a shelf (capacity, "
             "not necessarily issued yet).")
    return PlaybookResult("dilution", r, lines, focus)


def _capital_return(m: dict, deal: dict) -> PlaybookResult:
    cash, equity = m.get("cash"), m.get("equity")
    program = parse_money(deal.get("deal_value"))
    r = {
        "program_value_usd": program,
        "program_vs_cash": _div(program, cash),
        "program_vs_equity": _div(program, equity),
    }
    lines = [f"Program size: {_money(program)} vs cash {_money(cash)} "
             f"({_pct(r['program_vs_cash'])}), equity {_money(equity)} ({_pct(r['program_vs_equity'])})"]
    focus = ("Assess whether the company can comfortably fund this buyback/dividend from cash and "
             "balance sheet, and what it signals about capital allocation.")
    return PlaybookResult("capital_return", r, lines, focus)


def _generic(m: dict, deal: dict) -> PlaybookResult:
    r = {
        "debt_to_equity": _div(m.get("total_debt"), m.get("equity")),
        "operating_margin": _div(m.get("operating_income"), m.get("revenue")),
        "net_margin": _div(m.get("net_income"), m.get("revenue")),
        "current_ratio": _div(m.get("current_assets"), m.get("current_liabilities")),
        "interest_coverage": _div(m.get("operating_income"), m.get("interest_expense")),
    }
    lines = [
        f"Leverage D/E {_x(r['debt_to_equity'])}; interest coverage {_x(r['interest_coverage'])}",
        f"Operating margin {_pct(r['operating_margin'])}; net margin {_pct(r['net_margin'])}",
        f"Liquidity: current ratio {_x(r['current_ratio'])}",
    ]
    focus = ("Provide financial-health context (leverage, profitability, liquidity) that helps the "
             "reader judge how material this update is for the company.")
    return PlaybookResult("generic", r, lines, focus)


# ── registry: event type → playbook (priority order for multi-type events) ───
_REGISTRY = [
    ("Debt / Financing", _debt),
    ("M&A / Merger", _acquisition),
    ("Acquisition", _acquisition),
    ("Tender Offer", _acquisition),
    ("Shelf Registration", _dilution),
    ("Share Offering", _dilution),
    ("Rights Issue", _dilution),
    ("Capital Return", _capital_return),
    ("Dividend Change", _capital_return),
    ("Issuer Tender", _capital_return),
]
_REGISTRY_MAP = dict(_REGISTRY)
_PRIORITY = [name for name, _ in _REGISTRY]


def select_playbook(event_types):
    """Pick the highest-priority specific playbook for the event's types."""
    types = set(event_types or [])
    for name in _PRIORITY:
        if name in types:
            return _REGISTRY_MAP[name]
    return _generic


def run_playbook(event_types, snapshot: dict | None, deal_terms: dict | None) -> PlaybookResult:
    """Run the matching playbook and return its result (never raises)."""
    metrics = (snapshot or {}).get("metrics", {}) or {}
    deal = deal_terms or {}
    fn = select_playbook(event_types)
    try:
        return fn(metrics, deal)
    except Exception:  # noqa: BLE001 — a math hiccup must not break the pipeline
        return _generic(metrics, deal)
