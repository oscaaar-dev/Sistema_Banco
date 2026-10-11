"""Allow email OTP challenges to target non-transfer operations."""

from alembic import op
import sqlalchemy as sa


revision = 'b4f8a20d17c3'
down_revision = 'b1e4a95c0f27'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'operation_challenges',
        sa.Column('details_hash', sa.String(length=64), nullable=True),
    )
    with op.batch_alter_table('operation_challenges') as batch_op:
        batch_op.alter_column(
            'beneficiary_id', existing_type=sa.Integer(), nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('operation_challenges') as batch_op:
        batch_op.drop_column('details_hash')
        batch_op.alter_column(
            'beneficiary_id', existing_type=sa.Integer(), nullable=False,
        )
