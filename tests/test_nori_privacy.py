"""/privacidad: cada afirmación del apartado sobre Nori se contrasta con el comportamiento real del sistema."""
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import pytest

import app.routes as routes_module
from app import create_app
from app.models import AuditLog, User, db
from app.nori import safety


@pytest.fixture(autouse=True)
def clean_state():
    safety._memory_hits.clear()
    safety._last_purge = 0.0
    yield
    safety._memory_hits.clear()
    safety._last_purge = 0.0


@pytest.fixture
def app():
    return create_app()


def _section(app_or_client, path='/privacidad'):
    html = app_or_client.get(path).get_data(as_text=True)
    start = html.index('<article id="privacidad-nori">')
    block = html[start:html.index('</article>', start)]
    return unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', block)))


def test_the_nori_section_is_published_only_on_the_privacy_page(app):
    client = app.test_client()
    text = _section(client)
    assert '6. Nori, asistente automático de la web pública' in text
    for path in ('/terminos', '/acerca', '/seguridad', '/personas', '/'):
        assert 'privacidad-nori' not in client.get(path).get_data(as_text=True)


def test_the_section_covers_every_required_point(app):
    text = _section(app.test_client())
    for expected in ('sessionStorage', 'Nueva conversación', 'cookie de sesión técnica', 'huella no reversible', 'dato personal seudonimizado',
                     '24 horas', 'pestaña', 'no la conserva', 'inteligencia artificial generativa', 'servicios externos',
                     'no los guarda en su base de datos', 'ver sección 4'):
        assert expected in text, expected


def test_numbers_in_the_text_come_from_the_configuration_and_stay_in_sync(app):
    app.config.update(NORI_MEMORY_TURNS=5, NORI_MEMORY_TTL_SECONDS=2400, NORI_RATE_LIMIT_MESSAGES=7, NORI_DAILY_LIMIT=99)
    text = _section(app.test_client())
    assert 'las últimas 5 preguntas y respuestas' in text and 'tras 40 minutos sin actividad' in text
    assert '(7 mensajes por minuto o 99 al día por conexión)' in text


def test_the_text_matches_the_real_defaults(app):
    text = _section(app.test_client())
    assert app.config['NORI_MEMORY_TURNS'] == 8 and 'las últimas 8 preguntas' in text
    assert app.config['NORI_MEMORY_TTL_SECONDS'] == 1800 and 'tras 30 minutos sin actividad' in text
    assert '20 mensajes por minuto o 300 al día' in text
    assert safety.AUDIT_RETENTION == timedelta(hours=24)                       # «24 horas» del texto
    assert app.config['PERMANENT_SESSION_LIFETIME'] == timedelta(minutes=30) and 'a los 30 minutos' in text


def test_retention_is_stated_honestly_without_promising_an_exact_deletion_time(app):
    text = _section(app.test_client())
    assert 'se consideran vencidos a las 24 horas' in text
    assert 'cuando Nori recibe un mensaje o se abre el chat' in text
    assert 'pueden permanecer más tiempo hasta el siguiente uso' in text
    assert not re.search(r'exactamente|se eliminan a las 24|se borran a las 24|siempre se eliminan', text)


def test_it_does_not_claim_third_party_ai_or_sharing_that_would_be_false(app):
    html = app.test_client().get('/privacidad').get_data(as_text=True).lower()
    for forbidden in ('anthropic', 'openai', 'chatgpt', 'gemini', 'api.claude'):
        assert forbidden not in html


def test_section_four_that_the_text_points_to_exists(app):
    html = app.test_client().get('/privacidad').get_data(as_text=True)
    assert '4. Conservación, seguridad y terceros' in html


# ───────── Las afirmaciones del texto son ciertas ─────────
def _cookie_expiry(response):
    for header in response.headers.getlist('Set-Cookie'):
        if header.startswith('session='):
            match = re.search(r'Expires=([^;]+)', header, re.I)
            return header, (parsedate_to_datetime(match.group(1)) if match else None)
    return None, None


def test_the_session_cookie_is_a_browser_session_cookie_for_visitors(app):
    response = app.test_client().get('/api/nori/csrf')
    header, expiry = _cookie_expiry(response)
    assert header is not None and expiry is None                              # sin Expires: se elimina al cerrar el navegador
    assert 'HttpOnly' in header and 'SameSite=Lax' in header


def test_the_session_cookie_expires_in_thirty_minutes_after_logging_in(app, monkeypatch):
    codes = []
    monkeypatch.setattr(routes_module, 'send_login_code', lambda email, code: codes.append(code))
    with app.app_context():
        User.query.filter_by(username='demo').one().email = 'a@example.test'
        db.session.commit()
    client = app.test_client()
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get('/login').get_data(as_text=True)).group(1)
    client.post('/login', data={'csrf_token': token, 'username': 'demo', 'password': 'Demo123!'})
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get('/login/verify').get_data(as_text=True)).group(1)
    response = client.post('/login/verify', data={'csrf_token': token, 'code': codes[-1]})
    _, expiry = _cookie_expiry(response)
    assert expiry is not None
    remaining = expiry - datetime.now(timezone.utc)
    assert timedelta(minutes=28) < remaining <= timedelta(minutes=31)


def test_the_audit_record_has_a_hash_of_the_ip_and_not_the_message_or_the_address(app):
    client = app.test_client()
    token = client.get('/api/nori/csrf').get_json()['csrf']
    client.post('/api/nori/chat', json={'message': 'pregunta-privada-qwx sobre seguridad'}, headers={'X-CSRFToken': token},
                environ_overrides={'REMOTE_ADDR': '203.0.113.77'})
    with app.app_context():
        [row] = AuditLog.query.filter_by(action='NORI_CHAT').all()
        assert re.fullmatch(r'[0-9a-f]{64}', row.ip_local) and '203.0.113.77' not in row.ip_local
        assert row.user_id is None
        assert 'qwx' not in ' '.join(str(getattr(row, column.name)) for column in AuditLog.__table__.columns)


@pytest.mark.parametrize('message', ['mi DNI es 12345678', 'llámame al 987654321', 'tarjeta 4111 1111 1111 1111', 'mi código es 482913',
                                     'mi contraseña es Abc123'])
def test_the_kinds_of_data_named_in_the_text_are_really_blocked(app, message):
    client = app.test_client()
    token = client.get('/api/nori/csrf').get_json()['csrf']
    body = client.post('/api/nori/chat', json={'message': message}, headers={'X-CSRFToken': token}).get_json()
    assert body['redact'] is True


def test_nori_never_writes_message_text_to_the_application_log(app, caplog):
    client = app.test_client()
    token = client.get('/api/nori/csrf').get_json()['csrf']
    with caplog.at_level('DEBUG'):
        client.post('/api/nori/chat', json={'message': 'texto-unico-zzq para el log'}, headers={'X-CSRFToken': token})
    assert 'zzq' not in caplog.text


def test_paragraphs_of_the_section_are_separated_by_the_legal_page_styles(app):
    css = app.test_client().get('/static/css/norte-public.css').get_data(as_text=True)
    assert '.legal-content article p+p{margin-top:var(--s-4)}' in css
    html = app.test_client().get('/privacidad').get_data(as_text=True)
    assert 'css/norte-public.css?v=public-4' in html
