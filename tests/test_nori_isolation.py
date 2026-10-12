"""Nori: aislamiento del historial por identidad (cierre de sesión, cambio de usuario), purga de auditoría y zona de clic.

Los flujos de acceso usan el inicio de sesión real (contraseña + código por correo, con el envío simulado).
"""
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

import app.routes as routes_module
from app import create_app
from app.models import AuditLog, User, db, now_utc
from app.nori import memory, safety
from app.nori.rules import NO_CONTEXT_REPLY

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'nori'
MORE_LOGIN = 'Si es tu primera vez'


@pytest.fixture(autouse=True)
def clean_state():
    safety._memory_hits.clear()
    safety._last_purge = 0.0
    yield
    safety._memory_hits.clear()
    safety._last_purge = 0.0


@pytest.fixture
def app():
    app = create_app()
    app.config.update(LOGIN_CODE_RATE_LIMIT_IP=100, LOGIN_CODE_RATE_LIMIT_USER=100, NORI_RATE_LIMIT_MESSAGES=1000, NORI_DAILY_LIMIT=10000)
    return app


@pytest.fixture
def world(app, monkeypatch):
    """Dos clientes registrados (demo y bob) y un navegador compartido; captura los códigos de acceso."""
    codes = []
    monkeypatch.setattr(routes_module, 'send_login_code', lambda email, code: codes.append(code))
    with app.app_context():
        User.query.filter_by(username='demo').one().email = 'a@example.test'
        bob = User(username='bob', full_name='Beatriz Prueba', dni='22223333', email='b@example.test', role='CLIENTE')
        bob.set_password('Clave-De-Prueba-1!')
        db.session.add(bob)
        db.session.commit()
    return {'app': app, 'codes': codes, 'browser': app.test_client()}


def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _login(world, identifier='demo', password='Demo123!'):
    client = world['browser']
    token = _csrf(client.get('/login').get_data(as_text=True))
    client.post('/login', data={'csrf_token': token, 'username': identifier, 'password': password})
    token = _csrf(client.get('/login/verify').get_data(as_text=True))
    assert client.post('/login/verify', data={'csrf_token': token, 'code': world['codes'][-1]}).status_code == 302


def _logout(world):
    client = world['browser']
    token = _csrf(client.get('/dashboard').get_data(as_text=True))
    assert client.post('/logout', data={'csrf_token': token}).status_code == 302


def _scope(client):
    return client.get('/api/nori/csrf').get_json()['scope']


def _ask(client, message, history=None, scope=None):
    token = client.get('/api/nori/csrf').get_json()['csrf']
    payload = {'message': message}
    if history is not None:
        payload['history'] = history
    if scope is not None:
        payload['scope'] = scope
    response = client.post('/api/nori/chat', data=json.dumps(payload), content_type='application/json', headers={'X-CSRFToken': token})
    return response.get_json()


def _conversation(client):
    """Una conversación completa tal como la guardaría el navegador: (historial, scope con el que se creó)."""
    scope = _scope(client)
    reply = _ask(client, '¿Cómo ingreso?')['reply']
    return [{'role': 'user', 'text': '¿Cómo ingreso?'}, {'role': 'bot', 'text': reply}], scope


def _follow_up(client, history, scope):
    return _ask(client, 'más información', history=history, scope=scope)


def _accepted(body):
    return MORE_LOGIN in body['reply'] and not body.get('reset')


def _rejected(body):
    return body.get('reset') is True and body['reply'] == NO_CONTEXT_REPLY


# ───────── Cambio de identidad en la misma pestaña y navegador ─────────
def test_history_created_while_anonymous_is_rejected_after_logging_in(world):
    browser = world['browser']
    history, anonymous_scope = _conversation(browser)
    assert _accepted(_follow_up(browser, history, anonymous_scope))
    _login(world)
    assert _scope(browser) != anonymous_scope
    assert _rejected(_follow_up(browser, history, anonymous_scope))


def test_an_authenticated_user_keeps_their_own_conversation(world):
    _login(world)
    history, scope = _conversation(world['browser'])
    assert _accepted(_follow_up(world['browser'], history, scope))


def test_logging_out_in_the_same_tab_invalidates_the_previous_users_history(world):
    browser = world['browser']
    _login(world)
    history, scope_a = _conversation(browser)
    assert _accepted(_follow_up(browser, history, scope_a))
    _logout(world)
    scope_after_logout = _scope(browser)
    assert scope_after_logout and scope_after_logout != scope_a          # antes de C1 era igual: el historial de A seguía valiendo
    body = _follow_up(browser, history, scope_a)
    assert _rejected(body) and body['scope'] == scope_after_logout
    assert MORE_LOGIN not in json.dumps(body, ensure_ascii=False)


def test_after_logout_the_anonymous_session_has_its_own_stable_scope_and_conversation(world):
    browser = world['browser']
    _login(world)
    _, scope_a = _conversation(browser)
    _logout(world)
    anonymous = _scope(browser)
    assert _scope(browser) == anonymous and anonymous != scope_a
    history, scope = _conversation(browser)
    assert scope == anonymous and _accepted(_follow_up(browser, history, scope))


def test_a_second_user_in_the_same_browser_cannot_reuse_the_first_users_context(world):
    browser = world['browser']
    _login(world, 'demo', 'Demo123!')
    history_a, scope_a = _conversation(browser)
    _logout(world)
    history_anon, scope_anon = _conversation(browser)                   # lo que escribió quien quedó sin sesión
    _login(world, 'bob', 'Clave-De-Prueba-1!')
    scope_b = _scope(browser)
    assert scope_b not in (scope_a, scope_anon)
    assert _rejected(_follow_up(browser, history_a, scope_a))
    assert _rejected(_follow_up(browser, history_anon, scope_anon))
    history_b, scope = _conversation(browser)
    assert scope == scope_b and _accepted(_follow_up(browser, history_b, scope))


def test_a_returning_user_cannot_reuse_a_context_from_an_earlier_session(world):
    browser = world['browser']
    _login(world, 'demo', 'Demo123!')
    history_old, scope_old = _conversation(browser)
    _logout(world)
    _login(world, 'bob', 'Clave-De-Prueba-1!')
    _logout(world)
    _login(world, 'demo', 'Demo123!')                                   # la misma persona vuelve a entrar
    assert _scope(browser) != scope_old
    assert _rejected(_follow_up(browser, history_old, scope_old))


def test_deleting_the_session_cookie_discards_the_previous_history(world):
    browser = world['browser']
    _login(world)
    history, scope = _conversation(browser)
    browser.delete_cookie('session')
    assert _scope(browser) != scope
    assert _rejected(_follow_up(browser, history, scope))


def test_changing_the_password_invalidates_the_session_and_the_history(world):
    browser = world['browser']
    _login(world)
    history, scope = _conversation(browser)
    with world['app'].app_context():
        user = User.query.filter_by(username='demo').one()
        user.password_changed_at = now_utc() + timedelta(seconds=5)
        db.session.commit()
    browser.get('/dashboard')                                           # Norte cierra la sesión que ya no es válida
    assert _rejected(_follow_up(browser, history, scope))


def test_normal_activity_and_rewritten_cookies_do_not_reset_the_conversation(world):
    browser = world['browser']
    _login(world)
    history, scope = _conversation(browser)
    for path in ('/dashboard', '/movements', '/accounts', '/personas'):
        browser.get(path)
        assert _scope(browser) == scope
    assert _accepted(_follow_up(browser, history, scope))


def test_the_scope_depends_on_the_identity_and_not_only_on_the_cookie(world):
    browser = world['browser']
    _scope(browser)                                                    # crea el token CSRF de esta cookie

    def scope_as(user_id):
        with browser.session_transaction() as browser_session:
            if user_id is None:
                browser_session.pop('_user_id', None)
            else:
                browser_session['_user_id'] = str(user_id)
                browser_session['_fresh'] = True
                browser_session['login_2fa_verified'] = True
        return _scope(browser)

    with world['app'].app_context():
        demo_id, bob_id = (User.query.filter_by(username=name).one().id for name in ('demo', 'bob'))
    anonymous, as_demo, as_bob = scope_as(None), scope_as(demo_id), scope_as(bob_id)
    assert len({anonymous, as_demo, as_bob}) == 3                      # misma cookie, tres identidades, tres scopes
    assert scope_as(demo_id) == as_demo and scope_as(None) == anonymous    # y cada una es estable


def test_a_scope_taken_from_another_users_session_is_not_accepted(world):
    app = world['app']
    alice = world['browser']
    _login(world)
    history, scope_alice = _conversation(alice)
    stranger = app.test_client()                                        # otro navegador (otro usuario) que intenta reutilizarlo
    _scope(stranger)
    assert _rejected(_follow_up(stranger, history, scope_alice))


def test_the_scope_is_a_keyed_hash_that_does_not_reveal_the_token_or_the_user(world):
    browser = world['browser']
    _login(world)
    body = browser.get('/api/nori/csrf').get_json()
    assert len(body['scope']) == 24 and re.fullmatch(r'[0-9a-f]{24}', body['scope'])
    assert body['csrf'].split('.')[0] not in body['scope'] and 'user' not in body['scope']
    source = Path(memory.__file__).read_text(encoding='utf-8')
    assert 'current_user' in source and 'hmac.new' in source and 'SECRET_KEY' in source


# ───────── Purga de la auditoría vencida ─────────
def _audit(app, action='NORI_CHAT', age=timedelta(0), ip='x'):
    with app.app_context():
        db.session.add(AuditLog(user_id=None, action=action, module='NORI', result='ATENDIDA', ip_local=ip, created_at=now_utc() - age))
        db.session.commit()


def _count(app, action='NORI_CHAT'):
    with app.app_context():
        return AuditLog.query.filter_by(action=action).count()


def test_chat_purges_expired_nori_rows_and_keeps_fresh_and_foreign_ones(app):
    app.config['NORI_PURGE_INTERVAL_SECONDS'] = 0
    _audit(app, age=timedelta(days=2))
    _audit(app, age=timedelta(hours=23))
    _audit(app, action='LOGIN', age=timedelta(days=30), ip='y')
    client = app.test_client()
    _ask(client, 'hola')
    assert _count(app) == 2                 # la de 23 h y la mensaje nuevo; la de 2 días se fue
    assert _count(app, 'LOGIN') == 1        # otras acciones no se tocan


def test_opening_the_chat_also_purges_expired_rows(app):
    app.config['NORI_PURGE_INTERVAL_SECONDS'] = 0
    _audit(app, age=timedelta(days=3))
    _audit(app, age=timedelta(hours=1))
    assert app.test_client().get('/api/nori/csrf').status_code == 200
    assert _count(app) == 1


def test_purge_runs_at_most_once_per_interval_unless_forced(app):
    app.config['NORI_PURGE_INTERVAL_SECONDS'] = 3600
    _audit(app, age=timedelta(days=2))
    with app.app_context():
        assert safety.purge_expired_audit() == 1
        db.session.add(AuditLog(user_id=None, action='NORI_CHAT', module='NORI', result='ATENDIDA', ip_local='z', created_at=now_utc() - timedelta(days=5)))
        db.session.commit()
        assert safety.purge_expired_audit() == 0                      # dentro del intervalo: no escribe
        assert safety.purge_expired_audit(force=True) == 1            # forzada: sí


def test_a_purge_failure_never_breaks_the_chat_or_the_token_endpoint(app, monkeypatch):
    class DatabaseDown:
        class _Raise:
            def __get__(self, instance, owner):
                raise RuntimeError('base de datos caída')
        query = _Raise()

    client = app.test_client()
    monkeypatch.setattr(safety, 'AuditLog', DatabaseDown)
    assert client.get('/api/nori/csrf').status_code == 200
    assert _ask(client, 'hola')['reply']


def test_maintenance_command_counts_and_removes_expired_nori_audit_rows(app):
    _audit(app, age=timedelta(days=4))
    _audit(app, age=timedelta(hours=2))
    _audit(app, action='CUSTOMER_REGISTERED', age=timedelta(days=40), ip='r')
    runner = app.test_cli_runner()
    dry = runner.invoke(args=['purge-nori-audit', '--dry-run'])
    assert dry.exit_code == 0 and '1 registro(s) de Nori superan las 24 horas (no se borró nada)' in dry.output and _count(app) == 2
    real = runner.invoke(args=['purge-nori-audit'])
    assert real.exit_code == 0 and '1 registro(s) de Nori eliminados' in real.output
    assert _count(app) == 1 and _count(app, 'CUSTOMER_REGISTERED') == 1


def test_retention_is_documented_without_promising_an_exact_deletion_time():
    doc = safety.__doc__
    assert 'no hay una tarea programada' in doc and 'hasta la siguiente solicitud' in doc
    assert 'exactamente' not in doc


# ───────── Zona de clic y salida de Nori durante el scroll (C3 y C4) ─────────
def test_only_the_character_and_the_open_panel_capture_clicks():
    css = (STATIC / 'nori.css').read_text(encoding='utf-8')
    root = re.search(r'\.nori \{\n  --n-ink.*?\n\}', css, re.S).group(0)
    assert 'pointer-events: none' in root
    assert re.search(r'\.nori__mascot \{\s*pointer-events: none', css)
    assert re.search(r'\.nori__hit \{[^}]*pointer-events: auto', css)
    assert re.search(r'\.nori__panel \{ pointer-events: auto; \}', css)


def test_hit_area_covers_the_whole_opaque_body_of_every_pose():
    pytest.importorskip('PIL')
    from PIL import Image

    css = (STATIC / 'nori.css').read_text(encoding='utf-8')
    box = re.search(r'\.nori__hit \{\s*position: absolute; top: (\d+)%; right: (\d+)%; bottom: (\d+)%; left: (\d+)%;', css)
    top, right, bottom, left = (int(value) for value in box.groups())
    for image in sorted((STATIC / 'web').glob('nori-*.png')):
        picture = Image.open(image).convert('RGBA')
        width, height = picture.size
        x0, y0, x1, y1 = picture.split()[3].point(lambda value: 255 if value > 24 else 0).getbbox()
        assert x0 / width * 100 >= left - 1 and (width - x1) / width * 100 >= right - 1, image.name
        assert y0 / height * 100 >= top - 1 and (height - y1) / height * 100 >= bottom - 1, image.name
    assert (100 - left - right) * (100 - top - bottom) < 7000          # mucho menos que el cuadrado completo (10 000)


def test_scroll_tuck_is_wired_accessible_and_respects_reduced_motion():
    css = (STATIC / 'nori.css').read_text(encoding='utf-8')
    js = (STATIC / 'nori.js').read_text(encoding='utf-8')
    assert re.search(r'\.nori\.is-tucked \{ translate: calc\(100% \+ \d+px\) 0; \}', css)
    reduced = css[css.index('@media (prefers-reduced-motion: reduce)'):]
    assert '.nori { transition: none; }' in reduced
    assert "addEventListener('scroll'" in js and '{ passive: true }' in js
    assert 'root.contains(document.activeElement)' in js and "root.matches(':hover')" in js
    assert "root.addEventListener('focusin', untuck)" in js and 'untuck();' in js
    assert re.search(r'setTimeout\(untuck, \d+\)', js)
    assert 'aria-hidden' in js and "hit.setAttribute('aria-hidden', 'true')" in js     # la zona de clic no duplica al botón accesible
    assert 'display: none' not in css[css.index('.nori.is-tucked'):css.index('.nori.is-tucked') + 120]   # sigue en el árbol de accesibilidad
