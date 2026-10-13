"""Roles administrativos (ADMIN_GENERAL, ADMINISTRADOR y CLIENTE), permisos, cuentas privilegiadas y aprovisionamiento inicial.

Todo con cuentas de prueba sintéticas, SQLite en memoria y correo simulado: no hay credenciales reales, ni red, ni ``data/``.
La autorización se prueba **en el servidor** (accesos directos a URL, solicitudes manipuladas, sesiones abiertas), no solo la interfaz.
"""
import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.models import (Account, AdminActivationToken, AdminChangeLog, AdminPermission, AuditLog, BankCard, Beneficiary, LoginChallenge, Transaction, User,
                        db, now_utc)
from app.routes import admin_accounts
from app.services import admin_service as admins
from fraud_helpers import PASSWORD, make_beneficiary, make_client, session_client
from test_fraud_admin import _hold, _incident, _restriction

NEW_PASSWORD = 'Admin2026.'                                                       # cumple la política: 8-10 caracteres, mayúscula, minúscula, número y punto
FRAUD_PERMISSIONS = (admins.P_FRAUD_VIEW, admins.P_FRAUD_INVESTIGATE, admins.P_FRAUD_DECIDE, admins.P_FRAUD_HOLDS, admins.P_FRAUD_RESTRICTIONS, admins.P_FRAUD_EXPORT)


@pytest.fixture
def world(monkeypatch):
    app = create_app({'SECRET_KEY': 'clave-de-pruebas-' + 'k' * 30})
    sent, codes = [], []
    monkeypatch.setattr(admin_accounts, 'send_admin_activation_link', lambda recipient, link, hours: sent.append((recipient, link)))
    monkeypatch.setattr('app.routes.send_login_code', lambda recipient, code: codes.append((recipient, code)))
    with app.app_context():
        ids = {}
        ids['general'], _ = make_client('gen_uno', '71000001', '710000000001', role='ADMIN_GENERAL')
        ids['general2'], _ = make_client('gen_dos', '71000002', '710000000002', role='ADMIN_GENERAL')
        ids['admin'], _ = make_client('adm_total', '71000003', '710000000003', role='ADMINISTRADOR')                              # todos los permisos
        ids['bare'], _ = make_client('adm_sin', '71000004', '710000000004', role='ADMINISTRADOR', permissions=())                 # ninguno
        ids['viewer'], _ = make_client('adm_ver', '71000005', '710000000005', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_VIEW,))
        ids['support'], _ = make_client('soporte1', '71000006', '710000000006', role='SOPORTE')
        ids['a'], accounts_a = make_client('cliente_a', '71000007', '710000000007', balance='500.00')
        ids['b'], accounts_b = make_client('cliente_b', '71000008', '710000000008', balance='700.00')
    clients = {name: session_client(app, user_id) for name, user_id in ids.items()}
    return {'app': app, 'ids': ids, 'clients': clients, 'sent': sent, 'codes': codes, 'account_a': accounts_a, 'account_b': accounts_b,
            'customer': ids['a'], 'account': accounts_a, 'anon': app.test_client()}


def csrf(client, path='/admin'):
    page = client.get(path)
    assert page.status_code == 200, (path, page.status_code)
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def user(world, name):
    return db.session.get(User, world['ids'][name])


def token_from(link):
    return link.rsplit('/', 1)[1]


def create_via_http(world, *, name='Ana Pérez', email='ana.admin@example.test', permissions=(), password=PASSWORD, typed=None):
    general = world['clients']['general']
    return general.post('/admin/cuentas/nueva', data={'csrf_token': csrf(general, '/admin/cuentas/nueva'), 'full_name': name, 'email': email, 'permisos': list(permissions),
                                                      'password': password, 'confirmacion': email if typed is None else typed})


# ───────── 1. ADMIN_GENERAL accede a los paneles y gestiona cuentas ─────────

@pytest.mark.parametrize('path', ['/admin', '/admin/cuentas', '/admin/cuentas/nueva', '/admin/cuentas/historial', '/admin/fraude', '/admin/fraude/restricciones',
                                  '/admin/fraude/retenciones', '/admin/fraude/auditoria', '/admin/capturas', '/admin/fraude/exportar/evaluaciones.csv',
                                  '/admin/fraude/exportar/auditoria.csv'])
def test_the_general_administrator_reaches_every_authorized_panel(world, path):
    assert world['clients']['general'].get(path).status_code == 200


def test_the_general_administrator_creates_an_admin_who_defines_their_own_password_and_is_then_activated(world):
    # Las peticiones HTTP van FUERA de cualquier app_context abierto: dentro, Flask reutiliza ``g`` y una sesión «se heredaría» entre clientes.
    response = create_via_http(world, permissions=[admins.P_FRAUD_VIEW, admins.P_FRAUD_INVESTIGATE])
    assert response.status_code == 302 and '/admin/cuentas/' in response.headers['Location']
    (recipient, link), = world['sent']
    assert recipient == 'ana.admin@example.test' and '/admin/activar/' in link
    token = token_from(link)
    with world['app'].app_context():
        created = User.query.filter_by(email='ana.admin@example.test').one()
        created_id, created_username = created.id, created.username
        assert created.role == 'ADMINISTRADOR' and created.account_status == 'PENDIENTE' and created.password_changed_at is None
        assert created.username.startswith('adm-') and not created.check_password(PASSWORD)                     # nadie conoce su contraseña
        assert sorted(p.permission for p in AdminPermission.query.filter_by(user_id=created.id)) == sorted([admins.P_FRAUD_INVESTIGATE, admins.P_FRAUD_VIEW])
        # el token solo existe en el correo: en la base solo está su HMAC
        assert not AdminActivationToken.query.filter_by(token_hash=token).count() and AdminActivationToken.query.filter_by(token_hash=admins.token_hash(token)).count() == 1
    # activación: la persona elige su contraseña (la política se exige) y la cuenta sigue PENDIENTE hasta que un general la active
    anon = world['anon']
    page = anon.get(f'/admin/activar/{token}')
    assert page.status_code == 200
    # la página debe verse como las demás pantallas de acceso: con la hoja de estilos de autenticación y la política de contraseña
    html = page.get_data(as_text=True)
    assert 'css/auth.css' in html and 'js/auth.js' in html and 'js/password-policy.js' in html
    assert 'css/auth.css' in anon.get('/admin/activar/token-invalido').get_data(as_text=True)
    bad = anon.post(f'/admin/activar/{token}', data={'csrf_token': csrf(anon, f'/admin/activar/{token}'), 'password': 'corta', 'password_confirmation': 'corta'})
    assert bad.status_code == 400
    ok = anon.post(f'/admin/activar/{token}', data={'csrf_token': csrf(anon, f'/admin/activar/{token}'), 'password': NEW_PASSWORD, 'password_confirmation': NEW_PASSWORD})
    assert ok.status_code == 302 and '/login' in ok.headers['Location']
    with world['app'].app_context():
        created = db.session.get(User, created_id)
        assert created.check_password(NEW_PASSWORD) and created.account_status == 'PENDIENTE' and created.password_changed_at is not None
    again = anon.post(f'/admin/activar/{token}', data={'csrf_token': csrf(anon, '/login'), 'password': NEW_PASSWORD, 'password_confirmation': NEW_PASSWORD})
    assert again.status_code in (400, 403)                                                                      # el enlace es de un solo uso
    general = world['clients']['general']
    activate = general.post(f'/admin/cuentas/{created_id}/estado', data={'csrf_token': csrf(general, '/admin/cuentas/nueva'), 'accion': 'activar',
                                                                         'password': PASSWORD, 'confirmacion': created_username})
    assert activate.status_code == 302
    with world['app'].app_context():
        assert db.session.get(User, created_id).account_status == 'ACTIVA'


def test_the_full_login_of_an_activated_admin_uses_the_email_code_and_opens_only_what_is_granted(world):
    with world['app'].app_context():
        created, token = admins.create_admin(user(world, 'general'), full_name='Luis Gómez', email='luis@example.test', permissions=[admins.P_FRAUD_VIEW])
        db.session.commit()
        assert admins.complete_activation(token, NEW_PASSWORD) is not None
        admins.set_status(user(world, 'general'), created, 'activar')
        db.session.commit()
    client = world['app'].test_client()
    response = client.post('/login', data={'csrf_token': csrf(client, '/login'), 'username': 'luis@example.test', 'password': NEW_PASSWORD})
    assert response.status_code == 302 and '/login/verify' in response.headers['Location'] and len(world['codes']) == 1
    verify = client.post('/login/verify', data={'csrf_token': csrf(client, '/login/verify'), 'code': world['codes'][0][1]})
    assert verify.status_code == 302 and verify.headers['Location'].endswith('/admin')
    assert client.get('/admin').status_code == 200 and client.get('/admin/fraude').status_code == 200
    assert client.get('/admin/cuentas').status_code == 403 and client.get('/admin/fraude/auditoria').status_code == 403 and client.get('/admin/capturas').status_code == 403


def test_a_pending_account_cannot_activate_before_the_person_defines_a_password(world):
    with world['app'].app_context():
        created, _ = admins.create_admin(user(world, 'general'), full_name='Sin Contraseña', email='sc@example.test')
        db.session.commit()
        with pytest.raises(admins.AdminError, match='aún no definió su contraseña'):
            admins.set_status(user(world, 'general'), created, 'activar')


# ───────── 2. ADMIN no crea administradores ni eleva privilegios ─────────

def test_an_admin_with_every_permission_still_cannot_manage_administrators(world):
    admin = world['clients']['admin']
    token = csrf(admin)
    target = world['ids']['bare']
    attempts = [('get', '/admin/cuentas'), ('get', '/admin/cuentas/nueva'), ('get', '/admin/cuentas/historial'), ('get', f'/admin/cuentas/{target}'),
                ('post', '/admin/cuentas/nueva'), ('post', f'/admin/cuentas/{target}/permisos'), ('post', f'/admin/cuentas/{target}/estado'),
                ('post', f'/admin/cuentas/{target}/sesiones'), ('post', f'/admin/cuentas/{target}/reenviar'), ('post', f'/admin/cuentas/{target}/restablecer'), ('post', f"/admin/cuentas/{world['ids']['admin']}/permisos")]
    for method, path in attempts:
        forged = {'csrf_token': token, 'password': PASSWORD, 'confirmacion': 'adm_sin', 'accion': 'suspender', 'permisos': [admins.P_AUDIT], 'full_name': 'Intruso',
                  'email': 'intruso@example.test', 'role': 'ADMIN_GENERAL'}
        response = getattr(admin, method)(path, data=forged) if method == 'post' else admin.get(path)
        assert response.status_code == 403, (method, path)
    with world['app'].app_context():
        assert User.query.filter_by(email='intruso@example.test').count() == 0 and user(world, 'admin').role == 'ADMINISTRADOR'
        assert not AdminPermission.query.filter_by(user_id=world['ids']['bare']).count() and user(world, 'bare').account_status == 'ACTIVA'


def test_the_service_layer_also_refuses_an_admin_who_tries_to_elevate_anyone_including_themselves(world):
    with world['app'].app_context():
        admin, bare = user(world, 'admin'), user(world, 'bare')
        for call in (lambda: admins.create_admin(admin, full_name='X Y', email='x@example.test'),
                     lambda: admins.set_permissions(admin, admin, [admins.P_AUDIT]),
                     lambda: admins.set_permissions(admin, bare, [admins.P_AUDIT]),
                     lambda: admins.set_status(admin, bare, 'suspender'),
                     lambda: admins.revoke_sessions_of(admin, bare),
                     lambda: admins.resend_activation(admin, bare),
                     lambda: admins.reset_access(admin, bare)):
            with pytest.raises(admins.AdminPermissionError):
                call()
        db.session.rollback()
        assert admins.effective_permissions(user(world, 'bare')) == set()


def test_the_general_administrator_cannot_change_their_own_permissions_status_or_sessions(world):
    with world['app'].app_context():
        general = user(world, 'general')
        for call in (lambda: admins.set_permissions(general, general, [admins.P_AUDIT]), lambda: admins.set_status(general, general, 'suspender'),
                     lambda: admins.set_status(general, general, 'desactivar'), lambda: admins.revoke_sessions_of(general, general),
                     lambda: admins.reset_access(general, general)):
            with pytest.raises(admins.AdminPermissionError, match='tu propia cuenta'):
                call()


def test_a_customer_account_can_never_be_promoted_through_any_admin_path(world):
    general = world['clients']['general']
    token = csrf(general, '/admin/cuentas/nueva')
    for suffix in ('', '/permisos', '/estado', '/sesiones', '/reenviar', '/restablecer'):
        response = general.post(f"/admin/cuentas/{world['ids']['a']}{suffix}", data={'csrf_token': token, 'password': PASSWORD, 'confirmacion': 'cliente_a',
                                                                                    'accion': 'activar', 'permisos': [admins.P_AUDIT]}) if suffix else general.get(f"/admin/cuentas/{world['ids']['a']}")
        assert response.status_code == 404                                                                                 # un cliente no se distingue de un id inexistente
    with world['app'].app_context():
        assert user(world, 'a').role == 'CLIENTE' and not AdminPermission.query.filter_by(user_id=world['ids']['a']).count()
        with pytest.raises(admins.AdminError):
            admins.set_permissions(user(world, 'general'), world['ids']['a'], [admins.P_AUDIT])
        with pytest.raises(admins.AdminError, match='Ya existe una cuenta con ese correo'):
            admins.create_admin(user(world, 'general'), full_name='Otra Persona', email='CLIENTE_A@example.test')   # no se reutiliza el correo de un cliente


# ───────── 3 y 4. CLIENTE y accesos manipulados ─────────

def _admin_rules(app):
    rules = [rule for rule in app.url_map.iter_rules() if rule.rule.startswith('/admin') and not rule.rule.startswith('/admin/activar')]
    assert len(rules) >= 25                                                                                                    # el barrido cubre todas las rutas administrativas
    return rules


def _filled(rule):
    path = re.sub(r'<int:[a-z_]+>', '1', rule.rule)
    return path.replace('<dataset>', 'evaluaciones').replace('<version>', 'iforest-20260101000000000000')


@pytest.mark.parametrize('who', ['a', 'support'])
def test_customers_and_support_are_denied_on_every_administrative_route_for_every_method(world, who):
    client = world['clients'][who]
    token = csrf(client, '/dashboard' if who == 'a' else '/support')
    for rule in _admin_rules(world['app']):
        for method in sorted(rule.methods & {'GET', 'POST'}):
            response = client.get(_filled(rule)) if method == 'GET' else client.post(_filled(rule), data={'csrf_token': token, 'password': PASSWORD})
            assert response.status_code == 403, (who, method, rule.rule)


def test_anonymous_visitors_are_sent_to_login_on_every_administrative_route(world):
    client = world['anon']
    token = csrf(client, '/login')
    for rule in _admin_rules(world['app']):
        for method in sorted(rule.methods & {'GET', 'POST'}):
            response = client.get(_filled(rule)) if method == 'GET' else client.post(_filled(rule), data={'csrf_token': token})
            assert response.status_code in (302, 401) and (response.status_code == 401 or '/login' in response.headers['Location']), (method, rule.rule)


def test_admin_without_any_permission_only_sees_the_general_overview(world):
    client = world['clients']['bare']
    assert client.get('/admin').status_code == 200
    token = csrf(client)
    for rule in _admin_rules(world['app']):
        if rule.rule == '/admin':
            continue
        for method in sorted(rule.methods & {'GET', 'POST'}):
            response = client.get(_filled(rule)) if method == 'GET' else client.post(_filled(rule), data={'csrf_token': token})
            assert response.status_code == 403, (method, rule.rule)


def test_a_view_only_admin_reads_the_fraud_panel_but_cannot_act_export_or_see_other_panels(world):
    client = world['clients']['viewer']
    for path in ('/admin/fraude', '/admin/fraude/restricciones', '/admin/fraude/retenciones'):
        assert client.get(path).status_code == 200, path
    assert client.get('/admin/fraude/modelos/iforest-20260101000000000000').status_code == 404                             # permitido; solo que la versión no existe
    token = csrf(client)
    for path in ('/admin/fraude/auditoria', '/admin/fraude/exportar/evaluaciones.csv', '/admin/fraude/exportar/auditoria.csv', '/admin/capturas', '/admin/cuentas'):
        assert client.get(path).status_code == 403, path
    for path in ('/admin/fraude/incidentes/1/investigar', '/admin/fraude/incidentes/1/comentar', '/admin/fraude/incidentes/1/descartar',
                 '/admin/fraude/incidentes/1/confirmar', '/admin/fraude/restricciones/1/levantar', '/admin/fraude/retenciones/1/autorizar', '/admin/fraude/retenciones/1/rechazar'):
        assert client.post(path, data={'csrf_token': token, 'motivo': 'x' * 20}).status_code == 403, path


def test_every_mutating_administrative_route_is_known_and_none_can_edit_or_delete_the_audit(world):
    mutating = {rule.rule for rule in _admin_rules(world['app']) if rule.methods & {'POST', 'PUT', 'PATCH', 'DELETE'}}
    assert mutating == {'/admin/cuentas/nueva', '/admin/cuentas/<int:user_id>/permisos', '/admin/cuentas/<int:user_id>/estado', '/admin/cuentas/<int:user_id>/sesiones',
                        '/admin/cuentas/<int:user_id>/reenviar', '/admin/cuentas/<int:user_id>/restablecer', '/admin/fraude/incidentes/<int:incident_id>/investigar', '/admin/fraude/incidentes/<int:incident_id>/comentar',
                        '/admin/fraude/incidentes/<int:incident_id>/descartar', '/admin/fraude/incidentes/<int:incident_id>/confirmar',
                        '/admin/fraude/restricciones/<int:restriction_id>/levantar', '/admin/fraude/retenciones/<int:hold_id>/autorizar',
                        '/admin/fraude/retenciones/<int:hold_id>/rechazar'}                                                       # añadir una ruta obliga a revisar esta lista
    for rule in _admin_rules(world['app']):
        assert not rule.methods & {'PUT', 'PATCH', 'DELETE'}, rule.rule
    general = world['clients']['general']
    for path in ('/admin/cuentas/historial', '/admin/fraude/auditoria'):
        assert general.post(path, data={'csrf_token': csrf(general)}).status_code == 405                                       # la auditoría y el historial son de solo lectura


def test_forged_and_malformed_requests_are_rejected_without_changing_anything(world):
    general = world['clients']['general']
    token = csrf(general, '/admin/cuentas/nueva')
    target = world['ids']['bare']
    base = {'csrf_token': token, 'password': PASSWORD, 'confirmacion': 'adm_sin'}
    with world['app'].app_context():
        before = (AdminChangeLog.query.count(), user(world, 'bare').account_status, stored(world, 'bare'))
        users_before = User.query.count()
    # sin CSRF
    assert general.post(f'/admin/cuentas/{target}/estado', data={k: v for k, v in base.items() if k != 'csrf_token'} | {'accion': 'suspender'}).status_code == 400
    # CSRF ajeno/falso
    assert general.post(f'/admin/cuentas/{target}/estado', data=base | {'csrf_token': 'falso', 'accion': 'suspender'}).status_code == 400
    # contraseña incorrecta, confirmación incorrecta, acción desconocida, permiso desconocido
    for data in (base | {'password': 'no-es', 'accion': 'suspender'}, base | {'confirmacion': 'otra', 'accion': 'suspender'}, base | {'accion': 'borrar-todo'},
                 base | {'accion': 'suspender; DROP TABLE users'}, base | {'permisos': ['fraude.consultar', 'root']}):
        general.post(f'/admin/cuentas/{target}/' + ('permisos' if 'permisos' in data else 'estado'), data=data)
    with world['app'].app_context():
        assert (AdminChangeLog.query.count(), user(world, 'bare').account_status, stored(world, 'bare')) == before
        assert User.query.count() == users_before


def stored(world, name):
    return sorted(p.permission for p in AdminPermission.query.filter_by(user_id=world['ids'][name]))


# ───────── 5. Un cliente no ve los datos de otro ─────────

def test_a_customer_cannot_read_or_change_another_customers_banking_data(world):
    with world['app'].app_context():
        tx = Transaction(account_id=world['account_a'], operation_type='PAGO', description='Pago de prueba', amount=Decimal('-10.00'), balance_after=Decimal('490.00'))
        card = BankCard(user_id=world['ids']['a'], account_id=world['account_a'], last4='1111', limit_amount=Decimal('1000'))
        db.session.add_all([tx, card])
        db.session.commit()
        contact = make_beneficiary(world['ids']['a'], '710000000007', name='Contacto de A')
        tx_id, card_id, card_status = tx.id, card.id, card.status
    b = world['clients']['b']
    token = csrf(b, '/dashboard')
    a_account = world['account_a']
    for path in (f'/receipt/{tx_id}', f'/api/v1/accounts/{a_account}/transactions', f'/api/v1/accounts/{a_account}/transfers', f'/statements/{a_account}.pdf'):
        assert b.get(path).status_code in (403, 404), path
    for path in (f'/accounts/{a_account}/deposit', f'/cards/{card_id}/status', f'/contacts/{contact}/delete'):
        assert b.post(path, data={'csrf_token': token, 'amount': '10', 'status': 'BLOQUEADA'}).status_code in (403, 404), path
    with world['app'].app_context():
        assert db.session.get(Account, a_account).balance == Decimal('500.00') and db.session.get(BankCard, card_id).status == card_status
        assert db.session.get(Beneficiary, contact) is not None
    listed = b.get('/api/v1/accounts').get_json()
    assert 'cliente_a' not in str(listed) and '710000000007' not in str(listed)
    assert world['clients']['a'].get(f'/receipt/{tx_id}').status_code == 200                                                   # el dueño sí lo ve


def test_administrators_cannot_use_the_customer_banking_functions(world):
    for who in ('general', 'admin', 'bare', 'support'):
        client = world['clients'][who]
        for path in ('/dashboard', '/accounts', '/contacts', '/operation/transfer'):
            assert client.get(path).status_code in (302, 403), (who, path)
            assert client.get(path).status_code != 200, (who, path)


# ───────── 6. Los cambios quedan registrados ─────────

def test_every_role_permission_status_and_session_change_is_recorded_with_actor_target_and_no_secrets(world):
    with world['app'].app_context():
        general, bare = user(world, 'general'), user(world, 'bare')
        admins.set_permissions(general, bare, [admins.P_FRAUD_VIEW, admins.P_AUDIT])
        admins.set_permissions(general, bare, [admins.P_AUDIT])
        admins.set_status(general, bare, 'suspender')
        admins.set_status(general, bare, 'reactivar')
        admins.revoke_sessions_of(general, bare)
        created, _ = admins.create_admin(general, full_name='Nuevo Admin', email='nuevo@example.test', permissions=[admins.P_FRAUD_EXPORT])
        db.session.commit()
        changes = AdminChangeLog.query.order_by(AdminChangeLog.id).all()
        assert [c.action for c in changes] == ['PERMISOS_ACTUALIZADOS', 'PERMISOS_ACTUALIZADOS', 'ESTADO_SUSPENDIDA', 'ESTADO_ACTIVA', 'SESIONES_REVOCADAS', 'CUENTA_CREADA']
        assert all(c.actor_id == general.id for c in changes) and [c.target_id for c in changes] == [bare.id] * 5 + [created.id]
        assert changes[1].detail == {'antes': [admins.P_AUDIT, admins.P_FRAUD_VIEW], 'despues': [admins.P_AUDIT]}
        assert changes[2].detail == {'accion': 'suspender', 'antes': 'ACTIVA', 'despues': 'SUSPENDIDA'}
        blob = str([c.detail for c in changes]).lower()
        assert 'password' not in blob and 'token' not in blob and 'clave' not in blob and 'nuevo@example.test' not in blob
        audit = [a.action for a in AuditLog.query.filter(AuditLog.module == 'ADMIN').order_by(AuditLog.id)]
        assert audit == ['ADMIN_' + c.action for c in changes]
    page = world['clients']['general'].get('/admin/cuentas/historial').get_data(as_text=True)
    for action in ('PERMISOS_ACTUALIZADOS', 'ESTADO_SUSPENDIDA', 'SESIONES_REVOCADAS', 'CUENTA_CREADA', 'gen_uno', 'adm_sin'):
        assert action in page
    assert 'ADMIN_ESTADO_SUSPENDIDA' in page                                       # también se ve la actividad auditada de otros administradores


def test_failed_confirmations_are_audited_and_then_rate_limited(world):
    general = world['clients']['general']
    token = csrf(general, '/admin/cuentas/nueva')
    target = world['ids']['bare']
    for _ in range(world['app'].config['ADMIN_STEPUP_MAX_FAILURES']):
        general.post(f'/admin/cuentas/{target}/estado', data={'csrf_token': token, 'accion': 'suspender', 'password': 'mala', 'confirmacion': 'adm_sin'})
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='ADMIN_STEPUP_FAILED').count() == world['app'].config['ADMIN_STEPUP_MAX_FAILURES']
    general.post(f'/admin/cuentas/{target}/estado', data={'csrf_token': token, 'accion': 'suspender', 'password': PASSWORD, 'confirmacion': 'adm_sin'})   # ya correcta, pero bloqueada
    with world['app'].app_context():
        assert user(world, 'bare').account_status == 'ACTIVA'
        window = now_utc() - timedelta(seconds=world['app'].config['ADMIN_STEPUP_WINDOW_SECONDS'])
        AuditLog.query.filter(AuditLog.action == 'ADMIN_STEPUP_FAILED').update({'created_at': window - timedelta(seconds=1)})
        db.session.commit()
    ok = general.post(f'/admin/cuentas/{target}/estado', data={'csrf_token': token, 'accion': 'suspender', 'password': PASSWORD, 'confirmacion': 'adm_sin'})
    assert ok.status_code == 302
    with world['app'].app_context():
        assert user(world, 'bare').account_status == 'SUSPENDIDA'


# ───────── 7. Suspender o desactivar revoca las sesiones ─────────

@pytest.mark.parametrize('action,final', [('suspender', 'SUSPENDIDA'), ('desactivar', 'DESACTIVADA')])
def test_suspending_or_deactivating_an_account_ends_its_open_sessions_and_blocks_login(world, action, final):
    admin = world['clients']['admin']
    assert admin.get('/admin').status_code == 200
    general = world['clients']['general']
    response = general.post(f"/admin/cuentas/{world['ids']['admin']}/estado", data={'csrf_token': csrf(general, '/admin/cuentas/nueva'), 'accion': action, 'password': PASSWORD,
                                                                                   'confirmacion': 'adm_total'})
    assert response.status_code == 302
    with world['app'].app_context():
        target = user(world, 'admin')
        assert target.account_status == final and target.sessions_revoked_at is not None
    for _ in range(2):
        again = admin.get('/admin')
        assert again.status_code == 302 and '/login' in again.headers['Location']                                              # la sesión abierta ya no sirve
    fresh = world['app'].test_client()
    login = fresh.post('/login', data={'csrf_token': csrf(fresh, '/login'), 'username': 'adm_total', 'password': PASSWORD})
    assert login.status_code == 403 and not world['codes']                                                                    # ni siquiera se envía un código
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='LOGIN_BLOCKED_BY_ACCOUNT_STATUS').count() == 1
        assert LoginChallenge.query.filter_by(user_id=world['ids']['admin']).count() == 0


def test_revoking_sessions_keeps_the_account_active_and_lets_the_person_log_in_again(world):
    admin = world['clients']['admin']
    assert admin.get('/admin').status_code == 200
    with world['app'].app_context():
        admins.revoke_sessions_of(user(world, 'general'), user(world, 'admin'))
        db.session.commit()
        assert user(world, 'admin').account_status == 'ACTIVA'
    assert admin.get('/admin').status_code == 302
    assert session_client(world['app'], world['ids']['admin']).get('/admin').status_code == 200                              # una sesión nueva sí vale


def test_a_suspended_account_with_a_pending_login_code_cannot_finish_logging_in(world):
    client = world['app'].test_client()
    client.post('/login', data={'csrf_token': csrf(client, '/login'), 'username': 'adm_total', 'password': PASSWORD})
    assert len(world['codes']) == 1
    token = csrf(client, '/login/verify')                                                       # la página de verificación que ya tenía abierta
    with world['app'].app_context():
        admins.set_status(user(world, 'general'), user(world, 'admin'), 'suspender')
        db.session.commit()
    verify = client.post('/login/verify', data={'csrf_token': token, 'code': world['codes'][0][1]})
    assert verify.status_code == 302 and '/login' in verify.headers['Location']                                              # el código pendiente ya no sirve
    assert client.get('/admin').status_code == 302 and '/login' in client.get('/admin').headers['Location']
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='LOGIN', user_id=world['ids']['admin']).count() == 0                          # nunca llegó a iniciar sesión


def test_login_does_not_reveal_the_status_of_an_account_when_the_password_is_wrong(world):
    with world['app'].app_context():
        admins.set_status(user(world, 'general'), user(world, 'bare'), 'suspender')
        db.session.commit()
    client = world['app'].test_client()
    wrong = client.post('/login', data={'csrf_token': csrf(client, '/login'), 'username': 'adm_sin', 'password': 'incorrecta'})
    assert wrong.status_code == 200 and 'Usuario o clave incorrectos' in wrong.get_data(as_text=True) and 'habilitada' not in wrong.get_data(as_text=True)


# ───────── 8. Nunca se deja el sistema sin ADMIN_GENERAL activo ─────────

def test_the_last_active_general_administrator_cannot_be_suspended_or_deactivated(world):
    with world['app'].app_context():
        general, other = user(world, 'general'), user(world, 'general2')
        assert admins.set_status(general, other, 'suspender') == 'SUSPENDIDA'                                                 # hay otro general activo: se puede
        db.session.commit()
        with pytest.raises(admins.AdminError, match='último Administrador General activo'):
            admins.ensure_another_active_general(general)                                                                     # ahora «general» es el único activo
        # incluso si una condición de carrera dejara actuar a un general ya suspendido, la guarda del servicio lo impide
        other.account_status = 'ACTIVA'
        general.account_status = 'SUSPENDIDA'
        db.session.commit()
        for action in ('suspender', 'desactivar'):
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(admins, 'require_general', lambda actor: None)
                with pytest.raises(admins.AdminError, match='último Administrador General activo'):
                    admins.set_status(general, other, action)
        assert user(world, 'general2').account_status == 'ACTIVA'


def test_the_last_general_guard_query_is_valid_on_postgresql_not_just_sqlite(world):
    """Regresión real: ``SELECT count(*) ... FOR UPDATE`` falla en PostgreSQL (lo descubrió un ensayo en una base PostgreSQL desechable)."""
    from sqlalchemy.dialects import postgresql
    with world['app'].app_context():
        query = admins._other_active_generals_query(user(world, 'general'))
        sql = str(query.statement.compile(dialect=postgresql.dialect())).lower()
        assert 'for update' in sql and 'count(' not in sql                                           # bloquea filas y no mezcla FOR UPDATE con agregados
        assert [u.username for u in query.all()] == ['gen_dos']


def test_deactivation_is_final_and_a_suspended_general_cannot_manage_accounts(world):
    with world['app'].app_context():
        general, other = user(world, 'general'), user(world, 'general2')
        admins.set_status(general, other, 'suspender')
        db.session.commit()
        with pytest.raises(admins.AdminPermissionError):
            admins.create_admin(user(world, 'general2'), full_name='Quien Sea', email='q@example.test')                       # suspendido: sin poderes
        admins.set_status(general, user(world, 'bare'), 'desactivar')
        db.session.commit()
        with pytest.raises(admins.AdminError, match='No se puede reactivar'):
            admins.set_status(general, user(world, 'bare'), 'reactivar')
        with pytest.raises(admins.AdminError):
            admins.set_status(general, user(world, 'bare'), 'activar')
    assert world['clients']['general2'].get('/admin/cuentas').status_code == 302                                              # su sesión también se cerró


# ───────── 9. El panel antifraude conserva comportamiento y permisos ─────────

def test_fraud_actions_need_their_specific_permission_and_the_general_administrator_has_them_all(world):
    with world['app'].app_context():
        investigator, _ = make_client('adm_inv', '71000010', '710000000010', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_INVESTIGATE,))
        decider, _ = make_client('adm_dec', '71000011', '710000000011', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_DECIDE,))
        holder, _ = make_client('adm_ret', '71000012', '710000000012', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_HOLDS,))
        lifter, _ = make_client('adm_res', '71000013', '710000000013', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_RESTRICTIONS,))
        incident = _incident(world)
        hold = _hold(world, incident)
        restriction = _restriction(world)
    actors = {'inv': investigator, 'dec': decider, 'ret': holder, 'res': lifter}
    clients = {name: session_client(world['app'], user_id) for name, user_id in actors.items()}
    def post(client, path, **data):
        return client.post(path, data={'csrf_token': csrf(client, f'/admin/fraude/incidentes/{incident}'), 'motivo': 'Motivo suficientemente largo.', 'comentario': 'Comentario válido.', **data})
    # cada permiso abre solo su acción (los de acción implican poder consultar el panel)
    assert post(clients['inv'], f'/admin/fraude/incidentes/{incident}/investigar').status_code == 302
    assert post(clients['inv'], f'/admin/fraude/incidentes/{incident}/descartar').status_code == 403
    assert post(clients['dec'], f'/admin/fraude/incidentes/{incident}/investigar').status_code == 403
    assert post(clients['ret'], f'/admin/fraude/restricciones/{restriction}/levantar').status_code == 403
    assert post(clients['res'], f'/admin/fraude/retenciones/{hold}/autorizar').status_code == 403
    assert post(clients['res'], f'/admin/fraude/restricciones/{restriction}/levantar').status_code == 302
    assert post(clients['ret'], f'/admin/fraude/retenciones/{hold}/autorizar').status_code == 302
    assert post(clients['dec'], f'/admin/fraude/incidentes/{incident}/descartar').status_code == 302
    assert clients['ret'].get('/admin/fraude/exportar/retenciones.csv').status_code == 403 and clients['inv'].get('/admin/fraude/auditoria').status_code == 403
    with world['app'].app_context():
        assert admins.effective_permissions(user(world, 'general')) == set(admins.PERMISSIONS)
        assert admins.effective_permissions(user(world, 'bare')) == set()
        assert admins.P_FRAUD_VIEW in admins.effective_permissions(db.session.get(User, investigator))                      # investigar implica consultar


def test_the_audit_export_needs_the_audit_permission_in_addition_to_the_export_permission(world):
    with world['app'].app_context():
        exporter, _ = make_client('adm_exp', '71000014', '710000000014', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_EXPORT,))
        both, _ = make_client('adm_exp2', '71000015', '710000000015', role='ADMINISTRADOR', permissions=(admins.P_FRAUD_EXPORT, admins.P_AUDIT))
    only_export, both_client = session_client(world['app'], exporter), session_client(world['app'], both)
    assert only_export.get('/admin/fraude/exportar/evaluaciones.csv').status_code == 200
    assert only_export.get('/admin/fraude/exportar/auditoria.csv').status_code == 403
    assert both_client.get('/admin/fraude/exportar/auditoria.csv').status_code == 200


def test_captures_need_their_own_permission_and_every_view_is_audited(world):
    with world['app'].app_context():
        viewer, _ = make_client('adm_cap', '71000016', '710000000016', role='ADMINISTRADOR', permissions=(admins.P_CAPTURES,))
    allowed = session_client(world['app'], viewer)
    assert allowed.get('/admin/capturas').status_code == 200 and world['clients']['admin'].get('/admin/capturas').status_code == 200
    assert world['clients']['viewer'].get('/admin/capturas').status_code == 403 and world['clients']['bare'].get('/admin/capturas').status_code == 403
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='CAPTURE_LIST_VIEWED').count() == 2


# ───────── 10. Permisos: reglas del servicio ─────────

def test_granting_permissions_is_validated_and_action_permissions_imply_viewing(world):
    with world['app'].app_context():
        general, bare = user(world, 'general'), user(world, 'bare')
        with pytest.raises(admins.AdminError, match='Permiso desconocido'):
            admins.set_permissions(general, bare, ['fraude.consultar', 'root'])
        with pytest.raises(admins.AdminError, match='todos los permisos'):
            admins.set_permissions(general, user(world, 'general2'), [admins.P_AUDIT])
        admins.set_permissions(general, bare, [admins.P_FRAUD_HOLDS])
        db.session.commit()
        assert admins.effective_permissions(user(world, 'bare')) == {admins.P_FRAUD_HOLDS, admins.P_FRAUD_VIEW}
        admins.set_permissions(general, bare, [])
        db.session.commit()
        assert admins.effective_permissions(user(world, 'bare')) == set()
        assert not admins.has_permission(user(world, 'a'), admins.P_FRAUD_VIEW) and not admins.has_permission(user(world, 'support'), admins.P_FRAUD_VIEW)
        assert not admins.has_permission(None, admins.P_FRAUD_VIEW)


def test_a_suspended_admin_loses_every_permission_immediately(world):
    with world['app'].app_context():
        admin = user(world, 'admin')
        assert admins.has_permission(admin, admins.P_FRAUD_DECIDE)
        admins.set_status(user(world, 'general'), admin, 'suspender')
        db.session.commit()
        assert admins.effective_permissions(user(world, 'admin')) == set()


# ───────── 11. Alta: validaciones y enlace de activación ─────────

@pytest.mark.parametrize('name,email,message', [('A', 'ok@example.test', 'nombre'), ('Nombre Válido', 'no-es-correo', 'correo'), ('Nombre Válido', 'a@b', 'correo'),
                                                ('Nombre Válido', 'x' * 300 + '@example.test', 'correo')])
def test_account_creation_validates_name_and_email(world, name, email, message):
    with world['app'].app_context(), pytest.raises(admins.AdminError, match=message):
        admins.create_admin(user(world, 'general'), full_name=name, email=email)


def test_names_are_cleaned_html_is_escaped_and_duplicate_emails_are_refused(world):
    with world['app'].app_context():
        created, _ = admins.create_admin(user(world, 'general'), full_name='Ana\x00\n<script>alert(1)</script>', email='Ana.Mayus@Example.TEST')
        db.session.commit()
        assert '\x00' not in created.full_name and created.email == 'ana.mayus@example.test'
        with pytest.raises(admins.AdminError, match='Ya existe una cuenta con ese correo'):
            admins.create_admin(user(world, 'general'), full_name='Otra', email='ANA.MAYUS@example.test')
        created_id = created.id
    page = world['clients']['general'].get(f'/admin/cuentas/{created_id}').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in page and '&lt;script&gt;' in page


def test_an_account_is_not_created_when_the_activation_email_cannot_be_sent(world, monkeypatch):
    def failing(recipient, link, hours):
        raise admin_accounts.EmailDeliveryError('sin correo')
    monkeypatch.setattr(admin_accounts, 'send_admin_activation_link', failing)
    response = create_via_http(world, email='sincorreo@example.test')
    assert response.status_code == 503
    with world['app'].app_context():
        assert User.query.filter_by(email='sincorreo@example.test').count() == 0 and AdminChangeLog.query.filter_by(action='CUENTA_CREADA').count() == 0


def test_wrong_password_or_confirmation_does_not_create_the_account(world):
    with world['app'].app_context():
        assert create_via_http(world, email='uno@example.test', password='mala').status_code == 400
        assert create_via_http(world, email='dos@example.test', typed='otro@example.test').status_code == 400
        assert User.query.filter(User.email.in_(('uno@example.test', 'dos@example.test'))).count() == 0 and not world['sent']


def test_activation_links_expire_are_single_use_and_resending_invalidates_the_old_one(world):
    with world['app'].app_context():
        general = user(world, 'general')
        created, first = admins.create_admin(general, full_name='Enlace Prueba', email='enlace@example.test')
        db.session.commit()
        second = admins.resend_activation(general, created)
        db.session.commit()
        assert admins.find_valid_activation(first) is None and admins.find_valid_activation(second) is not None             # el anterior quedó sin efecto
        AdminActivationToken.query.filter_by(token_hash=admins.token_hash(second)).update({'expires_at': now_utc() - timedelta(seconds=1)})
        db.session.commit()
        assert admins.find_valid_activation(second) is None and admins.complete_activation(second, NEW_PASSWORD) is None
        assert admins.complete_activation('x' * 43, NEW_PASSWORD) is None and admins.complete_activation('corto', NEW_PASSWORD) is None
        third = admins.resend_activation(general, created)
        db.session.commit()
        assert admins.complete_activation(third, NEW_PASSWORD) is not None and admins.complete_activation(third, 'Otra2026.x') is None
        db.session.refresh(created)
        assert created.check_password(NEW_PASSWORD) and not created.check_password('Otra2026.x')


def test_an_activation_link_is_useless_once_the_account_is_not_pending(world):
    with world['app'].app_context():
        general = user(world, 'general')
        created, token = admins.create_admin(general, full_name='Cuenta Cerrada', email='cerrada@example.test')
        admins.set_status(general, created, 'desactivar')
        db.session.commit()
        assert admins.find_valid_activation(token) is None and admins.complete_activation(token, NEW_PASSWORD) is None
    anon = world['anon']
    assert anon.get(f'/admin/activar/{token}').status_code == 400


# ───────── 12. Aprovisionamiento inicial del primer ADMIN_GENERAL (flask admin-bootstrap) ─────────

@pytest.fixture
def clean():
    """Una aplicación sin ningún administrador y con el correo simulado: el escenario real del primer aprovisionamiento."""
    app = create_app({'SECRET_KEY': 'clave-de-pruebas-' + 'k' * 30})
    with app.app_context():
        AdminPermission.query.delete()
        User.query.filter(User.role.in_(('ADMINISTRADOR', 'ADMIN_GENERAL'))).delete()
        db.session.commit()
    return app


def test_bootstrap_requires_the_identity_confirmation_flag_and_an_explicit_yes(clean, monkeypatch):
    sent = []
    monkeypatch.setattr('app.services.email_service.send_admin_activation_link', lambda *a: sent.append(a))
    runner = clean.test_cli_runner()
    refused = runner.invoke(args=['admin-bootstrap', '--email', 'jefe@example.test', '--nombre', 'Jefa Principal'])
    assert refused.exit_code != 0 and '--confirmo-identidad' in refused.output and not sent
    declined = runner.invoke(args=['admin-bootstrap', '--email', 'jefe@example.test', '--nombre', 'Jefa Principal', '--confirmo-identidad'], input='n\n')
    assert declined.exit_code != 0 and not sent
    with clean.app_context():
        assert User.query.filter_by(role='ADMIN_GENERAL').count() == 0


def test_bootstrap_creates_a_pending_general_sends_a_link_and_never_prints_secrets(clean, monkeypatch):
    sent = []
    monkeypatch.setattr('app.services.email_service.send_admin_activation_link', lambda recipient, link, hours: sent.append((recipient, link, hours)))
    result = clean.test_cli_runner().invoke(args=['admin-bootstrap', '--email', 'Jefe@Example.test', '--nombre', 'Jefa Principal', '--confirmo-identidad'], input='y\n')
    assert result.exit_code == 0, result.output
    (recipient, link, hours), = sent
    assert recipient == 'jefe@example.test' and hours == 24 and '/admin/activar/' in link
    assert token_from(link) not in result.output and 'http' not in result.output and 'contraseña:' not in result.output.lower()
    with clean.app_context():
        general = User.query.filter_by(role='ADMIN_GENERAL').one()
        general_id = general.id
        assert general.account_status == 'PENDIENTE' and not general.check_password('')
    client = clean.test_client()
    page = client.get(f'/admin/activar/{token_from(link)}')
    assert page.status_code == 200
    done = client.post(f'/admin/activar/{token_from(link)}', data={'csrf_token': re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode(),
                                                                   'password': NEW_PASSWORD, 'password_confirmation': NEW_PASSWORD})
    assert done.status_code == 302
    with clean.app_context():
        general = db.session.get(User, general_id)
        assert general.account_status == 'ACTIVA' and general.check_password(NEW_PASSWORD)                                       # el aprovisionamiento inicial no necesita un segundo general
        actions = [c.action for c in AdminChangeLog.query.order_by(AdminChangeLog.id)]
        assert actions == ['ADMIN_GENERAL_APROVISIONADO', 'ACTIVADA_AL_DEFINIR_CONTRASENA']
    again = clean.test_cli_runner().invoke(args=['admin-bootstrap', '--email', 'otro@example.test', '--nombre', 'Otra Persona', '--confirmo-identidad'], input='y\n')
    assert again.exit_code != 0 and 'ya existe' in again.output.lower() and len(sent) == 1                                       # el aprovisionamiento no se repite


def test_bootstrap_never_promotes_an_existing_account(clean, monkeypatch):
    monkeypatch.setattr('app.services.email_service.send_admin_activation_link', lambda *a: None)
    with clean.app_context():
        make_client('cliente_real', '72000001', '720000000001', email='cliente@example.test')
    result = clean.test_cli_runner().invoke(args=['admin-bootstrap', '--email', 'cliente@example.test', '--nombre', 'Cliente Real', '--confirmo-identidad'], input='y\n')
    assert result.exit_code != 0 and 'no se promueven' in result.output
    with clean.app_context():
        assert User.query.filter_by(email='cliente@example.test').one().role == 'CLIENTE' and User.query.filter_by(role='ADMIN_GENERAL').count() == 0


def test_bootstrap_leaves_nothing_behind_when_the_email_cannot_be_sent(clean, monkeypatch):
    from app.services.email_service import EmailDeliveryError

    def failing(*args):
        raise EmailDeliveryError('sin correo')
    monkeypatch.setattr('app.services.email_service.send_admin_activation_link', failing)
    result = clean.test_cli_runner().invoke(args=['admin-bootstrap', '--email', 'jefe@example.test', '--nombre', 'Jefa Principal', '--confirmo-identidad'], input='y\n')
    assert result.exit_code != 0 and 'no se creó la cuenta' in result.output
    with clean.app_context():
        assert User.query.filter_by(email='jefe@example.test').count() == 0 and AdminChangeLog.query.count() == 0


def test_there_is_no_public_way_to_register_an_administrator(world):
    anon = world['anon']
    page = anon.get('/register').get_data(as_text=True)
    assert 'rol' not in page.lower().replace('control', '') and 'ADMIN' not in page
    token = re.search(rb'name="csrf_token" value="([^"]+)"', anon.get('/register').data).group(1).decode()
    anon.post('/register', data={'csrf_token': token, 'full_name': 'Intruso Total', 'dni': '73000001', 'email': 'intruso@example.test', 'role': 'ADMIN_GENERAL',
                                 'account_status': 'ACTIVA', 'password': NEW_PASSWORD, 'password_confirmation': NEW_PASSWORD})
    with world['app'].app_context():
        created = User.query.filter_by(email='intruso@example.test').first()
        assert created is None or created.role == 'CLIENTE'                                                                    # el campo de rol enviado se ignora


def test_the_demo_seed_administrator_keeps_working_and_is_a_plain_administrator(world):
    with world['app'].app_context():
        seed = User.query.filter_by(username='admin').one()
        assert seed.role == 'ADMINISTRADOR' and seed.is_seed and set(stored(world, 'bare')) == set()
        assert {p.permission for p in AdminPermission.query.filter_by(user_id=seed.id)} == set(admins.PERMISSIONS)
        assert User.query.filter_by(role='ADMIN_GENERAL', is_seed=True).count() == 0                                         # el seed nunca crea un Administrador General


# ───────── 13. Restablecer el acceso (contraseña olvidada o cuenta comprometida) ─────────

def test_resetting_access_kills_the_old_password_and_sessions_and_needs_the_person_and_a_general_again(world):
    admin_client, general = world['clients']['admin'], world['clients']['general']
    assert admin_client.get('/admin').status_code == 200
    response = general.post(f"/admin/cuentas/{world['ids']['admin']}/restablecer", data={'csrf_token': csrf(general, '/admin/cuentas/nueva'), 'password': PASSWORD, 'confirmacion': 'adm_total'})
    assert response.status_code == 302
    (recipient, link), = world['sent']
    assert recipient == 'adm_total@example.test' and '/admin/activar/' in link
    with world['app'].app_context():
        target = user(world, 'admin')
        assert target.account_status == 'PENDIENTE' and target.password_changed_at is None and target.sessions_revoked_at is not None
        assert not target.check_password(PASSWORD) and admins.effective_permissions(target) == set()                     # sin poderes mientras esté pendiente
        change = AdminChangeLog.query.filter_by(action='ACCESO_RESTABLECIDO').one()
        assert change.actor_id == world['ids']['general'] and change.target_id == target.id and change.detail == {'antes': 'ACTIVA'}
        with pytest.raises(admins.AdminError, match='aún no definió su contraseña'):
            admins.set_status(user(world, 'general'), target, 'activar')                                                 # no se puede reactivar con la contraseña vieja
    assert admin_client.get('/admin').status_code == 302                                                                 # su sesión abierta ya no sirve
    fresh = world['app'].test_client()
    old = fresh.post('/login', data={'csrf_token': csrf(fresh, '/login'), 'username': 'adm_total', 'password': PASSWORD})
    assert old.status_code == 200 and 'Usuario o clave incorrectos' in old.get_data(as_text=True) and not world['codes']   # la contraseña anterior ya no entra
    anon = world['anon']
    page = anon.get(f'/admin/activar/{token_from(link)}')
    done = anon.post(f'/admin/activar/{token_from(link)}', data={'csrf_token': re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode(),
                                                                'password': NEW_PASSWORD, 'password_confirmation': NEW_PASSWORD})
    assert done.status_code == 302
    with world['app'].app_context():
        assert user(world, 'admin').account_status == 'PENDIENTE'                                                        # definir la contraseña no basta
        admins.set_status(user(world, 'general'), user(world, 'admin'), 'activar')
        db.session.commit()
        assert user(world, 'admin').account_status == 'ACTIVA' and user(world, 'admin').check_password(NEW_PASSWORD)


def test_reset_access_is_refused_for_pending_accounts_and_never_leaves_the_system_without_a_general(world):
    with world['app'].app_context():
        general, other = user(world, 'general'), user(world, 'general2')
        pending, _ = admins.create_admin(general, full_name='Todavía Pendiente', email='pendiente@example.test')
        db.session.commit()
        with pytest.raises(admins.AdminError, match='activa o suspendida'):
            admins.reset_access(general, pending)
        admins.reset_access(general, other)                                                                              # hay otro general activo (general): se puede
        db.session.commit()
        assert user(world, 'general2').account_status == 'PENDIENTE'
        general.account_status = 'SUSPENDIDA'                                                                            # carrera: el actor ya no es el activo
        user(world, 'general2').account_status = 'ACTIVA'
        db.session.commit()
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(admins, 'require_general', lambda actor: None)
            with pytest.raises(admins.AdminError, match='último Administrador General activo'):
                admins.reset_access(general, user(world, 'general2'))
        assert user(world, 'general2').account_status == 'ACTIVA'


def test_a_failed_reset_email_changes_nothing(world, monkeypatch):
    def failing(recipient, link, hours):
        raise admin_accounts.EmailDeliveryError('sin correo')
    monkeypatch.setattr(admin_accounts, 'send_admin_activation_link', failing)
    general = world['clients']['general']
    response = general.post(f"/admin/cuentas/{world['ids']['admin']}/restablecer", data={'csrf_token': csrf(general, '/admin/cuentas/nueva'), 'password': PASSWORD,
                                                                                         'confirmacion': 'adm_total'})
    assert response.status_code == 302
    with world['app'].app_context():
        target = user(world, 'admin')
        assert target.account_status == 'ACTIVA' and target.check_password(PASSWORD) and AdminChangeLog.query.filter_by(action='ACCESO_RESTABLECIDO').count() == 0
    assert world['clients']['admin'].get('/admin').status_code == 200                                                    # su sesión sigue valiendo


# ───────── 14. La migración corresponde al código ─────────

ROLE_TABLES = ('admin_permissions', 'admin_change_log', 'admin_activation_tokens')


def test_the_roles_migration_matches_the_models_defaults_existing_users_to_active_and_reverts_cleanly(monkeypatch):
    """Migración frente a modelos (estructura exacta), usuarios previos y reversibilidad, en una base desechable en memoria."""
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text
    monkeypatch.setenv('APP_ENV', 'testing')
    monkeypatch.setenv('MIGRATION_DATABASE_URL', 'sqlite:///:memory:')
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'f7a1c4d9e620')                                                 # estado de staging antes de esta migración
        connection.execute(text("insert into users (username, full_name, password_hash, role, created_at, is_seed) values ('previo', 'Usuario Previo', 'x', 'CLIENTE', '2026-01-01 00:00:00', 0)"))
        command.upgrade(config, 'b6e2a9c15d37')
        inspector = inspect(connection)
        assert connection.execute(text("select account_status from users where username = 'previo'")).scalar() == 'ACTIVA'      # nadie queda sin acceso ni se promociona
        assert connection.execute(text("select role from users where username = 'previo'")).scalar() == 'CLIENTE'
        for name in ROLE_TABLES:
            table = db.metadata.tables[name]
            migrated = {column['name']: column for column in inspector.get_columns(name)}
            assert set(migrated) == {column.name for column in table.columns}, name
            for column in table.columns:
                assert migrated[column.name]['nullable'] == column.nullable, (name, column.name)
            indexed = {tuple(index['column_names']) for index in inspector.get_indexes(name)}
            assert all(tuple(column.name for column in index.columns) in indexed for index in table.indexes), name
            uniques = {tuple(unique['column_names']) for unique in inspector.get_unique_constraints(name)}
            assert all(tuple(column.name for column in constraint.columns) in uniques for constraint in table.constraints if constraint.__class__.__name__ == 'UniqueConstraint'), name
            foreign = {(tuple(fk['constrained_columns']), fk['referred_table'], (fk.get('options') or {}).get('ondelete')) for fk in inspector.get_foreign_keys(name)}
            assert all((tuple(column.name for column in fk.columns), list(fk.elements)[0].column.table.name, fk.ondelete) in foreign for fk in table.foreign_key_constraints), name
        status = {column['name']: column for column in inspector.get_columns('users')}['account_status']
        assert status['nullable'] is False
        command.downgrade(config, 'f7a1c4d9e620')                                                 # reversible: solo deshace lo de roles
        inspector = inspect(connection)
        assert not set(ROLE_TABLES) & set(inspector.get_table_names())
        assert 'account_status' not in {column['name'] for column in inspector.get_columns('users')}
        assert 'risk_evaluations' in inspector.get_table_names() and 'fraud_incidents' in inspector.get_table_names()         # el antifraude queda intacto
        assert connection.execute(text("select count(*) from users where username = 'previo'")).scalar() == 1               # los datos previos se conservan
        command.upgrade(config, 'b6e2a9c15d37')                                                   # y se puede volver a subir
        assert connection.execute(text("select account_status from users where username = 'previo'")).scalar() == 'ACTIVA'
    engine.dispose()
