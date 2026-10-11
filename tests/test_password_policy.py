"""Política única de contraseñas: 8 a 10 caracteres con mayúscula, minúscula, número y punto."""
import re
import secrets

import pytest

from app import create_app
from app.models import User, db
from app.services.password_policy import RULES, failed_rules, validate_new_password

VALID = ['Abc1234.', 'Abc12345.', 'Norte2026.']          # 8, 9 y 10 caracteres
INVALID = {
    '7 caracteres': 'Abc123.',
    '11 caracteres': 'Norte20261.',
    'sin mayúscula': 'norte2026.',
    'sin minúscula': 'NORTE2026.',
    'sin número': 'NorteSur.A',
    'sin punto': 'Norte20261',
}


def _csrf(response):
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


def _client():
    app = create_app()
    app.config['TESTING'] = True
    return app, app.test_client()


def _register(client, password, confirmation=None, *, email=None, dni=None, extra=None):
    token = _csrf(client.get('/register'))
    data = {
        'csrf_token': token,
        'full_name': 'Cliente Prueba',
        'dni': dni or f'{10000000 + secrets.randbelow(90000000)}',
        'email': email or f'c{secrets.token_hex(4)}@example.test',
        'password': password,
        'password_confirmation': password if confirmation is None else confirmation,
    }
    data.update(extra or {})
    return client.post('/register', data=data)


# ─── unidades de la política ───

@pytest.mark.parametrize('password', VALID)
def test_valid_passwords_pass_every_rule(password):
    assert failed_rules(password) == []
    assert validate_new_password(password, password) == ([], [])


@pytest.mark.parametrize('label,password', INVALID.items())
def test_each_missing_requirement_is_rejected(label, password):
    errors, failed = validate_new_password(password, password)
    assert errors and failed, label


def test_policy_reports_which_rule_failed():
    assert failed_rules('Abc123.') == ['length']
    assert failed_rules('norte2026.') == ['upper']
    assert failed_rules('NorteSur.A') == ['digit']
    assert failed_rules('Norte20261') == ['dot']


def test_policy_does_not_require_other_symbols_and_does_not_trim():
    assert failed_rules('Norte26. ') == []            # el espacio final cuenta como carácter
    assert failed_rules('Ab1.@#$%!') == []            # permitidos, no exigidos
    assert validate_new_password('Norte2026.', 'Norte2026')[0], 'una confirmación distinta se rechaza'
    assert validate_new_password('Abc12.\x00xy', 'Abc12.\x00xy')[0], 'caracteres de control rechazados'


def test_published_rules_match_policy_labels():
    assert [rule['key'] for rule in RULES] == ['length', 'upper', 'lower', 'digit', 'dot']


# ─── registro: el backend aplica la política aunque no haya JavaScript ───

@pytest.mark.parametrize('password', VALID)
def test_registration_accepts_valid_lengths_and_hashes_the_password(password):
    app, client = _client()
    email = f'ok{secrets.token_hex(4)}@example.test'
    response = _register(client, password, email=email)
    assert response.status_code == 302 and response.headers['Location'].endswith('/login')
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.password_hash != password and password not in user.password_hash
        assert user.password_hash.split('$')[0].split(':')[0] in ('scrypt', 'pbkdf2')
        assert user.check_password(password)
        assert user.role == 'CLIENTE'


@pytest.mark.parametrize('label,password', INVALID.items())
def test_registration_rejects_each_invalid_password_server_side(label, password):
    app, client = _client()
    email = f'bad{secrets.token_hex(4)}@example.test'
    response = _register(client, password, email=email)
    assert response.status_code == 400, label
    assert 'La contraseña debe tener entre 8 y 10 caracteres'.encode() in response.data
    with app.app_context():
        assert User.query.filter_by(email=email).first() is None


def test_registration_rejects_different_confirmation():
    app, client = _client()
    email = f'mm{secrets.token_hex(4)}@example.test'
    response = _register(client, 'Norte2026.', 'Norte2027.', email=email)
    assert response.status_code == 400
    assert 'Las contraseñas no coinciden'.encode() in response.data
    with app.app_context():
        assert User.query.filter_by(email=email).first() is None


def test_registration_keeps_whitespace_exactly_as_typed():
    app, client = _client()
    email = f'ws{secrets.token_hex(4)}@example.test'
    assert _register(client, 'Norte26. ', email=email).status_code == 302
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.check_password('Norte26. ') and not user.check_password('Norte26.')


def test_registration_ignores_a_role_sent_by_the_browser():
    app, client = _client()
    email = f'role{secrets.token_hex(4)}@example.test'
    _register(client, 'Norte2026.', email=email, extra={'role': 'ADMINISTRADOR', 'is_seed': '1'})
    with app.app_context():
        assert User.query.filter_by(email=email).one().role == 'CLIENTE'


def test_register_form_shows_requirements_without_client_side_truncation():
    _, client = _client()
    html = client.get('/register').get_data(as_text=True)
    for label in ('Entre 8 y 10 caracteres', 'Al menos una mayúscula', 'Al menos una minúscula',
                  'Al menos un número', 'Al menos un punto'):
        assert label in html
    assert 'data-pattern="[A-Z]"' in html and 'data-pattern="\\."' in html
    assert 'data-password-toggle="registerPassword"' in html and 'name="password_confirmation"' in html
    for tag in re.findall(r'<input[^>]*type="password"[^>]*>', html):
        assert 'maxlength' not in tag, 'maxlength recortaría en silencio lo que pega la persona'


# ─── compatibilidad: las cuentas existentes no se tocan ───

def test_existing_accounts_with_older_passwords_can_still_start_login(monkeypatch):
    import app.routes as routes_module

    app, client = _client()
    legacy_password = 'ContraseñaAntigua!2023'      # 21 caracteres, sin punto: incumple la política nueva
    with app.app_context():
        user = User(username='legacy1', full_name='Cliente Antiguo', email='legacy@example.test',
                    dni='12341234', role='CLIENTE')
        user.set_password(legacy_password)
        db.session.add(user)
        db.session.commit()
    sent = []
    monkeypatch.setattr(routes_module, 'send_login_code', lambda recipient, code: sent.append(recipient))
    response = client.post('/login', data={'csrf_token': _csrf(client.get('/login')),
                                           'username': 'legacy@example.test', 'password': legacy_password})
    assert response.status_code == 302 and response.headers['Location'].endswith('/login/verify')
    assert sent == ['legacy@example.test']
    with app.app_context():
        assert User.query.filter_by(username='legacy1').one().password_changed_at is None
