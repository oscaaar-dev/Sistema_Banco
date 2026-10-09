"""Add transaction-bound email OTP challenges for transfer confirmation."""

from alembic import op
import sqlalchemy as sa


revision = 'b1e4a95c0f27'
down_revision = 'a2e7c34bd591'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'operation_challenges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('operation_type', sa.String(length=30), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('beneficiary_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['beneficiary_id'], ['beneficiaries.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'operation_type', name='uq_operation_challenge_user_type'),
    )
    op.create_index(op.f('ix_operation_challenges_user_id'), 'operation_challenges', ['user_id'], unique=False)
    op.create_index(op.f('ix_operation_challenges_expires_at'), 'operation_challenges', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_operation_challenges_expires_at'), table_name='operation_challenges')
    op.drop_index(op.f('ix_operation_challenges_user_id'), table_name='operation_challenges')
    op.drop_table('operation_challenges')
