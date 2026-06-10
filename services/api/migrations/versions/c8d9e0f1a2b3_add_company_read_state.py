"""Add company_read_state table for chat-style watchlist UX

Revision ID: c8d9e0f1a2b3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c8d9e0f1a2b3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'company_read_state',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('company_id', sa.String(36), sa.ForeignKey('company.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('muted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_company_read_state_user_id', 'company_read_state', ['user_id'])
    op.create_index('ix_company_read_state_company_id', 'company_read_state', ['company_id'])
    op.create_unique_constraint(
        'uq_read_state_user_company',
        'company_read_state',
        ['user_id', 'company_id'],
    )


def downgrade():
    op.drop_table('company_read_state')
