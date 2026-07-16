"""Add press-release fields to filing_event (source, issuer, fingerprints, backfill)

Revision ID: e8f9a0b1c2d3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e8f9a0b1c2d3'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('filing_event', sa.Column(
        'source', sa.String(length=32), nullable=False, server_default='edgar'))
    op.add_column('filing_event', sa.Column(
        'issuer_name', sa.String(length=500), nullable=True))
    op.add_column('filing_event', sa.Column(
        'content_fingerprint', sa.String(length=64), nullable=True))
    op.add_column('filing_event', sa.Column(
        'headline_fingerprint', sa.String(length=64), nullable=True))
    op.add_column('filing_event', sa.Column(
        'content_simhash', sa.String(length=16), nullable=True))
    op.add_column('filing_event', sa.Column(
        'related_edgar_id', sa.String(length=500), nullable=True))
    op.add_column('filing_event', sa.Column(
        'related_filing_url', sa.String(length=500), nullable=True))
    op.add_column('filing_event', sa.Column(
        'related_accession_number', sa.String(length=50), nullable=True))

    op.create_index('ix_filing_event_source', 'filing_event', ['source'])
    op.create_index('ix_filing_event_content_fingerprint', 'filing_event',
                    ['content_fingerprint'])
    op.create_index('ix_filing_event_related_edgar_id', 'filing_event',
                    ['related_edgar_id'])


def downgrade():
    op.drop_index('ix_filing_event_related_edgar_id', table_name='filing_event')
    op.drop_index('ix_filing_event_content_fingerprint', table_name='filing_event')
    op.drop_index('ix_filing_event_source', table_name='filing_event')

    op.drop_column('filing_event', 'related_accession_number')
    op.drop_column('filing_event', 'related_filing_url')
    op.drop_column('filing_event', 'related_edgar_id')
    op.drop_column('filing_event', 'content_simhash')
    op.drop_column('filing_event', 'headline_fingerprint')
    op.drop_column('filing_event', 'content_fingerprint')
    op.drop_column('filing_event', 'issuer_name')
    op.drop_column('filing_event', 'source')
