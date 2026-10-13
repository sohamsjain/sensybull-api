"""Alerts on saved feed views

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('feed_view') as batch:
        batch.add_column(sa.Column('alerts_enabled', sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.create_index('ix_feed_view_alerts_enabled', ['alerts_enabled'])
    with op.batch_alter_table('notification') as batch:
        batch.add_column(sa.Column('feed_view_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_notification_feed_view_id', 'feed_view',
                                 ['feed_view_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('notification') as batch:
        batch.drop_constraint('fk_notification_feed_view_id', type_='foreignkey')
        batch.drop_column('feed_view_id')
    with op.batch_alter_table('feed_view') as batch:
        batch.drop_index('ix_feed_view_alerts_enabled')
        batch.drop_column('alerts_enabled')
