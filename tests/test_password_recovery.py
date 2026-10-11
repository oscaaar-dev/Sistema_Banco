"""Recuperación de contraseña exclusiva para clientes."""
import logging
import re
import threading
from datetime import timedelta

import pytest

from app import create_app
from app.models import AuditLog, PasswordResetToken, User, db, now_utc
from app.routes import recovery
from app.services import password_reset_service as resets

GOOD = 'Nueva2026.'
OLD = 'Antigua9.A'


def _csrf(response):
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


@pytest.fixture()
def env(monkeypatch):
    """Aplicación de prueba con tres cuentas y el envío de correo interceptado."""
    app = create_app()
    app.config['TESTING'] = True
    sent = []
    monkeypatch.setattr(recovery, 'send_password_reset_link',
                        lambda recipient, link, minutes: sent.append({'to': recipient, 'link': link, 'min': minutes}))
    with app.app_context():
        for username, email, dni, role in (('cli', 'cliente@example.test', '40000001', 'CLIENTE'),
                                           ('adm', 'admin2@example.test', '40000002', 'ADMINISTRADOR'),
                                           ('sup', 'soporte@example.test', '40000003', 'SOPORTE')):
            user = User(username=username, full_name='Persona Prueba', email=email, dni=dni, role=role)
            user.set_password(OLD)
            db.session.add(user)
        db.session.commit()
    return app, app.test_client(), sent


def _request(client, email, **kwargs):
    token = _csrf(client.get('/recuperar'))
    return client.post('/recuperar', data={'csrf_token': token, 'email': email}, **kwargs)


def _token_from(sent):
    return sent[-1]['link'].rsplit('/', 1)[1]


def _set_password(client, token, password=GOOD, confirmation=None):
    page = client.get(f'/recuperar/{token}')
    return client.post(f'/recuperar/{token}', data={
        'csrf_token': _csrf(page), 'password': password,
        'password_confirmation': password if confirmation is None else confirmation})


# ─── 14-17: solicitud ───

def test_customer_can_request_recovery_and_only_the_hash_is_stored(env):
    app, client, sent = env
    response = _request(client, 'Cliente@Example.test')
    assert response.status_code == 200
    assert len(sent) == 1 and sent[0]['to'] == 'cliente@example.test' and sent[0]['min'] == 30
    token = _token_from(sent)
    assert sent[0]['link'].startswith('http://localhost/recuperar/')
    assert token.encode() not in response.data, 'el token no se muestra en la interfaz'
    with app.app_context():
        record = PasswordResetToken.query.one()
        assert record.token_hash != token and token not in record.token_hash
        assert record.used_at is None and record.expires_at > now_utc()


def test_unknown_account_gets_the_same_public_response(env):
    _, client, sent = env
    known = _request(client, 'cliente@example.test')
    unknown = _request(client, 'nadie@example.test')
    assert known.status_code == unknown.status_code == 200
    strip = lambda page: re.sub(rb'name="csrf_token" value="[^"]+"', b'', page.data)
    assert strip(known) == strip(unknown)
    assert len(sent) == 1


@pytest.mark.parametrize('email', ['admin2@example.test', 'soporte@example.test'])
def test_admin_and_support_cannot_use_the_customer_flow(env, email):
    app, client, sent = env
    unknown = _request(client, 'nadie@example.test')
    response = _request(client, email)
    assert response.status_code == 200 and response.get_data() and sent == []
    assert re.sub(rb'name="csrf_token" value="[^"]+"', b'', response.data) == \
        re.sub(rb'name="csrf_token" value="[^"]+"', b'', unknown.data)
    with app.app_context():
        assert PasswordResetToken.query.count() == 0


@pytest.mark.parametrize('username', ['adm', 'sup'])
def test_a_token_can_never_change_a_non_customer_password(env, username):
    app, client, _ = env
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        now = now_utc()
        db.session.add(PasswordResetToken(user_id=user.id, token_hash=resets.token_hash('x' * 43),
                                          created_at=now, expires_at=now + timedelta(minutes=10)))
        db.session.commit()
    with app.app_context():
        assert resets.find_valid_token('x' * 43) is None
    assert client.get('/recuperar/' + 'x' * 43).status_code == 400
    with app.app_context():
        assert resets.reset_password('x' * 43, GOOD) is False
        user = User.query.filter_by(username=username).one()
        assert user.check_password(OLD) and not user.check_password(GOOD)
        assert PasswordResetToken.query.one().used_at is None, 'el token no se consume'


def test_malformed_email_is_rejected_and_requests_are_rate_limited(env):
    app, client, sent = env
    assert _request(client, 'no-es-correo').status_code == 400
    for _ in range(5):
        assert _request(client, 'cliente@example.test').status_code == 200
    assert len(sent) == 3, 'límite por cuenta: tres correos por ventana'
    for _ in range(3):
        assert _request(client, 'cliente@example.test').status_code == 200
    assert len(sent) == 3
    with app.app_context():
        assert AuditLog.query.filter_by(action='PASSWORD_RESET_ATTEMPT').count() == 8


# ─── 18-22: enlace y cambio ───

def test_valid_token_changes_the_password_once_and_invalidates_sessions(env):
    app, client, sent = env
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    open_session = app.test_client()
    with open_session.session_transaction() as browser_session:
        browser_session.update({'_user_id': str(_user_id(app, 'cli')), '_fresh': True,
                                'login_2fa_verified': True, 'pwd_stamp': '0'})
    assert open_session.get('/dashboard').status_code == 200

    page = client.get(f'/recuperar/{token}')
    assert page.status_code == 200
    assert page.headers['Cache-Control'] == 'no-store' and page.headers['Referrer-Policy'] == 'no-referrer'
    response = _set_password(client, token)
    assert response.status_code == 302 and response.headers['Location'].endswith('/login')

    with app.app_context():
        user = User.query.filter_by(username='cli').one()
        assert user.check_password(GOOD) and not user.check_password(OLD)
        assert user.password_changed_at is not None
        assert PasswordResetToken.query.one().used_at is not None
    reopened = open_session.get('/dashboard')
    assert reopened.status_code == 302 and reopened.headers['Location'].endswith('/login'), \
        'la sesión abierta antes del cambio deja de valer'


def test_used_token_is_rejected(env):
    app, client, sent = env
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    assert _set_password(client, token).status_code == 302
    assert client.get(f'/recuperar/{token}').status_code == 400
    again = client.post(f'/recuperar/{token}', data={'password': 'Otra2026.x', 'password_confirmation': 'Otra2026.x'})
    assert again.status_code in (400,)
    with app.app_context():
        assert User.query.filter_by(username='cli').one().check_password(GOOD)


def test_expired_token_is_rejected(env):
    app, client, sent = env
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    with app.app_context():
        record = PasswordResetToken.query.one()
        record.expires_at = now_utc() - timedelta(seconds=1)
        db.session.commit()
    assert client.get(f'/recuperar/{token}').status_code == 400
    with app.app_context():
        assert resets.reset_password(token, GOOD) is False
        assert User.query.filter_by(username='cli').one().check_password(OLD)


def test_a_new_request_replaces_the_previous_token(env):
    app, client, sent = env
    _request(client, 'cliente@example.test')
    first = _token_from(sent)
    _request(client, 'cliente@example.test')
    second = _token_from(sent)
    assert first != second
    assert client.get(f'/recuperar/{first}').status_code == 400
    assert client.get(f'/recuperar/{second}').status_code == 200


@pytest.mark.parametrize('password,confirmation', [('Abc123.', None), ('Norte20261.', None), ('norte2026.', None),
                                                   ('NORTE2026.', None), ('NorteSur.A', None), ('Norte20261', None),
                                                   (GOOD, 'Nueva2027.')])
def test_recovery_enforces_the_same_password_policy(env, password, confirmation):
    app, client, sent = env
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    response = _set_password(client, token, password, confirmation)
    assert response.status_code == 400
    with app.app_context():
        assert User.query.filter_by(username='cli').one().check_password(OLD)
        assert PasswordResetToken.query.one().used_at is None, 'un intento rechazado no consume el enlace'
    assert _set_password(client, token).status_code == 302


def test_two_simultaneous_requests_cannot_reuse_the_same_token(tmp_path, monkeypatch):
    database = tmp_path / 'concurrency.db'
    app = create_app({'SQLALCHEMY_DATABASE_URI': f'sqlite:///{database.as_posix()}'})
    app.config['TESTING'] = True
    sent = []
    monkeypatch.setattr(recovery, 'send_password_reset_link', lambda to, link, minutes: sent.append(link))
    with app.app_context():
        user = User(username='cli', full_name='Persona Prueba', email='cliente@example.test', dni='40000001',
                    role='CLIENTE')
        user.set_password(OLD)
        db.session.add(user)
        db.session.commit()
    first_client = app.test_client()
    _request(first_client, 'cliente@example.test')
    token = sent[-1].rsplit('/', 1)[1]

    clients = [app.test_client(), app.test_client()]
    pages = [client.get(f'/recuperar/{token}') for client in clients]
    passwords = ['Primera2.a', 'Segunda2.b']
    barrier = threading.Barrier(2)
    results = [None, None]

    def attempt(index):
        barrier.wait()
        results[index] = clients[index].post(f'/recuperar/{token}', data={
            'csrf_token': _csrf(pages[index]), 'password': passwords[index],
            'password_confirmation': passwords[index]}).status_code

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
    [thread.start() for thread in threads]
    [thread.join(30) for thread in threads]
    assert sorted(results) == [302, 400], results
    winner = passwords[results.index(302)]
    with app.app_context():
        user = User.query.filter_by(username='cli').one()
        assert user.check_password(winner)
        assert PasswordResetToken.query.filter(PasswordResetToken.used_at.is_(None)).count() == 0


def test_token_consumption_is_atomic_at_service_level(env):
    app, client, sent = env
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    with app.app_context():
        assert [resets.reset_password(token, GOOD), resets.reset_password(token, 'Otra2026.x')] == [True, False]


# ─── enlaces, secretos y rutas ───

def test_login_page_links_to_recovery_and_authenticated_users_are_redirected(env):
    app, client, _ = env
    assert 'href="/recuperar"' in client.get('/login').get_data(as_text=True)
    assert '¿Olvidaste tu contraseña?' in client.get('/login').get_data(as_text=True)
    with client.session_transaction() as browser_session:
        browser_session.update({'_user_id': str(_user_id(app, 'cli')), '_fresh': True,
                                'login_2fa_verified': True, 'pwd_stamp': '0'})
    assert client.get('/recuperar').status_code == 302


def test_tokens_and_passwords_never_reach_the_logs(env, caplog):
    app, client, sent = env
    caplog.set_level(logging.DEBUG)
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    _set_password(client, token)
    assert token not in caplog.text and GOOD not in caplog.text and OLD not in caplog.text


def test_reset_link_requires_public_base_url_in_production(env):
    app, _, _ = env
    token = 'a' * 43
    with app.test_request_context('/', headers={'Host': 'evil.example'}):
        app.config['APP_ENV'] = 'production'
        app.config['PUBLIC_BASE_URL'] = ''
        assert recovery._reset_link(token) is None, 'sin base pública no se confía en el encabezado Host'
        app.config['PUBLIC_BASE_URL'] = 'https://norte.example'
        assert recovery._reset_link(token) == f'https://norte.example/recuperar/{token}'


def _user_id(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).one().id


def test_head_requests_do_not_run_the_submission_logic(env):
    app, client, sent = env
    assert client.head('/recuperar').status_code == 200
    _request(client, 'cliente@example.test')
    token = _token_from(sent)
    assert client.head(f'/recuperar/{token}').status_code == 200
    with app.app_context():
        assert PasswordResetToken.query.one().used_at is None
        assert AuditLog.query.filter_by(action='PASSWORD_RESET_ATTEMPT').count() == 1

