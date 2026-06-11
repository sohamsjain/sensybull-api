"""Add push_subscription table for browser Web Push alerts

Revision ID: d0e1f2a3b4c5
Revises: c8d9e0f1a2b3
Create Date: 2026-06-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd0e1f2a3b4c5'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'push_subscription',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('user.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.String(255), nullable=False),
        sa.Column('auth', sa.String(255), nullable=False),
    )
    op.create_index('ix_push_subscription_user_id', 'push_subscription', ['user_id'])
    op.create_index('uq_push_subscription_endpoint', 'push_subscription', ['endpoint'],
                    unique=True)


def downgrade():
    op.drop_table('push_subscription')
