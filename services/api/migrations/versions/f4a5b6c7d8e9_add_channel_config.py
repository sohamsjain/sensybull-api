"""Add channel_config table for per-user notification channel settings

Revision ID: f4a5b6c7d8e9
Revises: e1f2a3b4c5d6
Create Date: 2026-06-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f4a5b6c7d8e9'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'channel_config',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('channel', sa.String(32), nullable=False),
        sa.Column('config_json', sa.JSON(), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_channel_config_user_id', 'channel_config', ['user_id'])
    op.create_unique_constraint(
        'uq_channel_config_user_channel',
        'channel_config',
        ['user_id', 'channel'],
    )


def downgrade():
    op.drop_table('channel_config')
