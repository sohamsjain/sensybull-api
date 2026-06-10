"""Add index on company.name for search

Revision ID: a7b8c9d0e1f2
Revises: f3a4b5c6d7e8
Create Date: 2026-06-10 13:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_company_name', 'company', ['name'])


def downgrade():
    op.drop_index('ix_company_name', table_name='company')
