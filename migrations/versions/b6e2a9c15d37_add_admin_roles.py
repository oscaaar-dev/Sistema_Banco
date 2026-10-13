"""Add administrative roles: account status, permissions, change history and activation links.

Migración **aditiva**: añade ``users.account_status`` (NOT NULL con valor por defecto ``ACTIVA``, de modo que todas las cuentas existentes
siguen activas) y crea tres tablas nuevas (``admin_permissions``, ``admin_change_log`` y ``admin_activation_tokens``). No modifica ni
borra filas existentes y no promociona a ningún usuario: el rol ``ADMIN_GENERAL`` se aprovisiona aparte (``flask admin-bootstrap``).

Debe aplicarse **antes** de desplegar este código: la aplicación se detiene al arrancar si el esquema no coincide.

Downgrade: elimina las tablas nuevas y la columna; se pierden los permisos, el historial de cambios y los enlaces de activación.
"""

from alembic import op
import sqlalchemy as sa


revision = 'b6e2a9c15d37'
down_revision = 'f7a1c4d9e620'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('account_status', sa.String(length=12), nullable=False, server_default='ACTIVA'))

    op.create_table(
        'admin_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.String(length=40), nullable=False),
        sa.Column('granted_by_id', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['granted_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'permission', name='uq_admin_permissions_user_permission'),
    )
    op.create_index('ix_admin_permissions_user_id', 'admin_permissions', ['user_id'])

    op.create_table(
        'admin_change_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_admin_change_log_actor_id', 'admin_change_log', ['actor_id'])
    op.create_index('ix_admin_change_log_target_id', 'admin_change_log', ['target_id'])
    op.create_index('ix_admin_change_log_created_at', 'admin_change_log', ['created_at'])

    op.create_table(
        'admin_activation_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('auto_activate', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_admin_activation_tokens_token_hash'),
    )
    op.create_index('ix_admin_activation_tokens_user_id', 'admin_activation_tokens', ['user_id'])
    op.create_index('ix_admin_activation_tokens_expires_at', 'admin_activation_tokens', ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_admin_activation_tokens_expires_at', table_name='admin_activation_tokens')
    op.drop_index('ix_admin_activation_tokens_user_id', table_name='admin_activation_tokens')
    op.drop_table('admin_activation_tokens')
    op.drop_index('ix_admin_change_log_created_at', table_name='admin_change_log')
    op.drop_index('ix_admin_change_log_target_id', table_name='admin_change_log')
    op.drop_index('ix_admin_change_log_actor_id', table_name='admin_change_log')
    op.drop_table('admin_change_log')
    op.drop_index('ix_admin_permissions_user_id', table_name='admin_permissions')
    op.drop_table('admin_permissions')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('account_status')
