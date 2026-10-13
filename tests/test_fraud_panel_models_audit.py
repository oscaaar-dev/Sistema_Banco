"""Panel administrativo: detalle de versiones del detector (resultados de evaluación) y auditoría antifraude.

Solo datos sintéticos, SQLite en memoria y carpetas temporales; nada lee ni escribe ``data/``.
"""
from datetime import timedelta

import pytest

from app import create_app
from app.fraud import event_training as et, synthetic_events as se
from app.models import AuditLog, FraudModelVersion, db, now_utc
from fraud_helpers import make_client, session_client


@pytest.fixture(scope='module')
def csv_path(tmp_path_factory):
    path = tmp_path_factory.mktemp('panel') / 'combinado.csv'
    se.generate_combined(2026).to_csv(path, index=False, lineterminator='\n')
    return path


@pytest.fixture
def world(tmp_path, csv_path):
    app = create_app({'SECRET_KEY': 'clave-de-prueba-' + 'k' * 30, 'FRAUD_MODEL_DIR': str(tmp_path / 'modelos')})
    with app.app_context():
        customer, _ = make_client('mcustomer', '55000001', '550000000001', email='privado@example.test')
        support, _ = make_client('msupport', '55000002', '550000000002', role='SOPORTE')
        admin, _ = make_client('madmin', '55000003', '550000000003', role='ADMINISTRADOR')
        report = et.run_pipeline(csv_path)
        versions = {name: part['version'] for name, part in report['modelos'].items()}
    return {'app': app, 'versions': versions, 'admin_id': admin, 'admin': session_client(app, admin), 'client': session_client(app, customer),
            'support': session_client(app, support), 'anon': app.test_client()}


def _table(response):
    """Solo el cuerpo de la tabla (el formulario de filtros menciona acciones de ejemplo)."""
    return response.get_data(as_text=True).split('<tbody>')[1].split('</tbody>')[0]


def _audit(world, action, module='FRAUDE', user=None, minutes_ago=0, result='COMPLETADA'):
    with world['app'].app_context():
        db.session.add(AuditLog(user_id=user, action=action, module=module, result=result, created_at=now_utc() - timedelta(minutes=minutes_ago)))
        db.session.commit()


# ───────── Detalle de una versión del detector ─────────

def test_the_model_page_shows_evaluation_results_gates_and_leakage_exclusions(world):
    version = world['versions']['transacciones']
    page = world['admin'].get(f'/admin/fraude/modelos/{version}')
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and version in text
    for expected in ('IsolationForest', 'CANDIDATO', 'Evaluación con el tramo temporal reservado', 'Tasa de falsos positivos', 'Sensibilidad',
                     'Variables excluidas de los predictores', 'scenario', 'is_fraud', 'No.</b> No supera las puertas', 'datos sintéticos', 'fraud-model-activate'):
        assert expected in text, expected
    assert 'proporcion_saldo' in text and 'Periodo de entrenamiento' in text and 'Periodo de evaluación (posterior)' in text


def test_the_page_renders_every_kind_of_candidate_including_the_classic_one(world):
    for version in world['versions'].values():
        assert world['admin'].get(f'/admin/fraude/modelos/{version}').status_code == 200


def test_the_model_page_never_offers_a_way_to_activate_from_the_browser(world):
    version = world['versions']['accesos']
    text = world['admin'].get(f'/admin/fraude/modelos/{version}').get_data(as_text=True)
    assert '<form' not in text.split('<main')[-1].lower() or 'method="post"' not in text.lower()
    assert world['admin'].post(f'/admin/fraude/modelos/{version}').status_code == 405
    with world['app'].app_context():
        assert FraudModelVersion.query.filter_by(status='ACTIVO').count() == 0


@pytest.mark.parametrize('client', ['client', 'support'])
def test_only_administrators_can_open_the_model_page(world, client):
    version = world['versions']['transacciones']
    assert world[client].get(f'/admin/fraude/modelos/{version}').status_code == 403


def test_anonymous_visitors_are_sent_to_login(world):
    version = world['versions']['transacciones']
    for path in (f'/admin/fraude/modelos/{version}', '/admin/fraude/auditoria'):
        response = world['anon'].get(path)
        assert response.status_code == 302 and '/login' in response.headers['Location']


@pytest.mark.parametrize('version', ['../../etc/passwd', 'iforest', 'iforest-tx-1', 'otra-cosa-20260101000000', 'iforest-tx-20260101000000000000%00',
                                     'iforest-TX-20260101000000', 'iforest-tx-99999999999999'])
def test_malformed_or_unknown_versions_are_a_plain_404(world, version):
    response = world['admin'].get(f'/admin/fraude/modelos/{version}')
    assert response.status_code == 404 and 'Traceback' not in response.get_data(as_text=True)


def test_free_text_stored_with_a_model_is_escaped_and_secrets_are_not_shown(world):
    version = world['versions']['transacciones']
    with world['app'].app_context():
        row = FraudModelVersion.query.filter_by(version=version).one()
        row.notes = '<script>alert(1)</script>'
        db.session.commit()
    text = world['admin'].get(f'/admin/fraude/modelos/{version}').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in text and '&lt;script&gt;' in text
    for secret in ('SECRET_KEY', 'clave-de-prueba', 'password_hash', 'sqlite://', 'FRAUD_MODEL_DIR'):
        assert secret not in text


def test_a_row_with_incomplete_metrics_still_renders(world):
    with world['app'].app_context():
        db.session.add(FraudModelVersion(version='iforest-20250101000000000001', source='CSV', status='RECHAZADO', n_train=0, n_eval=0, features=[],
                                         metrics={}, validation={}, sklearn_version='1.7.0', python_version='3.12.10'))
        db.session.commit()
    page = world['admin'].get('/admin/fraude/modelos/iforest-20250101000000000001')
    assert page.status_code == 200 and 'RECHAZADO' in page.get_data(as_text=True)


def test_undefined_metrics_are_shown_as_undefined_not_as_zero(world):
    version = world['versions']['transacciones']
    with world['app'].app_context():
        row = FraudModelVersion.query.filter_by(version=version).one()
        metrics = dict(row.metrics)
        metrics.update(precision=None, sensibilidad_recall=None, f1=None, tasa_falsos_positivos=None, independiente_del_umbral={'auc_roc': None, 'precision_media': None, 'prevalencia': 0.0})
        row.metrics = metrics
        db.session.commit()
    text = world['admin'].get(f'/admin/fraude/modelos/{version}').get_data(as_text=True)
    assert text.count('indefinida') >= 3 and 'indefinido' in text


def test_the_dashboard_links_each_version_to_its_page(world):
    page = world['admin'].get('/admin/fraude').get_data(as_text=True)
    for version in world['versions'].values():
        assert f'/admin/fraude/modelos/{version}' in page
    assert '/admin/fraude/auditoria' in page


# ───────── Auditoría ─────────

def test_the_audit_page_lists_only_antifraud_records_newest_first(world):
    _audit(world, 'FRAUD_INCIDENT_CONFIRMED', user=world['admin_id'], minutes_ago=30)
    _audit(world, 'FRAUD_HOLD_AUTHORIZED', user=world['admin_id'], minutes_ago=5)
    _audit(world, 'LOGIN_EXITOSO', module='SEGURIDAD', user=world['admin_id'])                      # otro módulo: no debe aparecer
    text = _table(world['admin'].get('/admin/fraude/auditoria'))
    assert 'FRAUD_INCIDENT_CONFIRMED' in text and 'FRAUD_HOLD_AUTHORIZED' in text and 'LOGIN_EXITOSO' not in text
    assert text.index('FRAUD_HOLD_AUTHORIZED') < text.index('FRAUD_INCIDENT_CONFIRMED') and 'madmin' in text
    assert 'privado@example.test' not in text and '550000000003' not in text and '55000003' not in text


def test_audit_filters_by_action_and_dates_and_rejects_invalid_ones(world):
    _audit(world, 'FRAUD_INCIDENT_CONFIRMED', user=world['admin_id'])
    _audit(world, 'FRAUD_HOLD_REJECTED', user=world['admin_id'])
    _audit(world, 'FRAUD_MODEL_ACTIVATED', minutes_ago=60 * 24 * 10)
    admin = world['admin']
    only = _table(admin.get('/admin/fraude/auditoria?accion=fraud_hold_rejected'))
    assert 'FRAUD_HOLD_REJECTED' in only and 'FRAUD_INCIDENT_CONFIRMED' not in only
    today = now_utc().date().isoformat()
    recent = _table(admin.get(f'/admin/fraude/auditoria?desde={today}'))
    assert 'FRAUD_MODEL_ACTIVATED' not in recent and 'FRAUD_HOLD_REJECTED' in recent
    assert 'No hay registros' in admin.get('/admin/fraude/auditoria?accion=FRAUD_NO_EXISTE').get_data(as_text=True)
    for bad in ('accion=x;DROP TABLE', 'accion=' + 'A' * 200, "accion='%20OR%201=1", 'desde=no-es-fecha', 'hasta=2026-99-99',
                f'desde={today}&hasta=2020-01-01'):
        assert admin.get('/admin/fraude/auditoria?' + bad).status_code == 400, bad


@pytest.mark.parametrize('client', ['client', 'support'])
def test_only_administrators_can_read_the_audit(world, client):
    _audit(world, 'FRAUD_INCIDENT_CONFIRMED', user=world['admin_id'])
    assert world[client].get('/admin/fraude/auditoria').status_code == 403
    assert world[client].get('/admin/fraude/exportar/auditoria').status_code in (403, 404, 405)


def test_the_audit_page_escapes_what_it_prints_and_is_bounded(world):
    _audit(world, 'FRAUD_<b>X</b>_ACCION', user=None)
    with world['app'].app_context():
        for number in range(150):
            db.session.add(AuditLog(user_id=None, action='FRAUD_MASIVA', module='FRAUDE', result='COMPLETADA', created_at=now_utc() - timedelta(seconds=number)))
        db.session.commit()
    text = _table(world['admin'].get('/admin/fraude/auditoria'))
    assert '<b>X</b>' not in text and text.count('FRAUD_MASIVA') <= 100 and 'Sistema' in text


def test_viewing_the_audit_does_not_write_to_it(world):
    with world['app'].app_context():
        before = AuditLog.query.count()
    world['admin'].get('/admin/fraude/auditoria')
    world['admin'].get(f"/admin/fraude/modelos/{world['versions']['transacciones']}")
    with world['app'].app_context():
        assert AuditLog.query.count() == before
