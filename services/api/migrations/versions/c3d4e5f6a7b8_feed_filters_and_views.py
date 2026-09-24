"""Feed filters: company sector/industry/exchange + saved feed views

Revision ID: c3d4e5f6a7b8
Revises: b2d3e4f5a6c7
Create Date: 2026-09-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2d3e4f5a6c7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('company') as batch:
        batch.add_column(sa.Column('sector', sa.String(64), nullable=True))
        batch.add_column(sa.Column('industry', sa.String(120), nullable=True))
        batch.add_column(sa.Column('exchange', sa.String(16), nullable=True))
        batch.create_index('ix_company_sector', ['sector'])

    # Seed from the fundamentals snapshots we already hold; the next
    # sync-companies run fills in the rest of the universe. Only canonical
    # sector names are copied (see app/services/feed_filters.py SECTORS).
    op.execute("""
        UPDATE company SET
            sector = (SELECT cf.sector FROM company_fundamentals cf
                      WHERE cf.company_id = company.id
                        AND cf.sector IN ('Technology', 'Healthcare', 'Financial Services',
                                          'Consumer Cyclical', 'Consumer Defensive',
                                          'Communication Services', 'Industrials', 'Energy',
                                          'Basic Materials', 'Real Estate', 'Utilities')),
            industry = (SELECT cf.industry FROM company_fundamentals cf
                        WHERE cf.company_id = company.id)
        WHERE EXISTS (SELECT 1 FROM company_fundamentals cf WHERE cf.company_id = company.id)
    """)

    op.create_table(
        'feed_view',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36),
                  sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('filters', sa.JSON(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_feed_view_user_id', 'feed_view', ['user_id'])


def downgrade():
    op.drop_index('ix_feed_view_user_id', table_name='feed_view')
    op.drop_table('feed_view')
    with op.batch_alter_table('company') as batch:
        batch.drop_index('ix_company_sector')
        batch.drop_column('exchange')
        batch.drop_column('industry')
        batch.drop_column('sector')
