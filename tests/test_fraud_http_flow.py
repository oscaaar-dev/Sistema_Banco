"""Flujo HTTP completo con OTP y cámara obligatorios: la retención ocurre ANTES de modificar saldos y no rompe las garantías existentes."""
import re
from datetime import timedelta
from decimal import Decimal

import pytest

import app.routes as routes_module
from app import create_app
from app.fraud import constants as c, service
from app.models import (Account, Beneficiary, FraudIncident, HeldOperation, OperationCapture, OperationChallenge, RiskEvaluation, SecurityRestriction, Transaction, Transfer, User,
                        db, now_utc)
from camera_helpers import accept_camera_notice, capture_file
from fraud_helpers import add_debits, make_client

KEY = 'transfer-otp-fraud-key-0000000001'
AMOUNT = '8400.00'            # 98 % del saldo de muestra del cliente demo


def _csrf(response):
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


@pytest.fixture
def flow(monkeypatch):
    sent = []
    monkeypatch.setattr(routes_module, 'send_operation_code', lambda email, code, details: sent.append(code))
    app = create_app({'SECRET_KEY': 'k' * 40})
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        user.email = 'client@example.test'
        account = Account.query.filter_by(user_id=user.id).one()
        beneficiary = Beneficiary.query.filter_by(user_id=user.id).first()
        admin_id, _ = make_client('flowadmin', '59000001', '590000000001', role='ADMINISTRADOR')
        db.session.commit()
        ids = {'user': user.id, 'account': account.id, 'beneficiary': beneficiary.id, 'admin': admin_id, 'balance': account.balance}
        add_debits(account.id, [30, 60, 90], amount='10.00', balance_after=str(account.balance))      # ráfaga sintética reciente
        stamp = user.session_stamp()
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session.update({'_user_id': str(ids['user']), '_fresh': True, 'login_2fa_verified': True, 'pwd_stamp': stamp})
    return {'app': app, 'client': client, 'sent': sent, 'ids': ids, 'token': _csrf(client.get('/operation/transfer'))}


def _send_code(flow):
    return flow['client'].post('/operation/transfer/send-code', data={
        'csrf_token': flow['token'], 'idempotency_key': KEY, 'beneficiary_id': str(flow['ids']['beneficiary']), 'amount': AMOUNT})


def _confirm(flow, code):
    accept_camera_notice(flow['client'], flow['token'], 'transfer', KEY)
    return flow['client'].post('/operation/transfer', data={
        'csrf_token': flow['token'], 'idempotency_key': KEY, 'beneficiary_id': str(flow['ids']['beneficiary']), 'amount': AMOUNT,
        'otp_code': code, 'form_duration_seconds': '10', 'camera_consent': 'yes', 'capture': capture_file()})


def _snapshot(flow):
    with flow['app'].app_context():
        return {'balance': db.session.get(Account, flow['ids']['account']).balance, 'transactions': Transaction.query.count(),
                'transfers': Transfer.query.count(), 'captures': OperationCapture.query.count()}


def test_enforce_holds_the_operation_before_balances_change_and_consumes_the_otp(flow):
    flow['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    before = _snapshot(flow)
    assert _send_code(flow).status_code == 302
    response = _confirm(flow, flow['sent'][-1])
    assert response.status_code == 200 and 'quedó retenida para revisión' in response.get_data(as_text=True)
    assert 'No se realizó ningún cargo' in response.get_data(as_text=True)
    assert _snapshot(flow) == before                                      # saldo, movimientos, transferencias y fotos sin cambios
    with flow['app'].app_context():
        assert OperationChallenge.query.filter_by(user_id=flow['ids']['user']).one().consumed_at is not None   # el OTP ya no sirve
        hold = HeldOperation.query.one()
        assert hold.status == c.HOLD_HELD and hold.amount == Decimal(AMOUNT) and hold.kind == 'TRANSFERENCIA' and hold.transaction_id is None
        assert RiskEvaluation.query.filter_by(level=c.LEVEL_SOSPECHOSO).count() == 1 and FraudIncident.query.count() == 1


def test_the_held_operation_cannot_be_completed_by_reusing_the_consumed_otp(flow):
    flow['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    _send_code(flow)
    code = flow['sent'][-1]
    _confirm(flow, code)
    before = _snapshot(flow)
    response = _confirm(flow, code)
    assert response.status_code == 200 and 'Solicita un nuevo código' in response.get_data(as_text=True)
    assert _snapshot(flow) == before


def test_after_an_administrator_authorizes_the_client_repeats_the_operation_with_otp_and_camera_and_it_completes_once(flow):
    flow['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    before = _snapshot(flow)
    _send_code(flow)
    _confirm(flow, flow['sent'][-1])
    with flow['app'].app_context():
        service.review_hold(HeldOperation.query.one(), db.session.get(User, flow['ids']['admin']), 'Verificado con el titular por teléfono', authorize=True)
        db.session.commit()
        challenge = OperationChallenge.query.filter_by(user_id=flow['ids']['user']).one()
        challenge.last_sent_at = now_utc() - timedelta(minutes=5)             # permite pedir otro código sin esperar el reenvío
        db.session.commit()
    assert _send_code(flow).status_code == 302 and len(flow['sent']) == 2
    response = _confirm(flow, flow['sent'][-1])
    assert response.status_code == 302 and '/receipt/' in response.headers['Location']
    after = _snapshot(flow)
    assert after['balance'] == before['balance'] - Decimal(AMOUNT) and after['transfers'] == before['transfers'] + 1 and after['captures'] == before['captures'] + 1
    with flow['app'].app_context():
        hold = HeldOperation.query.one()
        assert hold.status == c.HOLD_EXECUTED and hold.transaction_id is not None
        assert db.session.get(Transaction, hold.transaction_id).amount == -Decimal(AMOUNT)
        assert RiskEvaluation.query.filter(RiskEvaluation.applied_action == c.APPLIED_ADMIN_AUTHORIZED).count() == 1
    again = _confirm(flow, flow['sent'][-1])                               # reenviar el mismo formulario no debita dos veces
    assert _snapshot(flow)['balance'] == after['balance']
    assert again.status_code in (200, 302)


def test_shadow_mode_leaves_the_same_flow_unchanged_and_only_observes(flow):
    before = _snapshot(flow)
    _send_code(flow)
    response = _confirm(flow, flow['sent'][-1])
    assert response.status_code == 302 and '/receipt/' in response.headers['Location']
    after = _snapshot(flow)
    assert after['balance'] == before['balance'] - Decimal(AMOUNT) and after['transfers'] == before['transfers'] + 1
    with flow['app'].app_context():
        evaluation = RiskEvaluation.query.filter_by(level=c.LEVEL_SOSPECHOSO).one()
        assert evaluation.applied_action == c.APPLIED_NONE and evaluation.transaction_id is not None and HeldOperation.query.count() == 0
        assert FraudIncident.query.one().status == c.INCIDENT_OPEN                        # queda para revisión humana


def test_a_restricted_customer_cannot_operate_through_the_real_flow(flow):
    with flow['app'].app_context():
        db.session.add(SecurityRestriction(
            user_id=flow['ids']['user'], scope=c.SCOPE_OPERATIONS, origin=c.ORIGIN_SANCTION, reason='Prueba', expires_at=now_utc() + timedelta(days=1)))
        db.session.commit()
    before = _snapshot(flow)
    _send_code(flow)
    response = _confirm(flow, flow['sent'][-1])
    assert response.status_code == 200 and 'restringidas temporalmente' in response.get_data(as_text=True)
    assert _snapshot(flow) == before
