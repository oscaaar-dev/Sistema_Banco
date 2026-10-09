import re
import time
from decimal import Decimal

from app import create_app
from app.models import Account, Beneficiary, OperationChallenge, Transaction, Transfer, User


def _csrf(response):
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


def _transfer_context():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        user.email = 'client@example.test'
        account = Account.query.filter_by(user_id=user.id).one()
        beneficiary = Beneficiary.query.filter_by(user_id=user.id).first()
        user_id, account_id, beneficiary_id = user.id, account.id, beneficiary.id
        account_balance = account.balance
        from app.models import db
        db.session.commit()

    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    form = client.get('/operation/transfer')
    token = _csrf(form)
    key = 'transfer-otp-test-key-00000001'
    return app, client, token, key, user_id, account_id, beneficiary_id, account_balance


def _send_code(client, token, key, beneficiary_id, amount='12.34'):
    return client.post('/operation/transfer/send-code', data={
        'csrf_token': token,
        'idempotency_key': key,
        'beneficiary_id': str(beneficiary_id),
        'amount': amount,
    })


def _submit_transfer(client, token, key, beneficiary_id, code, amount='12.34'):
    with client.session_transaction() as browser_session:
        browser_session['camera_state'] = {
            'ready': True,
            'checked_at': time.time(),
            'face_confidence': 0.9,
        }
    return client.post('/operation/transfer', data={
        'csrf_token': token,
        'idempotency_key': key,
        'beneficiary_id': str(beneficiary_id),
        'amount': amount,
        'otp_code': code,
        'camera_verified': 'yes',
        'form_duration_seconds': '10',
    })


def test_transfer_requires_transaction_bound_email_otp_and_consumes_it_atomically(monkeypatch):
    import app.routes as routes_module

    sent = []
    monkeypatch.setattr(routes_module, 'send_operation_code', lambda email, code, details: sent.append((email, code, details)))
    app, client, token, key, user_id, account_id, beneficiary_id, balance = _transfer_context()

    response = _send_code(client, token, key, beneficiary_id)
    assert response.status_code == 302
    assert sent and sent[0][0] == 'client@example.test'
    assert '12.34' in sent[0][2]
    with app.app_context():
        challenge = OperationChallenge.query.filter_by(user_id=user_id).one()
        assert challenge.amount == Decimal('12.34')
        assert challenge.account_id == account_id
        assert challenge.beneficiary_id == beneficiary_id
        assert challenge.idempotency_key == key
        assert sent[0][1] not in challenge.code_hash

    response = _submit_transfer(client, token, key, beneficiary_id, sent[0][1])

    assert response.status_code == 302
    assert '/receipt/' in response.headers['Location']
    with app.app_context():
        account = Account.query.filter_by(id=account_id).one()
        challenge = OperationChallenge.query.filter_by(user_id=user_id).one()
        transfer = Transfer.query.one()
        assert account.balance == balance - Decimal('12.34')
        assert challenge.consumed_at is not None
        assert transfer.amount == Decimal('12.34')
        assert Transaction.query.filter_by(idempotency_key=key).count() == 1


def test_transfer_otp_is_bound_to_amount_and_beneficiary(monkeypatch):
    import app.routes as routes_module

    sent = []
    monkeypatch.setattr(routes_module, 'send_operation_code', lambda email, code, details: sent.append(code))
    app, client, token, key, user_id, _, beneficiary_id, balance = _transfer_context()
    _send_code(client, token, key, beneficiary_id, '12.34')

    response = _submit_transfer(client, token, key, beneficiary_id, sent[0], '20.00')

    assert response.status_code == 200
    assert 'Los datos cambiaron después de enviar el código'.encode() in response.data
    with app.app_context():
        assert Account.query.filter_by(user_id=user_id).one().balance == balance
        assert Transfer.query.count() == 0
        assert Transaction.query.filter_by(idempotency_key=key).count() == 0
        assert OperationChallenge.query.filter_by(user_id=user_id).one().consumed_at is not None


def test_transfer_otp_locks_after_five_incorrect_codes(monkeypatch):
    import app.routes as routes_module

    monkeypatch.setattr(routes_module, 'send_operation_code', lambda *args: None)
    app, client, token, key, user_id, _, beneficiary_id, balance = _transfer_context()
    _send_code(client, token, key, beneficiary_id)

    for _ in range(5):
        response = _submit_transfer(client, token, key, beneficiary_id, '000000')
        assert response.status_code == 200

    with app.app_context():
        challenge = OperationChallenge.query.filter_by(user_id=user_id).one()
        assert challenge.attempts == 5
        assert challenge.consumed_at is not None
        assert Account.query.filter_by(user_id=user_id).one().balance == balance
        assert Transfer.query.count() == 0
