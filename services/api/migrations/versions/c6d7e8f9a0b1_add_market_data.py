"""Add market data columns to company and the price_reaction table

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c6d7e8f9a0b1'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('company', sa.Column('shares_outstanding', sa.BigInteger(), nullable=True))
    op.add_column('company', sa.Column('shares_as_of', sa.Date(), nullable=True))
    op.add_column('company', sa.Column('last_price', sa.Numeric(14, 4), nullable=True))
    op.add_column('company', sa.Column('price_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('company', sa.Column('market_cap', sa.BigInteger(), nullable=True))
    op.add_column('company', sa.Column('atr_14', sa.Numeric(14, 4), nullable=True))
    op.add_column('company', sa.Column('atr_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_company_market_cap'), 'company', ['market_cap'], unique=False)

    op.create_table(
        'price_reaction',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('filing_event_id', sa.String(36), nullable=False),
        sa.Column('ticker', sa.String(10), nullable=False),
        sa.Column('interval', sa.String(8), nullable=False),
        sa.Column('measure_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('baseline_price', sa.Numeric(14, 4), nullable=True),
        sa.Column('baseline_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('measured_price', sa.Numeric(14, 4), nullable=True),
        sa.Column('measured_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pct_change', sa.Float(), nullable=True),
        sa.Column('atr_14', sa.Numeric(14, 4), nullable=True),
        sa.Column('is_explosive', sa.Boolean(), nullable=False),
        sa.Column('error', sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(['filing_event_id'], ['filing_event.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('filing_event_id', 'interval', name='uq_price_reaction_event_interval'),
    )
    op.create_index(op.f('ix_price_reaction_filing_event_id'), 'price_reaction',
                    ['filing_event_id'], unique=False)
    op.create_index('ix_price_reaction_due', 'price_reaction',
                    ['status', 'measure_at'], unique=False)


def downgrade():
    op.drop_index('ix_price_reaction_due', table_name='price_reaction')
    op.drop_index(op.f('ix_price_reaction_filing_event_id'), table_name='price_reaction')
    op.drop_table('price_reaction')

    op.drop_index(op.f('ix_company_market_cap'), table_name='company')
    op.drop_column('company', 'atr_updated_at')
    op.drop_column('company', 'atr_14')
    op.drop_column('company', 'market_cap')
    op.drop_column('company', 'price_updated_at')
    op.drop_column('company', 'last_price')
    op.drop_column('company', 'shares_as_of')
    op.drop_column('company', 'shares_outstanding')
