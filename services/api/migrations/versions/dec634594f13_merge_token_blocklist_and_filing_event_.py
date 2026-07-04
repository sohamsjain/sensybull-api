"""merge token_blocklist and filing_event index heads

Revision ID: dec634594f13
Revises: d1e2f3a4b5c6, d7e8f9a0b1c2
Create Date: 2026-07-04 13:16:40.060990

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dec634594f13'
down_revision = ('d1e2f3a4b5c6', 'd7e8f9a0b1c2')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
