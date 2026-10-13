"""Panel administrativo de fraude: permisos en el servidor, CSRF, flujos de decisión, historial y auditoría."""
import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.fraud import constants as c, service
from app.models import (AuditLog, FraudIncident, HeldOperation, IncidentEvent, RiskEvaluation, SecurityRestriction, User, db, now_utc)
from fraud_helpers import make_client, session_client


@pytest.fixture
def world():
    app = create_app({'SECRET_KEY': 'k' * 40})
    with app.app_context():
        customer, account = make_client('pcustomer', '54000001', '540000000001', email='privado@example.test')
        support, _ = make_client('psupport', '54000002', '540000000002', role='SOPORTE')
        admin, _ = make_client('padmin', '54000003', '540000000003', role='ADMINISTRADOR')
        admin_two, _ = make_client('padmin2', '54000004', '540000000004', role='ADMINISTRADOR')
    return {'app': app, 'customer': customer, 'account': account, 'support_id': support, 'admin_id': admin,
            'client': session_client(app, customer), 'support': session_client(app, support),
            'admin': session_client(app, admin), 'admin_two_id': admin_two, 'anon': app.test_client()}


def _incident(world, *, status=c.INCIDENT_OPEN, user=None, title='Operación TRANSFERENCIA con señales de riesgo (puntuación 78).', signals=None):
    with world['app'].app_context():
        evaluation = RiskEvaluation(user_id=user or world['customer'], account_id=world['account'], context='TRANSFERENCIA',
                                    event=c.EVENT_OPERATION, level=c.LEVEL_SOSPECHOSO, score=78, confidence='SUFICIENTE',
                                    signals=signals or [{'code': 'VACIADO_DE_CUENTA', 'group': 'monto', 'points': 30, 'detail': 'El monto equivale al 95 % o más del saldo.'}],
                                    rules_version='reglas-1.0', mode='shadow', recommended_action=c.ACTION_HOLD, applied_action=c.APPLIED_NONE)
        db.session.add(evaluation)
        db.session.flush()
        incident = FraudIncident(user_id=user or world['customer'], account_id=world['account'], evaluation_id=evaluation.id, origin='AUTOMATICO',
                                 category=c.CATEGORY_TRANSACTIONAL, status=status, level=c.LEVEL_SOSPECHOSO, title=title)
        db.session.add(incident)
        db.session.commit()
        service.add_event(incident, None, 'ABIERTO', title)
        db.session.commit()
        return incident.id


def _token(client, incident_id):
    page = client.get(f'/admin/fraude/incidentes/{incident_id}')
    assert page.status_code == 200
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def _post(client, path, token, **data):
    return client.post(path, data={'csrf_token': token, **data})


def _flashes(client):
    with client.session_transaction() as browser_session:
        return [message for _, message in browser_session.get('_flashes', [])]


def _hold(world, incident_id, status=c.HOLD_HELD):
    with world['app'].app_context():
        hold = HeldOperation(user_id=world['customer'], account_id=world['account'], incident_id=incident_id, kind='TRANSFERENCIA',
                             amount=Decimal('960.00'), fingerprint='f' * 64, reason='Operación sospechosa retenida antes de ejecutarse.',
                             status=status, expires_at=now_utc() + timedelta(hours=72))
        db.session.add(hold)
        db.session.commit()
        return hold.id


def _restriction(world, scope=c.SCOPE_OPERATIONS, origin=c.ORIGIN_PREVENTIVE, incident_id=None, minutes=30):
    with world['app'].app_context():
        restriction = SecurityRestriction(user_id=world['customer'], incident_id=incident_id, scope=scope, origin=origin, reason='Prueba',
                                          expires_at=now_utc() + timedelta(minutes=minutes))
        db.session.add(restriction)
        db.session.commit()
        return restriction.id


ADMIN_GETS = ['/admin/fraude', '/admin/fraude/incidentes/{i}', '/admin/fraude/restricciones', '/admin/fraude/retenciones',
              '/admin/fraude/exportar/evaluaciones.csv']
ADMIN_POSTS = ['/admin/fraude/incidentes/{i}/investigar', '/admin/fraude/incidentes/{i}/comentar', '/admin/fraude/incidentes/{i}/descartar',
               '/admin/fraude/incidentes/{i}/confirmar', '/admin/fraude/restricciones/{r}/levantar',
               '/admin/fraude/retenciones/{h}/autorizar', '/admin/fraude/retenciones/{h}/rechazar']


def _paths(world):
    incident = _incident(world)
    return {'i': incident, 'r': _restriction(world, incident_id=incident), 'h': _hold(world, incident)}


# ───────── Permisos (se comprueban en el servidor) ─────────

@pytest.mark.parametrize('path', ADMIN_GETS)
@pytest.mark.parametrize('role', ['client', 'support'])
def test_customers_and_support_cannot_open_any_fraud_page(world, path, role):
    ids = _paths(world)
    assert world[role].get(path.format(**ids)).status_code == 403


@pytest.mark.parametrize('path', ADMIN_GETS)
def test_anonymous_visitors_are_sent_to_the_login(world, path):
    ids = _paths(world)
    response = world['anon'].get(path.format(**ids))
    assert response.status_code == 302 and '/login' in response.headers['Location']


@pytest.mark.parametrize('path', ADMIN_POSTS)
@pytest.mark.parametrize('role', ['client', 'support'])
def test_customers_and_support_cannot_run_any_fraud_action_even_with_a_valid_csrf_token(world, path, role):
    ids = _paths(world)
    page = world[role].get('/dashboard' if role == 'client' else '/support')
    token_match = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
    token = token_match.group(1).decode() if token_match else _token(world['admin'], ids['i'])
    response = _post(world[role], path.format(**ids), token, motivo='Intento de escalada de privilegios', comentario='x' * 12,
                     alcance='OPERACIONES', dias='30', role='ADMINISTRADOR')
    assert response.status_code in (403, 400)
    with world['app'].app_context():
        incident = db.session.get(FraudIncident, ids['i'])
        assert incident.status == c.INCIDENT_OPEN and db.session.get(HeldOperation, ids['h']).status == c.HOLD_HELD
        assert db.session.get(SecurityRestriction, ids['r']).status == c.RESTRICTION_ACTIVE


@pytest.mark.parametrize('path', ADMIN_POSTS)
def test_every_fraud_action_requires_csrf(world, path):
    ids = _paths(world)
    assert world['admin'].post(path.format(**ids), data={'motivo': 'Motivo suficientemente largo'}).status_code == 400
    assert world['admin'].post(path.format(**ids), data={'csrf_token': 'falso', 'motivo': 'Motivo suficientemente largo'}).status_code == 400
    with world['app'].app_context():
        assert db.session.get(FraudIncident, ids['i']).status == c.INCIDENT_OPEN


def test_administrators_can_open_every_page_and_nothing_personal_is_shown(world):
    ids = _paths(world)
    for path in ADMIN_GETS[:4]:
        response = world['admin'].get(path.format(**ids))
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'privado@example.test' not in html and '54000001' not in html and 'Cliente pcustomer' not in html


def test_an_administrator_cannot_act_on_their_own_user(world):
    own = _incident(world, user=world['admin_id'])
    page = world['admin'].get(f'/admin/fraude/incidentes/{own}').get_data(as_text=True)
    assert 'Confirmar fraude y aplicar sanción' not in page and 'Descartar incidente' not in page          # tampoco se ofrecen los botones
    token = _token(world['admin'], _incident(world))
    for action, data in (('descartar', {'motivo': 'Motivo suficientemente largo'}), ('investigar', {}),
                         ('confirmar', {'motivo': 'Motivo suficientemente largo', 'alcance': 'OPERACIONES', 'dias': '7'})):
        assert _post(world['admin'], f'/admin/fraude/incidentes/{own}/{action}', token, **data).status_code == 403
    with world['app'].app_context():
        assert db.session.get(FraudIncident, own).status == c.INCIDENT_OPEN and SecurityRestriction.query.count() == 0


def test_the_service_layer_rechecks_the_role_even_if_a_route_were_misconfigured(world):
    incident = _incident(world)
    with world['app'].app_context():
        for actor in (db.session.get(User, world['support_id']), db.session.get(User, world['customer']), None):
            with pytest.raises(service.FraudPermissionError):
                service.confirm_fraud(db.session.get(FraudIncident, incident), actor, 'Motivo suficientemente largo', ['OPERACIONES'], 7)
            with pytest.raises(service.FraudPermissionError):
                service.discard_incident(db.session.get(FraudIncident, incident), actor, 'Motivo suficientemente largo')


# ───────── Consulta y filtros ─────────

def test_the_incident_page_explains_why_it_was_flagged_and_shows_the_history(world):
    incident = _incident(world)
    html = world['admin'].get(f'/admin/fraude/incidentes/{incident}').get_data(as_text=True)
    assert 'VACIADO_DE_CUENTA' in html and 'no es una probabilidad' in html and 'reglas-1.0' in html and 'Quién hizo qué' in html


def test_incident_filters_by_status_level_type_and_dates(world):
    open_one = _incident(world, title='Abierto reciente')
    closed = _incident(world, status=c.INCIDENT_DISCARDED, title='Descartado antiguo')
    with world['app'].app_context():
        db.session.get(FraudIncident, closed).created_at = now_utc() - timedelta(days=10)
        db.session.commit()
    page = lambda query: world['admin'].get('/admin/fraude' + query).get_data(as_text=True)
    assert 'Abierto reciente' in page('?estado=ABIERTO') and 'Descartado antiguo' not in page('?estado=ABIERTO')
    assert 'Descartado antiguo' in page('?estado=DESCARTADO') and 'Abierto reciente' not in page('?estado=DESCARTADO')
    assert 'Abierto reciente' in page('?tipo=TRANSACCIONAL') and 'Abierto reciente' not in page('?tipo=ACCESO')
    today = now_utc().date().isoformat()
    assert 'Abierto reciente' in page(f'?desde={today}') and 'Descartado antiguo' not in page(f'?desde={today}')
    assert 'Descartado antiguo' in page(f'?hasta={(now_utc() - timedelta(days=5)).date().isoformat()}')
    assert 'Abierto reciente' in page('?nivel=SOSPECHOSO') and 'Abierto reciente' not in page('?nivel=FRAUDE')


@pytest.mark.parametrize('query', ['?estado=HACKEADO', '?nivel=NORMAL', '?tipo=OTRO', '?desde=ayer', '?desde=2026-02-01&hasta=2026-01-01'])
def test_invalid_filters_are_rejected_not_ignored(world, query):
    assert world['admin'].get('/admin/fraude' + query).status_code == 400


def test_html_in_titles_and_comments_is_escaped(world):
    incident = _incident(world, title='<script>alert(1)</script> Operación')
    token = _token(world['admin'], incident)
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/comentar', token, comentario='<img src=x onerror=alert(2)> revisar')
    html = world['admin'].get(f'/admin/fraude/incidentes/{incident}').get_data(as_text=True)
    assert '<script>alert(1)' not in html and '&lt;script&gt;' in html and '<img src=x' not in html and '&lt;img' in html


# ───────── Decisiones: investigar, comentar, descartar, confirmar ─────────

def test_start_investigation_and_comment_are_recorded_with_the_actor(world):
    incident = _incident(world)
    token = _token(world['admin'], incident)
    assert _post(world['admin'], f'/admin/fraude/incidentes/{incident}/investigar', token).status_code == 302
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/comentar', token, comentario='Se llamó al cliente, no contestó.')
    with world['app'].app_context():
        assert db.session.get(FraudIncident, incident).status == c.INCIDENT_INVESTIGATING
        events = IncidentEvent.query.filter_by(incident_id=incident).order_by(IncidentEvent.id).all()
        assert [e.event_type for e in events] == ['ABIERTO', 'INVESTIGACION_INICIADA', 'COMENTARIO']
        assert events[1].actor_id == world['admin_id'] and events[2].detail == 'Se llamó al cliente, no contestó.'
        assert AuditLog.query.filter_by(action='FRAUD_INVESTIGATION_STARTED', user_id=world['admin_id']).count() == 1
        assert AuditLog.query.filter_by(action='FRAUD_COMMENT_ADDED', user_id=world['admin_id']).count() == 1
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/investigar', token)
    assert any('abierto' in m for m in _flashes(world['admin']))                  # no se puede reiniciar


def test_discarding_marks_a_false_positive_lifts_preventive_restrictions_and_authorizes_the_held_operation(world):
    incident = _incident(world)
    restriction, hold = _restriction(world, incident_id=incident), _hold(world, incident)
    token = _token(world['admin'], incident)
    assert _post(world['admin'], f'/admin/fraude/incidentes/{incident}/descartar', token, motivo='corto').status_code == 302
    with world['app'].app_context():
        assert db.session.get(FraudIncident, incident).status == c.INCIDENT_OPEN         # motivo demasiado corto: no cambia nada
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/descartar', token, motivo='El cliente confirmó la operación por teléfono')
    with world['app'].app_context():
        item = db.session.get(FraudIncident, incident)
        assert (item.status, item.closed_by_id) == (c.INCIDENT_DISCARDED, world['admin_id']) and item.closure_reason and item.closed_at
        assert item.confirmed_at is None and item.level == c.LEVEL_SOSPECHOSO                # descartar NO confirma ningún fraude
        lifted = db.session.get(SecurityRestriction, restriction)
        assert lifted.status == c.RESTRICTION_LIFTED and lifted.lifted_by_id == world['admin_id']
        assert db.session.get(HeldOperation, hold).status == c.HOLD_AUTHORIZED
        assert AuditLog.query.filter_by(action='FRAUD_INCIDENT_DISCARDED', user_id=world['admin_id']).count() == 1
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/descartar', token, motivo='Segundo intento sin sentido')
    assert any('cerrado' in m for m in _flashes(world['admin']))


def test_confirming_fraud_is_a_human_decision_with_a_temporary_sanction_and_full_traceability(world):
    incident = _incident(world)
    hold = _hold(world, incident)
    token = _token(world['admin'], incident)
    assert _post(world['admin'], f'/admin/fraude/incidentes/{incident}/confirmar', token, motivo='Evidencia: la cuenta destino es de una estafa conocida',
                 alcance=['OPERACIONES', 'ACCESO'], dias='30').status_code == 302
    with world['app'].app_context():
        item = db.session.get(FraudIncident, incident)
        assert (item.status, item.level, item.confirmed_by_id) == (c.INCIDENT_CONFIRMED, c.LEVEL_FRAUDE, world['admin_id'])
        assert item.confirmation_reason and item.confirmed_at
        sanctions = SecurityRestriction.query.filter_by(incident_id=incident, origin=c.ORIGIN_SANCTION).all()
        assert {s.scope for s in sanctions} == {c.SCOPE_OPERATIONS, c.SCOPE_ACCESS}
        for sanction in sanctions:
            assert sanction.created_by_id == world['admin_id'] and sanction.status == c.RESTRICTION_ACTIVE
            assert 29 <= (sanction.expires_at - sanction.starts_at).days <= 30                # temporal, nunca permanente
        assert db.session.get(User, world['customer']).sessions_revoked_at is not None          # alcance ACCESO: sesiones revocadas
        assert db.session.get(HeldOperation, hold).status == c.HOLD_REJECTED
        assert [e.event_type for e in IncidentEvent.query.filter_by(incident_id=incident).order_by(IncidentEvent.id)][-1] == 'FRAUDE_CONFIRMADO'
        assert AuditLog.query.filter_by(action='FRAUD_INCIDENT_CONFIRMED', user_id=world['admin_id']).count() == 1
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/confirmar', token, motivo='Confirmar otra vez no debe duplicar sanciones',
          alcance='OPERACIONES', dias='7')
    with world['app'].app_context():
        assert SecurityRestriction.query.filter_by(incident_id=incident).count() == 2


@pytest.mark.parametrize('data', [
    {'motivo': 'corto', 'alcance': 'OPERACIONES', 'dias': '7'},
    {'motivo': 'Motivo suficientemente largo', 'dias': '7'},
    {'motivo': 'Motivo suficientemente largo', 'alcance': 'INVENTADO', 'dias': '7'},
    {'motivo': 'Motivo suficientemente largo', 'alcance': 'OPERACIONES', 'dias': '0'},
    {'motivo': 'Motivo suficientemente largo', 'alcance': 'OPERACIONES', 'dias': '366'},
    {'motivo': 'Motivo suficientemente largo', 'alcance': 'OPERACIONES', 'dias': '-5'},
    {'motivo': 'Motivo suficientemente largo', 'alcance': 'OPERACIONES', 'dias': 'para siempre'},
    {'motivo': 'Motivo suficientemente largo', 'alcance': 'OPERACIONES', 'dias': ''},
    {'motivo': 'x' * 501, 'alcance': 'OPERACIONES', 'dias': '7'},
])
def test_an_invalid_confirmation_changes_nothing_and_there_are_no_permanent_sanctions(world, data):
    incident = _incident(world)
    token = _token(world['admin'], incident)
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/confirmar', token, **data)
    with world['app'].app_context():
        assert db.session.get(FraudIncident, incident).status == c.INCIDENT_OPEN and SecurityRestriction.query.count() == 0
        assert AuditLog.query.filter_by(action='FRAUD_INCIDENT_CONFIRMED').count() == 0


def test_the_maximum_allowed_sanction_is_one_year_and_it_still_expires(world):
    incident = _incident(world)
    token = _token(world['admin'], incident)
    _post(world['admin'], f'/admin/fraude/incidentes/{incident}/confirmar', token, motivo='Fraude acreditado con documentación', alcance='OPERACIONES', dias='365')
    with world['app'].app_context():
        sanction = SecurityRestriction.query.one()
        assert sanction.expires_at is not None and (sanction.expires_at - sanction.starts_at).days == 365


def test_only_confirmed_incidents_count_for_recidivism_never_unverified_alerts(world):
    with world['app'].app_context():
        assert service.suggested_sanction_days(world['customer']) == 7
    for status in (c.INCIDENT_OPEN, c.INCIDENT_INVESTIGATING, c.INCIDENT_DISCARDED, c.INCIDENT_OPEN, c.INCIDENT_DISCARDED):
        _incident(world, status=status)
    with world['app'].app_context():
        assert service.suggested_sanction_days(world['customer']) == 7                 # cinco alertas no verificadas: sin reincidencia
    expected = [30, 90, 90]
    for step in expected:
        _incident(world, status=c.INCIDENT_CONFIRMED)
        with world['app'].app_context():
            assert service.suggested_sanction_days(world['customer']) == step


# ───────── Restricciones y retenciones ─────────

def test_active_and_expired_restrictions_are_listed_and_an_administrator_can_lift_one(world):
    active = _restriction(world)
    with world['app'].app_context():
        db.session.add(SecurityRestriction(user_id=world['customer'], scope=c.SCOPE_ACCESS, origin=c.ORIGIN_SANCTION, reason='Vencida de prueba',
                                           starts_at=now_utc() - timedelta(days=3), expires_at=now_utc() - timedelta(days=1)))
        db.session.commit()
    token = _token(world['admin'], _incident(world))
    actives = world['admin'].get('/admin/fraude/restricciones?estado=ACTIVA').get_data(as_text=True)
    expired = world['admin'].get('/admin/fraude/restricciones?estado=VENCIDA').get_data(as_text=True)
    assert 'Levantar' in actives and 'Vencida de prueba' not in actives and 'Vencida de prueba' in expired
    assert world['admin'].get('/admin/fraude/restricciones?estado=NADA').status_code == 400
    _post(world['admin'], f'/admin/fraude/restricciones/{active}/levantar', token, motivo='corto')
    with world['app'].app_context():
        assert db.session.get(SecurityRestriction, active).status == c.RESTRICTION_ACTIVE
    _post(world['admin'], f'/admin/fraude/restricciones/{active}/levantar', token, motivo='Se verificó la identidad del titular')
    with world['app'].app_context():
        lifted = db.session.get(SecurityRestriction, active)
        assert lifted.status == c.RESTRICTION_LIFTED and lifted.lifted_by_id == world['admin_id'] and lifted.lift_reason
        assert AuditLog.query.filter_by(action='FRAUD_RESTRICTION_LIFTED', user_id=world['admin_id']).count() == 1
    _post(world['admin'], f'/admin/fraude/restricciones/{active}/levantar', token, motivo='Levantar dos veces no tiene sentido')
    assert any('ya no está activa' in m for m in _flashes(world['admin']))


def test_held_operations_can_be_authorized_or_rejected_once(world):
    incident = _incident(world)
    first, second = _hold(world, incident), _hold(world, incident)
    token = _token(world['admin'], incident)
    page = world['admin'].get('/admin/fraude/retenciones').get_data(as_text=True)
    assert 'no se ejecutó' in page and 'Autorizar' in page and 'Rechazar' in page
    _post(world['admin'], f'/admin/fraude/retenciones/{first}/autorizar', token, motivo='Verificado por llamada al cliente')
    _post(world['admin'], f'/admin/fraude/retenciones/{second}/rechazar', token, motivo='No se pudo verificar al titular')
    _post(world['admin'], f'/admin/fraude/retenciones/{first}/rechazar', token, motivo='Cambiar de opinión no está permitido')
    with world['app'].app_context():
        assert db.session.get(HeldOperation, first).status == c.HOLD_AUTHORIZED and db.session.get(HeldOperation, second).status == c.HOLD_REJECTED
        assert db.session.get(HeldOperation, first).reviewed_by_id == world['admin_id']
        assert AuditLog.query.filter_by(action='FRAUD_HOLD_AUTHORIZED').count() == 1 and AuditLog.query.filter_by(action='FRAUD_HOLD_REJECTED').count() == 1
        assert [e.event_type for e in IncidentEvent.query.filter_by(incident_id=incident).order_by(IncidentEvent.id)][-2:] == ['RETENCION_AUTORIZADA', 'RETENCION_RECHAZADA']


def test_unknown_ids_return_404_and_expired_holds_cannot_be_reviewed(world):
    token = _token(world['admin'], _incident(world))
    assert _post(world['admin'], '/admin/fraude/incidentes/99999/descartar', token, motivo='Motivo suficientemente largo').status_code == 404
    assert _post(world['admin'], '/admin/fraude/restricciones/99999/levantar', token, motivo='Motivo suficientemente largo').status_code == 404
    assert world['admin'].get('/admin/fraude/incidentes/99999').status_code == 404
    expired = _hold(world, None)
    with world['app'].app_context():
        db.session.get(HeldOperation, expired).expires_at = now_utc() - timedelta(hours=1)
        db.session.commit()
    world['admin'].get('/admin/fraude/retenciones')                                  # la consulta marca como vencidas las caducadas
    with world['app'].app_context():
        assert db.session.get(HeldOperation, expired).status == c.HOLD_EXPIRED
    _post(world['admin'], f'/admin/fraude/retenciones/{expired}/autorizar', token, motivo='Ya venció, no debe autorizarse')
    with world['app'].app_context():
        assert db.session.get(HeldOperation, expired).status == c.HOLD_EXPIRED


def test_a_customer_cannot_lift_their_own_restriction_or_change_their_risk(world):
    restriction = _restriction(world, scope=c.SCOPE_OPERATIONS)
    page = world['client'].get('/dashboard')
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert _post(world['client'], f'/admin/fraude/restricciones/{restriction}/levantar', token, motivo='Quiero operar de nuevo ya').status_code == 403
    with world['app'].app_context():
        assert db.session.get(SecurityRestriction, restriction).status == c.RESTRICTION_ACTIVE
        assert db.session.get(User, world['customer']).role == 'CLIENTE'
