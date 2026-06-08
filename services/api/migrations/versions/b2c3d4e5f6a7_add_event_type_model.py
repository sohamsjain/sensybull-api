"""add event_type model

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-28 12:00:00.000000

"""
import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Create the event_type table
    op.create_table(
        'event_type',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('filing_event_id', sa.String(length=36), nullable=False),
        sa.Column('type_name', sa.String(length=100), nullable=False),
        sa.Column('attributes', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['filing_event_id'], ['filing_event.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_event_type_filing_event_id', 'event_type', ['filing_event_id'])
    op.create_index('ix_event_type_type_name', 'event_type', ['type_name'])

    # Backfill: migrate existing event_types_json data into event_type rows
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, event_types_json FROM filing_event WHERE event_types_json IS NOT NULL")
    )
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        raw = row[1]  # event_types_json
        if isinstance(raw, str):
            try:
                types = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
        elif isinstance(raw, list):
            types = raw
        else:
            continue

        for type_name in types:
            if not isinstance(type_name, str) or not type_name.strip():
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO event_type (id, created_at, filing_event_id, type_name, attributes) "
                    "VALUES (:id, :now, :fid, :name, NULL)"
                ),
                {"id": str(uuid.uuid4()), "now": now, "fid": row[0], "name": type_name.strip()},
            )


def downgrade():
    op.drop_index('ix_event_type_type_name', table_name='event_type')
    op.drop_index('ix_event_type_filing_event_id', table_name='event_type')
    op.drop_table('event_type')
