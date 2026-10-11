"""Add persistent temporary login throttling.

Revision ID: 8d6c1a7402b1
Revises: c9f9cff56362
"""
from alembic import op
import sqlalchemy as sa

revision = '8d6c1a7402b1'
down_revision = 'c9f9cff56362'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'login_attempt_limits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('identifier_hash', sa.String(length=64), nullable=False),
        sa.Column('failure_count', sa.Integer(), nullable=False),
        sa.Column('window_started_at', sa.DateTime(), nullable=False),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('identifier_hash'),
    )
    op.create_index(op.f('ix_login_attempt_limits_identifier_hash'), 'login_attempt_limits', ['identifier_hash'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_login_attempt_limits_identifier_hash'), table_name='login_attempt_limits')
    op.drop_table('login_attempt_limits')
