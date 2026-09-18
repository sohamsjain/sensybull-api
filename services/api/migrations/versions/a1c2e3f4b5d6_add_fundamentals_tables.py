"""Add fundamentals_period and company_fundamentals tables

Revision ID: a1c2e3f4b5d6
Revises: e8f9a0b1c2d3
Create Date: 2026-09-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1c2e3f4b5d6'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None

AMOUNT_COLUMNS = [
    'revenue', 'cost_of_revenue', 'gross_profit', 'sga', 'rnd', 'other_opex',
    'operating_expenses', 'depreciation_amortization', 'operating_income',
    'ebitda', 'interest_expense', 'interest_income', 'other_income_net',
    'pretax_income', 'income_tax', 'net_income',
    'cash', 'short_term_investments', 'receivables', 'inventory',
    'other_current_assets', 'total_current_assets', 'ppe_net', 'goodwill',
    'intangibles', 'long_term_investments', 'other_noncurrent_assets',
    'total_assets', 'payables', 'deferred_revenue', 'short_term_debt',
    'other_current_liabilities', 'total_current_liabilities', 'long_term_debt',
    'capital_leases', 'total_debt', 'total_liabilities', 'common_stock',
    'retained_earnings', 'total_equity', 'minority_interest',
    'cfo', 'capex', 'acquisitions', 'cfi', 'debt_issued', 'debt_repaid',
    'buybacks', 'dividends_paid', 'cff', 'net_change_in_cash', 'free_cash_flow',
    'stock_based_compensation',
]


def upgrade():
    op.create_table(
        'fundamentals_period',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('company_id', sa.String(36), nullable=False),
        sa.Column('period_type', sa.String(8), nullable=False),
        sa.Column('fiscal_year', sa.Integer(), nullable=True),
        sa.Column('fiscal_period', sa.String(4), nullable=True),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('filing_date', sa.Date(), nullable=True),
        sa.Column('reported_currency', sa.String(8), nullable=True),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('source_fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('quality_flags', sa.JSON(), nullable=True),
        sa.Column('raw', sa.JSON(), nullable=True),
        *[sa.Column(col, sa.BigInteger(), nullable=True) for col in AMOUNT_COLUMNS],
        sa.Column('eps_basic', sa.Numeric(14, 4), nullable=True),
        sa.Column('eps_diluted', sa.Numeric(14, 4), nullable=True),
        sa.Column('weighted_shares_basic', sa.BigInteger(), nullable=True),
        sa.Column('weighted_shares_diluted', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'period_type', 'period_end',
                            name='uq_fundamentals_period_company_type_end'),
    )
    op.create_index('ix_fundamentals_period_company_type_end', 'fundamentals_period',
                    ['company_id', 'period_type', 'period_end'], unique=False)

    op.create_table(
        'company_fundamentals',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('company_id', sa.String(36), nullable=False),
        sa.Column('exchange', sa.String(32), nullable=True),
        sa.Column('industry', sa.String(120), nullable=True),
        sa.Column('sector', sa.String(120), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('website', sa.String(300), nullable=True),
        sa.Column('ceo', sa.String(120), nullable=True),
        sa.Column('employees', sa.Integer(), nullable=True),
        sa.Column('ipo_date', sa.Date(), nullable=True),
        sa.Column('country', sa.String(8), nullable=True),
        sa.Column('is_adr', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('fiscal_year_end_month', sa.Integer(), nullable=True),
        sa.Column('has_fundamentals', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('coverage_from', sa.Integer(), nullable=True),
        sa.Column('latest_annual_end', sa.Date(), nullable=True),
        sa.Column('latest_quarter_end', sa.Date(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sync_error', sa.String(300), nullable=True),
        sa.Column('price_ref_date', sa.Date(), nullable=True),
        sa.Column('price_1y_ago', sa.Numeric(14, 4), nullable=True),
        sa.Column('price_3y_ago', sa.Numeric(14, 4), nullable=True),
        sa.Column('price_5y_ago', sa.Numeric(14, 4), nullable=True),
        sa.Column('price_10y_ago', sa.Numeric(14, 4), nullable=True),
        sa.Column('high_52w', sa.Numeric(14, 4), nullable=True),
        sa.Column('low_52w', sa.Numeric(14, 4), nullable=True),
        sa.Column('dividends_ttm_ps', sa.Numeric(14, 4), nullable=True),
        sa.Column('derived', sa.JSON(), nullable=True),
        sa.Column('pe_ttm', sa.Float(), nullable=True),
        sa.Column('roce', sa.Float(), nullable=True),
        sa.Column('roe', sa.Float(), nullable=True),
        sa.Column('dividend_yield', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_company_fundamentals_company_id'), 'company_fundamentals',
                    ['company_id'], unique=True)
    op.create_index(op.f('ix_company_fundamentals_industry'), 'company_fundamentals',
                    ['industry'], unique=False)
    op.create_index(op.f('ix_company_fundamentals_has_fundamentals'), 'company_fundamentals',
                    ['has_fundamentals'], unique=False)
    op.create_index(op.f('ix_company_fundamentals_last_synced_at'), 'company_fundamentals',
                    ['last_synced_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_company_fundamentals_last_synced_at'), table_name='company_fundamentals')
    op.drop_index(op.f('ix_company_fundamentals_has_fundamentals'), table_name='company_fundamentals')
    op.drop_index(op.f('ix_company_fundamentals_industry'), table_name='company_fundamentals')
    op.drop_index(op.f('ix_company_fundamentals_company_id'), table_name='company_fundamentals')
    op.drop_table('company_fundamentals')
    op.drop_index('ix_fundamentals_period_company_type_end', table_name='fundamentals_period')
    op.drop_table('fundamentals_period')
