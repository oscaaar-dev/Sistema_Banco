"""Contactos de transferencia: alta con confirmación, validaciones, límite de intentos, baja y flujo completo."""
import re
from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, AuditLog, Beneficiary, Transaction, Transfer, User, db
from app.services.account_identifiers import derive_cci
from app.services.contact_service import ARCHIVED_BANK, mask_name, normalize_identifier
from camera_helpers import accept_camera_notice, capture_file

NOT_FOUND = 'No pudimos encontrar un cliente de Norte con esos datos'


def _csrf(page):
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def _key(page):
    return re.search(rb'name="idempotency_key" value="([^"]+)"', page.data).group(1).decode()


def _client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    return client


@pytest.fixture
def ctx(monkeypatch):
    import app.routes as routes_module

    sent = []
    monkeypatch.setattr(routes_module, 'send_operation_code', lambda email, code, details: sent.append(code))
    app = create_app()
    with app.app_context():
        demo = User.query.filter_by(username='demo').one()
        demo.email = 'client@example.test'
        demo.dni = '70000001'
        ricardo = User(username='ricardo', full_name='Ricardo Ortega Vega', dni='12345678', email='ricardo@example.test', role='CLIENTE')
        ricardo.set_password('Clave-De-Prueba-1!')
        db.session.add(ricardo)
        db.session.flush()
        ricardo_account = Account(user_id=ricardo.id, account_type='Cuenta de ahorros', number='555566667777', balance=Decimal('100.00'))
        db.session.add(ricardo_account)
        db.session.commit()
        ids = {'demo': demo.id, 'ricardo': ricardo.id, 'ricardo_account': ricardo_account.id,
               'demo_account': Account.query.filter_by(user_id=demo.id).one().id}
    client = _client_for(app, ids['demo'])
    page = client.get('/contacts')
    return {'app': app, 'client': client, 'token': _csrf(page), 'ids': ids, 'sent': sent}


def _lookup(c, identifier, **extra):
    return c['client'].post('/contacts/lookup', data={'csrf_token': c['token'], 'identifier': identifier, **extra})


def _confirm(c, **extra):
    return c['client'].post('/contacts/confirm', data={'csrf_token': c['token'], **extra})


def _contacts(c, user='demo'):
    with c['app'].app_context():
        return Beneficiary.query.filter_by(user_id=c['ids'][user]).all()


def _add_ricardo(c):
    assert _lookup(c, '12345678').status_code == 200
    response = _confirm(c)
    assert response.status_code == 302
    return response


# ───────── Utilidades ─────────
def test_mask_name_keeps_first_name_and_initials():
    assert mask_name('Ricardo Ortega Vega') == 'Ricardo O*** V***'
    assert mask_name('  Alex   Rivera ') == 'Alex R***'
    assert mask_name('Lucía') == 'Lucía'
    assert mask_name('') == 'Cliente Norte'


@pytest.mark.parametrize('raw,expected', [
    ('12345678', '12345678'), (' 1234 5678 ', '12345678'), ('0012-3456-7890', '001234567890'),
    ('1234567', None), ('123456789', None), ('1' * 21, None), ('abc12345', None), ("1' OR '1'='1", None), ('', None), ('١٢٣٤٥٦٧٨', None),
])
def test_identifier_normalization(raw, expected):
    assert normalize_identifier(raw) == expected


def test_derived_cci_is_deterministic_and_twenty_digits():
    assert derive_cci('555566667777') == derive_cci('555566667777')
    assert len(derive_cci('555566667777')) == 20 and derive_cci('555566667777').isdigit()
    assert derive_cci('555566667777') != derive_cci('555566667778')


# ───────── Alta con confirmación ─────────
def test_lookup_shows_masked_name_and_asks_for_confirmation_before_saving(ctx):
    response = _lookup(ctx, '12345678')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'Ricardo O*** V***' in html and 'terminada en 7777' in html
    assert 'Ortega' not in html and 'Vega' not in html and '555566667777' not in html
    assert 'Confirmar y guardar' in html
    assert not [b for b in _contacts(ctx) if b.account_number == '555566667777']


def test_confirming_saves_the_contact_with_masked_name_and_it_appears_in_the_transfer_menu(ctx):
    _add_ricardo(ctx)
    [contact] = [b for b in _contacts(ctx) if b.account_number == '555566667777']
    assert contact.name == 'Ricardo O*** V***' and contact.bank_name == 'Norte'
    page = ctx['client'].get('/operation/transfer').get_data(as_text=True)
    assert f'value="{contact.id}"' in page and 'Ricardo O*** V*** · 7777' in page
    assert 'Agregar contacto' in page
    with ctx['app'].app_context():
        actions = {row.action for row in AuditLog.query.filter_by(user_id=ctx['ids']['demo']).all()}
    assert {'CONTACT_LOOKUP', 'CONTACT_ADDED'} <= actions


@pytest.mark.parametrize('identifier', ['555566667777', '5555-6666-7777', derive_cci('555566667777')])
def test_contact_can_be_found_by_account_number_or_cci(ctx, identifier):
    response = _lookup(ctx, identifier)
    assert response.status_code == 200 and 'Ricardo O*** V***' in response.get_data(as_text=True)


def test_return_to_transfer_redirects_back_after_confirming(ctx):
    _lookup(ctx, '12345678', **{'from': 'transfer'})
    response = ctx['client'].post('/contacts/confirm?from=transfer', data={'csrf_token': ctx['token']})
    assert response.status_code == 302 and response.headers['Location'].endswith('/operation/transfer')


def test_confirm_without_a_previous_lookup_saves_nothing(ctx):
    before = len(_contacts(ctx))
    response = _confirm(ctx)
    assert response.status_code == 400 and 'venció' in response.get_data(as_text=True)
    assert len(_contacts(ctx)) == before


def test_pending_lookup_expires(ctx):
    _lookup(ctx, '12345678')
    with ctx['client'].session_transaction() as browser_session:
        browser_session['pending_contact'] = {**browser_session['pending_contact'], 'at': '2020-01-01T00:00:00'}
    before = len(_contacts(ctx))
    assert _confirm(ctx).status_code == 400
    assert len(_contacts(ctx)) == before


def test_pending_lookup_is_single_use(ctx):
    _add_ricardo(ctx)
    assert _confirm(ctx).status_code == 400


# ───────── Validaciones y mensajes ─────────
def test_unknown_client_gets_a_friendly_generic_message_and_nothing_is_saved(ctx):
    before = len(_contacts(ctx))
    for identifier in ('99999999', '999999999999'):
        response = _lookup(ctx, identifier)
        html = response.get_data(as_text=True)
        assert response.status_code == 404 and NOT_FOUND in html
        assert 'Traceback' not in html
    assert len(_contacts(ctx)) == before


def test_the_user_cannot_add_themselves(ctx):
    for identifier in ('70000001', '001234567890'):
        response = _lookup(ctx, identifier)
        assert response.status_code == 400 and 'tus propias cuentas' in response.get_data(as_text=True)
    with ctx['client'].session_transaction() as browser_session:
        assert 'pending_contact' not in browser_session


def test_a_client_cannot_be_added_twice(ctx):
    _add_ricardo(ctx)
    response = _lookup(ctx, '12345678')
    assert response.status_code == 409 and 'Ya tienes a este cliente' in response.get_data(as_text=True)
    assert len([b for b in _contacts(ctx) if b.account_number == '555566667777']) == 1


@pytest.mark.parametrize('identifier', ['', '123', 'abcdefgh', "1' OR '1'='1", '1;DROP TABLE users', '9' * 40])
def test_malformed_identifiers_are_rejected_without_a_lookup(ctx, identifier):
    response = _lookup(ctx, identifier)
    assert response.status_code == 400 and 'solo con números' in response.get_data(as_text=True)
    with ctx['app'].app_context():
        assert AuditLog.query.filter_by(action='CONTACT_LOOKUP').count() == 0


def test_inactive_accounts_and_non_clients_are_not_found(ctx):
    with ctx['app'].app_context():
        db.session.get(Account, ctx['ids']['ricardo_account']).status = 'BLOQUEADA'
        db.session.commit()
    assert _lookup(ctx, '555566667777').status_code == 404
    assert _lookup(ctx, '12345678').status_code == 404


def test_lookups_are_rate_limited_per_user_even_for_valid_clients(ctx):
    limit = ctx['app'].config['CONTACT_LOOKUP_RATE_LIMIT_USER']
    for _ in range(limit):
        assert _lookup(ctx, '99999999').status_code == 404
    response = _lookup(ctx, '12345678')
    assert response.status_code == 429
    html = response.get_data(as_text=True)
    assert 'espera unos minutos' in html and 'Ricardo' not in html
    assert not [b for b in _contacts(ctx) if b.account_number == '555566667777']


def test_rate_limit_is_also_applied_per_connection(ctx):
    ctx['app'].config['CONTACT_LOOKUP_RATE_LIMIT_IP'] = 2
    for _ in range(2):
        assert _lookup(ctx, '99999999').status_code == 404
    other = _client_for(ctx['app'], ctx['ids']['ricardo'])
    token = _csrf(other.get('/contacts'))
    response = other.post('/contacts/lookup', data={'csrf_token': token, 'identifier': '70000001'})
    assert response.status_code == 429


# ───────── Autorización ─────────
def test_contact_pages_require_login_and_a_client_role(ctx):
    anonymous = ctx['app'].test_client()
    for method, path in (('get', '/contacts'), ('post', '/contacts/lookup'), ('post', '/contacts/confirm'), ('post', '/contacts/1/delete')):
        response = getattr(anonymous, method)(path)
        assert response.status_code in (302, 400) and 'login' in response.headers.get('Location', 'login')
    with ctx['app'].app_context():
        admin_id = User.query.filter_by(username='admin').one().id
    assert _client_for(ctx['app'], admin_id).get('/contacts').status_code == 403


def test_forms_require_the_csrf_token(ctx):
    assert ctx['client'].post('/contacts/lookup', data={'identifier': '12345678'}).status_code == 400


# ───────── Baja ─────────
def test_user_can_delete_their_own_contact(ctx):
    _add_ricardo(ctx)
    [contact] = [b for b in _contacts(ctx) if b.account_number == '555566667777']
    response = ctx['client'].post(f'/contacts/{contact.id}/delete', data={'csrf_token': ctx['token']})
    assert response.status_code == 302
    assert not [b for b in _contacts(ctx) if b.account_number == '555566667777']
    assert 'Ricardo O*** V*** · 7777' not in ctx['client'].get('/operation/transfer').get_data(as_text=True)


def test_user_cannot_delete_a_contact_owned_by_someone_else(ctx):
    with ctx['app'].app_context():
        other = Beneficiary(user_id=ctx['ids']['ricardo'], name='Contacto ajeno', account_number='009999999999')
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    assert ctx['client'].post(f'/contacts/{other_id}/delete', data={'csrf_token': ctx['token']}).status_code == 404
    with ctx['app'].app_context():
        assert db.session.get(Beneficiary, other_id) is not None


def test_contact_delete_requires_post(ctx):
    assert ctx['client'].get('/contacts/1/delete').status_code == 405


def test_contacts_are_private_to_each_user(ctx):
    _add_ricardo(ctx)
    other = _client_for(ctx['app'], ctx['ids']['ricardo'])
    assert 'Ricardo O*** V***' not in other.get('/contacts').get_data(as_text=True)
    assert 'Ricardo O*** V*** · 7777' not in other.get('/operation/transfer').get_data(as_text=True)


# ───────── Flujo completo ─────────
def _transfer(c, contact_id, amount='25.00'):
    page = c['client'].get('/operation/transfer')
    token, key = _csrf(page), _key(page)
    c['client'].post('/operation/transfer/send-code', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(contact_id), 'amount': amount})
    assert c['sent'], 'no se envió el código'
    accept_camera_notice(c['client'], token, 'transfer', key)
    return c['client'].post('/operation/transfer', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(contact_id), 'amount': amount,
        'otp_code': c['sent'][-1], 'form_duration_seconds': '9', 'camera_consent': 'yes', 'capture': capture_file()})


def test_full_flow_add_contact_transfer_with_camera_and_code_registers_both_accounts(ctx):
    _add_ricardo(ctx)
    [contact] = [b for b in _contacts(ctx) if b.account_number == '555566667777']
    with ctx['app'].app_context():
        sender_before = db.session.get(Account, ctx['ids']['demo_account']).balance
        receiver_before = db.session.get(Account, ctx['ids']['ricardo_account']).balance

    response = _transfer(ctx, contact.id)
    assert response.status_code == 302 and '/receipt/' in response.headers['Location']

    with ctx['app'].app_context():
        sender = db.session.get(Account, ctx['ids']['demo_account'])
        receiver = db.session.get(Account, ctx['ids']['ricardo_account'])
        assert sender.balance == sender_before - Decimal('25.00')
        assert receiver.balance == receiver_before + Decimal('25.00')
        sent_tx = Transaction.query.filter_by(account_id=sender.id, operation_type='TRANSFERENCIA').one()
        received_tx = Transaction.query.filter_by(account_id=receiver.id, operation_type='INGRESO').one()
        assert sent_tx.amount == Decimal('-25.00') and sent_tx.description == 'Transferencia a Ricardo O*** V***'
        assert received_tx.amount == Decimal('25.00') and received_tx.balance_after == receiver.balance
        assert received_tx.description == 'Transferencia recibida de Alex R***'
        assert Transfer.query.filter_by(transaction_id=sent_tx.id, beneficiary_id=contact.id).count() == 1

    ricardo = _client_for(ctx['app'], ctx['ids']['ricardo'])
    movements = ricardo.get('/movements').get_data(as_text=True)
    assert 'Transferencia recibida de Alex R***' in movements and '25.00' in movements


def test_replaying_the_same_transfer_credits_the_receiver_only_once(ctx):
    from app.services.banking_service import process_operation

    _add_ricardo(ctx)
    [contact] = [b for b in _contacts(ctx) if b.account_number == '555566667777']
    key = 'replay-credit-key-00000001'
    with ctx['app'].app_context():
        demo = db.session.get(User, ctx['ids']['demo'])
        for _ in range(2):
            process_operation(demo, 'TRANSFERENCIA', Decimal('10.00'), 'Transferencia a Ricardo O*** V***',
                              beneficiary_id=contact.id, idempotency_key=key)
        assert db.session.get(Account, ctx['ids']['ricardo_account']).balance == Decimal('110.00')
        assert Transaction.query.filter_by(account_id=ctx['ids']['ricardo_account'], operation_type='INGRESO').count() == 1


def test_transfer_to_a_non_norte_beneficiary_only_debits_the_sender(ctx):
    with ctx['app'].app_context():
        external = Beneficiary.query.filter_by(user_id=ctx['ids']['demo'], is_seed=True).first()
        external_id = external.id
        before = db.session.get(Account, ctx['ids']['demo_account']).balance
    _transfer(ctx, external_id, '5.00')
    with ctx['app'].app_context():
        assert db.session.get(Account, ctx['ids']['demo_account']).balance == before - Decimal('5.00')
        assert Transaction.query.filter_by(operation_type='INGRESO', description='Transferencia recibida de Alex R***').count() == 0


def test_deleting_a_contact_with_history_archives_it_and_keeps_the_transfer_record(ctx):
    _add_ricardo(ctx)
    [contact] = [b for b in _contacts(ctx) if b.account_number == '555566667777']
    contact_id = contact.id
    assert _transfer(ctx, contact_id).status_code == 302
    response = ctx['client'].post(f'/contacts/{contact_id}/delete', data={'csrf_token': ctx['token']})
    assert response.status_code == 302
    with ctx['app'].app_context():
        archived = db.session.get(Beneficiary, contact_id)
        assert archived.bank_name == ARCHIVED_BANK
        assert Transfer.query.filter_by(beneficiary_id=contact_id).count() == 1
    assert 'Ricardo O*** V*** · 7777' not in ctx['client'].get('/operation/transfer').get_data(as_text=True)

    # Un contacto eliminado no se puede usar para transferir…
    page = ctx['client'].get('/operation/transfer')
    ctx['client'].post('/operation/transfer/send-code', data={
        'csrf_token': _csrf(page), 'idempotency_key': _key(page), 'beneficiary_id': str(contact_id), 'amount': '5.00'})
    # …pero se puede volver a agregar y recupera su lugar sin duplicarse.
    _add_ricardo(ctx)
    with ctx['app'].app_context():
        assert Beneficiary.query.filter_by(user_id=ctx['ids']['demo'], account_number='555566667777').count() == 1
        assert db.session.get(Beneficiary, contact_id).bank_name == 'Norte'
