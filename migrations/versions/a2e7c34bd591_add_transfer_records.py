"""Add records for simulated internal transfer flows."""

from alembic import op
import sqlalchemy as sa


revision = 'a2e7c34bd591'
down_revision = '8d6c1a7402b1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'transfers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('beneficiary_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('is_seed', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['beneficiary_id'], ['beneficiaries.id']),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transaction_id'),
    )
    op.create_index(op.f('ix_transfers_account_id'), 'transfers', ['account_id'], unique=False)
    op.create_index(op.f('ix_transfers_beneficiary_id'), 'transfers', ['beneficiary_id'], unique=False)
    op.create_index(op.f('ix_transfers_created_at'), 'transfers', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_transfers_created_at'), table_name='transfers')
    op.drop_index(op.f('ix_transfers_beneficiary_id'), table_name='transfers')
    op.drop_index(op.f('ix_transfers_account_id'), table_name='transfers')
    op.drop_table('transfers')
