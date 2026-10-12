"""Validación del monto antes de convertirlo a Decimal: mensajes amables, nunca el texto técnico de Python."""
import re
from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, Beneficiary, OperationChallenge, User, db
from app.services.validation import parse_amount, parse_duration_seconds

TECHNICAL_FRAGMENTS = ('decimal.', 'ConversionSyntax', 'InvalidOperation', '<class', 'Traceback')


@pytest.mark.parametrize('raw, expected', [
    ('10', Decimal('10.00')),
    (' 25.5 ', Decimal('25.50')),
    ('25,50', Decimal('25.50')),
    ('0.01', Decimal('0.01')),
    ('10000', Decimal('10000.00')),
    ('1 000', Decimal('1000.00')),
])
def test_parse_amount_accepts_plain_numbers_and_normalizes_the_comma(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize('raw', [None, '', '   ', 'abc', '10abc', '-5', '+5', '1e3', '1.234,56', '1,000.50', '10.555', '.', ',', '0x10', 'NaN', 'Infinity'])
def test_parse_amount_rejects_empty_or_invalid_text_with_a_friendly_message(raw):
    with pytest.raises(ValueError) as error:
        parse_amount(raw)
    assert 'monto' in str(error.value).lower()
    assert not any(fragment in str(error.value) for fragment in TECHNICAL_FRAGMENTS)


@pytest.mark.parametrize('raw', ['0', '0.00', '10000.01', '999999'])
def test_parse_amount_enforces_the_operation_limits(raw):
    with pytest.raises(ValueError, match='entre S/ 0.01 y S/ 10,000.00'):
        parse_amount(raw)


def test_parse_duration_rejects_garbage_without_leaking_python_errors():
    assert parse_duration_seconds('12') == 12
    assert parse_duration_seconds('') == 0
    for raw in ('abc', '-1', '86401', '1.5'):
        with pytest.raises(ValueError) as error:
            parse_duration_seconds(raw)
        assert 'invalid literal' not in str(error.value)


def _client(monkeypatch):
    import app.routes as routes_module

    sent = []
    monkeypatch.setattr(routes_module, 'send_operation_code', lambda email, code, details: sent.append((email, code, details)))
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        user.email = 'client@example.test'
        beneficiary = Beneficiary.query.filter_by(user_id=user.id).first()
        user_id, beneficiary_id = user.id, beneficiary.id
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    page = client.get('/operation/transfer')
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    key = re.search(rb'name="idempotency_key" value="([^"]+)"', page.data).group(1).decode()
    return app, client, token, key, user_id, beneficiary_id, sent


def _text(response):
    return response.get_data(as_text=True)


def _assert_friendly(html, message):
    assert message in html
    assert not any(fragment in html for fragment in TECHNICAL_FRAGMENTS)


def test_send_code_with_empty_amount_shows_a_friendly_message(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    response = client.post('/operation/transfer/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(beneficiary_id), 'amount': '',
    }, follow_redirects=True)
    _assert_friendly(_text(response), 'Completa el destinatario y el monto antes de enviar el código.')
    assert sent == []


def test_send_code_without_recipient_asks_to_complete_the_form(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    response = client.post('/operation/transfer/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': '', 'amount': '10',
    }, follow_redirects=True)
    _assert_friendly(_text(response), 'Completa el destinatario y el monto antes de enviar el código.')
    assert sent == []


@pytest.mark.parametrize('amount', ['abc', '1,000.50', '10.555', '-3'])
def test_send_code_with_invalid_amount_shows_a_friendly_message(monkeypatch, amount):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    response = client.post('/operation/transfer/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(beneficiary_id), 'amount': amount,
    }, follow_redirects=True)
    _assert_friendly(_text(response), 'Ingresa un monto válido')
    assert sent == []


def test_send_code_with_a_valid_amount_and_recipient_sends_the_code(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    response = client.post('/operation/transfer/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(beneficiary_id), 'amount': '10',
    }, follow_redirects=True)
    assert response.status_code == 200
    assert len(sent) == 1 and sent[0][0] == 'client@example.test'
    assert 'S/ 10.00' in sent[0][2]
    with app.app_context():
        assert OperationChallenge.query.filter_by(user_id=user_id).one().amount == Decimal('10.00')


def test_send_code_accepts_a_decimal_comma(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    client.post('/operation/transfer/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(beneficiary_id), 'amount': '12,5',
    })
    assert len(sent) == 1 and 'S/ 12.50' in sent[0][2]


@pytest.mark.parametrize('kind, extra, label', [
    ('payment', {'service': ''}, 'el servicio'),
    ('recharge', {'phone': ''}, 'el número de celular'),
])
def test_other_operations_ask_for_their_missing_fields(monkeypatch, kind, extra, label):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    response = client.post(f'/operation/{kind}/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'amount': '', **extra,
    }, follow_redirects=True)
    _assert_friendly(_text(response), f'Completa {label} y el monto antes de enviar el código.')
    assert sent == []


@pytest.mark.parametrize('kind, extra', [
    ('transfer', {}), ('payment', {'service': 'Agua'}), ('recharge', {'phone': '987654321'}),
])
@pytest.mark.parametrize('amount', ['', 'abc', '   '])
def test_confirming_with_a_blank_or_invalid_amount_never_leaks_the_decimal_error(monkeypatch, kind, extra, amount):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    data = {'csrf_token': token, 'idempotency_key': key, 'amount': amount, 'otp_code': '123456',
            'form_duration_seconds': '5', **extra}
    if kind == 'transfer':
        data['beneficiary_id'] = str(beneficiary_id)
    response = client.post(f'/operation/{kind}', data=data)
    html = _text(response)
    assert response.status_code == 200
    assert 'Ingresa un monto válido' in html
    assert not any(fragment in html for fragment in TECHNICAL_FRAGMENTS)


def test_confirming_with_a_garbled_form_duration_does_not_leak_python_errors(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    response = client.post('/operation/transfer', data={
        'csrf_token': token, 'idempotency_key': key, 'amount': '10', 'beneficiary_id': str(beneficiary_id),
        'otp_code': '123456', 'form_duration_seconds': 'abc',
    })
    html = _text(response)
    assert 'invalid literal' not in html
    assert 'No se pudo validar la duración del formulario.' in html


def test_internal_transfer_rejects_invalid_amounts_politely(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    with app.app_context():
        first = Account.query.filter_by(user_id=user_id).first()
        second = Account(user_id=user_id, account_type='Corriente', number='900000000000000077', balance=Decimal('50.00'))
        db.session.add(second)
        db.session.commit()
        source_id, destination_id = first.id, second.id
    page = client.get('/internal-transfer')
    internal_token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    internal_key = re.search(rb'name="idempotency_key" value="([^"]+)"', page.data).group(1).decode()
    for amount, message in (('', 'Completa las cuentas y el monto antes de continuar.'), ('abc', 'Ingresa un monto válido')):
        response = client.post('/internal-transfer', data={
            'csrf_token': internal_token, 'idempotency_key': internal_key, 'source_account_id': str(source_id),
            'destination_account_id': str(destination_id), 'amount': amount, 'action': 'send_code',
        })
        html = _text(response)
        assert message in html
        assert not any(fragment in html for fragment in TECHNICAL_FRAGMENTS)


def test_risk_endpoint_answers_invalid_amounts_with_a_json_message(monkeypatch):
    app, client, token, key, user_id, beneficiary_id, sent = _client(monkeypatch)
    for amount in ('', 'abc', '0'):
        response = client.get('/operation-risk', query_string={'kind': 'transfer', 'amount': amount})
        assert response.status_code == 400
        assert 'monto' in response.get_json()['error']
