"""Panel del cliente: resumen del mes, depósito de demostración, apertura de cuentas y tarjeta de débito.

Todo con CSRF real y bases en memoria. Cada prueba crea sus propios clientes, de modo que no depende de los datos de muestra.
"""
import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, AuditLog, BankCard, Transaction, User, db, now_utc
from app.routes import MAX_ACCOUNTS, MAX_DEPOSIT, MAX_DEPOSIT_PER_DAY
from app.services.account_identifiers import cci_for


def _client_for(app, user_id):
    client = app.test_client()
    with app.app_context():
        stamp = db.session.get(User, user_id).session_stamp()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
        browser_session['pwd_stamp'] = stamp
    return client


def _make_client(username, dni, number, balance='0.00', role='CLIENTE'):
    user = User(username=username, full_name=f'Cliente {username}', dni=dni, email=f'{username}@example.test', role=role)
    user.set_password('Clave-De-Prueba-1!')
    db.session.add(user)
    db.session.flush()
    account = Account(user_id=user.id, account_type='Cuenta de ahorros', number=number, balance=Decimal(balance))
    db.session.add(account)
    db.session.commit()
    return user.id, account.id


@pytest.fixture
def world():
    app = create_app()
    with app.app_context():
        mine_user, mine_account = _make_client('panelone', '41000001', '410000000001')
        other_user, other_account = _make_client('paneltwo', '41000002', '410000000002', balance='50.00')
        admin_user, _ = _make_client('paneladmin', '41000003', '410000000003', role='ADMINISTRADOR')
    return {'app': app, 'user': mine_user, 'account': mine_account, 'other_account': other_account,
            'client': _client_for(app, mine_user), 'other': _client_for(app, other_user), 'admin': _client_for(app, admin_user)}


def _csrf(client):
    page = client.get('/dashboard')
    assert page.status_code == 200
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def _deposit_key(client, account_id=None):
    html = client.get('/dashboard').get_data(as_text=True)
    return re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)


def _post(world, path, **data):
    client = world.get('who', world['client'])
    return client.post(path, data={'csrf_token': _csrf(client), **data})


def _balance(world, account_id=None):
    with world['app'].app_context():
        return db.session.get(Account, account_id or world['account']).balance


def _transactions(world, account_id=None):
    with world['app'].app_context():
        return Transaction.query.filter_by(account_id=account_id or world['account']).order_by(Transaction.id).all()


def _count(world, model, **filters):
    with world['app'].app_context():
        return model.query.filter_by(**filters).count()


# ───────── Resumen del mes ─────────

def test_month_summary_counts_real_income_and_expenses_and_ignores_own_transfers_and_other_months(world):
    with world['app'].app_context():
        now = now_utc()
        rows = [(Decimal('100.00'), 'Sueldo de prueba', now), (Decimal('-30.00'), 'Pago: luz', now),
                (Decimal('-500.00'), 'Traslado a cuenta propia terminada en 0002', now),
                (Decimal('500.00'), 'Traslado desde cuenta propia terminada en 0001', now),
                (Decimal('999.00'), 'Ingreso de hace varios meses', now - timedelta(days=75))]
        for amount, description, when in rows:
            db.session.add(Transaction(account_id=world['account'], operation_type='INGRESO', description=description, amount=amount,
                                       balance_after=Decimal('0.00'), created_at=when))
        db.session.commit()
    html = world['client'].get('/dashboard').get_data(as_text=True)
    summary = re.search(r'class="dash-summary".*?</section>', html, re.S).group(0)
    assert '+ S/ 100.00' in summary and 'Egresos del mes</span><b>S/ 30.00' in summary
    assert '999' not in summary and '500.00' not in summary
    assert 'No incluye traslados entre tus cuentas' in summary


def test_dashboard_shows_total_balance_links_and_no_saved_recipients_panel(world):
    with world['app'].app_context():
        db.session.get(Account, world['account']).balance = Decimal('1234.56')
        db.session.commit()
    html = world['client'].get('/dashboard').get_data(as_text=True)
    assert 'S/ 1,234.56' in html and f'/statements/{world["account"]}.pdf' in html and 'Depositar (demostración)' in html
    assert 'beneficiar' not in html.lower() and 'Destinatarios guardados' not in html     # los contactos viven en /contacts


# ───────── Depósito de demostración ─────────

def test_deposit_updates_the_balance_records_the_movement_and_opens_the_receipt(world):
    key = _deposit_key(world['client'])
    response = _post(world, f'/accounts/{world["account"]}/deposit', amount='100.50', idempotency_key=key)
    assert response.status_code == 302 and '/receipt/' in response.headers['Location']
    assert _balance(world) == Decimal('100.50')
    (tx,) = _transactions(world)
    assert (tx.operation_type, tx.amount, tx.balance_after, tx.status, tx.description) == \
        ('INGRESO', Decimal('100.50'), Decimal('100.50'), 'COMPLETADA', 'Depósito de demostración')
    assert world['client'].get(response.headers['Location']).status_code == 200
    assert _count(world, AuditLog, action='DEPOSIT_DEMO', user_id=world['user']) == 1


def test_a_repeated_submission_with_the_same_key_does_not_deposit_twice(world):
    key = _deposit_key(world['client'])
    first = _post(world, f'/accounts/{world["account"]}/deposit', amount='80.00', idempotency_key=key)
    second = _post(world, f'/accounts/{world["account"]}/deposit', amount='80.00', idempotency_key=key)
    assert first.headers['Location'] == second.headers['Location']
    assert _balance(world) == Decimal('80.00') and len(_transactions(world)) == 1


@pytest.mark.parametrize('amount', ['', '0', '0.00', '-5', 'abc', '1e3', '5,000.00', '5000.01', '10000', '12.345', ' ', 'NaN', 'Infinity'])
def test_invalid_or_out_of_range_amounts_are_rejected_without_touching_the_balance(world, amount):
    key = _deposit_key(world['client'])
    response = _post(world, f'/accounts/{world["account"]}/deposit', amount=amount, idempotency_key=key)
    assert response.status_code == 302 and response.headers['Location'].endswith('/dashboard#accounts')
    assert _balance(world) == Decimal('0.00') and _transactions(world) == []


def test_the_maximum_single_deposit_is_accepted_and_the_message_names_the_real_limit(world):
    assert _post(world, f'/accounts/{world["account"]}/deposit', amount=str(MAX_DEPOSIT), idempotency_key='m' * 24).status_code == 302
    assert _balance(world) == MAX_DEPOSIT
    _post(world, f'/accounts/{world["account"]}/deposit', amount='5000.01', idempotency_key='n' * 24)
    html = world['client'].get('/dashboard').get_data(as_text=True)
    assert 'entre S/ 0.01 y S/ 5,000.00' in html and '10,000' not in html


def test_the_daily_limit_per_account_blocks_more_deposits_and_frees_up_after_24_hours(world):
    for index in range(3):
        _post(world, f'/accounts/{world["account"]}/deposit', amount='5000.00', idempotency_key=f'{index}' * 24)
    assert _balance(world) == MAX_DEPOSIT_PER_DAY == Decimal('15000')
    blocked = _post(world, f'/accounts/{world["account"]}/deposit', amount='0.01', idempotency_key='z' * 24)
    assert blocked.headers['Location'].endswith('/dashboard#accounts') and _balance(world) == Decimal('15000.00')
    assert 'límite de depósitos de demostración' in world['client'].get('/dashboard').get_data(as_text=True)
    with world['app'].app_context():
        for tx in Transaction.query.filter_by(account_id=world['account']).all():
            tx.created_at = now_utc() - timedelta(hours=25)
        db.session.commit()
    assert _post(world, f'/accounts/{world["account"]}/deposit', amount='10.00', idempotency_key='y' * 24).status_code == 302
    assert _balance(world) == Decimal('15010.00')


@pytest.mark.parametrize('key', ['', 'corta', 'con espacios y símbolos !!!!!!!', 'x' * 65])
def test_a_missing_or_malformed_idempotency_key_is_rejected(world, key):
    _post(world, f'/accounts/{world["account"]}/deposit', amount='10.00', idempotency_key=key)
    assert _balance(world) == Decimal('0.00') and _transactions(world) == []


def test_nobody_can_deposit_into_someone_elses_or_a_closed_account(world):
    key = 'k' * 24
    assert _post(world, f'/accounts/{world["other_account"]}/deposit', amount='10.00', idempotency_key=key).status_code == 404
    assert _post(world, '/accounts/987654/deposit', amount='10.00', idempotency_key=key).status_code == 404
    assert _balance(world, world['other_account']) == Decimal('50.00') and _transactions(world, world['other_account']) == []
    with world['app'].app_context():
        db.session.get(Account, world['account']).status = 'CERRADA'
        db.session.commit()
    assert _post(world, f'/accounts/{world["account"]}/deposit', amount='10.00', idempotency_key=key).status_code == 404


def test_the_receipt_of_a_deposit_is_private_to_its_owner(world):
    response = _post(world, f'/accounts/{world["account"]}/deposit', amount='25.00', idempotency_key='r' * 24)
    location = response.headers['Location']
    assert world['client'].get(location).status_code == 200
    assert world['other'].get(location).status_code in (403, 404)


def test_deposits_appear_in_movements_filtered_by_income_and_in_the_pdf_statement(world):
    _post(world, f'/accounts/{world["account"]}/deposit', amount='42.00', idempotency_key='p' * 24)
    page = world['client'].get(f'/movements?account_id={world["account"]}&type=INGRESO')
    assert page.status_code == 200 and 'Depósito de demostración' in page.get_data(as_text=True)
    pdf = world['client'].get(f'/statements/{world["account"]}.pdf')
    assert pdf.status_code == 200 and pdf.data.startswith(b'%PDF')


# ───────── Abrir otra cuenta ─────────

def test_opening_accounts_creates_unique_12_digit_numbers_with_a_derivable_cci_up_to_the_limit(world):
    for _ in range(MAX_ACCOUNTS + 2):
        assert _post(world, '/accounts/open').status_code == 302
    with world['app'].app_context():
        accounts = Account.query.filter_by(user_id=world['user']).order_by(Account.id).all()
        assert len(accounts) == MAX_ACCOUNTS == 3
        numbers = [item.number for item in accounts]
        assert len(set(numbers)) == 3 and all(re.fullmatch(r'\d{12}', number) for number in numbers)
        assert all(item.balance == Decimal('0.00') and item.status == 'ACTIVA' for item in accounts[1:])
        assert all(re.fullmatch(r'\d{20}', cci_for(item)) for item in accounts)
        assert AuditLog.query.filter_by(action='ACCOUNT_OPENED', user_id=world['user']).count() == 2
    html = world['client'].get('/dashboard').get_data(as_text=True)
    assert 'Abrir otra cuenta de ahorros' not in html and 'hasta 3 cuentas activas' in html


def test_the_open_account_button_appears_below_the_limit_and_does_not_touch_other_customers(world):
    assert 'Abrir otra cuenta de ahorros' in world['client'].get('/dashboard').get_data(as_text=True)
    _post(world, '/accounts/open')
    assert _count(world, Account, user_id=world['user']) == 2
    with world['app'].app_context():
        assert Account.query.filter(Account.user_id != world['user'], Account.number.like('1%')).count() == 0


# ───────── Tarjeta de débito ─────────

def test_a_card_can_be_requested_once_per_own_account(world):
    assert 'Solicitar tarjeta de débito' in world['client'].get('/dashboard').get_data(as_text=True)
    first = _post(world, '/cards/request', account_id=world['account'])
    assert first.headers['Location'].endswith('/dashboard#tarjetas')
    _post(world, '/cards/request', account_id=world['account'])
    with world['app'].app_context():
        (card,) = BankCard.query.filter_by(account_id=world['account']).all()
        assert (card.user_id, card.status, card.card_type, card.limit_amount) == (world['user'], 'Activa', 'Débito', Decimal('3000'))
        assert re.fullmatch(r'\d{4}', card.last4)
        assert AuditLog.query.filter_by(action='CARD_REQUESTED', user_id=world['user']).count() == 1
    assert 'Solicitar tarjeta de débito' not in world['client'].get('/dashboard').get_data(as_text=True)


@pytest.mark.parametrize('account_id', ['', 'abc', '987654', None])
def test_requesting_a_card_for_an_invalid_account_creates_nothing(world, account_id):
    data = {} if account_id is None else {'account_id': account_id}
    assert _post(world, '/cards/request', **data).status_code == 302
    assert _count(world, BankCard, user_id=world['user']) == 0


def test_nobody_can_request_a_card_for_someone_elses_account(world):
    _post(world, '/cards/request', account_id=world['other_account'])
    assert _count(world, BankCard, account_id=world['other_account']) == 0 and _count(world, BankCard, user_id=world['user']) == 0


def test_locking_a_card_from_the_dashboard_returns_to_the_dashboard_and_the_cards_page_keeps_working(world):
    _post(world, '/cards/request', account_id=world['account'])
    with world['app'].app_context():
        card_id = BankCard.query.filter_by(account_id=world['account']).one().id
    response = _post(world, f'/cards/{card_id}/status', status='Bloqueada', next='dashboard')
    assert response.headers['Location'].endswith('/dashboard#tarjetas')
    with world['app'].app_context():
        assert db.session.get(BankCard, card_id).status == 'Bloqueada'
    html = world['client'].get('/dashboard').get_data(as_text=True)
    assert 'Desbloquear tarjeta' in html and 'is-locked' in html
    assert _post(world, f'/cards/{card_id}/status', status='Activa').headers['Location'].endswith('/cards')


def test_an_unknown_next_value_never_redirects_outside_the_site(world):
    _post(world, '/cards/request', account_id=world['account'])
    with world['app'].app_context():
        card_id = BankCard.query.filter_by(account_id=world['account']).one().id
    response = _post(world, f'/cards/{card_id}/status', status='Bloqueada', next='https://evil.example/')
    assert response.headers['Location'].endswith('/cards') and 'evil' not in response.headers['Location']


def test_nobody_can_lock_someone_elses_card(world):
    with world['app'].app_context():
        other_user = db.session.get(Account, world['other_account']).user_id
        card = BankCard(user_id=other_user, account_id=world['other_account'], last4='4321')
        db.session.add(card)
        db.session.commit()
        card_id = card.id
    assert _post(world, f'/cards/{card_id}/status', status='Bloqueada', next='dashboard').status_code == 404
    with world['app'].app_context():
        assert db.session.get(BankCard, card_id).status == 'Activa'


# ───────── Seguridad común ─────────

@pytest.mark.parametrize('method,path,data', [
    ('post', '/accounts/open', {}),
    ('post', '/accounts/{account}/deposit', {'amount': '10', 'idempotency_key': 'a' * 24}),
    ('post', '/cards/request', {'account_id': '1'}),
])
def test_these_actions_need_a_valid_csrf_token_and_a_session(world, method, path, data):
    path = path.format(account=world['account'])
    assert getattr(world['client'], method)(path, data=data).status_code == 400                    # sin token
    assert getattr(world['client'], method)(path, data={'csrf_token': 'falso', **data}).status_code == 400
    anonymous = world['app'].test_client()
    response = getattr(anonymous, method)(path, data=data)
    assert response.status_code in (302, 400) and (response.status_code == 400 or '/login' in response.headers['Location'])
    assert _balance(world) == Decimal('0.00') and _count(world, Account, user_id=world['user']) == 1


@pytest.mark.parametrize('path,data', [
    ('/accounts/open', {}), ('/accounts/{account}/deposit', {'amount': '10', 'idempotency_key': 'a' * 24}), ('/cards/request', {'account_id': '1'}),
])
def test_only_customers_can_use_these_actions(world, path, data):
    admin = world['admin']
    token = re.search(rb'name="csrf_token" value="([^"]+)"', admin.get('/admin').data)
    token = token.group(1).decode() if token else ''
    response = admin.post(path.format(account=world['account']), data={'csrf_token': token, **data})
    assert response.status_code in (400, 403)
    assert _balance(world) == Decimal('0.00')
