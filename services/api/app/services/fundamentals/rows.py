"""Canonical screener-style rows, computed from one period's columns.

This is the single definition of every row on the page. The API payload,
the row charts, the header ratios and the pros/cons rules all read these,
so a number can only ever mean one thing.

Mirrored (labels/order/format only, never arithmetic) by sensybull-web
`src/lib/fundamentals/rows.ts` — keep the keys in sync.

US balance sheets are presented Assets → Liabilities → Equity (10-K order),
not screener's Indian Schedule III order.
"""


def _sum(*values):
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _num(value):
    """Decimal → float so JSON and comparisons behave."""
    return float(value) if value is not None else None


def _sub(a, b):
    if a is None or b is None:
        return None
    return a - b


def _pct(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(100.0 * numerator / denominator, 2)


def _days(numerator, denominator, days_in_period: float):
    if numerator is None or not denominator or denominator <= 0:
        return None
    return round(days_in_period * numerator / denominator, 1)


# ── Income statement (Quarters and P&L) ────────────────────────────────

INCOME_ROWS = [
    'sales', 'expenses', 'operating_profit', 'opm_pct', 'other_income',
    'interest', 'depreciation', 'profit_before_tax', 'tax_pct', 'net_profit',
    'eps', 'dividend_payout_pct',
]

# Breakdown rows revealed by the "+" on Expenses (each as % of Sales)
EXPENSE_BREAKDOWN_ROWS = ['cost_of_revenue_pct', 'sga_pct', 'rnd_pct', 'other_opex_pct']


def income_rows(p: dict, include_payout: bool = True) -> dict:
    """p is FundamentalsPeriod.as_dict() (or a mapper dict)."""
    sales = p.get('revenue')
    op = p.get('ebitda')
    if op is None and p.get('operating_income') is not None:
        op = _sum(p.get('operating_income'), p.get('depreciation_amortization'))
    interest = p.get('interest_expense')
    other_net = p.get('other_income_net')
    # "Other income" excludes interest expense so Interest can be its own row.
    other_income = _sum(other_net, interest) if other_net is not None else None
    pbt = p.get('pretax_income')
    tax = p.get('income_tax')
    ni = p.get('net_income')
    rows = {
        'sales': sales,
        'expenses': _sub(sales, op),
        'operating_profit': op,
        'opm_pct': _pct(op, sales),
        'other_income': other_income,
        'interest': interest,
        'depreciation': p.get('depreciation_amortization'),
        'profit_before_tax': pbt,
        'tax_pct': _pct(tax, pbt) if pbt and pbt > 0 else None,
        'net_profit': ni,
        'eps': _num(p.get('eps_diluted') if p.get('eps_diluted') is not None else p.get('eps_basic')),
    }
    if include_payout:
        dividends = p.get('dividends_paid')
        payout = None
        if dividends is not None and ni and ni > 0:
            payout = _pct(abs(dividends), ni)
        rows['dividend_payout_pct'] = payout
    return rows


def expense_breakdown(p: dict) -> dict:
    sales = p.get('revenue')
    return {
        'cost_of_revenue_pct': _pct(p.get('cost_of_revenue'), sales),
        'sga_pct': _pct(p.get('sga'), sales),
        'rnd_pct': _pct(p.get('rnd'), sales),
        'other_opex_pct': _pct(p.get('other_opex'), sales),
    }


# ── Balance sheet (Assets → Liabilities → Equity) ─────────────────────

BALANCE_ROWS = [
    'fixed_assets', 'investments', 'other_assets', 'total_assets',
    'borrowings', 'other_liabilities', 'total_liabilities',
    'equity_capital', 'reserves', 'total_equity',
]

BALANCE_BREAKDOWN = {
    'fixed_assets': ['ppe_net', 'goodwill', 'intangibles'],
    'other_assets': ['cash', 'receivables', 'inventory', 'other_current_assets', 'other_noncurrent_assets'],
    'borrowings': ['short_term_debt', 'long_term_debt', 'capital_leases'],
    'other_liabilities': ['payables', 'deferred_revenue', 'other_current_liabilities', 'minority_interest'],
}


def balance_rows(p: dict) -> dict:
    fixed = _sum(p.get('ppe_net'), p.get('goodwill'), p.get('intangibles'))
    investments = _sum(p.get('long_term_investments'), p.get('short_term_investments'))
    total_assets = p.get('total_assets')
    other_assets = None
    if total_assets is not None:
        other_assets = total_assets - (fixed or 0) - (investments or 0)
    total_debt = p.get('total_debt')
    total_liabilities = p.get('total_liabilities')
    other_liab = None
    if total_liabilities is not None:
        other_liab = total_liabilities - (total_debt or 0) + (p.get('minority_interest') or 0)
    equity = p.get('total_equity')
    common = p.get('common_stock')
    return {
        'fixed_assets': fixed,
        'investments': investments,
        'other_assets': other_assets,
        'total_assets': total_assets,
        'borrowings': total_debt,
        'other_liabilities': other_liab,
        'total_liabilities': _sum(total_liabilities, p.get('minority_interest')),
        'equity_capital': common,
        'reserves': _sub(equity, common) if equity is not None and common is not None else None,
        'total_equity': equity,
    }


def balance_breakdown(p: dict) -> dict:
    out = {}
    for parent, children in BALANCE_BREAKDOWN.items():
        out[parent] = {child: p.get(child) for child in children}
    return out


# ── Cash flow ──────────────────────────────────────────────────────────

CASHFLOW_ROWS = ['cash_from_operating', 'cash_from_investing', 'cash_from_financing', 'net_cash_flow']

CASHFLOW_BREAKDOWN = {
    'cash_from_operating': ['net_income', 'depreciation_amortization', 'stock_based_compensation'],
    'cash_from_investing': ['capex', 'acquisitions'],
    'cash_from_financing': ['debt_issued', 'debt_repaid', 'buybacks', 'dividends_paid'],
}


def cashflow_rows(p: dict) -> dict:
    return {
        'cash_from_operating': p.get('cfo'),
        'cash_from_investing': p.get('cfi'),
        'cash_from_financing': p.get('cff'),
        'net_cash_flow': p.get('net_change_in_cash'),
        'free_cash_flow': p.get('free_cash_flow'),
    }


def cashflow_breakdown(p: dict) -> dict:
    out = {}
    for parent, children in CASHFLOW_BREAKDOWN.items():
        out[parent] = {child: p.get(child) for child in children}
    return out


# ── Ratios (annual only) ───────────────────────────────────────────────

RATIO_ROWS = ['debtor_days', 'inventory_days', 'days_payable', 'cash_conversion_cycle',
              'working_capital_days', 'roce_pct']

# Days in the period a row is computed over. A days ratio divides a balance
# (a point in time) by a flow (the period's revenue or COGS), so the flow's
# length has to be the multiplier: run a quarter through 365 and every days
# figure comes out four times too high. ROCE is a rate, so a quarter's is
# annualised by the same ratio instead.
DAYS_IN_YEAR = 365.0
DAYS_IN_QUARTER = 365.0 / 4


def ratio_rows(p: dict, period_days: float = DAYS_IN_YEAR) -> dict:
    """p is one period; period_days is how long that period is (see above)."""
    revenue = p.get('revenue')
    cogs = p.get('cost_of_revenue')
    debtor = _days(p.get('receivables'), revenue, period_days)
    inv = _days(p.get('inventory'), cogs, period_days)
    payable = _days(p.get('payables'), cogs, period_days)
    ccc = None
    if debtor is not None:
        ccc = round(debtor + (inv or 0) - (payable or 0), 1)
    wc = None
    if p.get('total_current_assets') is not None and p.get('total_current_liabilities') is not None:
        wc = _days(p['total_current_assets'] - p['total_current_liabilities'], revenue, period_days)
    return {
        'debtor_days': debtor,
        'inventory_days': inv,
        'days_payable': payable,
        'cash_conversion_cycle': ccc,
        'working_capital_days': wc,
        'roce_pct': roce_pct(p, period_days),
    }


def quarter_ratio_rows(p: dict) -> dict:
    """ratio_rows over a quarter, with the quarter's own day count."""
    return ratio_rows(p, DAYS_IN_QUARTER)


def roce_pct(p: dict, period_days: float = DAYS_IN_YEAR):
    """EBIT / (total assets − current liabilities), as an annual rate."""
    pbt = p.get('pretax_income')
    if pbt is None:
        return None
    ebit = pbt + (p.get('interest_expense') or 0)
    assets = p.get('total_assets')
    cl = p.get('total_current_liabilities')
    if assets is None:
        return None
    capital = assets - (cl or 0)
    if capital <= 0:
        return None
    annualised = ebit * (DAYS_IN_YEAR / period_days)
    return _pct(annualised, capital)
