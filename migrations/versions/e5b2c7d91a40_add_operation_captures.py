"""Add operation captures (photo taken when an operation is confirmed).

Solo crea la tabla ``operation_captures`` con sus índices. No modifica ni borra ninguna tabla ni fila existente,
así que el código anterior sigue funcionando con la tabla ya creada.
"""

from alembic import op
import sqlalchemy as sa


revision = 'e5b2c7d91a40'
down_revision = 'd3a91c5e7f20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'operation_captures',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=False),
        sa.Column('image', sa.LargeBinary(), nullable=False),
        sa.Column('mime_type', sa.String(length=30), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('consent_version', sa.String(length=20), nullable=True),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_operation_captures_user_id', 'operation_captures', ['user_id'])
    op.create_index('ix_operation_captures_transaction_id', 'operation_captures', ['transaction_id'])
    op.create_index('ix_operation_captures_created_at', 'operation_captures', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_operation_captures_created_at', table_name='operation_captures')
    op.drop_index('ix_operation_captures_transaction_id', table_name='operation_captures')
    op.drop_index('ix_operation_captures_user_id', table_name='operation_captures')
    op.drop_table('operation_captures')
