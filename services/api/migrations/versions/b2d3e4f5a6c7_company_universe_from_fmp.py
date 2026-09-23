"""Company universe from FMP: company.listed + company_ticker_alias

Revision ID: b2d3e4f5a6c7
Revises: a1c2e3f4b5d6
Create Date: 2026-09-23 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b2d3e4f5a6c7'
down_revision = 'a1c2e3f4b5d6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('company') as batch:
        batch.add_column(sa.Column('listed', sa.Boolean(), nullable=True))
        batch.create_index('ix_company_listed', ['listed'])

    op.create_table(
        'company_ticker_alias',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ticker', sa.String(10), nullable=False),
        sa.Column('company_id', sa.String(36),
                  sa.ForeignKey('company.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
    )
    op.create_index('ix_company_ticker_alias_ticker', 'company_ticker_alias', ['ticker'], unique=True)
    op.create_index('ix_company_ticker_alias_company_id', 'company_ticker_alias', ['company_id'])


def downgrade():
    op.drop_index('ix_company_ticker_alias_company_id', table_name='company_ticker_alias')
    op.drop_index('ix_company_ticker_alias_ticker', table_name='company_ticker_alias')
    op.drop_table('company_ticker_alias')
    with op.batch_alter_table('company') as batch:
        batch.drop_index('ix_company_listed')
        batch.drop_column('listed')
