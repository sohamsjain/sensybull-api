"""Add alert_preference and notification tables

Revision ID: f3a4b5c6d7e8
Revises: e25db7d1dccb
Create Date: 2026-06-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f3a4b5c6d7e8'
down_revision = 'e25db7d1dccb'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'alert_preference',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('max_tier', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('channels_json', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_alert_preference_user_id', 'alert_preference', ['user_id'])

    op.create_table(
        'notification',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('filing_event_id', sa.String(36),
                  sa.ForeignKey('filing_event.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel', sa.String(32), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_notification_user_id', 'notification', ['user_id'])
    op.create_index('ix_notification_filing_event_id', 'notification', ['filing_event_id'])
    op.create_index('ix_notification_channel', 'notification', ['channel'])
    op.create_index('ix_notification_status', 'notification', ['status'])
    op.create_index('ix_notification_user_created', 'notification', ['user_id', 'created_at'])
    op.create_unique_constraint(
        'uq_notification_user_event_channel',
        'notification',
        ['user_id', 'filing_event_id', 'channel'],
    )


def downgrade():
    op.drop_table('notification')
    op.drop_table('alert_preference')
