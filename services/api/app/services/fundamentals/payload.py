"""Assemble the `GET /fundamentals/<symbol>` response.

Column-oriented: each table is {"periods": [...], "rows": {key: [values]}}
with one value per period, newest LAST (screener reads left → right in
time). Row charts plot a row's array directly.

Both `quarterly` and `annual` carry the same four statements, so the page
can offer one Quarterly/Annual switch per statement rather than two
separate sections. The quarterly figures cost nothing extra: `sync.py`
already fetches all three statements at quarter granularity.
"""

from datetime import date

from app.models.company import Company
from app.models.fundamentals import CompanyFundamentals, FundamentalsPeriod
from app.services.fundamentals import rows as R
from app.utils.sectors import sic_to_sector

QUARTERS_IN_PAYLOAD = 40
ANNUAL_TABLE_YEARS = 12
QUARTER_TABLE_COUNT = 12

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def period_label(period_end: date) -> str:
    return f'{MONTHS[period_end.month - 1]} {period_end.year}'


def _period_meta(p: dict) -> dict:
    end = p['period_end']
    return {
        'key': end.isoformat(),
        'label': period_label(end),
        'fiscal_year': p.get('fiscal_year'),
        'fiscal_period': p.get('fiscal_period'),
        'quality_flags': p.get('quality_flags') or [],
    }


def _table(periods: list[dict], row_fn, row_keys: list[str], breakdown_fn=None) -> dict:
    """periods oldest-first."""
    computed = [row_fn(p) for p in periods]
    rows = {key: [c.get(key) for c in computed] for key in row_keys}
    out = {'periods': [_period_meta(p) for p in periods], 'rows': rows}
    if breakdown_fn:
        breakdowns = [breakdown_fn(p) for p in periods]
        if breakdowns and isinstance(next(iter(breakdowns[0].values()), None), dict):
            # nested: parent -> child -> [values]
            out['breakdown'] = {
                parent: {child: [b[parent].get(child) for b in breakdowns]
                         for child in breakdowns[0][parent]}
                for parent in breakdowns[0]
            }
        else:
            out['breakdown'] = {key: [b.get(key) for b in breakdowns] for key in breakdowns[0]} if breakdowns else {}
    return out


def build_payload(company: Company, snap: CompanyFundamentals | None,
                  periods: list[FundamentalsPeriod], *, status: str) -> dict:
    """status: ready | building | unavailable | empty"""
    annual = sorted([p.as_dict() for p in periods if p.period_type == 'annual'],
                    key=lambda p: p['period_end'])
    quarters = sorted([p.as_dict() for p in periods if p.period_type == 'quarter'],
                      key=lambda p: p['period_end'])[-QUARTERS_IN_PAYLOAD:]

    derived = (snap.derived if snap and snap.derived else {}) or {}
    ratios = dict(derived.get('ratios') or {})
    # Price-dependent numbers always come from the live company row.
    price = float(company.last_price) if company.last_price is not None else ratios.get('price')
    ratios['price'] = price
    ratios['market_cap'] = company.market_cap or ratios.get('market_cap')
    if price and ratios.get('eps_ttm') and ratios['eps_ttm'] > 0:
        ratios['pe_ttm'] = round(price / ratios['eps_ttm'], 2)
    if price and ratios.get('book_value_ps') and ratios['book_value_ps'] > 0:
        ratios['pb'] = round(price / ratios['book_value_ps'], 2)
    if price and ratios.get('dividends_ttm_ps') is not None:
        ratios['dividend_yield'] = round(100.0 * ratios['dividends_ttm_ps'] / price, 2)

    ttm_row = None
    if quarters:
        from app.services.fundamentals.derive import ttm_period
        ttm_src = ttm_period(list(reversed(quarters)))
        if ttm_src:
            ttm_row = ttm_src

    annual_income = _table(annual, R.income_rows, R.INCOME_ROWS, R.expense_breakdown)
    if ttm_row:
        ttm_rows = R.income_rows(ttm_row)
        for key in R.INCOME_ROWS:
            annual_income['rows'][key].append(ttm_rows.get(key))
        bd = R.expense_breakdown(ttm_row)
        for key in annual_income.get('breakdown', {}):
            annual_income['breakdown'][key].append(bd.get(key))
        annual_income['periods'].append({
            'key': 'ttm', 'label': 'TTM', 'fiscal_year': None, 'fiscal_period': 'TTM',
            'quality_flags': ttm_row.get('quality_flags') or [],
        })

    return {
        'status': status,
        'company': {
            'id': company.id,
            'ticker': company.ticker,
            'name': company.name,
            'cik': company.cik,
            'exchange': snap.exchange if snap else None,
            'industry': snap.industry if snap else None,
            'sector': (snap.sector if snap else None) or sic_to_sector(company.sic),
            'description': snap.description if snap else None,
            'website': snap.website if snap else None,
            'ceo': snap.ceo if snap else None,
            'employees': snap.employees if snap else None,
            'ipo_date': snap.ipo_date.isoformat() if snap and snap.ipo_date else None,
            'is_adr': bool(snap.is_adr) if snap else False,
            'fiscal_year_end_month': snap.fiscal_year_end_month if snap else None,
        },
        'as_of': {
            'latest_annual_end': snap.latest_annual_end.isoformat() if snap and snap.latest_annual_end else None,
            'latest_quarter_end': snap.latest_quarter_end.isoformat() if snap and snap.latest_quarter_end else None,
            'last_synced_at': snap.last_synced_at.isoformat() if snap and snap.last_synced_at else None,
            'price_updated_at': company.price_updated_at.isoformat() if company.price_updated_at else None,
        },
        'ratios': ratios,
        'growth': derived.get('growth') or {},
        'analysis': derived.get('analysis') or {'pros': [], 'cons': [], 'key_points': []},
        'quarterly': {
            'income': _table(quarters, lambda p: R.income_rows(p, include_payout=False),
                             [k for k in R.INCOME_ROWS if k != 'dividend_payout_pct'],
                             R.expense_breakdown),
            'balance': _table(quarters, R.balance_rows, R.BALANCE_ROWS, R.balance_breakdown),
            'cashflow': _table(quarters, R.cashflow_rows, R.CASHFLOW_ROWS + ['free_cash_flow'],
                               R.cashflow_breakdown),
            'ratios': _table(quarters, R.quarter_ratio_rows, R.RATIO_ROWS),
        },
        'annual': {
            'income': annual_income,
            'balance': _table(annual, R.balance_rows, R.BALANCE_ROWS, R.balance_breakdown),
            'cashflow': _table(annual, R.cashflow_rows, R.CASHFLOW_ROWS + ['free_cash_flow'],
                               R.cashflow_breakdown),
            'ratios': _table(annual, R.ratio_rows, R.RATIO_ROWS),
        },
        'table_defaults': {
            'annual_years': ANNUAL_TABLE_YEARS,
            'quarters': QUARTER_TABLE_COUNT,
        },
    }
