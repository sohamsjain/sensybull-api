"""Add index on filing_event.created_at for received-order feed

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-04 12:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'd7e8f9a0b1c2'
down_revision = 'c6d7e8f9a0b1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_filing_event_created_at', 'filing_event', ['created_at'])


def downgrade():
    op.drop_index('ix_filing_event_created_at', table_name='filing_event')
