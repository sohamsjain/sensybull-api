"""Add picture_url to user table

Revision ID: b5c6d7e8f9a0
Revises: a0b1c2d3e4f5
Create Date: 2026-07-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b5c6d7e8f9a0'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('picture_url', sa.String(500), nullable=True))


def downgrade():
    op.drop_column('user', 'picture_url')
