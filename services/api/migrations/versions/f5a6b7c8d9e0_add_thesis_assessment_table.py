"""Add thesis_assessment table (filing event judged against a thesis)

Revision ID: f5a6b7c8d9e0
Revises: c9d0e1f2a3b4
Create Date: 2026-07-04 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f5a6b7c8d9e0'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'thesis_assessment',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('position_id', sa.String(length=36), nullable=False),
        sa.Column('filing_event_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('impact', sa.String(length=12), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('prior_status', sa.String(length=12), nullable=True),
        sa.Column('new_status', sa.String(length=12), nullable=True),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['position_id'], ['position.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['filing_event_id'], ['filing_event.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('position_id', 'filing_event_id', name='uq_assessment_position_event'),
    )
    op.create_index('ix_thesis_assessment_position_id', 'thesis_assessment', ['position_id'])
    op.create_index('ix_thesis_assessment_filing_event_id', 'thesis_assessment', ['filing_event_id'])
    op.create_index('ix_thesis_assessment_user_id', 'thesis_assessment', ['user_id'])


def downgrade():
    op.drop_index('ix_thesis_assessment_user_id', table_name='thesis_assessment')
    op.drop_index('ix_thesis_assessment_filing_event_id', table_name='thesis_assessment')
    op.drop_index('ix_thesis_assessment_position_id', table_name='thesis_assessment')
    op.drop_table('thesis_assessment')
