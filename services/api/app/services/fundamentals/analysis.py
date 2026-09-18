"""Deterministic pros / cons, screener-style.

Every sentence is generated from the number it cites so the copy can never
disagree with the tables. Thresholds live in this file only. No LLM.
"""

def _f(v):
    return float(v) if v is not None else None


def _fmt_pct(v):
    return f'{v:.1f}%' if v is not None else None


def build_analysis(annual: list[dict], derived: dict) -> dict:
    """annual newest-first; derived = derive.build_snapshot() output."""
    ratios = derived.get('ratios') or {}
    growth = derived.get('growth') or {}
    pros: list[str] = []
    cons: list[str] = []

    # ── Debt ──
    debt_now = _f(annual[0].get('total_debt')) if annual else None
    debt_3y = _f(annual[3].get('total_debt')) if len(annual) > 3 else None
    equity_now = _f(annual[0].get('total_equity')) if annual else None
    if debt_now is not None and equity_now and equity_now > 0:
        if debt_now <= 0.02 * equity_now:
            pros.append('Company is almost debt free.')
        elif debt_3y and debt_now < 0.8 * debt_3y:
            pros.append(f'Company has reduced debt by {_money(debt_3y - debt_now)} over the last 3 years.')
    coverage = ratios.get('interest_coverage')
    if coverage is not None and coverage < 2:
        cons.append(f'Interest coverage is low at {coverage:.1f}x.')

    # ── Growth ──
    profit_5y = (growth.get('profit') or {}).get('5y')
    sales_5y = (growth.get('sales') or {}).get('5y')
    if profit_5y is not None and profit_5y >= 20:
        pros.append(f'Company has delivered good profit growth of {_fmt_pct(profit_5y)} CAGR over the last 5 years.')
    if sales_5y is not None and sales_5y < 5:
        cons.append(f'Company has delivered a poor sales growth of {_fmt_pct(sales_5y)} over the past 5 years.')

    # ── Returns ──
    roe_3y = (growth.get('roe') or {}).get('3y')
    if roe_3y is not None and roe_3y >= 20:
        pros.append(f'Company has a good return on equity (ROE) track record: 3-year ROE {_fmt_pct(roe_3y)}.')
    elif roe_3y is not None and roe_3y < 10:
        cons.append(f'Company has a low return on equity of {_fmt_pct(roe_3y)} over the last 3 years.')

    # ── Valuation ──
    pb = ratios.get('pb')
    roe_last = ratios.get('roe')
    if pb is not None and pb > 5 and (roe_last is None or roe_last < 15):
        book = ratios.get('book_value_ps')
        pros_or = f'Stock is trading at {pb:.1f} times its book value'
        if book:
            pros_or += f' (${book:,.2f} per share)'
        cons.append(pros_or + '.')

    # ── Dividends ──
    payouts = []
    for p in annual[:3]:
        ni = _f(p.get('net_income'))
        div = _f(p.get('dividends_paid'))
        if ni and ni > 0 and div is not None:
            payouts.append(abs(div) / ni)
    if len(payouts) == 3 and all(x >= 0.25 for x in payouts):
        pros.append(f'Company has been maintaining a healthy dividend payout of {100 * sum(payouts) / 3:.1f}%.')
    elif annual and not _pays_dividends(annual[0], ratios) and (_f(annual[0].get('net_income')) or 0) > 0:
        cons.append('Company is not paying out dividends despite being profitable.')

    # ── Cash generation ──
    fcf = [_f(p.get('free_cash_flow')) for p in annual[:5]]
    if len(fcf) == 5 and all(v is not None and v > 0 for v in fcf):
        pros.append('Company has generated positive free cash flow in each of the last 5 years.')
    losses = [p for p in annual[:3] if (_f(p.get('net_income')) or 0) < 0]
    if len(annual) >= 3 and len(losses) >= 2:
        cons.append('Company has reported a net loss in 2 of the last 3 years.')

    # ── Working capital ──
    if len(annual) > 3:
        rec_now = _receivable_days(annual[0])
        rec_3y = _receivable_days(annual[3])
        if rec_now and rec_3y and rec_now > 1.3 * rec_3y and rec_now - rec_3y > 15:
            cons.append(f'Debtor days have increased from {rec_3y:.0f} to {rec_now:.0f} days.')

    key_points = _key_points(annual, derived)
    return {'pros': pros, 'cons': cons, 'key_points': key_points}


def _pays_dividends(latest_fy: dict, ratios: dict) -> bool:
    paid = _f(latest_fy.get('dividends_paid'))
    if paid is not None and abs(paid) > 0:
        return True
    return bool(ratios.get('dividend_yield'))


def _receivable_days(p: dict):
    rec = _f(p.get('receivables'))
    rev = _f(p.get('revenue'))
    if rec is None or not rev or rev <= 0:
        return None
    return 365.0 * rec / rev


def _key_points(annual: list[dict], derived: dict) -> list[str]:
    """Short factual lines for the About block."""
    points = []
    ratios = derived.get('ratios') or {}
    ttm = derived.get('ttm')
    if ttm and ttm.get('revenue'):
        points.append(f'Trailing 12-month revenue of {_money(ttm["revenue"])}.')
    elif annual and annual[0].get('revenue'):
        points.append(f'Latest fiscal-year revenue of {_money(annual[0]["revenue"])}.')
    opm = ratios.get('opm_ttm')
    if opm is not None:
        points.append(f'Operating margin (EBITDA / sales) of {opm:.1f}%.')
    if len(annual) >= 2:
        points.append(f'Financial history available from FY{annual[-1].get("fiscal_year") or annual[-1]["period_end"].year}.')
    return points


def _money(dollars) -> str:
    v = float(dollars)
    sign = '-' if v < 0 else ''
    v = abs(v)
    if v >= 1e12:
        return f'{sign}${v / 1e12:.2f}T'
    if v >= 1e9:
        return f'{sign}${v / 1e9:.2f}B'
    if v >= 1e6:
        return f'{sign}${v / 1e6:.1f}M'
    return f'{sign}${v:,.0f}'

