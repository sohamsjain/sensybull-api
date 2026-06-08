"""add event_types_json to filing_event

Revision ID: a1b2c3d4e5f6
Revises: 900e9c669a72
Create Date: 2026-05-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '900e9c669a72'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('filing_event', sa.Column('event_types_json', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('filing_event', 'event_types_json')
