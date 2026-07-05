"""Add structured thesis, thesis versions, and deep-assessment fields

- position: thesis_structured (JSON), thesis_version (int counter)
- thesis_version: new append-only snapshot table of thesis revisions
- thesis_assessment: two-stage judgment fields (stage, triage_impact,
  confidence, assumption_verdicts_json, citations_json), retroactive flag,
  and the thesis_version the verdict judged

Revision ID: a9c1e3b5d7f9
Revises: f5a6b7c8d9e0
Create Date: 2026-07-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a9c1e3b5d7f9'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('position', sa.Column('thesis_structured', sa.JSON(), nullable=True))
    op.add_column('position', sa.Column('thesis_version', sa.Integer(), nullable=False,
                                        server_default='0'))

    op.create_table(
        'thesis_version',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('position_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('thesis', sa.Text(), nullable=True),
        sa.Column('thesis_structured', sa.JSON(), nullable=True),
        sa.Column('source', sa.String(length=8), nullable=False, server_default='user'),
        sa.ForeignKeyConstraint(['position_id'], ['position.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('position_id', 'version',
                            name='uq_thesis_version_position_version'),
    )
    op.create_index('ix_thesis_version_position_id', 'thesis_version', ['position_id'])

    op.add_column('thesis_assessment', sa.Column('stage', sa.String(8), nullable=False,
                                                 server_default='triage'))
    op.add_column('thesis_assessment', sa.Column('triage_impact', sa.String(12), nullable=True))
    op.add_column('thesis_assessment', sa.Column('confidence', sa.Float(), nullable=True))
    op.add_column('thesis_assessment', sa.Column('assumption_verdicts_json', sa.JSON(), nullable=True))
    op.add_column('thesis_assessment', sa.Column('citations_json', sa.JSON(), nullable=True))
    op.add_column('thesis_assessment', sa.Column('retroactive', sa.Boolean(), nullable=False,
                                                 server_default=sa.false()))
    op.add_column('thesis_assessment', sa.Column('thesis_version', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('thesis_assessment', 'thesis_version')
    op.drop_column('thesis_assessment', 'retroactive')
    op.drop_column('thesis_assessment', 'citations_json')
    op.drop_column('thesis_assessment', 'assumption_verdicts_json')
    op.drop_column('thesis_assessment', 'confidence')
    op.drop_column('thesis_assessment', 'triage_impact')
    op.drop_column('thesis_assessment', 'stage')

    op.drop_index('ix_thesis_version_position_id', table_name='thesis_version')
    op.drop_table('thesis_version')

    op.drop_column('position', 'thesis_version')
    op.drop_column('position', 'thesis_structured')
