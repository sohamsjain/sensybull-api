"""Add token_blocklist table for JWT revocation

Revision ID: d1e2f3a4b5c6
Revises: c6d7e8f9a0b1
Create Date: 2026-07-04 07:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c6d7e8f9a0b1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'token_blocklist',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('jti', sa.String(36), nullable=False),
        sa.Column('token_type', sa.String(16), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('jti', name='uq_token_blocklist_jti'),
    )
    op.create_index(op.f('ix_token_blocklist_jti'), 'token_blocklist', ['jti'], unique=False)
    op.create_index(op.f('ix_token_blocklist_user_id'), 'token_blocklist', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_token_blocklist_user_id'), table_name='token_blocklist')
    op.drop_index(op.f('ix_token_blocklist_jti'), table_name='token_blocklist')
    op.drop_table('token_blocklist')
