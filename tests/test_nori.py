"""Nori (etapa 1): widget solo en páginas públicas, CSRF activo, respuestas por reglas, datos sensibles y límites."""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from flask import url_for

from app import create_app
from app.models import AuditLog, User, db, now_utc
from app.nori import safety
from app.nori.rules import FALLBACK_REPLY, INTENTS, OFFICIAL_PAGES, PAGES, answer

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'nori'
PUBLIC_PAGES = ['/', '/personas', '/herramientas', '/seguridad', '/acerca', '/terminos', '/privacidad']


@pytest.fixture(autouse=True)
def clean_memory_limiter():
    safety._memory_hits.clear()
    yield
    safety._memory_hits.clear()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _csrf(client):
    response = client.get('/api/nori/csrf')
    assert response.status_code == 200
    return response.get_json()['csrf']


def _chat(client, message, token='auto', **kwargs):
    if token == 'auto':
        token = _csrf(client)
    headers = {'X-CSRFToken': token} if token else {}
    return client.post('/api/nori/chat', json={'message': message}, headers=headers, **kwargs)


def _logged_in_client(app, username='demo'):
    client = app.test_client()
    with app.app_context():
        user_id = User.query.filter_by(username=username).one().id
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    return client


# ───────── Dónde aparece Nori ─────────
@pytest.mark.parametrize('path', PUBLIC_PAGES)
def test_widget_is_present_on_public_pages(client, path):
    html = client.get(path).get_data(as_text=True)
    assert 'id="nori"' in html and 'nori/nori.css' in html and 'nori/nori.js' in html
    assert 'data-endpoint="/api/nori/chat"' in html and 'data-csrf-url="/api/nori/csrf"' in html


def test_nori_replaces_the_black_help_button_on_the_home_page(client):
    html = client.get('/').get_data(as_text=True)
    assert 'id="nori"' in html and 'data-open-cookies' in html
    assert 'data-open-help' not in html and 'helpDialog' not in html and 'home-help-button' not in html
    assert 'nori--lift' not in html


@pytest.mark.parametrize('path', ['/login', '/register', '/recuperar', '/pagina-que-no-existe'])
def test_widget_is_absent_from_access_pages_and_errors(client, path):
    assert 'id="nori"' not in client.get(path).get_data(as_text=True)


def test_widget_is_absent_from_the_private_area(app):
    client = _logged_in_client(app)
    for path in ('/dashboard', '/movements', '/cards', '/accounts', '/contacts', '/operation/transfer', '/internal-transfer', '/cards'):
        assert 'id="nori"' not in client.get(path).get_data(as_text=True), path
    # las páginas informativas siguen siendo públicas aunque haya sesión iniciada
    assert 'id="nori"' in client.get('/personas').get_data(as_text=True)


def test_public_pages_do_not_open_a_session_cookie_just_for_nori(client):
    response = client.get('/personas')
    assert not any(header.startswith('session=') for header in response.headers.getlist('Set-Cookie'))


# ───────── CSRF ─────────
def test_chat_requires_the_csrf_token_and_offers_a_fresh_one(client):
    response = client.post('/api/nori/chat', json={'message': 'hola'})
    body = response.get_json()
    assert response.status_code == 400 and body['error'] == 'csrf' and body['csrf']
    assert response.headers['Cache-Control'] == 'no-store'
    # con el token recibido el reintento funciona
    retry = client.post('/api/nori/chat', json={'message': 'hola'}, headers={'X-CSRFToken': body['csrf']})
    assert retry.status_code == 200 and retry.get_json()['reply']


def test_chat_rejects_a_forged_token(client):
    _csrf(client)
    response = client.post('/api/nori/chat', json={'message': 'hola'}, headers={'X-CSRFToken': 'falso'})
    assert response.status_code == 400 and response.get_json()['error'] == 'csrf'


def test_every_chat_response_carries_a_new_token_that_works(client):
    first = _chat(client, 'hola').get_json()
    second = client.post('/api/nori/chat', json={'message': 'gracias'}, headers={'X-CSRFToken': first['csrf']})
    assert second.status_code == 200


def test_csrf_endpoint_is_get_only_and_chat_is_post_only(client):
    assert client.post('/api/nori/csrf').status_code in (400, 405)
    assert client.get('/api/nori/chat').status_code == 405


def test_csrf_protection_stays_enabled_for_the_rest_of_the_app(client):
    assert client.post('/login', data={'username': 'demo', 'password': 'x'}).status_code == 400


# ───────── Respuestas por reglas ─────────
QUESTIONS = [
    ('¡Hola!', 'saludo'), ('gracias', 'gracias'), ('Hasta luego', 'adios'), ('¿Quién eres?', 'quien'),
    ('¿Qué puedes hacer?', 'ayuda'), ('Olvidé mi contraseña', 'recuperar'), ('¿Cómo ingreso?', 'ingresar'),
    ('quiero abrir una cuenta', 'abrir'), ('Abrir una cuenta', 'abrir'), ('¿Pide la cámara una foto?', 'camara'),
    ('¿Es seguro?', 'seguridad'), ('¿Qué es la sección Personas?', 'personas'), ('¿Qué herramientas hay?', 'herramientas'),
    ('¿Puedo transferir a otro cliente?', 'operaciones'), ('¿Cómo agrego un contacto?', 'operaciones'),
    ('¿Puedo bajar mi estado de cuenta en PDF?', 'cuenta'), ('¿Cuál es el horario de atención?', 'horario'),
    ('¿Qué es Norte?', 'acerca'), ('¿Es dinero real?', 'acerca'), ('¿Dónde están los términos y condiciones?', 'legal'),
    ('¿Qué tasa de interés pagan?', 'productos'), ('¿Dan préstamos?', 'productos'),
]


@pytest.mark.parametrize('question,intent', QUESTIONS)
def test_questions_are_matched_to_the_right_intent(app, question, intent):
    with app.test_request_context('/'):
        assert answer(question)['intent'] == intent


def test_reply_is_json_with_text_mood_links_and_suggestions(client):
    body = _chat(client, '¿Cómo ingreso?').get_json()
    assert 'Banca por Internet' in body['reply'] and body['mood'] == 'hola'
    assert body['links'] == [{'label': 'Banca por Internet', 'href': '/login'}]
    assert body['suggestions'] == []
    assert _chat(client, 'hola').get_json()['suggestions'] == ['¿Cómo ingreso?', '¿Es seguro?', 'Abrir una cuenta', '¿Qué es Norte?']


def test_suggested_questions_resolve_to_a_real_answer(app):
    with app.test_request_context('/'):
        for suggestion in answer('hola')['suggestions']:
            assert answer(suggestion)['intent'] is not None, suggestion


def test_answers_describe_real_features_and_invent_nothing(app):
    with app.test_request_context('/'):
        assert answer('¿Qué es Norte?')['reply'] == app.config['LEGAL_NOTICE']
        assert app.config['CONTACT_HOURS'] in answer('horario de atención')['reply']
        assert str(app.config['CAPTURE_RETENTION_DAYS']) in answer('¿pide foto la cámara?')['reply']
        reply = answer('¿Qué tasa de interés pagan?')['reply']
        assert 'no ofrece productos financieros reales' in reply and not re.search(r'\d\s?%', reply)


def test_contact_email_is_mentioned_only_when_configured(app):
    app.config['CONTACT_EMAIL'] = ''
    with app.test_request_context('/'):
        assert '@' not in answer('horario')['reply']
        app.config['CONTACT_EMAIL'] = 'contacto@example.test'
        assert 'contacto@example.test' in answer('horario')['reply']


def test_unknown_question_admits_it_and_points_to_official_pages(client):
    body = _chat(client, 'blablabla xyzzy').get_json()
    assert body['reply'] == FALLBACK_REPLY and 'no encontré información suficiente' in body['reply'].lower()
    assert body['mood'] == 'confundido'
    assert [link['href'] for link in body['links']] == ['/personas', '/herramientas', '/seguridad', '/acerca']


def test_every_link_is_an_internal_page_that_exists(app):
    with app.test_request_context('/'):
        for key, (label, endpoint) in PAGES.items():
            assert url_for(endpoint).startswith('/') and not url_for(endpoint).startswith('//')
        client = app.test_client()
        for intent in INTENTS:
            for link in answer(intent['kw'][0])['links']:
                assert link['href'].startswith('/') and client.get(link['href']).status_code in (200, 302), link
    assert set(OFFICIAL_PAGES) <= set(PAGES)


def test_replies_are_plain_text_without_html(client):
    for question in ('hola', '<script>alert(1)</script>', 'seguridad <b>x</b>', 'zzz'):
        body = _chat(client, question).get_json()
        assert '<' not in body['reply'] and all('<' not in link['label'] for link in body['links'])


def test_a_logged_in_customer_can_use_nori_too_without_any_account_data(app):
    client = _logged_in_client(app)
    body = _chat(client, '¿Cuál es mi saldo?').get_json()
    assert '8,540.50' not in body['reply'] and '8540' not in body['reply']


# ───────── Datos sensibles ─────────
SENSITIVE = ['mi DNI es 12345678', '12345678', '4111 1111 1111 1111', '4111-1111-1111-1111', 'mi código es 482913',
             '482913', 'mi contraseña es Abc123', 'contraseña: hunter2', 'clave = 1234abcd', 'mi clave es secreta',
             'llámame al 987654321', 'cvv es 123']


@pytest.mark.parametrize('message', SENSITIVE)
def test_sensitive_data_is_rejected_hidden_and_never_echoed(app, client, message):
    body = _chat(client, message).get_json()
    assert body['redact'] is True and body['mood'] == 'seguridad' and body['links'] == []
    assert body['reply'] == safety.SENSITIVE_REPLY
    assert message not in body['reply']
    secrets_in_message = [token for token in re.findall(r'\S+', message) if re.search(r'\d', token)]
    assert not any(token in body['reply'] for token in secrets_in_message)
    with app.app_context():
        stored = ' '.join(f'{row.action} {row.module} {row.result} {row.ip_local}' for row in AuditLog.query.all())
        assert not any(token in stored for token in secrets_in_message) and message not in stored


@pytest.mark.parametrize('message', ['¿Cómo ingreso?', '¿Qué es el código de seguridad?', 'Atienden de 9:00 a 18:00?', 'Quiero 2 cuentas',
                                     '¿Qué es una contraseña segura?', 'hola, soy nuevo'])
def test_normal_questions_are_not_flagged_as_sensitive(client, message):
    assert 'redact' not in _chat(client, message).get_json()


def test_sensitive_detector_unit():
    assert safety.contains_sensitive_data('1234 5678') and safety.contains_sensitive_data('12.34.56.78')
    assert not safety.contains_sensitive_data('abre 24 horas, 7 dias') and not safety.contains_sensitive_data('quiero 12345')


# ───────── Validación de entrada ─────────
def test_non_json_and_wrong_types_are_rejected(client):
    token = _csrf(client)
    headers = {'X-CSRFToken': token}
    assert client.post('/api/nori/chat', data='hola', headers=headers).status_code == 400
    for payload in ({}, {'message': 5}, {'message': None}, {'message': ['hola']}, [], 'texto'):
        assert client.post('/api/nori/chat', json=payload, headers=headers).status_code == 400
    assert client.post('/api/nori/chat', json={'message': '   \n\t '}, headers=headers).status_code == 400


def test_long_messages_are_rejected_or_trimmed(app, client):
    limit = app.config['NORI_MAX_MESSAGE_CHARS']
    assert _chat(client, 'a' * (limit * 4 + 1)).status_code == 400
    assert _chat(client, 'hola ' + 'x' * (limit * 2)).status_code == 200


def test_control_characters_and_spacing_are_cleaned():
    assert safety.clean_message('ho\x00la\x07  \n  mundo\t', 100) == 'ho la mundo'
    assert len(safety.clean_message('a' * 900, 500)) == 500
    assert safety.normalize('¿Qué es Cámara, Ñandú?') == 'que es camara nandu'


# ───────── Límite de mensajes y privacidad ─────────
def test_message_rate_limit_per_connection(app, client):
    app.config['NORI_RATE_LIMIT_MESSAGES'] = 3
    other = {'environ_overrides': {'REMOTE_ADDR': '10.9.9.9'}}
    for _ in range(3):
        assert _chat(client, 'hola').status_code == 200
    limited = _chat(client, 'hola')
    assert limited.status_code == 429 and limited.get_json()['error'] == 'rate_limit'
    assert 'muy rápido' in limited.get_json()['reply']
    assert _chat(app.test_client(), 'hola', **other).status_code == 200      # otra conexión no se ve afectada


def test_daily_limit_uses_the_audit_table_without_storing_text(app, client):
    app.config['NORI_DAILY_LIMIT'] = 2
    app.config['NORI_RATE_LIMIT_MESSAGES'] = 100
    safety._memory_hits.clear()
    assert _chat(client, 'hola').status_code == 200 and _chat(client, 'hola').status_code == 200
    assert _chat(client, 'hola').status_code == 429
    with app.app_context():
        rows = AuditLog.query.filter_by(action='NORI_CHAT').all()
        assert len(rows) == 2 and all(row.user_id is None and row.module == 'NORI' for row in rows)


def test_audit_rows_older_than_a_day_are_purged(app, client):
    with app.app_context():
        db.session.add(AuditLog(user_id=None, action='NORI_CHAT', module='NORI', result='ATENDIDA', ip_local='x',
                                created_at=now_utc() - timedelta(days=3)))
        db.session.add(AuditLog(user_id=None, action='LOGIN', module='SEGURIDAD', result='COMPLETADA',
                                created_at=now_utc() - timedelta(days=3)))
        db.session.commit()
    assert _chat(client, 'hola').status_code == 200
    with app.app_context():
        assert AuditLog.query.filter_by(action='NORI_CHAT').count() == 1
        assert AuditLog.query.filter_by(action='LOGIN').count() == 1       # lo demás no se toca


def test_chat_does_not_persist_conversations(app, client):
    texto = 'pregunta-unica-zxq sobre seguridad'
    assert _chat(client, texto).status_code == 200
    with app.app_context():
        for table in db.metadata.sorted_tables:
            for row in db.session.execute(table.select()).mappings():
                assert 'zxq' not in ' '.join(str(value) for value in row.values()), table.name


def test_memory_limiter_still_protects_if_the_database_fails(app, client, monkeypatch):
    app.config['NORI_RATE_LIMIT_MESSAGES'] = 2
    token = _csrf(client)

    class DatabaseDown:
        class _Raise:
            def __get__(self, instance, owner):
                raise RuntimeError('base de datos caída')
        query = _Raise()

    monkeypatch.setattr(safety, 'AuditLog', DatabaseDown)
    codes = [client.post('/api/nori/chat', json={'message': 'hola'}, headers={'X-CSRFToken': token}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ───────── Recursos estáticos ─────────
def test_assets_are_local_and_every_pose_image_exists():
    js = (STATIC / 'nori.js').read_text(encoding='utf-8')
    css = (STATIC / 'nori.css').read_text(encoding='utf-8')
    assert not re.search(r'https?://|//cdn|@import|url\(', js + css), 'Nori no debe cargar recursos externos'
    assert 'innerHTML' not in js and 'eval(' not in js and 'localStorage' not in js
    names = set(re.findall(r"b: '([a-z0-9-]+)'", js)) | {'fx-' + n for n in re.findall(r"fx: '([a-z0-9-]+)'", js)}
    names = {n if n.startswith('fx-') else 'nori-' + n for n in names}
    assert names and all((STATIC / 'web' / f'{name}.png').is_file() for name in names), names


def test_generic_css_classes_are_prefixed_to_avoid_clashes():
    css = (STATIC / 'nori.css').read_text(encoding='utf-8')
    for generic in ('.pose', '.chip', '.msg', '.eye', '.line', '.fx', '.fr1', '.sr-only'):
        assert not re.search(re.escape(generic) + r'(?![\w])', css), generic
    assert not re.search(r'@keyframes (?!nori-)', css)


def test_widget_respects_reduced_motion_and_uses_site_colors():
    css = (STATIC / 'nori.css').read_text(encoding='utf-8')
    assert 'prefers-reduced-motion: reduce' in css and '--n-pur: #6d3bd2' in css
    assert 'nori--lift' not in css and '--n-lift' not in css
