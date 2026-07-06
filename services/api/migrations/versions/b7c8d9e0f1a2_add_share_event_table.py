"""Add share_event table (Track-on-Sensybull funnel analytics)

Revision ID: b7c8d9e0f1a2
Revises: a9c1e3b5d7f9
Create Date: 2026-07-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7c8d9e0f1a2'
down_revision = 'a9c1e3b5d7f9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'share_event',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event', sa.String(length=32), nullable=False),
        sa.Column('symbol', sa.String(length=10), nullable=True),
        sa.Column('ref', sa.String(length=64), nullable=True),
        sa.Column('utm_source', sa.String(length=64), nullable=True),
        sa.Column('utm_medium', sa.String(length=64), nullable=True),
        sa.Column('utm_campaign', sa.String(length=64), nullable=True),
        sa.Column('referrer', sa.String(length=255), nullable=True),
        sa.Column('device', sa.String(length=16), nullable=True),
        sa.Column('browser', sa.String(length=32), nullable=True),
        sa.Column('country', sa.String(length=8), nullable=True),
        sa.Column('logged_in', sa.Boolean(), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_share_event_event', 'share_event', ['event'])
    op.create_index('ix_share_event_symbol', 'share_event', ['symbol'])
    op.create_index('ix_share_event_user_id', 'share_event', ['user_id'])


def downgrade():
    op.drop_index('ix_share_event_user_id', table_name='share_event')
    op.drop_index('ix_share_event_symbol', table_name='share_event')
    op.drop_index('ix_share_event_event', table_name='share_event')
    op.drop_table('share_event')
