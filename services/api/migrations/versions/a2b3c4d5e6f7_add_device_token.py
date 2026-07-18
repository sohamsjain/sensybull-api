"""Add device_token table for mobile (Expo) push registrations

Revision ID: a2b3c4d5e6f7
Revises: e8f9a0b1c2d3
Create Date: 2026-07-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'device_token',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('token', sa.String(255), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
    )
    op.create_index('ix_device_token_user_id', 'device_token', ['user_id'])
    op.create_index('uq_device_token_token', 'device_token', ['token'], unique=True)


def downgrade():
    op.drop_table('device_token')
