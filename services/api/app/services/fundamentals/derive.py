"""Derived numbers: TTM, CAGRs, header ratios, growth grids.

Everything here is computed from the stored periods plus the company's
current price / share count, and written into
`CompanyFundamentals.derived` by `build_snapshot()`. The page never
computes a ratio itself.
"""

from datetime import date, timedelta

from app.services.fundamentals import rows as R

# The four quarters that make a TTM must span roughly one year; anything
# looser means a quarter is missing and the sum would be wrong.
TTM_MAX_SPAN_DAYS = 400

CAGR_WINDOWS = {'10y': 10, '5y': 5, '3y': 3}


def _f(value):
    return float(value) if value is not None else None


def _cagr(start, end, years: float):
    if start is None or end is None or years <= 0:
        return None
    if start <= 0 or end <= 0:
        return None
    try:
        return round(100.0 * ((end / start) ** (1.0 / years) - 1.0), 2)
    except (OverflowError, ZeroDivisionError, ValueError):
        return None


def _pct(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(100.0 * numerator / denominator, 2)


def _ratio(numerator, denominator, digits=2):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, digits)


# ── TTM ────────────────────────────────────────────────────────────────

SUMMED_TTM_COLUMNS = [
    'revenue', 'cost_of_revenue', 'gross_profit', 'sga', 'rnd', 'other_opex',
    'operating_expenses', 'depreciation_amortization', 'operating_income',
    'ebitda', 'interest_expense', 'interest_income', 'other_income_net',
    'pretax_income', 'income_tax', 'net_income', 'eps_basic', 'eps_diluted',
    'cfo', 'capex', 'acquisitions', 'cfi', 'debt_issued', 'debt_repaid',
    'buybacks', 'dividends_paid', 'cff', 'net_change_in_cash', 'free_cash_flow',
    'stock_based_compensation',
]


def ttm_period(quarters: list[dict], offset: int = 0) -> dict | None:
    """Sum the four quarters ending `offset` quarters before the latest.

    `quarters` newest-first. Balance-sheet columns come from the latest of
    the four. Returns None unless four quarters exist and span ≤ 400 days.
    """
    window = quarters[offset:offset + 4]
    if len(window) < 4:
        return None
    newest, oldest = window[0]['period_end'], window[-1]['period_end']
    if (newest - oldest).days > TTM_MAX_SPAN_DAYS:
        return None
    ttm = dict(window[0])  # balance sheet + metadata from the latest quarter
    ttm['period_type'] = 'ttm'
    ttm['fiscal_period'] = 'TTM'
    for col in SUMMED_TTM_COLUMNS:
        values = [q.get(col) for q in window]
        if any(v is None for v in values):
            ttm[col] = None
        else:
            total = sum(float(v) for v in values)
            ttm[col] = round(total, 4) if col.startswith('eps') else int(round(total))
    flags = set()
    for q in window:
        flags.update(q.get('quality_flags') or [])
    ttm['quality_flags'] = sorted(flags)
    return ttm


# ── Snapshot ───────────────────────────────────────────────────────────

def build_snapshot(annual: list[dict], quarters: list[dict], *, price, shares_outstanding,
                   market_cap, price_refs: dict | None = None, dividends_ttm_ps=None,
                   high_52w=None, low_52w=None) -> dict:
    """The `derived` JSON. `annual` and `quarters` newest-first."""
    price = _f(price)
    price_refs = price_refs or {}
    latest_fy = annual[0] if annual else None
    prior_fy = annual[1] if len(annual) > 1 else None
    latest_q = quarters[0] if quarters else None
    ttm = ttm_period(quarters) if quarters else None
    prior_ttm = ttm_period(quarters, offset=4) if quarters else None
    # When quarters are missing, the latest fiscal year stands in for TTM.
    ttm_src = ttm or latest_fy
    balance_src = latest_q or latest_fy

    eps_ttm = None
    if ttm_src:
        eps_ttm = _f(ttm_src.get('eps_diluted'))
        if eps_ttm is None:
            eps_ttm = _f(ttm_src.get('eps_basic'))

    shares = _f(shares_outstanding)
    if not shares and balance_src and balance_src.get('weighted_shares_diluted'):
        shares = _f(balance_src['weighted_shares_diluted'])
    if not market_cap and price and shares:
        market_cap = int(price * shares)

    equity = _f(balance_src.get('total_equity')) if balance_src else None
    book_value_ps = _ratio(equity, shares) if equity is not None and shares else None
    total_debt = _f(balance_src.get('total_debt')) if balance_src else None
    cash = _f(balance_src.get('cash')) if balance_src else None
    ebitda_ttm = _f(ttm_src.get('ebitda')) if ttm_src else None
    ev = None
    if market_cap is not None:
        ev = market_cap + (total_debt or 0) - (cash or 0)

    interest = _f(ttm_src.get('interest_expense')) if ttm_src else None
    pbt_ttm = _f(ttm_src.get('pretax_income')) if ttm_src else None
    interest_coverage = None
    if interest and interest > 0 and pbt_ttm is not None:
        interest_coverage = round((pbt_ttm + interest) / interest, 2)

    roe = _roe(latest_fy, prior_fy)
    roce = R.roce_pct(latest_fy) if latest_fy else None
    pe = _ratio(price, eps_ttm) if price and eps_ttm and eps_ttm > 0 else None
    div_ps = _f(dividends_ttm_ps)
    dividend_yield = _pct(div_ps, price) if div_ps is not None and price else None

    ratios = {
        'market_cap': market_cap,
        'price': price,
        'high_52w': _f(high_52w),
        'low_52w': _f(low_52w),
        'pe_ttm': pe,
        'book_value_ps': book_value_ps,
        'dividend_yield': dividend_yield,
        'roce': roce,
        'roe': roe,
        'shares_outstanding': int(shares) if shares else None,
        'pb': _ratio(price, book_value_ps) if price and book_value_ps and book_value_ps > 0 else None,
        'ev': int(ev) if ev is not None else None,
        'ev_ebitda': _ratio(ev, ebitda_ttm) if ev is not None and ebitda_ttm and ebitda_ttm > 0 else None,
        'debt_to_equity': _ratio(total_debt, equity) if total_debt is not None and equity and equity > 0 else None,
        'interest_coverage': interest_coverage,
        'opm_ttm': _pct(ebitda_ttm, _f(ttm_src.get('revenue'))) if ttm_src else None,
        'eps_ttm': eps_ttm,
        'revenue_ttm': ttm_src.get('revenue') if ttm_src else None,
        'net_income_ttm': ttm_src.get('net_income') if ttm_src else None,
        'fcf_ttm': ttm_src.get('free_cash_flow') if ttm_src else None,
        'dividends_ttm_ps': div_ps,
        'sales_cagr_3y': _cagr_from_annual(annual, 'revenue', 3),
        'profit_cagr_3y': _cagr_from_annual(annual, 'net_income', 3),
        'ttm_is_fy': ttm is None and latest_fy is not None,
    }

    growth = {
        'sales': _growth_grid(annual, 'revenue', ttm, prior_ttm),
        'profit': _growth_grid(annual, 'net_income', ttm, prior_ttm),
        'price': {
            '10y': _cagr(_f(price_refs.get('10y')), price, 10),
            '5y': _cagr(_f(price_refs.get('5y')), price, 5),
            '3y': _cagr(_f(price_refs.get('3y')), price, 3),
            '1y': _cagr(_f(price_refs.get('1y')), price, 1),
        },
        'roe': {
            '10y': _roe_average(annual, 10),
            '5y': _roe_average(annual, 5),
            '3y': _roe_average(annual, 3),
            'last': roe,
        },
    }

    return {
        'ratios': ratios,
        'growth': growth,
        'ttm': {
            'period_end': ttm['period_end'].isoformat() if ttm.get('period_end') else None,
            'revenue': ttm.get('revenue'),
            'net_income': ttm.get('net_income'),
        } if ttm else None,
    }


def _cagr_from_annual(annual: list[dict], col: str, years: int):
    if len(annual) <= years:
        return None
    return _cagr(_f(annual[years].get(col)), _f(annual[0].get(col)), years)


def _growth_grid(annual: list[dict], col: str, ttm: dict | None, prior_ttm: dict | None) -> dict:
    grid = {key: _cagr_from_annual(annual, col, n) for key, n in CAGR_WINDOWS.items()}
    if ttm and prior_ttm:
        grid['ttm'] = _cagr(_f(prior_ttm.get(col)), _f(ttm.get(col)), 1)
    else:
        grid['ttm'] = _cagr_from_annual(annual, col, 1)
    return grid


def _roe(fy: dict | None, prior: dict | None):
    """Net income / average equity (latest equity alone when no prior year)."""
    if not fy:
        return None
    ni = _f(fy.get('net_income'))
    eq = _f(fy.get('total_equity'))
    if ni is None or eq is None:
        return None
    prior_eq = _f(prior.get('total_equity')) if prior else None
    avg = (eq + prior_eq) / 2 if prior_eq is not None else eq
    if avg <= 0:
        return None
    return _pct(ni, avg)


def _roe_average(annual: list[dict], years: int):
    values = []
    for i in range(min(years, len(annual))):
        v = _roe(annual[i], annual[i + 1] if i + 1 < len(annual) else None)
        if v is not None:
            values.append(v)
    if len(values) < min(years, 2):
        return None
    return round(sum(values) / len(values), 2)


# ── Price references ───────────────────────────────────────────────────

def price_references(eod: list[dict], today: date | None = None) -> dict:
    """From FMP EOD light rows ({date, price}) pick the closes nearest to
    1/3/5/10 years ago plus the 52-week high/low."""
    today = today or date.today()
    closes = []
    for row in eod or []:
        d = row.get('date')
        p = row.get('price', row.get('close'))
        if not d or p is None:
            continue
        try:
            closes.append((date.fromisoformat(str(d)[:10]), float(p)))
        except (ValueError, TypeError):
            continue
    if not closes:
        return {}
    closes.sort()
    refs = {}
    for key, years in (('1y', 1), ('3y', 3), ('5y', 5), ('10y', 10)):
        target = today - timedelta(days=365 * years)
        if closes[0][0] > target + timedelta(days=14):
            refs[key] = None  # not enough history
            continue
        best = min(closes, key=lambda c: abs((c[0] - target).days))
        refs[key] = best[1] if abs((best[0] - target).days) <= 14 else None
    year_ago = today - timedelta(days=365)
    last_year = [p for d, p in closes if d >= year_ago]
    if last_year:
        refs['high_52w'] = max(last_year)
        refs['low_52w'] = min(last_year)
    refs['ref_date'] = closes[-1][0]
    return refs


def dividends_ttm_per_share(dividends: list[dict], today: date | None = None):
    """Sum of per-share dividends with an ex-date in the last 12 months."""
    today = today or date.today()
    cutoff = today - timedelta(days=365)
    total = 0.0
    seen = False
    for row in dividends or []:
        d = row.get('date') or row.get('exDate')
        amount = row.get('adjDividend', row.get('dividend'))
        if not d or amount is None:
            continue
        try:
            ex = date.fromisoformat(str(d)[:10])
            amount = float(amount)
        except (ValueError, TypeError):
            continue
        if ex >= cutoff and ex <= today:
            total += amount
            seen = True
    return round(total, 4) if seen else None
