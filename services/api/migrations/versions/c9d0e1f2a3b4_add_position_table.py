"""Add position table (holdings + investment thesis)

Revision ID: c9d0e1f2a3b4
Revises: dec634594f13
Create Date: 2026-07-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c9d0e1f2a3b4'
down_revision = 'dec634594f13'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'position',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('company_id', sa.String(length=36), nullable=False),
        sa.Column('direction', sa.String(length=5), server_default='long', nullable=False),
        sa.Column('shares', sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column('cost_basis', sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column('thesis', sa.Text(), nullable=True),
        sa.Column('thesis_status', sa.String(length=12), server_default='intact', nullable=False),
        sa.Column('thesis_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_assessment_id', sa.String(length=36), nullable=True),
        sa.Column('opened_at', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'company_id', name='uq_position_user_company'),
    )
    op.create_index('ix_position_user_id', 'position', ['user_id'])
    op.create_index('ix_position_company_id', 'position', ['company_id'])
    op.create_index('ix_position_thesis_status', 'position', ['thesis_status'])


def downgrade():
    op.drop_index('ix_position_thesis_status', table_name='position')
    op.drop_index('ix_position_company_id', table_name='position')
    op.drop_index('ix_position_user_id', table_name='position')
    op.drop_table('position')
