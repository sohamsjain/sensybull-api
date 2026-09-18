"""FMP field names, pinned in one place.

FMP has two live generations of field names: the `/stable` API (2025+) and
the legacy `/api/v3` names, which differ in a handful of spellings
(`epsDiluted` vs `epsdiluted`, `netCashProvidedByInvestingActivities` vs
the misspelled `netCashUsedForInvestingActivites`, `fiscalYear` vs
`calendarYear`, `filingDate` vs `fillingDate`). Every canonical column
lists its aliases in priority order so the mapper reads either generation.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# canonical column -> FMP field aliases (first non-null wins)
INCOME_FIELDS = {
    'revenue': ['revenue'],
    'cost_of_revenue': ['costOfRevenue'],
    'gross_profit': ['grossProfit'],
    'sga': ['sellingGeneralAndAdministrativeExpenses'],
    'rnd': ['researchAndDevelopmentExpenses'],
    'other_opex': ['otherExpenses'],
    'operating_expenses': ['operatingExpenses'],
    'depreciation_amortization': ['depreciationAndAmortization'],
    'operating_income': ['operatingIncome'],
    'ebitda': ['ebitda'],
    'interest_expense': ['interestExpense'],
    'interest_income': ['interestIncome'],
    'other_income_net': ['totalOtherIncomeExpensesNet'],
    'pretax_income': ['incomeBeforeTax'],
    'income_tax': ['incomeTaxExpense'],
    'net_income': ['netIncome'],
    'eps_basic': ['eps'],
    'eps_diluted': ['epsDiluted', 'epsdiluted'],
    'weighted_shares_basic': ['weightedAverageShsOut'],
    'weighted_shares_diluted': ['weightedAverageShsOutDil'],
}

BALANCE_FIELDS = {
    'cash': ['cashAndCashEquivalents'],
    'short_term_investments': ['shortTermInvestments'],
    'receivables': ['netReceivables'],
    'inventory': ['inventory'],
    'other_current_assets': ['otherCurrentAssets'],
    'total_current_assets': ['totalCurrentAssets'],
    'ppe_net': ['propertyPlantEquipmentNet'],
    'goodwill': ['goodwill'],
    'intangibles': ['intangibleAssets'],
    'long_term_investments': ['longTermInvestments'],
    'other_noncurrent_assets': ['otherNonCurrentAssets'],
    'total_assets': ['totalAssets'],
    'payables': ['accountPayables'],
    'deferred_revenue': ['deferredRevenue'],
    'short_term_debt': ['shortTermDebt'],
    'other_current_liabilities': ['otherCurrentLiabilities'],
    'total_current_liabilities': ['totalCurrentLiabilities'],
    'long_term_debt': ['longTermDebt'],
    'capital_leases': ['capitalLeaseObligations'],
    'total_debt': ['totalDebt'],
    'total_liabilities': ['totalLiabilities'],
    'common_stock': ['commonStock'],
    'retained_earnings': ['retainedEarnings'],
    'total_equity': ['totalStockholdersEquity', 'totalEquity'],
    'minority_interest': ['minorityInterest'],
}

CASHFLOW_FIELDS = {
    'cfo': ['netCashProvidedByOperatingActivities', 'operatingCashFlow'],
    'capex': ['capitalExpenditure', 'investmentsInPropertyPlantAndEquipment'],
    'acquisitions': ['acquisitionsNet'],
    'cfi': ['netCashProvidedByInvestingActivities', 'netCashUsedForInvestingActivites'],
    'debt_issued': ['netDebtIssuance', 'debtIssued'],
    'debt_repaid': ['debtRepayment'],
    'buybacks': ['commonStockRepurchased'],
    'dividends_paid': ['netDividendsPaid', 'commonDividendsPaid', 'dividendsPaid'],
    'cff': ['netCashProvidedByFinancingActivities', 'netCashUsedProvidedByFinancingActivities'],
    'net_change_in_cash': ['netChangeInCash'],
    'free_cash_flow': ['freeCashFlow'],
    'stock_based_compensation': ['stockBasedCompensation'],
}

# Period metadata, shared by the three statements
META_FIELDS = {
    'period_end': ['date'],
    'fiscal_year': ['fiscalYear', 'calendarYear'],
    'fiscal_period': ['period'],
    'filing_date': ['filingDate', 'fillingDate'],
    'reported_currency': ['reportedCurrency'],
}

PROFILE_FIELDS = {
    'name': ['companyName'],
    'exchange': ['exchange', 'exchangeShortName'],
    'industry': ['industry'],
    'sector': ['sector'],
    'description': ['description'],
    'website': ['website'],
    'ceo': ['ceo'],
    'employees': ['fullTimeEmployees'],
    'ipo_date': ['ipoDate'],
    'country': ['country'],
    'is_adr': ['isAdr'],
    'is_etf': ['isEtf'],
    'is_fund': ['isFund'],
    'is_actively_trading': ['isActivelyTrading'],
    'market_cap': ['marketCap', 'mktCap'],
    'price': ['price'],
    'last_dividend': ['lastDividend', 'lastDiv'],
    'cik': ['cik'],
}


def pick(record: dict, aliases: list[str]):
    """First alias present in record with a non-null value, else None."""
    for name in aliases:
        if name in record and record[name] is not None:
            return record[name]
    return None


def to_int(value) -> int | None:
    """Whole-dollar integer from FMP's number-or-string values."""
    if value is None or value == '':
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def to_decimal(value) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def to_date(value) -> date | None:
    """FMP dates arrive as 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes')
    return bool(value)
