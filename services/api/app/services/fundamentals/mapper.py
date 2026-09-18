"""FMP statement records → FundamentalsPeriod column dicts.

The three statements arrive as separate lists keyed by period-end date. We
join them on `date`, map every canonical column through its alias list,
and run the reconciliation checks that decide the `quality_flags`.
"""

import logging
from datetime import datetime, timezone

from app.services.fundamentals import fields as F

log = logging.getLogger(__name__)

# A period fails PBT reconciliation when the screener-style identity
# Sales - Expenses - Depreciation - Interest + Other Income deviates from
# reported pretax income by more than this fraction of |pretax| (or of
# revenue when pretax is ~0).
RECONCILE_TOLERANCE = 0.02

FLAG_PBT_RECONCILE = 'pbt_reconcile_fail'
FLAG_NO_EBITDA = 'no_ebitda'
FLAG_NO_REVENUE = 'no_revenue'
FLAG_BALANCE_MISMATCH = 'balance_mismatch'
FLAG_MISSING_STATEMENT = 'missing_statement'
FLAG_VALUE_OUT_OF_RANGE = 'value_out_of_range'


def _map_fields(record: dict, spec: dict, per_share: set, shares: set,
                flags: list | None = None) -> dict:
    out = {}
    for col, aliases in spec.items():
        raw = F.pick(record, aliases)
        if col in per_share:
            dec = F.to_decimal(raw)
            if dec is None and raw not in (None, '') and flags is not None:
                # Present but implausible (or unparsable): dropped, and the
                # period says so rather than failing the whole company.
                if FLAG_VALUE_OUT_OF_RANGE not in flags:
                    flags.append(FLAG_VALUE_OUT_OF_RANGE)
            out[col] = dec
        else:
            out[col] = F.to_int(raw)
    return out


def _period_key(record: dict) -> str | None:
    d = F.to_date(F.pick(record, F.META_FIELDS['period_end']))
    return d.isoformat() if d else None


def merge_statements(income: list[dict], balance: list[dict], cashflow: list[dict],
                     period_type: str, fetched_at: datetime | None = None) -> list[dict]:
    """Join the three statement lists on period end; return column dicts.

    Periods with an income statement but no balance sheet / cash flow (or
    vice versa) are kept with `missing_statement` flagged, so a company
    whose cash flow history is shorter still shows its P&L.
    """
    fetched_at = fetched_at or datetime.now(timezone.utc)
    by_key: dict[str, dict] = {}
    for kind, records in (('income', income), ('balance', balance), ('cashflow', cashflow)):
        for rec in records or []:
            key = _period_key(rec)
            if not key:
                continue
            slot = by_key.setdefault(key, {'income': None, 'balance': None, 'cashflow': None})
            # FMP occasionally returns duplicate periods (restated); keep the
            # one with the latest filing date.
            existing = slot[kind]
            if existing is not None:
                old = F.to_date(F.pick(existing, F.META_FIELDS['filing_date']))
                new = F.to_date(F.pick(rec, F.META_FIELDS['filing_date']))
                if old and new and new < old:
                    continue
            slot[kind] = rec

    periods = []
    per_share = set(['eps_basic', 'eps_diluted'])
    for key in sorted(by_key):
        slot = by_key[key]
        meta_src = slot['income'] or slot['balance'] or slot['cashflow']
        row = {
            'period_type': period_type,
            'period_end': F.to_date(F.pick(meta_src, F.META_FIELDS['period_end'])),
            'fiscal_year': F.to_int(F.pick(meta_src, F.META_FIELDS['fiscal_year'])),
            'fiscal_period': _normalize_fiscal_period(
                F.pick(meta_src, F.META_FIELDS['fiscal_period']), period_type),
            'filing_date': F.to_date(F.pick(meta_src, F.META_FIELDS['filing_date'])),
            'reported_currency': F.pick(meta_src, F.META_FIELDS['reported_currency']),
            'source': 'fmp',
            'source_fetched_at': fetched_at,
            'raw': {k: v for k, v in slot.items() if v is not None},
        }
        flags = []
        if slot['income'] is not None:
            row.update(_map_fields(slot['income'], F.INCOME_FIELDS, per_share, set(), flags))
        else:
            row.update({c: None for c in F.INCOME_FIELDS})
            flags.append(FLAG_MISSING_STATEMENT)
        if slot['balance'] is not None:
            row.update(_map_fields(slot['balance'], F.BALANCE_FIELDS, set(), set()))
        else:
            row.update({c: None for c in F.BALANCE_FIELDS})
            if FLAG_MISSING_STATEMENT not in flags:
                flags.append(FLAG_MISSING_STATEMENT)
        if slot['cashflow'] is not None:
            row.update(_map_fields(slot['cashflow'], F.CASHFLOW_FIELDS, set(), set()))
        else:
            row.update({c: None for c in F.CASHFLOW_FIELDS})
            if FLAG_MISSING_STATEMENT not in flags:
                flags.append(FLAG_MISSING_STATEMENT)

        _fill_derivable(row)
        flags.extend(reconcile(row))
        row['quality_flags'] = flags
        periods.append(row)
    return periods


def _normalize_fiscal_period(value, period_type: str) -> str | None:
    if not value:
        return 'FY' if period_type == 'annual' else None
    text = str(value).strip().upper()
    if period_type == 'annual':
        return 'FY'
    if text in ('Q1', 'Q2', 'Q3', 'Q4'):
        return text
    return None


def _fill_derivable(row: dict) -> None:
    """Fill gaps FMP leaves that follow from other fields."""
    if row.get('total_debt') is None:
        parts = [row.get('short_term_debt'), row.get('long_term_debt')]
        if any(p is not None for p in parts):
            row['total_debt'] = sum(p or 0 for p in parts)
    if row.get('free_cash_flow') is None and row.get('cfo') is not None and row.get('capex') is not None:
        row['free_cash_flow'] = row['cfo'] + row['capex']  # capex is negative in FMP
    if row.get('gross_profit') is None and row.get('revenue') is not None and row.get('cost_of_revenue') is not None:
        row['gross_profit'] = row['revenue'] - row['cost_of_revenue']
    if row.get('ebitda') is None and row.get('operating_income') is not None \
            and row.get('depreciation_amortization') is not None:
        row['ebitda'] = row['operating_income'] + row['depreciation_amortization']


def reconcile(row: dict) -> list[str]:
    """Quality flags for one period (see module docstring)."""
    flags = []
    revenue = row.get('revenue')
    if revenue is None or revenue == 0:
        flags.append(FLAG_NO_REVENUE)
    if row.get('ebitda') is None:
        flags.append(FLAG_NO_EBITDA)

    pretax = row.get('pretax_income')
    ebitda = row.get('ebitda')
    if pretax is not None and ebitda is not None and revenue:
        dep = row.get('depreciation_amortization') or 0
        interest = row.get('interest_expense') or 0
        other = (row.get('other_income_net') or 0) + interest  # strip interest back out of "other"
        implied = ebitda - dep - interest + other
        base = max(abs(pretax), abs(revenue) * 0.01)
        if abs(implied - pretax) > RECONCILE_TOLERANCE * base:
            flags.append(FLAG_PBT_RECONCILE)

    assets = row.get('total_assets')
    liabilities = row.get('total_liabilities')
    equity = row.get('total_equity')
    if assets and liabilities is not None and equity is not None:
        total = liabilities + equity + (row.get('minority_interest') or 0)
        if abs(total - assets) > 0.02 * abs(assets):
            flags.append(FLAG_BALANCE_MISMATCH)
    return flags


def map_profile(profile: dict | None) -> dict:
    """FMP profile → CompanyFundamentals profile columns."""
    if not profile:
        return {}
    ipo = F.to_date(F.pick(profile, F.PROFILE_FIELDS['ipo_date']))
    employees = F.to_int(F.pick(profile, F.PROFILE_FIELDS['employees']))
    return {
        'exchange': _truncate(F.pick(profile, F.PROFILE_FIELDS['exchange']), 32),
        'industry': _truncate(F.pick(profile, F.PROFILE_FIELDS['industry']), 120),
        'sector': _truncate(F.pick(profile, F.PROFILE_FIELDS['sector']), 120),
        'description': F.pick(profile, F.PROFILE_FIELDS['description']),
        'website': _truncate(F.pick(profile, F.PROFILE_FIELDS['website']), 300),
        'ceo': _truncate(F.pick(profile, F.PROFILE_FIELDS['ceo']), 120),
        'employees': employees,
        'ipo_date': ipo,
        'country': _truncate(F.pick(profile, F.PROFILE_FIELDS['country']), 8),
        'is_adr': F.to_bool(F.pick(profile, F.PROFILE_FIELDS['is_adr'])),
    }


def profile_is_operating_company(profile: dict | None) -> bool:
    """ETFs, funds and delisted tickers have no fundamentals page."""
    if not profile:
        return False
    if F.to_bool(F.pick(profile, F.PROFILE_FIELDS['is_etf'])):
        return False
    if F.to_bool(F.pick(profile, F.PROFILE_FIELDS['is_fund'])):
        return False
    return True


def _truncate(value, width: int):
    if value is None:
        return None
    text = str(value).strip()
    return text[:width] if text else None
