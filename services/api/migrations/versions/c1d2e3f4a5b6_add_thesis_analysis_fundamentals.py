"""Add thesis, event analysis, and fundamentals cache

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
Create Date: 2026-06-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    # ── company_fundamentals (XBRL companyfacts cache) ────────────────────
    op.create_table(
        'company_fundamentals',
        sa.Column('cik', sa.String(length=20), nullable=False),
        sa.Column('snapshot_json', sa.JSON(), nullable=True),
        sa.Column('as_of_period', sa.String(length=20), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('company_fundamentals', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_company_fundamentals_cik'), ['cik'], unique=True)

    # ── thesis_revision (append-only per-company thesis) ──────────────────
    op.create_table(
        'thesis_revision',
        sa.Column('company_id', sa.String(length=36), nullable=False),
        sa.Column('filing_event_id', sa.String(length=36), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('narrative', sa.Text(), nullable=False),
        sa.Column('change_summary', sa.Text(), nullable=True),
        sa.Column('points_json', sa.JSON(), nullable=True),
        sa.Column('as_of', sa.DateTime(timezone=True), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['filing_event_id'], ['filing_event.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('thesis_revision', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_thesis_revision_company_id'), ['company_id'], unique=False)
        batch_op.create_index(
            batch_op.f('ix_thesis_revision_filing_event_id'), ['filing_event_id'], unique=False)
        batch_op.create_index(
            'ix_thesis_revision_company_version', ['company_id', 'version'], unique=False)

    # ── event_analysis (second-order analysis per filing) ─────────────────
    op.create_table(
        'event_analysis',
        sa.Column('filing_event_id', sa.String(length=36), nullable=False),
        sa.Column('metrics_json', sa.JSON(), nullable=True),
        sa.Column('insight_json', sa.JSON(), nullable=True),
        sa.Column('thesis_revision_id', sa.String(length=36), nullable=True),
        sa.Column('fundamentals_as_of', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['filing_event_id'], ['filing_event.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['thesis_revision_id'], ['thesis_revision.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('event_analysis', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_event_analysis_filing_event_id'), ['filing_event_id'], unique=True)

    # ── new columns on existing tables ────────────────────────────────────
    with op.batch_alter_table('filing_event', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'analysis_status', sa.String(length=16), nullable=False,
            server_default='pending'))
        batch_op.create_index(
            batch_op.f('ix_filing_event_analysis_status'), ['analysis_status'], unique=False)

    # Backfill: events that predate analysis must NOT be picked up by the worker's
    # pending-sweep (that would re-fan-out old filings + burn LLM calls). Mark them
    # terminal. New rows inserted by the subscriber default to 'pending'.
    op.execute("UPDATE filing_event SET analysis_status = 'skipped'")

    with op.batch_alter_table('company', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'current_thesis_revision_id', sa.String(length=36), nullable=True))


def downgrade():
    with op.batch_alter_table('company', schema=None) as batch_op:
        batch_op.drop_column('current_thesis_revision_id')

    with op.batch_alter_table('filing_event', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_filing_event_analysis_status'))
        batch_op.drop_column('analysis_status')

    with op.batch_alter_table('event_analysis', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_event_analysis_filing_event_id'))
    op.drop_table('event_analysis')

    with op.batch_alter_table('thesis_revision', schema=None) as batch_op:
        batch_op.drop_index('ix_thesis_revision_company_version')
        batch_op.drop_index(batch_op.f('ix_thesis_revision_filing_event_id'))
        batch_op.drop_index(batch_op.f('ix_thesis_revision_company_id'))
    op.drop_table('thesis_revision')

    with op.batch_alter_table('company_fundamentals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_fundamentals_cik'))
    op.drop_table('company_fundamentals')
