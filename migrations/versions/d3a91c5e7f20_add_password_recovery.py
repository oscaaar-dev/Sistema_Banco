"""Add customer password recovery tokens and a credential-change timestamp.

- ``password_reset_tokens``: solo guarda el hash del token, con caducidad y marca de uso (un solo uso).
- ``users.password_changed_at``: nullable, permite invalidar sesiones abiertas tras cambiar la contraseña.
No borra ni modifica datos existentes.
"""

from alembic import op
import sqlalchemy as sa


revision = 'd3a91c5e7f20'
down_revision = 'b4f8a20d17c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime(), nullable=True))
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_password_reset_tokens_token_hash'),
    )
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'])
    op.create_index('ix_password_reset_tokens_expires_at', 'password_reset_tokens', ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_password_reset_tokens_expires_at', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('password_changed_at')
