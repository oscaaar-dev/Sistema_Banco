"""Migración frente a modelos (sin tocar ninguna base existente) y coste acotado de la evaluación de riesgo."""
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect

from app import create_app
from app.fraud import constants as c, engine as fraud_engine
from app.models import Account, Beneficiary, FraudModelVersion, InteractionSession, MLResult, RiskEvaluation, Transaction, User, db, now_utc
from app.services.banking_service import process_operation
from fraud_helpers import add_debits, make_beneficiary, make_client

NEW_TABLES = ('risk_evaluations', 'fraud_incidents', 'incident_events', 'security_restrictions', 'held_operations', 'fraud_model_versions')


def _migrated_inspector(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'testing')
    monkeypatch.setenv('MIGRATION_DATABASE_URL', 'sqlite:///:memory:')
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    engine = create_engine('sqlite:///:memory:')
    return engine, config


def test_the_migration_creates_exactly_what_the_models_define_and_downgrades_cleanly(monkeypatch):
    engine, config = _migrated_inspector(monkeypatch)
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        inspector = inspect(connection)
        for name in NEW_TABLES:
            table = db.metadata.tables[name]
            migrated = {column['name']: column for column in inspector.get_columns(name)}
            assert set(migrated) == {column.name for column in table.columns}, name
            for column in table.columns:
                assert migrated[column.name]['nullable'] == column.nullable, (name, column.name)
            indexed = {tuple(index['column_names']) for index in inspector.get_indexes(name)}
            for index in table.indexes:
                assert tuple(col.name for col in index.columns) in indexed, (name, index.name)
        user_columns = {column['name']: column for column in inspector.get_columns('users')}
        assert 'sessions_revoked_at' in user_columns and user_columns['sessions_revoked_at']['nullable'] is True
        assert inspector.get_unique_constraints('fraud_model_versions')
        command.downgrade(config, 'e5b2c7d91a40')                       # vuelve a la revisión anterior
        inspector = inspect(connection)
        assert not set(NEW_TABLES) & set(inspector.get_table_names())
        assert 'sessions_revoked_at' not in {column['name'] for column in inspector.get_columns('users')}
        assert 'operation_captures' in inspector.get_table_names()      # lo anterior queda intacto
    engine.dispose()


def test_the_migration_is_additive_and_has_a_single_head():
    from alembic.script import ScriptDirectory
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ['b6e2a9c15d37']                   # la cabeza actual es la de roles administrativos (encadenada tras la antifraude)
    assert script.get_revision('b6e2a9c15d37').down_revision == 'f7a1c4d9e620'
    for name in ('f7a1c4d9e620_add_fraud_detection.py', 'b6e2a9c15d37_add_admin_roles.py'):
        source = (root / 'migrations' / 'versions' / name).read_text(encoding='utf-8')
        upgrade = source.split('def upgrade')[1].split('def downgrade')[0]
        assert 'drop_' not in upgrade and 'alter_column' not in upgrade and 'execute(' not in upgrade     # nada destructivo ni de datos


# ───────── Rendimiento acotado ─────────

@pytest.fixture
def world():
    app = create_app({'SECRET_KEY': 'k' * 40})
    with app.app_context():
        sender, account = make_client('perfsender', '60000001', '600000000001', balance='100000.00')
        make_client('perfreceiver', '60000002', '600000000002', balance='0.00')
        beneficiary = make_beneficiary(sender, '600000000002')
    return {'app': app, 'sender': sender, 'account': account, 'beneficiary': beneficiary}


class StatementCounter:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        event.listen(db.engine, 'before_cursor_execute', self._record)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, 'before_cursor_execute', self._record)

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)


def _evaluate(world, amount='25.00'):
    from app.fraud import features as feature_extraction
    user, account = db.session.get(User, world['sender']), db.session.get(Account, world['account'])
    beneficiary = db.session.get(Beneficiary, world['beneficiary'])
    return fraud_engine.evaluate_operation(user, account, 'TRANSFERENCIA', Decimal(amount), beneficiary=beneficiary)


def test_an_evaluation_needs_a_small_fixed_number_of_queries_regardless_of_the_history_size(world):
    with world['app'].app_context():
        with StatementCounter() as small:
            _evaluate(world)
        add_debits(world['account'], [86400 * d for d in range(1, 80)] * 6, amount='15.00', balance_after='100000.00')   # ~470 filas de historial
        db.session.commit()
        with StatementCounter() as large:
            _evaluate(world)
        selects = lambda counter: [s for s in counter.statements if s.lstrip().upper().startswith('SELECT')]
        assert len(selects(large)) == len(selects(small)) <= 12            # no crece con el historial
        assert all('LIMIT' in s.upper() for s in selects(large) if 'FROM transactions' in s and 'count(' not in s.lower())   # historial acotado
        assert not [s for s in large.statements if s.lstrip().upper().startswith(('DELETE', 'UPDATE'))]


def test_evaluation_latency_stays_small_with_a_large_history(world):
    with world['app'].app_context():
        add_debits(world['account'], [86400 * d for d in range(1, 90)] * 10, amount='15.00', balance_after='100000.00')
        durations = []
        for _ in range(60):
            started = time.perf_counter()
            _evaluate(world)
            durations.append((time.perf_counter() - started) * 1000)
        durations.sort()
        p95 = durations[int(len(durations) * 0.95) - 1]
        assert p95 < 250, f'p95={p95:.1f} ms'                                # cota holgada (SQLite en memoria); el valor real se informa aparte
        recorded = [e.duration_ms for e in RiskEvaluation.query.all()]
        assert len(recorded) == 60 and all(value is not None and value >= 0 for value in recorded)


def test_the_session_clustering_is_bounded_and_no_longer_reprocesses_every_session(world):
    world['app'].config['INTERACTION_CLUSTER_MAX_SESSIONS'] = 8
    with world['app'].app_context():
        user = db.session.get(User, world['sender'])
        now = now_utc()
        for index in range(30):                                              # 30 sesiones previas con resultado de agrupamiento ya guardado
            session = InteractionSession(user_id=user.id, operation_type='PAGO', started_at=now - timedelta(minutes=100 - index),
                                         duration_seconds=10 + index % 7, steps=5)
            db.session.add(session)
            db.session.flush()
            db.session.add(MLResult(session_id=session.id, cluster_id=0, label='ANTIGUO', method='previo'))
        db.session.commit()
        with StatementCounter() as counter:
            process_operation(user, 'TRANSFERENCIA', Decimal('20.00'), 'Transferencia a Destino', beneficiary_id=world['beneficiary'],
                              idempotency_key='clave-cluster-0001-0000000000')
        session_selects = [s for s in counter.statements if 'FROM sessions' in s and 'ORDER BY sessions.started_at DESC' in s]
        assert session_selects and all('LIMIT' in s.upper() for s in session_selects)
        refreshed = MLResult.query.filter(MLResult.label != 'ANTIGUO').count()
        assert refreshed <= 8                                                # solo las sesiones recientes se recalculan
        assert MLResult.query.filter_by(label='ANTIGUO').count() >= 22       # las antiguas ya no se tocan


def test_the_engine_reads_only_bounded_windows_and_never_scans_the_whole_database(world):
    with world['app'].app_context():
        with StatementCounter() as counter:
            _evaluate(world)
        text = ' '.join(counter.statements).lower()
        assert 'from fraud_incidents' not in text and 'from risk_evaluations' not in text      # no consulta las tablas propias del sistema
        assert 'from transactions' in text and 'created_at >=' in text
        assert 'from sessions' not in text and 'ml_results' not in text         # no toca las tablas del agrupamiento


def test_model_inference_adds_no_database_queries(world, monkeypatch):
    from app.fraud import anomaly
    monkeypatch.setattr(anomaly, 'score', lambda features: ('iforest-x', 0.1))
    with world['app'].app_context():
        with StatementCounter() as with_model:
            _evaluate(world)
        monkeypatch.setattr(anomaly, 'score', lambda features: (None, None))
        with StatementCounter() as without_model:
            _evaluate(world)
        assert len(with_model.statements) == len(without_model.statements)
