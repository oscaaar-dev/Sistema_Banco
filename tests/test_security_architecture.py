"""Separación frontend / backend / base de datos y autorización en el servidor."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.models import Account, BankCard, Transaction, User, db

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIRS = (ROOT / 'app' / 'static', ROOT / 'app' / 'templates')
TEXT_SUFFIXES = {'.js', '.css', '.html', '.svg', '.json', '.txt', '.md'}


def _public_files():
    for directory in PUBLIC_DIRS:
        for path in directory.rglob('*'):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path


# ─── 23-25: el navegador no habla con la base de datos ───

FORBIDDEN_IN_PUBLIC_FILES = {
    'cadena de conexión SQL': re.compile(r'\b(?:mysql|postgres(?:ql)?|sqlite|mssql|mongodb)(?:\+\w+)?://', re.I),
    'driver de base de datos': re.compile(r'\b(?:pymysql|psycopg2?|mysql2|mysql-connector|sqlalchemy|knex|sequelize)\b', re.I),
    'consulta SQL en el cliente': re.compile(r'\b(?:SELECT\s+[\w*,\s`]+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b', re.I),
    'variable privada': re.compile(r'\b(?:DATABASE_URL|MYSQL_URL|SECRET_KEY|BREVO_API_KEY|SMTP_PASSWORD|password_hash)\b'),
}


@pytest.mark.parametrize('label,pattern', FORBIDDEN_IN_PUBLIC_FILES.items())
def test_public_files_contain_no_database_credentials_drivers_or_sql(label, pattern):
    offenders = []
    for path in _public_files():
        text = path.read_text(encoding='utf-8', errors='ignore')
        # El texto legal de las plantillas puede mencionar «credenciales protegidas mediante hash».
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f'{label} en archivos servidos al navegador: {offenders}'


def test_browser_scripts_only_call_the_norte_backend():
    calls = []
    for path in (ROOT / 'app' / 'static' / 'js').glob('*.js'):
        text = path.read_text(encoding='utf-8')
        for call in re.findall(r'(?:fetch|XMLHttpRequest|axios[\w.]*)\s*\(([^)]*)\)?', text):
            calls.append((path.name, call))
            assert not re.search(r'https?://', call), f'{path.name} llama a un host externo: {call}'
        assert 'WebSocket(' not in text and 'indexedDB' not in text
    assert calls, 'se esperaba al menos una llamada fetch hacia el backend'


def test_api_never_exposes_password_hashes_or_internal_columns():
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()
    _login_as(client, 1)
    for path in ('/api/v1/me', '/api/v1/accounts'):
        body = client.get(path).get_data(as_text=True)
        assert 'password' not in body.lower() and 'scrypt' not in body and 'pbkdf2' not in body


# ─── 26: la autorización se decide en el servidor ───

def _login_as(client, user_id):
    with client.session_transaction() as browser_session:
        browser_session.update({'_user_id': str(user_id), '_fresh': True, 'login_2fa_verified': True, 'pwd_stamp': '0'})


@pytest.fixture()
def world():
    """Cliente víctima (demo, con cuenta, tarjeta y movimientos), cliente atacante, soporte y administrador."""
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        attacker = User(username='atk', full_name='Cliente Atacante', email='atk@example.test', dni='55555555',
                        role='CLIENTE')
        attacker.set_password('Atacante9.')
        support = User(username='sup', full_name='Soporte Uno', email='sup@example.test', dni='55555556', role='SOPORTE')
        support.set_password('Soporte99.')
        db.session.add_all((attacker, support))
        db.session.flush()
        db.session.add(Account(user_id=attacker.id, number='900000000001', balance=Decimal('10.00')))
        db.session.commit()
        ids = {
            'victim': User.query.filter_by(username='demo').one().id,
            'attacker': attacker.id,
            'support': support.id,
            'admin': User.query.filter_by(username='admin').one().id,
        }
        victim_account = Account.query.filter_by(user_id=ids['victim']).first()
        ids['victim_account'] = victim_account.id
        ids['victim_card'] = BankCard.query.filter_by(user_id=ids['victim']).first().id
        ids['victim_tx'] = Transaction.query.filter_by(account_id=victim_account.id).first().id
    return app, ids


PRIVATE_GET = ['/dashboard', '/movements', '/cards', '/operation/transfer', '/internal-transfer', '/admin', '/support',
               '/api/v1/me', '/api/v1/accounts', '/operation-risk?kind=transfer&amount=10']


@pytest.mark.parametrize('path', PRIVATE_GET)
def test_private_routes_require_authentication(world, path):
    app, _ = world
    response = app.test_client().get(path)
    assert response.status_code in (302, 401)
    if response.status_code == 302:
        assert '/login' in response.headers['Location']


def test_roles_are_checked_on_the_server_not_by_hidden_buttons(world):
    app, ids = world
    customer, support, admin = app.test_client(), app.test_client(), app.test_client()
    _login_as(customer, ids['attacker']); _login_as(support, ids['support']); _login_as(admin, ids['admin'])
    assert customer.get('/admin').status_code == 403 and customer.get('/support').status_code == 403
    assert support.get('/admin').status_code == 403 and support.get('/dashboard').status_code == 403
    assert admin.get('/support').status_code == 403
    for forbidden_client in (support, admin):
        assert forbidden_client.get('/operation/transfer').status_code == 403
        assert forbidden_client.get('/movements').status_code == 403
        assert forbidden_client.get('/cards').status_code == 403
    assert admin.get('/admin').status_code == 200 and support.get('/support').status_code == 200


# ─── 27: un cliente no accede a datos de otro cambiando identificadores ───

def test_a_customer_cannot_read_or_change_another_customers_data(world):
    app, ids = world
    client = app.test_client()
    _login_as(client, ids['attacker'])
    assert client.get(f"/receipt/{ids['victim_tx']}").status_code == 404
    assert client.get(f"/statements/{ids['victim_account']}.pdf").status_code == 404
    assert client.get(f"/movements?account_id={ids['victim_account']}").status_code == 404
    assert client.get(f"/api/v1/accounts/{ids['victim_account']}/transactions").status_code == 404
    assert client.get(f"/api/v1/accounts/{ids['victim_account']}/transfers").status_code == 404
    token_page = client.get('/cards')
    card_change = client.post(f"/cards/{ids['victim_card']}/status",
                              data={'csrf_token': _csrf(token_page), 'status': 'Bloqueada'})
    assert card_change.status_code == 404
    with app.app_context():
        assert db.session.get(BankCard, ids['victim_card']).status != 'Bloqueada'


def test_the_victim_still_reaches_their_own_data(world):
    app, ids = world
    client = app.test_client()
    _login_as(client, ids['victim'])
    assert client.get(f"/receipt/{ids['victim_tx']}").status_code == 200
    assert client.get(f"/statements/{ids['victim_account']}.pdf").status_code == 200
    assert client.get(f"/api/v1/accounts/{ids['victim_account']}/transactions").status_code == 200
    assert client.get('/dashboard').status_code == 200


def test_post_without_csrf_token_is_rejected(world):
    app, ids = world
    client = app.test_client()
    _login_as(client, ids['victim'])
    assert client.post(f"/cards/{ids['victim_card']}/status", data={'status': 'Bloqueada'}).status_code == 400
    assert client.post('/logout').status_code == 400


def test_session_cookie_has_safe_attributes_and_errors_hide_internals(world):
    app, ids = world
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True and app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'
    client = app.test_client()
    response = client.get('/ruta-inexistente')
    body = response.get_data(as_text=True)
    assert response.status_code == 404 and 'Traceback' not in body and 'sqlalchemy' not in body.lower()
    assert response.headers['X-Frame-Options'] == 'DENY' and response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Access-Control-Allow-Origin' not in client.get('/api/v1/me').headers, 'sin CORS abierto'


def _csrf(response):
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
