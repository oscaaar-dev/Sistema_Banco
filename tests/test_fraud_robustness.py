"""Robustez del detector y consistencia bancaria: un modelo defectuoso, datos extremos o ausentes y los modos de funcionamiento no
pueden aprobar, bloquear ni descuadrar nada por su cuenta. Solo datos sintéticos en SQLite en memoria; nada toca ``data/``.
"""
import math
from decimal import Decimal

import numpy as np
import pytest

from app import create_app, fraud
from app.fraud import anomaly, constants as c, features as feature_extraction, rules
from app.fraud.features import AccessFeatures, OperationFeatures
from app.models import Account, HeldOperation, FraudIncident, RiskEvaluation, Transaction, User, db
from app.services.banking_service import process_operation
from fraud_helpers import add_debits, balance, make_beneficiary, make_client

KEY = 'robustez-{:04d}-0000000000000000'


class FakeModel:
    """Sustituye al modelo activo: devuelve lo que se le indique o falla."""

    def __init__(self, value=None, error=None):
        self.value, self.error = value, error
        self.calls = 0

    def decision_function(self, rows):
        self.calls += 1
        if self.error:
            raise self.error
        return np.array([self.value] * len(rows))


@pytest.fixture
def world():
    app = create_app({'SECRET_KEY': 'k' * 40})
    with app.app_context():
        sender, account = make_client('rsender', '56000001', '560000000001', balance='1000.00')
        receiver, receiver_account = make_client('rreceiver', '56000002', '560000000002', balance='100.00')
        beneficiary = make_beneficiary(sender, '560000000002')
    return {'app': app, 'sender': sender, 'account': account, 'receiver_account': receiver_account, 'beneficiary': beneficiary}


def _install(monkeypatch, model):
    monkeypatch.setattr(anomaly, '_load', lambda: (model, 'iforest-prueba', None))


def _transfer(world, amount, key=1):
    user = db.session.get(User, world['sender'])
    return process_operation(user, 'TRANSFERENCIA', Decimal(str(amount)), 'Transferencia a Destino', beneficiary_id=world['beneficiary'],
                             idempotency_key=KEY.format(key))


def _features(**overrides):
    base = dict(kind='TRANSFERENCIA', amount=Decimal('50'), balance=Decimal('1000'))
    base.update(overrides)
    return OperationFeatures(**base)


# ───────── Resultados del modelo no utilizables ─────────

@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_a_non_finite_model_score_is_discarded_and_the_operation_completes(world, monkeypatch, value):
    _install(monkeypatch, FakeModel(value))
    with world['app'].app_context():
        assert anomaly.score(_features()) == (None, None)
        _transfer(world, 20)
        evaluation = RiskEvaluation.query.one()
        assert evaluation.model_score is None and evaluation.model_version is None and evaluation.level == c.LEVEL_NORMAL
        assert balance(world['account']) == Decimal('980.00')


@pytest.mark.parametrize('error', [RuntimeError('roto'), ValueError('forma de entrada distinta'), MemoryError(), KeyError('x')])
def test_a_model_that_raises_never_propagates_out_of_scoring(world, monkeypatch, error):
    _install(monkeypatch, FakeModel(error=error))
    with world['app'].app_context():
        assert anomaly.score(_features()) == (None, None)
        _transfer(world, 20)
        assert RiskEvaluation.query.one().model_score is None and balance(world['account']) == Decimal('980.00')


def test_a_model_with_a_different_number_of_inputs_is_ignored_not_trusted(world, monkeypatch):
    from sklearn.ensemble import IsolationForest
    wrong = IsolationForest(n_estimators=10, random_state=1).fit(np.random.RandomState(0).normal(size=(200, 7)))      # espera 7 variables, no 4
    _install(monkeypatch, wrong)
    with world['app'].app_context():
        assert anomaly.score(_features()) == (None, None)


def test_scoring_is_off_when_the_detector_is_disabled_or_missing(world):
    with world['app'].app_context():
        assert anomaly.score(_features()) == (None, None)                                    # sin modelo activo
        world['app'].config['FRAUD_MODEL_ENABLED'] = False
        assert anomaly.score(_features()) == (None, None)


# ───────── El modelo nunca decide por sí solo ─────────

def test_in_enforce_a_very_atypical_model_score_cannot_hold_a_normal_operation(world, monkeypatch):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    _install(monkeypatch, FakeModel(-0.99))                                                  # «máximamente anómalo»
    with world['app'].app_context():
        _transfer(world, 20)
        assert HeldOperation.query.count() == 0 and FraudIncident.query.count() == 0
        evaluation = RiskEvaluation.query.one()
        assert evaluation.level == c.LEVEL_NORMAL and evaluation.applied_action == c.APPLIED_ALLOW and evaluation.model_score == pytest.approx(-0.99)
        assert balance(world['account']) == Decimal('980.00')


def test_in_enforce_a_reassuring_model_score_cannot_release_a_suspicious_operation(world, monkeypatch):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    _install(monkeypatch, FakeModel(0.99))                                                   # «completamente normal»
    with world['app'].app_context():
        add_debits(world['account'], [30, 60, 90], amount='10.00')
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 960)
        assert HeldOperation.query.one().status == c.HOLD_HELD and balance(world['account']) == Decimal('1000.00')
        assert RiskEvaluation.query.one().level == c.LEVEL_SOSPECHOSO


def test_the_message_shown_to_the_customer_does_not_reveal_scores_or_rules(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        add_debits(world['account'], [30, 60, 90], amount='10.00')
        with pytest.raises(fraud.OperationHeld) as held:
            _transfer(world, 960)
        message = str(held.value).lower()
        for leaked in ('puntuaci', 'umbral', 'vaciado', 'ráfaga', 'rafaga', 'modelo', 'isolation', 'regla'):
            assert leaked not in message, leaked


# ───────── Datos extremos o ausentes ─────────

@pytest.mark.parametrize('amount,balance_value,ops', [
    (Decimal('0.01'), Decimal('0.01'), 0), (Decimal('1E+15'), Decimal('1E+15'), 10 ** 6), (Decimal('1E+15'), Decimal('0'), 0),
    (Decimal('0.00'), Decimal('0.00'), 0), (Decimal('999999999999.99'), Decimal('0.01'), 5), (Decimal('1'), Decimal('1E-8'), 3),
])
def test_extreme_amounts_balances_and_counts_give_finite_inputs_and_a_valid_level(amount, balance_value, ops):
    f = _features(amount=amount, balance=balance_value, n_history=500, median_amount=Decimal('10'), ops_2m=ops, ops_10m=ops, ops_1h=ops,
                  distinct_beneficiaries_24h=ops, first_time_beneficiary=True, new_context=True, password_changed_recently=True,
                  failed_logins_30m=ops, otp_failures_30m=ops)
    vector = anomaly.vector_from_features(f)
    assert len(vector) == len(anomaly.FEATURE_NAMES) and all(math.isfinite(v) for v in vector) and 0 <= vector[1] <= 1.5
    config = create_app({'SECRET_KEY': 'k' * 40}).config
    signals = rules.score_operation(f, config)
    score, level, groups = rules.classify(signals, 'SUFICIENTE', config)
    assert 0 <= score <= 100 and level in (c.LEVEL_NORMAL, c.LEVEL_INTERMEDIO, c.LEVEL_SOSPECHOSO)


def test_missing_values_are_not_risk_signals_and_never_crash():
    config = create_app({'SECRET_KEY': 'k' * 40}).config
    f = _features(median_amount=None, first_time_beneficiary=None, new_context=None, n_history=0)
    signals = rules.score_operation(f, config)
    score, level, _ = rules.classify(signals, 'INSUFICIENTE', config)
    assert level == c.LEVEL_NORMAL and score == 0
    nothing = AccessFeatures(new_context=None)
    assert rules.classify(rules.score_access(nothing, config), 'INSUFICIENTE', config)[1] == c.LEVEL_NORMAL
    assert anomaly.vector_from_features(_features(balance=Decimal('0'))) [1] == 0.0


def test_a_very_large_amount_is_still_governed_by_the_balance_rules(world):
    with world['app'].app_context():
        movements = Transaction.query.count()
        with pytest.raises(ValueError, match='El monto debe estar entre'):
            _transfer(world, '999999999999.99', key=1)
        with pytest.raises(ValueError, match='[Ss]aldo'):
            _transfer(world, '10000.00', key=2)                                                # dentro del límite, pero mayor que el saldo
        assert balance(world['account']) == Decimal('1000.00') and Transaction.query.count() == movements


def test_the_evaluation_failure_in_enforce_never_approves_a_large_operation(world, monkeypatch):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    monkeypatch.setattr(feature_extraction, 'operation_features', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('fallo de extracción')))
    with world['app'].app_context():
        _transfer(world, 20, key=1)                                                           # importe pequeño: continúa (fail-safe documentado)
        assert RiskEvaluation.query.one().status != 'OK'
        with pytest.raises(fraud.OperationHeld):
            _transfer(world, 600, key=2)                                                      # importe grande: se retiene
        assert balance(world['account']) == Decimal('980.00')


# ───────── Consistencia bancaria con datos aislados ─────────

@pytest.mark.parametrize('mode', [c.MODE_OFF, c.MODE_SHADOW, c.MODE_ENFORCE])
def test_the_ledger_reconciles_in_every_mode_and_held_operations_never_touch_it(world, mode):
    world['app'].config['FRAUD_MODE'] = mode
    with world['app'].app_context():
        add_debits(world['account'], [30, 60, 90], amount='1.00', balance_after='1000.00')       # historial sintético (no mueve saldos)
        start_sender, start_receiver = balance(world['account']), balance(world['receiver_account'])
        history_before = Transaction.query.filter_by(account_id=world['account']).count()
        done, held = [], 0
        for key, amount in enumerate(['10.00', '25.50', '5.25', '980.00', '40.00', '3.10'], start=1):
            try:
                done.append(Decimal(amount) if _transfer(world, amount, key=key) else Decimal('0'))
            except fraud.OperationHeld:
                held += 1
            except Exception:                                                                    # saldo insuficiente u otra regla bancaria: sin cambios
                db.session.rollback()
        db.session.expire_all()
        moved = sum(done)
        assert balance(world['account']) == start_sender - moved and balance(world['receiver_account']) == start_receiver + moved
        assert (balance(world['account']) + balance(world['receiver_account'])) == start_sender + start_receiver          # el dinero se conserva
        new_debits = Transaction.query.filter_by(account_id=world['account']).count() - history_before
        assert new_debits == len(done)
        assert HeldOperation.query.filter(HeldOperation.transaction_id.isnot(None)).count() == 0                          # lo retenido no tiene movimiento
        if mode != c.MODE_ENFORCE:
            assert held == 0 and HeldOperation.query.count() == 0
        keys = [t.idempotency_key for t in Transaction.query.filter_by(account_id=world['account']).all() if t.idempotency_key]
        assert len(keys) == len(set(keys))                                                    # ninguna clave de idempotencia duplicada


def test_replaying_the_same_request_never_charges_twice_in_enforce(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        first = _transfer(world, 30, key=5)
        second = _transfer(world, 30, key=5)
        assert first.id == second.id and balance(world['account']) == Decimal('970.00') and balance(world['receiver_account']) == Decimal('130.00')
        assert Transaction.query.filter_by(account_id=world['account'], operation_type='TRANSFERENCIA').count() == 1
        assert RiskEvaluation.query.count() == 1


def test_no_account_ever_ends_with_a_negative_balance_after_a_burst(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        for key in range(1, 15):
            try:
                _transfer(world, 150, key=key)
            except Exception:
                db.session.rollback()
        db.session.expire_all()
        assert all(a.balance >= 0 for a in Account.query.all())
        assert balance(world['account']) + balance(world['receiver_account']) == Decimal('1100.00')
