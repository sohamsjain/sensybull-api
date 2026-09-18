"""Fundamentals storage: one row per company × reporting period, plus a
per-company snapshot of everything the page header, peers table and
analysis read.

Amounts are stored in whole dollars (BigInteger); per-share figures and
ratios are Numeric. Every period keeps the raw FMP records it was mapped
from so the mapping can be re-run without re-fetching.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel

# Canonical line items, in the order they appear on the page. Each maps to a
# BigInteger column on FundamentalsPeriod (dollars) except the per-share ones.
AMOUNT_COLUMNS = [
    # income statement
    'revenue', 'cost_of_revenue', 'gross_profit', 'sga', 'rnd', 'other_opex',
    'operating_expenses', 'depreciation_amortization', 'operating_income',
    'ebitda', 'interest_expense', 'interest_income', 'other_income_net',
    'pretax_income', 'income_tax', 'net_income',
    # balance sheet
    'cash', 'short_term_investments', 'receivables', 'inventory',
    'other_current_assets', 'total_current_assets', 'ppe_net', 'goodwill',
    'intangibles', 'long_term_investments', 'other_noncurrent_assets',
    'total_assets', 'payables', 'deferred_revenue', 'short_term_debt',
    'other_current_liabilities', 'total_current_liabilities', 'long_term_debt',
    'capital_leases', 'total_debt', 'total_liabilities', 'common_stock',
    'retained_earnings', 'total_equity', 'minority_interest',
    # cash flow
    'cfo', 'capex', 'acquisitions', 'cfi', 'debt_issued', 'debt_repaid',
    'buybacks', 'dividends_paid', 'cff', 'net_change_in_cash', 'free_cash_flow',
    'stock_based_compensation',
]
PER_SHARE_COLUMNS = ['eps_basic', 'eps_diluted']
SHARE_COLUMNS = ['weighted_shares_basic', 'weighted_shares_diluted']


class FundamentalsPeriod(BaseModel):
    __tablename__ = 'fundamentals_period'
    __table_args__ = (
        sa.UniqueConstraint('company_id', 'period_type', 'period_end',
                            name='uq_fundamentals_period_company_type_end'),
        sa.Index('ix_fundamentals_period_company_type_end',
                 'company_id', 'period_type', 'period_end'),
    )

    company_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('company.id', ondelete='CASCADE'), nullable=False)
    period_type: so.Mapped[str] = so.mapped_column(sa.String(8), nullable=False)  # annual|quarter
    fiscal_year: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, nullable=True)
    fiscal_period: so.Mapped[Optional[str]] = so.mapped_column(sa.String(4), nullable=True)  # FY|Q1..Q4
    period_end: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    filing_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    reported_currency: so.Mapped[Optional[str]] = so.mapped_column(sa.String(8), nullable=True)
    source: so.Mapped[str] = so.mapped_column(sa.String(16), nullable=False, default='fmp')
    source_fetched_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True)
    # Reconciliation flags, e.g. ["pbt_reconcile_fail", "no_ebitda"]
    quality_flags: so.Mapped[Optional[list]] = so.mapped_column(sa.JSON, nullable=True)
    raw: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)

    for _col in AMOUNT_COLUMNS:
        locals()[_col] = so.mapped_column(sa.BigInteger, nullable=True)
    for _col in PER_SHARE_COLUMNS:
        locals()[_col] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    for _col in SHARE_COLUMNS:
        locals()[_col] = so.mapped_column(sa.BigInteger, nullable=True)
    del _col

    def as_dict(self) -> dict:
        """Plain dict of every canonical value (used by rows/derive)."""
        out = {
            'period_type': self.period_type,
            'fiscal_year': self.fiscal_year,
            'fiscal_period': self.fiscal_period,
            'period_end': self.period_end,
            'filing_date': self.filing_date,
            'quality_flags': list(self.quality_flags or []),
        }
        for col in AMOUNT_COLUMNS + SHARE_COLUMNS:
            out[col] = getattr(self, col)
        for col in PER_SHARE_COLUMNS:
            val = getattr(self, col)
            out[col] = float(val) if val is not None else None
        return out

    def __repr__(self):
        return f"<FundamentalsPeriod {self.company_id} {self.period_type} {self.period_end}>"


class CompanyFundamentals(BaseModel):
    """One row per company: profile + derived numbers the page reads directly."""
    __tablename__ = 'company_fundamentals'

    company_id: so.Mapped[str] = so.mapped_column(
        sa.String(36), sa.ForeignKey('company.id', ondelete='CASCADE'),
        nullable=False, unique=True, index=True)

    # Profile
    exchange: so.Mapped[Optional[str]] = so.mapped_column(sa.String(32), nullable=True)
    industry: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True, index=True)
    sector: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True)
    description: so.Mapped[Optional[str]] = so.mapped_column(sa.Text, nullable=True)
    website: so.Mapped[Optional[str]] = so.mapped_column(sa.String(300), nullable=True)
    ceo: so.Mapped[Optional[str]] = so.mapped_column(sa.String(120), nullable=True)
    employees: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, nullable=True)
    ipo_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    country: so.Mapped[Optional[str]] = so.mapped_column(sa.String(8), nullable=True)
    is_adr: so.Mapped[bool] = so.mapped_column(sa.Boolean, nullable=False, default=False)
    fiscal_year_end_month: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, nullable=True)

    # Coverage
    has_fundamentals: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=False, index=True)
    coverage_from: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, nullable=True)
    latest_annual_end: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    latest_quarter_end: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    last_synced_at: so.Mapped[Optional[datetime]] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True, index=True)
    sync_error: so.Mapped[Optional[str]] = so.mapped_column(sa.String(300), nullable=True)

    # Price references for CAGR / yield (from FMP EOD history)
    price_ref_date: so.Mapped[Optional[date]] = so.mapped_column(sa.Date, nullable=True)
    price_1y_ago: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    price_3y_ago: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    price_5y_ago: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    price_10y_ago: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    high_52w: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    low_52w: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)
    dividends_ttm_ps: so.Mapped[Optional[Decimal]] = so.mapped_column(sa.Numeric(14, 4), nullable=True)

    # Everything derived from statements + price, rebuilt by derive.build_snapshot():
    # {"ratios": {...}, "growth": {...}, "analysis": {...}, "ttm": {...}}
    derived: so.Mapped[Optional[dict]] = so.mapped_column(sa.JSON, nullable=True)

    # Denormalised for peer queries (ordering/filtering without JSON ops)
    pe_ttm: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    roce: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    roe: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)
    dividend_yield: so.Mapped[Optional[float]] = so.mapped_column(sa.Float, nullable=True)

    company = so.relationship('Company', backref=so.backref('fundamentals', uselist=False))

    def __repr__(self):
        return f"<CompanyFundamentals {self.company_id} industry={self.industry}>"
