"""Add apple_id to user table

Revision ID: f4b5c6d7e8f9
Revises: e1f2a3b4c5d6
Create Date: 2026-06-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f4b5c6d7e8f9'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('apple_id', sa.String(100), nullable=True))
    op.create_index('ix_user_apple_id', 'user', ['apple_id'], unique=True)


def downgrade():
    op.drop_index('ix_user_apple_id', table_name='user')
    op.drop_column('user', 'apple_id')
