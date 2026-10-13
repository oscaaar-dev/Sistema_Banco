"""Motor antifraude: niveles, modo de observación, retención ANTES de modificar saldos, idempotencia y fallos controlados."""
import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from app import create_app, fraud
from app.fraud import anomaly, constants as c, engine, rules, service
from app.models import (Account, AuditLog, Beneficiary, FraudIncident, HeldOperation, IncidentEvent, InteractionSession, OperationCapture, RiskEvaluation,
                        SecurityRestriction, Transaction, Transfer, User, db, now_utc)
from app.services.banking_service import process_internal_transfer, process_operation
from fraud_helpers import add_debits, balance, counts, make_beneficiary, make_client

KEY = 'clave-de-prueba-{:04d}-0000000000'
TRACKED = (Transaction, Transfer, InteractionSession, OperationCapture)


@pytest.fixture
def world():
    app = create_app({'SECRET_KEY': 'k' * 40})
    with app.app_context():
        sender, sender_account = make_client('fsender', '51000001', '510000000001', balance='1000.00')
        receiver, receiver_account = make_client('freceiver', '51000002', '510000000002', balance='100.00')
        beneficiary = make_beneficiary(sender, '510000000002')
        make_beneficiary(sender, '519999999999', name='Externo X***')      # destinatario sin cuenta en Norte
        admin, _ = make_client('fadmin', '51000003', '510000000003', role='ADMINISTRADOR')
    return {'app': app, 'sender': sender, 'account': sender_account, 'receiver_account': receiver_account, 'beneficiary': beneficiary,
            'admin': admin}


def _transfer(world, amount, key=1, beneficiary=None, kind='TRANSFERENCIA', description=None):
    user = db.session.get(User, world['sender'])
    return process_operation(user, kind, Decimal(str(amount)), description or 'Transferencia a Destino', beneficiary_id=beneficiary or world['beneficiary'],
                             idempotency_key=KEY.format(key))


def _suspicious_setup(world):
    """Historial sintético que, con un monto casi igual al saldo, produce SOSPECHOSO (monto + frecuencia + ráfaga)."""
    add_debits(world['account'], [30, 60, 90], amount='10.00')


def _evaluations(**filters):
    return RiskEvaluation.query.filter_by(**filters).order_by(RiskEvaluation.id).all()


# ───────── Clasificación en niveles (reglas) ─────────

def _classify(world, amount, setup=None, **config):
    world['app'].config.update(config)
    with world['app'].app_context():
        if setup:
            setup()
        user, account = db.session.get(User, world['sender']), db.session.get(Account, world['account'])
        from app.fraud import features as feature_extraction
        beneficiary = db.session.get(Beneficiary, world['beneficiary'])
        f = feature_extraction.operation_features(user, account, 'TRANSFERENCIA', Decimal(str(amount)), beneficiary=beneficiary)
        signals = rules.score_operation(f, world['app'].config)
        confidence = 'SUFICIENTE' if f.n_history >= world['app'].config['FRAUD_MIN_HISTORY'] else 'INSUFICIENTE'
        score, level, groups = rules.classify(signals, confidence, world['app'].config)
        return {'score': score, 'level': level, 'groups': groups, 'codes': {s.code for s in signals}, 'confidence': confidence}


def test_level_1_a_routine_operation_is_normal(world):
    result = _classify(world, 20, setup=lambda: add_debits(world['account'], [86400 * 5, 86400 * 9, 86400 * 12], amount='20.00'))
    assert result['level'] == c.LEVEL_NORMAL and result['score'] < 25 and result['confidence'] == 'SUFICIENTE'


def test_level_2_unusual_but_isolated_signals_are_intermediate_not_suspicious(world):
    result = _classify(world, 850)           # 85 % del saldo + destinatario nuevo, sin historial
    assert result['level'] == c.LEVEL_INTERMEDIO and 'MONTO_80_SALDO' in result['codes']
    assert result['confidence'] == 'INSUFICIENTE'


def test_a_single_group_can_never_reach_suspicious_even_with_a_very_high_score(world):
    result = _classify(world, 990, setup=lambda: add_debits(world['account'], [86400 * 5, 86400 * 9, 86400 * 12], amount='10.00'))
    assert result['score'] >= 38 and result['level'] == c.LEVEL_INTERMEDIO       # solo el grupo «monto» es relevante
    assert result['groups'] == ['monto']


def test_level_3_several_independent_groups_are_suspicious(world):
    result = _classify(world, 960, setup=lambda: _suspicious_setup(world))
    assert result['level'] == c.LEVEL_SOSPECHOSO and result['score'] >= 55
    assert {'monto', 'frecuencia', 'automatizacion'} <= set(result['groups'])


def test_insufficient_history_raises_the_bar_for_suspicious(world):
    # sin historial del mismo tipo (la ráfaga son PAGOS), la confianza es insuficiente y el umbral de SOSPECHOSO sube a 65
    result = _classify(world, 700, setup=lambda: add_debits(world['account'], [30, 60, 90], amount='10.00', kind='PAGO'))
    assert result['confidence'] == 'INSUFICIENTE'
    assert result['level'] in (c.LEVEL_INTERMEDIO, c.LEVEL_SOSPECHOSO)
    low = _classify(world, 700, setup=None, FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE=999)
    assert low['level'] != c.LEVEL_SOSPECHOSO


def test_lack_of_history_is_not_a_risk_signal_by_itself(world):
    result = _classify(world, 5)
    assert result['confidence'] == 'INSUFICIENTE' and result['level'] == c.LEVEL_NORMAL and 'MONTO_6X_HISTORIAL' not in result['codes']


def test_false_positive_guard_regular_large_payments_stay_normal(world):
    # un cliente que siempre paga montos grandes en el mismo rango no debe dispararse por el monto frente a su mediana
    result = _classify(world, 300, setup=lambda: add_debits(world['account'], [86400 * 2, 86400 * 9, 86400 * 16], amount='280.00',
                                                           balance_after='5000.00'))
    assert 'MONTO_3X_HISTORIAL' not in result['codes'] and result['level'] == c.LEVEL_NORMAL


def test_group_caps_stop_a_pile_of_similar_signals_from_inflating_the_score():
    config = {'FRAUD_LEVEL_INTERMEDIO': 25, 'FRAUD_LEVEL_SOSPECHOSO': 55, 'FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE': 65}
    many = [rules.Signal(f'S{i}', 'monto', 30, 'x') for i in range(5)]
    score, level, groups = rules.classify(many, 'SUFICIENTE', config)
    assert score == rules.GROUP_CAPS['monto'] and level == c.LEVEL_INTERMEDIO and groups == ['monto']


def test_engine_levels_are_only_the_three_automatic_ones():
    assert c.LEVEL_FRAUDE not in c.ENGINE_LEVELS and set(c.ENGINE_LEVELS) == {c.LEVEL_NORMAL, c.LEVEL_INTERMEDIO, c.LEVEL_SOSPECHOSO}


# ───────── Modo de observación (shadow): registra, no interviene ─────────

def test_shadow_mode_is_the_default_and_records_a_normal_evaluation_linked_to_the_movement(world):
    assert world['app'].config['FRAUD_MODE'] == c.MODE_SHADOW
    with world['app'].app_context():
        tx = _transfer(world, 20)
        (evaluation,) = _evaluations()
        assert evaluation.transaction_id == tx.id and evaluation.level == c.LEVEL_NORMAL and evaluation.mode == c.MODE_SHADOW
        assert evaluation.applied_action == c.APPLIED_NONE and evaluation.rules_version == world['app'].config['FRAUD_RULES_VERSION']
        assert evaluation.status == 'OK' and evaluation.duration_ms is not None
        assert balance(world['account']) == Decimal('980.00') and balance(world['receiver_account']) == Decimal('120.00')


def test_shadow_mode_never_blocks_even_a_suspicious_operation_but_opens_an_incident_for_review(world):
    with world['app'].app_context():
        _suspicious_setup(world)
        tx = _transfer(world, 960)
        assert tx.amount == Decimal('-960.00') and balance(world['account']) == Decimal('40.00')
        evaluation = _evaluations(level=c.LEVEL_SOSPECHOSO)[0]
        assert evaluation.applied_action == c.APPLIED_NONE and evaluation.transaction_id == tx.id
        incident = FraudIncident.query.one()
        assert (incident.status, incident.category, incident.level, incident.origin) == (c.INCIDENT_OPEN, c.CATEGORY_TRANSACTIONAL, c.LEVEL_SOSPECHOSO, 'AUTOMATICO')
        assert HeldOperation.query.count() == 0 and SecurityRestriction.query.count() == 0
        assert {s['group'] for s in evaluation.signals} >= {'monto', 'frecuencia', 'automatizacion'}


def test_mode_off_does_not_evaluate_and_the_operation_still_completes(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_OFF
    with world['app'].app_context():
        _transfer(world, 20)
        assert RiskEvaluation.query.count() == 0 and balance(world['account']) == Decimal('980.00')


def test_an_unknown_mode_value_falls_back_to_observation():
    import config
    assert config.FRAUD_MODES == ('off', 'shadow', 'enforce')


# ───────── Modo enforce: retención ANTES de modificar saldos ─────────

def test_enforce_holds_a_suspicious_operation_before_touching_balances_or_creating_movements(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _suspicious_setup(world)
        before_sender, before_receiver, before = balance(world['account']), balance(world['receiver_account']), counts(*TRACKED)
        with pytest.raises(fraud.OperationHeld) as held:
            _transfer(world, 960)
        assert 'No se realizó ningún cargo' in str(held.value)
        db.session.expire_all()
        assert balance(world['account']) == before_sender and balance(world['receiver_account']) == before_receiver
        assert counts(*TRACKED) == before                                                    # sin movimientos, transferencias ni sesiones parciales
        hold = HeldOperation.query.one()
        assert (hold.status, hold.kind, hold.amount, hold.transaction_id) == (c.HOLD_HELD, 'TRANSFERENCIA', Decimal('960.00'), None)
        evaluation = RiskEvaluation.query.filter_by(level=c.LEVEL_SOSPECHOSO).one()
        assert evaluation.applied_action == c.APPLIED_HELD and evaluation.transaction_id is None and hold.evaluation_id == evaluation.id
        incident = FraudIncident.query.one()
        assert hold.incident_id == incident.id and incident.status == c.INCIDENT_OPEN
        assert IncidentEvent.query.filter_by(incident_id=incident.id).count() >= 1
        assert AuditLog.query.filter_by(action='FRAUD_OPERATION_HELD', user_id=world['sender']).count() == 1


def test_a_held_operation_repeated_with_the_same_data_does_not_duplicate_holds_or_incidents(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _suspicious_setup(world)
        for attempt in range(3):
            with pytest.raises(fraud.OperationHeld):
                _transfer(world, 960, key=attempt)
        assert HeldOperation.query.count() == 1 and FraudIncident.query.count() == 1
        assert balance(world['account']) == Decimal('1000.00')      # el historial sintético son filas, no cargos; nada se cargó


def test_enforce_lets_normal_and_intermediate_operations_through_with_the_existing_verifications(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _transfer(world, 20, key=1)
        _transfer(world, 940, key=2)              # 96 % del saldo restante (vaciado): una sola señal fuerte → INTERMEDIO
        levels = [e.level for e in _evaluations()]
        assert levels[0] == c.LEVEL_NORMAL and levels[1] == c.LEVEL_INTERMEDIO
        assert _evaluations()[1].applied_action == c.APPLIED_ALERT and HeldOperation.query.count() == 0
        assert Transaction.query.filter_by(account_id=world['account'], operation_type='TRANSFERENCIA').count() == 2


def test_own_account_transfers_are_evaluated_but_never_held(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        other = Account(user_id=world['sender'], account_type='Cuenta de ahorros', number='510000000099', balance=Decimal('0.00'))
        db.session.add(other)
        db.session.commit()
        add_debits(world['account'], [20, 40, 60, 80])
        user = db.session.get(User, world['sender'])
        process_internal_transfer(user, world['account'], other.id, Decimal('950.00'), idempotency_key=KEY.format(7))
        evaluation = RiskEvaluation.query.filter_by(context='TRASLADO_PROPIO').one()
        assert evaluation.level == c.LEVEL_SOSPECHOSO and evaluation.applied_action == c.APPLIED_ALERT
        assert HeldOperation.query.count() == 0 and balance(other.id) == Decimal('950.00')


def test_an_active_operations_restriction_blocks_operating_in_every_mode_including_off(world):
    for mode in (c.MODE_OFF, c.MODE_SHADOW, c.MODE_ENFORCE):
        world['app'].config['FRAUD_MODE'] = mode
        with world['app'].app_context():
            SecurityRestriction.query.delete()
            db.session.add(SecurityRestriction(user_id=world['sender'], scope=c.SCOPE_OPERATIONS, origin=c.ORIGIN_SANCTION, reason='Prueba',
                                               expires_at=now_utc() + timedelta(days=1)))
            db.session.commit()
            before = counts(*TRACKED)
            with pytest.raises(fraud.OperationRestricted):
                _transfer(world, 20, key=11)
            assert counts(*TRACKED) == before and balance(world['account']) == Decimal('1000.00')


# ───────── Autorización administrativa de una retención ─────────

def test_an_authorized_hold_lets_the_same_operation_complete_once_and_is_consumed(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _suspicious_setup(world)
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960, key=1)
        hold = HeldOperation.query.one()
        service.review_hold(hold, db.session.get(User, world['admin']), 'Verificado por llamada al cliente', authorize=True)
        db.session.commit()
        tx = _transfer(world, 960, key=2)                                         # el cliente repite la operación
        db.session.expire_all()
        hold = db.session.get(HeldOperation, hold.id)
        assert hold.status == c.HOLD_EXECUTED and hold.transaction_id == tx.id and balance(world['account']) == Decimal('40.00')
        assert _evaluations()[-1].applied_action == c.APPLIED_ADMIN_AUTHORIZED
        # la autorización se usó una vez: otra operación idéntica vuelve a evaluarse (y a retenerse)
        add_debits(world['account'], [30, 60, 90], amount='1.00')
        db.session.get(Account, world['account']).balance = Decimal('1000.00')
        db.session.commit()
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960, key=3)


def test_a_rejected_or_expired_hold_does_not_authorize_anything(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _suspicious_setup(world)
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960, key=1)
        hold = HeldOperation.query.one()
        service.review_hold(hold, db.session.get(User, world['admin']), 'No se pudo verificar al cliente', authorize=False)
        db.session.commit()
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960, key=2)
        assert HeldOperation.query.filter_by(status=c.HOLD_REJECTED).count() == 1 and balance(world['account']) == Decimal('1000.00')


def test_an_authorization_for_one_operation_does_not_cover_a_different_amount_or_recipient(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _suspicious_setup(world)
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960, key=1)
        service.review_hold(HeldOperation.query.one(), db.session.get(User, world['admin']), 'Verificado por llamada', authorize=True)
        db.session.commit()
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 955, key=2)                        # otro monto
        other = [b.id for b in Beneficiary.query.filter_by(user_id=world['sender']).all() if b.id != world['beneficiary']][0]
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960, key=3, beneficiary=other)     # otro destinatario


# ───────── Fallos controlados ─────────

def test_a_failed_evaluation_is_recorded_as_not_evaluated_and_never_as_no_risk(world, monkeypatch):
    monkeypatch.setattr(rules, 'score_operation', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('fallo simulado')))
    with world['app'].app_context():
        _transfer(world, 20)
        evaluation = RiskEvaluation.query.one()
        assert (evaluation.level, evaluation.status, evaluation.error_code) == (c.LEVEL_NO_EVALUADO, 'ERROR', 'RuntimeError')
        assert evaluation.level != c.LEVEL_NORMAL and evaluation.recommended_action == c.ACTION_MANUAL
        assert balance(world['account']) == Decimal('980.00')               # shadow: el fallo no afecta a la operación


def test_enforce_failsafe_small_operations_continue_and_large_ones_are_held_when_evaluation_fails(world, monkeypatch):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    monkeypatch.setattr(rules, 'score_operation', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('fallo simulado')))
    with world['app'].app_context():
        _transfer(world, 400, key=1)                                          # ≤ FRAUD_FAILSAFE_MAX_AMOUNT (500)
        assert _evaluations()[0].applied_action == c.APPLIED_FAILSAFE_ALLOW
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 550, key=2)
        hold = HeldOperation.query.one()
        assert 'No se pudo evaluar el riesgo' in hold.reason and balance(world['account']) == Decimal('600.00')
        assert FraudIncident.query.count() == 0                                # un fallo no es sospecha: no abre incidente


def test_an_unavailable_or_failing_model_does_not_change_the_level_or_break_the_operation(world, monkeypatch):
    monkeypatch.setattr(anomaly, 'score', lambda f: (_ for _ in ()).throw(RuntimeError('modelo roto')))
    with world['app'].app_context():
        _transfer(world, 20)
        evaluation = RiskEvaluation.query.one()
        assert evaluation.level == c.LEVEL_NORMAL and evaluation.model_score is None and evaluation.model_version is None
        assert evaluation.status == 'OK'


def test_the_model_score_is_recorded_for_observation_but_never_changes_the_level(world, monkeypatch):
    monkeypatch.setattr(anomaly, 'score', lambda f: ('iforest-prueba', -0.4321))        # el modelo dice «muy atípico»
    with world['app'].app_context():
        _transfer(world, 20)
        evaluation = RiskEvaluation.query.one()
        assert evaluation.model_version == 'iforest-prueba' and evaluation.model_score == pytest.approx(-0.4321)
        assert evaluation.level == c.LEVEL_NORMAL                                          # una puntuación del modelo no es fraude ni cambia nada


def test_a_failure_while_saving_the_evaluation_never_breaks_the_bank_operation(world):
    world['app'].config['FRAUD_RULES_VERSION'] = None            # viola NOT NULL al guardar la evaluación (dentro de un savepoint)
    with world['app'].app_context():
        tx = _transfer(world, 20)
        assert tx.amount == Decimal('-20.00') and RiskEvaluation.query.count() == 0
        assert balance(world['account']) == Decimal('980.00') and balance(world['receiver_account']) == Decimal('120.00')


def test_a_zero_balance_or_missing_history_never_crashes_the_feature_extraction(world):
    with world['app'].app_context():
        from app.fraud import features as feature_extraction
        user, account = db.session.get(User, world['sender']), db.session.get(Account, world['account'])
        account.balance = Decimal('0.00')
        f = feature_extraction.operation_features(user, account, 'PAGO', Decimal('5.00'))
        assert f.balance_share is None and f.n_history == 0 and f.first_time_beneficiary is None
        assert rules.score_operation(f, world['app'].config) == []


# ───────── Atomicidad, idempotencia y concurrencia ─────────

def test_idempotent_replay_returns_the_same_movement_and_evaluates_only_once(world):
    with world['app'].app_context():
        first = _transfer(world, 20, key=5)
        second = _transfer(world, 20, key=5)
        assert first.id == second.id and balance(world['account']) == Decimal('980.00')
        assert RiskEvaluation.query.count() == 1 and Transaction.query.filter_by(idempotency_key=KEY.format(5)).count() == 1


def test_insufficient_balance_is_still_rejected_by_the_bank_rules_regardless_of_the_engine(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        with pytest.raises(ValueError, match='Saldo insuficiente'):
            _transfer(world, 5000)
        assert RiskEvaluation.query.count() == 0 and counts(*TRACKED) == counts(*TRACKED)


def test_a_rolled_back_operation_leaves_no_evaluation_linked_to_a_movement(world, monkeypatch):
    from app.services import banking_service
    monkeypatch.setattr(banking_service, 'log', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('fallo tras evaluar')))
    with world['app'].app_context():
        with pytest.raises(RuntimeError):
            _transfer(world, 20)
        db.session.rollback()
        assert Transaction.query.filter_by(account_id=world['account'], operation_type='TRANSFERENCIA').count() == 0
        assert balance(world['account']) == Decimal('1000.00') and balance(world['receiver_account']) == Decimal('100.00')
        # En SQLite (solo pruebas) el savepoint de la evaluación puede confirmarse antes que la transacción; en PostgreSQL se
        # revierte con ella. En ambos casos ninguna evaluación queda vinculada a un movimiento que no existe.
        assert all(e.transaction_id is None for e in RiskEvaluation.query.all())


def test_two_simultaneous_submissions_with_the_same_key_debit_only_once(tmp_path):
    """Dos hilos con la misma clave de idempotencia sobre una base SQLite en archivo temporal (fuera de data/)."""
    database = tmp_path / 'concurrency.db'
    app = create_app({'SECRET_KEY': 'k' * 40, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database.as_posix()}',
                      'SQLALCHEMY_ENGINE_OPTIONS': {'connect_args': {'timeout': 30}}})
    with app.app_context():
        db.create_all()
        sender, account = make_client('csender', '52000001', '520000000001')
        make_client('creceiver', '52000002', '520000000002', balance='0.00')
        beneficiary = make_beneficiary(sender, '520000000002')
    outcomes, barrier = [], threading.Barrier(2)

    def attempt():
        with app.app_context():
            barrier.wait()
            try:
                user = db.session.get(User, sender)
                tx = process_operation(user, 'TRANSFERENCIA', Decimal('100.00'), 'Transferencia a Destino', beneficiary_id=beneficiary,
                                       idempotency_key=KEY.format(9))
                outcomes.append(('ok', tx.id))
            except Exception as error:                                           # el segundo hilo puede ver la clave ya usada
                db.session.rollback()
                outcomes.append(('error', type(error).__name__))
            finally:
                db.session.remove()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    with app.app_context():
        assert db.session.get(Account, account).balance == Decimal('900.00')       # un solo cargo
        assert Transaction.query.filter_by(account_id=account, operation_type='TRANSFERENCIA').count() == 1
        assert any(kind == 'ok' for kind, _ in outcomes)
        db.session.remove()
    with app.app_context():
        db.engine.dispose()
