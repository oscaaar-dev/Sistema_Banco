"""Anomalías de acceso: fallos de clave y de OTP atribuidos al usuario, niveles, bloqueo preventivo temporal y recuperación."""
import re
from datetime import timedelta

import pytest

import app.routes as routes_module
from app import create_app, fraud
from app.fraud import constants as c, features as feature_extraction, rules, service
from app.models import AuditLog, FraudIncident, RiskEvaluation, SecurityRestriction, User, db, now_utc
from fraud_helpers import PASSWORD, add_audit, make_client, session_client

OLD_IP = 'a' * 64


@pytest.fixture
def world(monkeypatch):
    app = create_app({'SECRET_KEY': 'k' * 40})
    app.config.update(LOGIN_CODE_RATE_LIMIT_IP=100, LOGIN_CODE_RATE_LIMIT_USER=100, LOGIN_CODE_RESEND_SECONDS=0)
    codes = []
    monkeypatch.setattr(routes_module, 'send_login_code', lambda email, code: codes.append(code))
    with app.app_context():
        user, account = make_client('accuser', '53000001', '530000000001', email='acc@example.test')
        admin, _ = make_client('accadmin', '53000002', '530000000002', role='ADMINISTRADOR')
    return {'app': app, 'user': user, 'admin': admin, 'codes': codes, 'client': app.test_client()}


def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _password_step(world, username='accuser', password=PASSWORD):
    client = world['client']
    token = _csrf(client.get('/login').get_data(as_text=True))
    return client.post('/login', data={'csrf_token': token, 'username': username, 'password': password})


def _code_step(world, code):
    client = world['client']
    token = _csrf(client.get('/login/verify').get_data(as_text=True))
    return client.post('/login/verify', data={'csrf_token': token, 'code': code})


def _ip_hash(world):
    with world['app'].test_request_context('/', environ_overrides={'REMOTE_ADDR': '127.0.0.1'}):
        return routes_module._request_ip_hash('login-otp-ip')


# ───────── Atribución de eventos de acceso ─────────

def test_a_wrong_password_is_now_attributed_to_the_existing_user_and_evaluated(world):
    response = _password_step(world, password='incorrecta')
    assert response.status_code == 200 and 'Usuario o clave incorrectos' in response.get_data(as_text=True)
    with world['app'].app_context():
        entry = AuditLog.query.filter_by(action='LOGIN_FAILURE').one()
        assert entry.user_id == world['user']
        evaluation = RiskEvaluation.query.filter_by(event=c.EVENT_LOGIN_FAILURE).one()
        assert evaluation.user_id == world['user'] and evaluation.context == c.CONTEXT_ACCESS and evaluation.level == c.LEVEL_NORMAL


def test_a_failed_attempt_for_an_unknown_identifier_stays_anonymous_and_creates_no_evaluation(world):
    _password_step(world, username='no-existe', password='incorrecta')
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='LOGIN_FAILURE').one().user_id is None
        assert RiskEvaluation.query.count() == 0 and SecurityRestriction.query.count() == 0


def test_wrong_login_codes_are_now_audited_and_evaluated(world):
    assert _password_step(world).status_code == 302
    for _ in range(2):
        _code_step(world, '000000')
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='LOGIN_OTP_FAILURE', user_id=world['user']).count() == 2
        assert RiskEvaluation.query.filter_by(event=c.EVENT_LOGIN_OTP_FAILURE).count() == 2


def test_exhausting_the_login_code_attempts_is_audited_and_ends_the_pending_login(world):
    _password_step(world)
    for _ in range(world['app'].config['LOGIN_CODE_MAX_ATTEMPTS']):
        response = _code_step(world, '000000')
    assert response.status_code == 302 and response.headers['Location'].endswith('/login')
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='LOGIN_OTP_FAILURE', user_id=world['user']).count() == world['app'].config['LOGIN_CODE_MAX_ATTEMPTS']


def test_a_normal_login_is_unchanged_and_records_a_normal_access_evaluation(world):
    assert _password_step(world).status_code == 302
    response = _code_step(world, world['codes'][-1])
    assert response.status_code == 302 and response.headers['Location'].endswith('/dashboard')
    assert world['client'].get('/dashboard').status_code == 200
    with world['app'].app_context():
        evaluation = RiskEvaluation.query.filter_by(event=c.EVENT_LOGIN_SUCCESS).one()
        assert evaluation.level == c.LEVEL_NORMAL and evaluation.applied_action == c.APPLIED_NONE and evaluation.mode == c.MODE_SHADOW


# ───────── Niveles de las anomalías de acceso ─────────

@pytest.mark.parametrize('logins,otps,new_ip,expected', [
    (0, 0, False, c.LEVEL_NORMAL),
    (2, 1, False, c.LEVEL_NORMAL),
    (3, 0, False, c.LEVEL_NORMAL),            # 15 puntos: aún no es relevante
    (5, 0, False, c.LEVEL_INTERMEDIO),
    (3, 2, False, c.LEVEL_INTERMEDIO),
    (5, 3, False, c.LEVEL_INTERMEDIO),        # muchas señales, pero de un solo grupo (fallos): nunca SOSPECHOSO
    (0, 0, True, c.LEVEL_NORMAL),             # un contexto nuevo aislado no basta
    (5, 3, True, c.LEVEL_SOSPECHOSO),         # fallos reiterados + contexto nuevo: dos grupos independientes
])
def test_access_levels_need_independent_groups_to_become_suspicious(world, logins, otps, new_ip, expected):
    with world['app'].app_context():
        add_audit(world['user'], 'LOGIN_FAILURE', minutes_ago=5, count=logins)
        add_audit(world['user'], 'LOGIN_OTP_FAILURE', minutes_ago=5, count=otps)
        for hours in (2, 3, 4):                                               # historial de accesos desde otra conexión
            add_audit(world['user'], 'LOGIN_OTP_SENT', minutes_ago=hours * 60, ip_local=OLD_IP)
        user = db.session.get(User, world['user'])
        f = feature_extraction.access_features(user, ip_hash=('b' * 64) if new_ip else OLD_IP)
        score, level, _ = rules.classify(rules.score_access(f, world['app'].config), 'SUFICIENTE', world['app'].config)
        assert level == expected, (score, f)


def test_a_new_connection_is_only_inferred_with_enough_access_history(world):
    with world['app'].app_context():
        user = db.session.get(User, world['user'])
        add_audit(world['user'], 'LOGIN_OTP_SENT', minutes_ago=300, ip_local=OLD_IP)           # un solo acceso previo
        assert feature_extraction.access_features(user, ip_hash='b' * 64).new_context is None


# ───────── Modo de observación y modo enforce ─────────

def _seed_suspicious_access(world):
    add_audit(world['user'], 'LOGIN_FAILURE', minutes_ago=5, count=5)
    add_audit(world['user'], 'LOGIN_OTP_FAILURE', minutes_ago=5, count=3)
    for hours in (2, 3, 4):
        add_audit(world['user'], 'LOGIN_OTP_SENT', minutes_ago=hours * 60, ip_local=OLD_IP)


def test_shadow_mode_records_a_suspicious_access_and_opens_an_incident_but_blocks_nothing(world):
    with world['app'].app_context():
        _seed_suspicious_access(world)
        user = db.session.get(User, world['user'])
        evaluation = fraud.evaluate_access(user, c.EVENT_LOGIN_FAILURE, ip_hash='b' * 64)
        assert evaluation.level == c.LEVEL_SOSPECHOSO and evaluation.applied_action == c.APPLIED_NONE
        incident = FraudIncident.query.one()
        assert incident.category == c.CATEGORY_ACCESS and incident.status == c.INCIDENT_OPEN
        assert SecurityRestriction.query.count() == 0 and user.sessions_revoked_at is None
    assert _password_step(world).status_code == 302                              # el acceso sigue funcionando


def test_enforce_restricts_access_temporarily_revokes_sessions_and_opens_an_incident(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _seed_suspicious_access(world)
        user = db.session.get(User, world['user'])
        stamp_before = user.session_stamp()
        evaluation = fraud.evaluate_access(user, c.EVENT_LOGIN_FAILURE, ip_hash='b' * 64)
        assert evaluation.applied_action == c.APPLIED_ACCESS_RESTRICTED
        restriction = SecurityRestriction.query.one()
        assert (restriction.scope, restriction.origin, restriction.status) == (c.SCOPE_ACCESS, c.ORIGIN_PREVENTIVE, c.RESTRICTION_ACTIVE)
        minutes = (restriction.expires_at - restriction.starts_at).total_seconds() / 60
        assert 29 <= minutes <= 31                                                  # temporal: nunca permanente
        assert restriction.incident_id == FraudIncident.query.one().id and restriction.created_by_id is None
        assert user.sessions_revoked_at is not None and user.session_stamp() != stamp_before
        assert AuditLog.query.filter_by(action='FRAUD_ACCESS_RESTRICTED', user_id=world['user']).count() == 1


def test_a_restricted_user_with_the_right_password_is_refused_without_sending_a_code_and_can_still_recover_the_password(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _seed_suspicious_access(world)
        fraud.evaluate_access(db.session.get(User, world['user']), c.EVENT_LOGIN_FAILURE, ip_hash='b' * 64)
    response = _password_step(world)
    assert response.status_code == 403 and 'restringido temporalmente' in response.get_data(as_text=True)
    assert world['codes'] == []                                                    # no se envió ningún código
    recovery = world['client'].get('/recuperar')
    assert recovery.status_code == 200                                              # la recuperación de cuenta sigue disponible
    with world['app'].app_context():
        assert AuditLog.query.filter_by(action='LOGIN_BLOCKED_BY_RESTRICTION', user_id=world['user']).count() == 1


def test_a_wrong_password_for_a_restricted_user_does_not_reveal_the_restriction(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _seed_suspicious_access(world)
        fraud.evaluate_access(db.session.get(User, world['user']), c.EVENT_LOGIN_FAILURE, ip_hash='b' * 64)
    response = _password_step(world, password='incorrecta')
    assert 'Usuario o clave incorrectos' in response.get_data(as_text=True) and 'restringido' not in response.get_data(as_text=True)


def test_an_already_open_session_is_closed_on_the_next_request_when_access_is_restricted(world):
    client = session_client(world['app'], world['user'])
    assert client.get('/dashboard').status_code == 200
    with world['app'].app_context():
        db.session.add(SecurityRestriction(user_id=world['user'], scope=c.SCOPE_ACCESS, origin=c.ORIGIN_PREVENTIVE, reason='Prueba',
                                           expires_at=now_utc() + timedelta(minutes=30)))
        db.session.commit()
    response = client.get('/dashboard')
    assert response.status_code == 302 and '/login' in response.headers['Location']
    assert client.get('/dashboard').status_code == 302


def test_revoking_sessions_invalidates_old_sessions_only_after_the_revocation(world):
    client = session_client(world['app'], world['user'])
    with world['app'].app_context():
        user = db.session.get(User, world['user'])
        assert user.session_stamp() == '0'                       # sin revocación, el sello es el de siempre (compatibilidad)
    assert client.get('/dashboard').status_code == 200
    with world['app'].app_context():
        user = db.session.get(User, world['user'])
        service.revoke_sessions(user)
        db.session.commit()
        assert user.session_stamp().startswith('0|r')
    assert client.get('/dashboard').status_code == 302            # la sesión anterior ya no sirve


def test_an_expired_restriction_no_longer_blocks_and_is_marked_as_expired(world):
    with world['app'].app_context():
        db.session.add(SecurityRestriction(user_id=world['user'], scope=c.SCOPE_ACCESS, origin=c.ORIGIN_PREVENTIVE, reason='Prueba',
                                           starts_at=now_utc() - timedelta(hours=2), expires_at=now_utc() - timedelta(minutes=1)))
        db.session.commit()
        assert service.active_restriction(world['user'], c.SCOPE_ACCESS) is None
        assert service.expire_stale()[0] == 1 and SecurityRestriction.query.one().status == c.RESTRICTION_EXPIRED
    assert _password_step(world).status_code == 302


def test_an_administrator_can_lift_a_restriction_and_the_user_can_log_in_again(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    with world['app'].app_context():
        _seed_suspicious_access(world)
        fraud.evaluate_access(db.session.get(User, world['user']), c.EVENT_LOGIN_FAILURE, ip_hash='b' * 64)
    assert _password_step(world).status_code == 403
    with world['app'].app_context():
        restriction = SecurityRestriction.query.one()
        service.lift_restriction(restriction, db.session.get(User, world['admin']), 'Verificado: era el titular legítimo')
        db.session.commit()
        assert restriction.status == c.RESTRICTION_LIFTED and restriction.lifted_by_id == world['admin'] and restriction.lift_reason
        assert service.access_restriction(db.session.get(User, world['user'])) is None
    assert _password_step(world).status_code == 302


def test_an_evaluation_failure_never_breaks_the_login(world, monkeypatch):
    monkeypatch.setattr(feature_extraction, 'access_features', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('fallo simulado')))
    assert _password_step(world).status_code == 302
    response = _code_step(world, world['codes'][-1])
    assert response.status_code == 302 and response.headers['Location'].endswith('/dashboard')
    with world['app'].app_context():
        failed = RiskEvaluation.query.filter_by(event=c.EVENT_LOGIN_SUCCESS).one()
        assert failed.level == c.LEVEL_NO_EVALUADO and failed.status == 'ERROR' and failed.level != c.LEVEL_NORMAL


def test_failures_against_an_unknown_account_cannot_be_used_to_lock_anyone(world):
    world['app'].config['FRAUD_MODE'] = c.MODE_ENFORCE
    for _ in range(8):
        _password_step(world, username='nadie', password='x')
    with world['app'].app_context():
        assert SecurityRestriction.query.count() == 0


def test_access_evaluation_does_not_store_ip_addresses_or_secrets(world):
    _password_step(world, password='incorrecta')
    with world['app'].app_context():
        evaluation = RiskEvaluation.query.one()
        dump = repr([evaluation.signals, evaluation.error_code, evaluation.event, evaluation.context])
        assert '127.0.0.1' not in dump and 'incorrecta' not in dump and PASSWORD not in dump
