"""Add fraud detection: risk evaluations, incidents, restrictions, held operations and session revocation mark.

Migración **aditiva**: crea seis tablas nuevas y añade una columna opcional (``users.sessions_revoked_at``). No modifica ni borra
filas existentes. Con el código anterior, las tablas nuevas simplemente no se usan; con el código nuevo hay que aplicarla
antes de desplegar (la aplicación se detiene al arrancar si el esquema no coincide).

Downgrade: elimina las tablas nuevas y la columna; se pierde el registro de evaluaciones, incidentes y restricciones.
"""

from alembic import op
import sqlalchemy as sa


revision = 'f7a1c4d9e620'
down_revision = 'e5b2c7d91a40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('sessions_revoked_at', sa.DateTime(), nullable=True))

    op.create_table(
        'risk_evaluations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('context', sa.String(length=20), nullable=False),
        sa.Column('event', sa.String(length=30), nullable=False),
        sa.Column('level', sa.String(length=14), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('confidence', sa.String(length=12), nullable=False),
        sa.Column('signals', sa.JSON(), nullable=False),
        sa.Column('rules_version', sa.String(length=30), nullable=False),
        sa.Column('model_version', sa.String(length=60), nullable=True),
        sa.Column('model_score', sa.Float(), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('recommended_action', sa.String(length=40), nullable=False),
        sa.Column('applied_action', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('error_code', sa.String(length=60), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_risk_evaluations_user_id', 'risk_evaluations', ['user_id'])
    op.create_index('ix_risk_evaluations_level', 'risk_evaluations', ['level'])
    op.create_index('ix_risk_evaluations_created_at', 'risk_evaluations', ['created_at'])

    op.create_table(
        'fraud_incidents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('evaluation_id', sa.Integer(), nullable=True),
        sa.Column('origin', sa.String(length=12), nullable=False),
        sa.Column('category', sa.String(length=14), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('level', sa.String(length=14), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('confirmed_by_id', sa.Integer(), nullable=True),
        sa.Column('confirmation_reason', sa.String(length=500), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('closed_by_id', sa.Integer(), nullable=True),
        sa.Column('closure_reason', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['evaluation_id'], ['risk_evaluations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['confirmed_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['closed_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fraud_incidents_user_id', 'fraud_incidents', ['user_id'])
    op.create_index('ix_fraud_incidents_status', 'fraud_incidents', ['status'])
    op.create_index('ix_fraud_incidents_created_at', 'fraud_incidents', ['created_at'])

    op.create_table(
        'incident_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incident_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=40), nullable=False),
        sa.Column('detail', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['incident_id'], ['fraud_incidents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_incident_events_incident_id', 'incident_events', ['incident_id'])

    op.create_table(
        'security_restrictions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('incident_id', sa.Integer(), nullable=True),
        sa.Column('scope', sa.String(length=14), nullable=False),
        sa.Column('origin', sa.String(length=12), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=False),
        sa.Column('starts_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('lifted_by_id', sa.Integer(), nullable=True),
        sa.Column('lifted_at', sa.DateTime(), nullable=True),
        sa.Column('lift_reason', sa.String(length=300), nullable=True),
        sa.Column('rules_version', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['incident_id'], ['fraud_incidents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['lifted_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_security_restrictions_active', 'security_restrictions', ['user_id', 'status', 'expires_at'])

    op.create_table(
        'fraud_model_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=60), nullable=False),
        sa.Column('algorithm', sa.String(length=40), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('trained_at', sa.DateTime(), nullable=False),
        sa.Column('n_train', sa.Integer(), nullable=False),
        sa.Column('n_eval', sa.Integer(), nullable=False),
        sa.Column('features', sa.JSON(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('validation', sa.JSON(), nullable=False),
        sa.Column('data_sha256', sa.String(length=64), nullable=True),
        sa.Column('sklearn_version', sa.String(length=20), nullable=False),
        sa.Column('python_version', sa.String(length=10), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
        sa.Column('activated_by_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['activated_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version', name='uq_fraud_model_versions_version'),
    )
    op.create_index('ix_fraud_model_versions_status', 'fraud_model_versions', ['status'])

    op.create_table(
        'held_operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('evaluation_id', sa.Integer(), nullable=True),
        sa.Column('incident_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('reason', sa.String(length=300), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('review_reason', sa.String(length=300), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['evaluation_id'], ['risk_evaluations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['incident_id'], ['fraud_incidents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_held_operations_user_id', 'held_operations', ['user_id'])
    op.create_index('ix_held_operations_fingerprint', 'held_operations', ['fingerprint'])
    op.create_index('ix_held_operations_status', 'held_operations', ['status'])
    op.create_index('ix_held_operations_created_at', 'held_operations', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_fraud_model_versions_status', table_name='fraud_model_versions')
    op.drop_table('fraud_model_versions')
    op.drop_index('ix_held_operations_created_at', table_name='held_operations')
    op.drop_index('ix_held_operations_status', table_name='held_operations')
    op.drop_index('ix_held_operations_fingerprint', table_name='held_operations')
    op.drop_index('ix_held_operations_user_id', table_name='held_operations')
    op.drop_table('held_operations')
    op.drop_index('ix_security_restrictions_active', table_name='security_restrictions')
    op.drop_table('security_restrictions')
    op.drop_index('ix_incident_events_incident_id', table_name='incident_events')
    op.drop_table('incident_events')
    op.drop_index('ix_fraud_incidents_created_at', table_name='fraud_incidents')
    op.drop_index('ix_fraud_incidents_status', table_name='fraud_incidents')
    op.drop_index('ix_fraud_incidents_user_id', table_name='fraud_incidents')
    op.drop_table('fraud_incidents')
    op.drop_index('ix_risk_evaluations_created_at', table_name='risk_evaluations')
    op.drop_index('ix_risk_evaluations_level', table_name='risk_evaluations')
    op.drop_index('ix_risk_evaluations_user_id', table_name='risk_evaluations')
    op.drop_table('risk_evaluations')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('sessions_revoked_at')
