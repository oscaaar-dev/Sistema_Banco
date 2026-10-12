"""Preparación para producción: ``SECRET_KEY``, sesiones compartidas entre workers e identificación de la IP del cliente.

Solo comprueba lo verificable en local. Que ``X-Real-IP`` sea fiable detrás del proxy de Railway **no** se puede demostrar aquí:
las pruebas de IP describen el comportamiento actual del código, no certifican que esa confianza sea segura.
"""
import importlib.util
import json
import logging
from pathlib import Path

import pytest

import app.routes as routes_module
from app import create_app
from app.nori import safety

ROOT = Path(__file__).resolve().parents[1]
GOOD_KEY = 'k' * 40
PRODUCTION = {'APP_ENV': 'production', 'DATABASE_URL': 'sqlite://', 'SMTP_HOST': 'smtp.example.test', 'SMTP_USERNAME': 'usuario',
              'SMTP_PASSWORD': 'clave', 'SMTP_FROM': 'norte@example.test'}
CLEARED = ('SECRET_KEY', 'DATABASE_URL', 'MYSQL_URL', 'SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM', 'EMAIL_TRANSPORT',
           'RAILWAY_ENVIRONMENT_NAME', 'COOKIE_SECURE', 'BREVO_API_KEY')


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    # migrations/env.py llama a logging.config.fileConfig (que desactiva los registradores existentes) y las pruebas de
    # migraciones lo ejecutan en este mismo proceso: se reactiva el registrador de la aplicación para poder comprobar los avisos.
    monkeypatch.setattr(logging.getLogger('app'), 'disabled', False)
    safety._memory_hits.clear()
    yield
    safety._memory_hits.clear()


def load_config(monkeypatch, **env):
    """Carga una copia independiente de ``config.py`` con un entorno controlado (sin leer ``.env`` y sin tocar el módulo real)."""
    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('dotenv.load_dotenv', lambda *args, **kwargs: False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location('config_under_test', ROOT / 'config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ───────── SECRET_KEY en producción ─────────

@pytest.mark.parametrize('secret', [None, '', ' ', '     ', '\t \n'], ids=['ausente', 'vacia', 'un-espacio', 'espacios', 'blancos'])
def test_production_refuses_a_missing_empty_or_blank_secret_key(monkeypatch, secret):
    env = dict(PRODUCTION)
    if secret is not None:
        env['SECRET_KEY'] = secret
    with pytest.raises(RuntimeError) as error:
        load_config(monkeypatch, **env)
    assert 'SECRET_KEY' in str(error.value)


def test_production_still_requires_the_database_url(monkeypatch):
    env = {key: value for key, value in PRODUCTION.items() if key != 'DATABASE_URL'}
    with pytest.raises(RuntimeError) as error:
        load_config(monkeypatch, SECRET_KEY=GOOD_KEY, **env)
    assert 'DATABASE_URL' in str(error.value) and 'SECRET_KEY' not in str(error.value)


def test_production_with_a_real_key_starts_and_keeps_it_exactly_as_configured(monkeypatch):
    module = load_config(monkeypatch, SECRET_KEY=GOOD_KEY, **PRODUCTION)
    assert module.Config.SECRET_KEY == GOOD_KEY and module.Config.SECRET_KEY_IS_TEMPORARY is False
    padded = '  ' + GOOD_KEY + '  '
    assert load_config(monkeypatch, SECRET_KEY=padded, **PRODUCTION).Config.SECRET_KEY == padded    # no se recorta: no cambia la firma de sesiones


def test_the_error_never_reveals_other_configured_secrets(monkeypatch):
    env = dict(PRODUCTION, SMTP_PASSWORD='contrasena-smtp-secreta')
    with pytest.raises(RuntimeError) as error:
        load_config(monkeypatch, **env)
    assert 'contrasena-smtp-secreta' not in str(error.value)


# ───────── SECRET_KEY fuera de producción ─────────

def test_outside_production_a_missing_or_blank_key_is_temporary_and_differs_per_process(monkeypatch):
    first = load_config(monkeypatch, APP_ENV='development').Config
    second = load_config(monkeypatch, APP_ENV='development').Config
    blank = load_config(monkeypatch, APP_ENV='development', SECRET_KEY='   ').Config
    assert first.SECRET_KEY_IS_TEMPORARY and second.SECRET_KEY_IS_TEMPORARY and blank.SECRET_KEY_IS_TEMPORARY
    assert len(first.SECRET_KEY) == 64 and first.SECRET_KEY != second.SECRET_KEY            # justo lo que los workers no deben compartir
    assert load_config(monkeypatch, APP_ENV='development', SECRET_KEY=GOOD_KEY).Config.SECRET_KEY_IS_TEMPORARY is False


def test_a_temporary_key_logs_a_clear_warning_without_revealing_the_key(caplog):
    with caplog.at_level(logging.WARNING):
        app = create_app({'SECRET_KEY_IS_TEMPORARY': True})
    assert 'SECRET_KEY no está definida' in caplog.text and 'workers' in caplog.text
    assert app.config['SECRET_KEY'] not in caplog.text


def test_an_explicit_long_key_logs_no_secret_key_warning(caplog):
    with caplog.at_level(logging.WARNING):
        create_app({'SECRET_KEY': GOOD_KEY, 'SECRET_KEY_IS_TEMPORARY': True})
    assert 'SECRET_KEY' not in caplog.text


def test_a_short_key_warns_without_revealing_it_and_does_not_stop_the_app(caplog):
    short = 'corta-1234567890'
    with caplog.at_level(logging.WARNING):
        app = create_app({'SECRET_KEY': short})
    assert 'menos de 32 caracteres' in caplog.text and short not in caplog.text
    assert str(len(short)) not in caplog.text                                          # tampoco se revela la longitud exacta
    assert app.test_client().get('/').status_code == 200


def test_key_length_warning_boundary():
    module_config = importlib.import_module('config')
    assert module_config.secret_key_warnings('x' * 32, False) == []
    assert len(module_config.secret_key_warnings('x' * 31, False)) == 1
    assert 'x' * 31 not in module_config.secret_key_warnings('x' * 31, False)[0]
    assert len(module_config.secret_key_warnings('x' * 64, True)) == 1                      # temporal: avisa aunque sea larga


# ───────── Dos «workers» con la misma clave comparten sesión y scope de Nori ─────────

def _worker(key):
    app = create_app({'SECRET_KEY': key})
    app.config.update(NORI_RATE_LIMIT_MESSAGES=1000, NORI_DAILY_LIMIT=10000)
    return app


def _move_session(source, target):
    """Lo que hace un navegador cuando el balanceador lo envía a otro worker: presenta la misma cookie de sesión."""
    cookie = source.get_cookie('session')
    assert cookie is not None
    target.set_cookie('session', cookie.value)


def _ask(client, message, history=None, scope=None, token=None):
    token = token or client.get('/api/nori/csrf').get_json()['csrf']
    payload = {'message': message}
    if history is not None:
        payload['history'] = history
    if scope is not None:
        payload['scope'] = scope
    response = client.post('/api/nori/chat', data=json.dumps(payload), content_type='application/json', headers={'X-CSRFToken': token})
    return response.get_json()


def test_two_workers_with_the_same_key_accept_each_others_session_scope_and_csrf_token():
    one, two = _worker(GOOD_KEY), _worker(GOOD_KEY)
    first, second = one.test_client(), two.test_client()
    handshake = first.get('/api/nori/csrf').get_json()
    reply = _ask(first, '¿Cómo ingreso?', token=handshake['csrf'])['reply']
    _move_session(first, second)
    assert second.get('/api/nori/csrf').get_json()['scope'] == handshake['scope']
    history = [{'role': 'user', 'text': '¿Cómo ingreso?'}, {'role': 'bot', 'text': reply}]
    body = _ask(second, 'más información', history=history, scope=handshake['scope'], token=handshake['csrf'])   # token emitido por el otro worker
    assert 'Si es tu primera vez' in body['reply'] and not body.get('reset') and 'error' not in body


def test_workers_with_different_keys_do_not_share_scope_or_accept_each_others_history():
    one, two = _worker(GOOD_KEY), _worker('otra-clave-distinta-' + 'z' * 20)
    first, second = one.test_client(), two.test_client()
    handshake = first.get('/api/nori/csrf').get_json()
    reply = _ask(first, '¿Cómo ingreso?', token=handshake['csrf'])['reply']
    _move_session(first, second)
    other = second.get('/api/nori/csrf').get_json()
    assert other['scope'] != handshake['scope']                                              # la cookie ajena no se puede leer: sesión nueva
    history = [{'role': 'user', 'text': '¿Cómo ingreso?'}, {'role': 'bot', 'text': reply}]
    body = _ask(second, 'más información', history=history, scope=handshake['scope'], token=other['csrf'])
    assert body.get('reset') is True and 'Si es tu primera vez' not in body['reply']


def test_the_token_of_one_worker_is_refused_by_a_worker_with_another_key():
    one, two = _worker(GOOD_KEY), _worker('otra-clave-distinta-' + 'z' * 20)
    first, second = one.test_client(), two.test_client()
    token = first.get('/api/nori/csrf').get_json()['csrf']
    _move_session(first, second)
    response = second.post('/api/nori/chat', data=json.dumps({'message': 'hola'}), content_type='application/json',
                           headers={'X-CSRFToken': token})
    assert response.status_code == 400 and response.get_json()['error'] == 'csrf'           # CSRF sigue activo


# ───────── Identificación de la IP: comportamiento ACTUAL (no demuestra que X-Real-IP sea fiable) ─────────

def _ip_hash(app, remote='10.0.0.1', headers=None, railway=False):
    app.config['RAILWAY_ENVIRONMENT_NAME'] = 'staging' if railway else ''
    with app.test_request_context('/', headers=headers or {}, environ_overrides={'REMOTE_ADDR': remote}):
        return routes_module._request_ip_hash('prueba')


@pytest.fixture
def ip_app():
    return create_app({'SECRET_KEY': GOOD_KEY})


def test_outside_railway_only_the_connection_address_is_used(ip_app):
    base = _ip_hash(ip_app, remote='10.0.0.1')
    assert _ip_hash(ip_app, remote='10.0.0.1', headers={'X-Real-IP': '198.51.100.7'}) == base
    assert _ip_hash(ip_app, remote='10.0.0.1', headers={'X-Forwarded-For': '198.51.100.8, 10.0.0.1'}) == base
    assert _ip_hash(ip_app, remote='10.0.0.2') != base


def test_on_railway_x_real_ip_is_what_identifies_the_client_today(ip_app):
    """Describe la confianza actual: con RAILWAY_ENVIRONMENT_NAME se usa X-Real-IP tal como llega. Que Railway la sobrescriba
    (y que un cliente no pueda falsificarla) está PENDIENTE de verificar en la infraestructura real."""
    via_header = _ip_hash(ip_app, remote='10.0.0.1', headers={'X-Real-IP': '198.51.100.7'}, railway=True)
    assert via_header == _ip_hash(ip_app, remote='198.51.100.7', railway=False)             # equivale a ser esa dirección
    assert via_header != _ip_hash(ip_app, remote='10.0.0.1', railway=True)
    assert _ip_hash(ip_app, remote='10.0.0.1', headers={'X-Real-IP': '198.51.100.9'}, railway=True) != via_header


def test_on_railway_x_forwarded_for_is_ignored_and_a_missing_x_real_ip_falls_back_to_the_connection(ip_app):
    base = _ip_hash(ip_app, remote='10.0.0.1', railway=True)
    assert _ip_hash(ip_app, remote='10.0.0.1', headers={'X-Forwarded-For': '198.51.100.8'}, railway=True) == base
    assert _ip_hash(ip_app, remote='10.0.0.1', headers={'X-Real-IP': ''}, railway=True) == base
    assert base == _ip_hash(ip_app, remote='10.0.0.1', railway=False)


def test_the_ip_is_hashed_with_the_secret_key_and_never_stored_in_clear(ip_app):
    value = _ip_hash(ip_app, remote='203.0.113.77')
    assert '203.0.113.77' not in value and len(value) == 64
    other = create_app({'SECRET_KEY': 'otra-clave-' + 'q' * 30})
    assert _ip_hash(other, remote='203.0.113.77') != value


# ───────── Documentación honesta ─────────

def test_the_production_checklist_is_documented_and_marks_the_proxy_trust_as_pending():
    docs = (ROOT / 'docs' / 'seguridad-arquitectura.md').read_text(encoding='utf-8')
    assert 'Lista de verificación de producción' in docs
    for item in ('APP_ENV=production', 'SECRET_KEY', 'DATABASE_URL', 'X-Real-IP'):
        assert item in docs
    section = ' '.join(docs.split('Lista de verificación de producción')[1].lower().split())     # los saltos de línea no cuentan
    assert 'pendiente de verificar' in section and 'no se ha ejecutado' in section and 'no está validada' in section
    nori = (ROOT / 'docs' / 'nori.md').read_text(encoding='utf-8')
    assert 'pendiente de verificar' in nori and 'apartado 9' in nori
