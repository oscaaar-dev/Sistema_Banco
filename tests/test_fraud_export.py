"""Exportación CSV del panel antifraude: permisos, filtros, formato, inyección de fórmulas, datos mínimos y auditoría."""
import csv
import io
import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.fraud import constants as c, export
from app.models import (AuditLog, FraudIncident, FraudModelVersion, HeldOperation, RiskEvaluation, SecurityRestriction, db, now_utc)
from fraud_helpers import make_client, session_client

FORMULAS = ['=HYPERLINK("http://malo.example","clic")', '+cmd|"/c calc"!A1', '-2+3', '@SUM(1+1)', '\t=1+1', ' =1+1', '\r=1+1']


@pytest.fixture
def world():
    app = create_app({'SECRET_KEY': 'k' * 40})
    with app.app_context():
        customer, account = make_client('ecustomer', '55000001', '550000000001', email='privado@example.test')
        support, _ = make_client('esupport', '55000002', '550000000002', role='SOPORTE')
        admin, _ = make_client('eadmin', '55000003', '550000000003', role='ADMINISTRADOR')
    return {'app': app, 'customer': customer, 'account': account, 'admin_id': admin, 'client': session_client(app, customer),
            'support': session_client(app, support), 'admin': session_client(app, admin), 'anon': app.test_client()}


def _rows(response):
    return list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'), newline='')))


def _evaluation(world, *, level=c.LEVEL_NORMAL, context='TRANSFERENCIA', mode='shadow', score=10, minutes_ago=0, signals=None, **extra):
    evaluation = RiskEvaluation(user_id=world['customer'], account_id=world['account'], context=context,
                                event=c.CONTEXT_ACCESS if context == c.CONTEXT_ACCESS else c.EVENT_OPERATION, level=level, score=score,
                                confidence='SUFICIENTE', signals=signals or [], rules_version='reglas-1.0', mode=mode,
                                recommended_action=c.ACTION_ALLOW, applied_action=c.APPLIED_NONE, duration_ms=3.14159,
                                created_at=now_utc() - timedelta(minutes=minutes_ago), **extra)
    db.session.add(evaluation)
    db.session.commit()
    return evaluation


def _seed(world):
    with world['app'].app_context():
        _evaluation(world, level=c.LEVEL_NORMAL, score=5)
        _evaluation(world, level=c.LEVEL_INTERMEDIO, score=30, mode='enforce', model_version='iforest-x', model_score=-0.4321,
                    signals=[{'code': 'MONTO_80_SALDO', 'group': 'monto', 'points': 20, 'detail': 'x'}, {'code': 'DESTINATARIO_NUEVO', 'group': 'destinatario', 'points': 8, 'detail': 'y'}])
        _evaluation(world, level=c.LEVEL_SOSPECHOSO, score=78, context=c.CONTEXT_ACCESS, minutes_ago=60 * 24 * 3)
        db.session.add(FraudIncident(user_id=world['customer'], category=c.CATEGORY_TRANSACTIONAL, status=c.INCIDENT_OPEN, level=c.LEVEL_SOSPECHOSO,
                                     title='Operación sospechosa', closure_reason='MOTIVO-SECRETO-DEL-ADMIN'))
        db.session.add(FraudIncident(user_id=world['customer'], category=c.CATEGORY_ACCESS, status=c.INCIDENT_CONFIRMED, level=c.LEVEL_FRAUDE,
                                     title='Acceso confirmado', confirmation_reason='EVIDENCIA-PRIVADA'))
        db.session.add(HeldOperation(user_id=world['customer'], account_id=world['account'], kind='TRANSFERENCIA', amount=Decimal('960.5'),
                                     fingerprint='f' * 64, reason='Retenida', expires_at=now_utc() + timedelta(hours=72)))
        db.session.add(SecurityRestriction(user_id=world['customer'], scope=c.SCOPE_OPERATIONS, origin=c.ORIGIN_PREVENTIVE, reason='Motivo libre',
                                           expires_at=now_utc() + timedelta(minutes=30)))
        db.session.add(FraudModelVersion(version='iforest-20260101000000000', source='CSV', status='CANDIDATO', n_train=500, n_eval=125,
                                         features=['log_monto', 'proporcion_saldo'], metrics={'evaluacion': {'tasa_de_alertas': 0.08}, 'deriva_maxima_estandarizada': 0.12,
                                                                                               'supervisadas': {'disponibles': False}},
                                         validation={}, data_sha256='a' * 64, sklearn_version='1.7', python_version='3.12'))
        db.session.commit()


# ───────── Neutralización de fórmulas ─────────

@pytest.mark.parametrize('text', FORMULAS)
def test_cells_that_could_be_formulas_are_neutralized(text):
    assert export.neutralize(text).startswith("'") and export.cell(text).startswith("'")


@pytest.mark.parametrize('text', ['Operación normal', 'abc=def', 'Pago: luz', "O'Brien", '', '123', 'a-b'])
def test_harmless_text_is_left_untouched(text):
    assert export.neutralize(text) == text


def test_trusted_numbers_and_dates_are_not_mangled_but_untrusted_text_that_looks_numeric_is():
    assert export.cell(export.Raw('-0.4321')) == '-0.4321'                       # número formateado por el propio módulo
    assert export.cell('-0.4321') == "'-0.4321"                                   # el mismo texto, si viene de un campo no confiable
    assert export.cell(None) == '' and export.cell(True) == 'SI' and export.cell(12) == '12'


def test_the_csv_neutralizes_formulas_stored_in_text_fields_and_round_trips_special_characters(world):
    with world['app'].app_context():
        for index, formula in enumerate(FORMULAS):
            db.session.add(FraudIncident(user_id=world['customer'], category=c.CATEGORY_TRANSACTIONAL, status=c.INCIDENT_OPEN, level=c.LEVEL_SOSPECHOSO,
                                         title=formula))
        db.session.add(FraudIncident(user_id=world['customer'], category=c.CATEGORY_TRANSACTIONAL, status=c.INCIDENT_OPEN, level=c.LEVEL_SOSPECHOSO,
                                     title='con, coma "comillas" y\nsalto de línea ñ á'))
        db.session.commit()
    rows = _rows(world['admin'].get('/admin/fraude/exportar/incidentes.csv'))
    titles = [row[rows[0].index('titulo')] for row in rows[1:]]
    assert all(title.startswith("'") for title in titles if title.lstrip().startswith(('=', '+', '-', '@')))
    assert not any(title.lstrip().startswith(('=', '+', '-', '@')) for title in titles)
    assert 'con, coma "comillas" y\nsalto de línea ñ á' in titles                  # comas, comillas, saltos de línea y tildes se conservan


def test_formulas_in_signal_codes_and_versions_are_neutralized_too(world):
    with world['app'].app_context():
        _evaluation(world, signals=[{'code': '=cmd|calc', 'group': 'monto', 'points': 1, 'detail': ''}], model_version='@modelo')
    rows = _rows(world['admin'].get('/admin/fraude/exportar/evaluaciones.csv'))
    assert rows[1][rows[0].index('senales')] == "'=cmd|calc" and rows[1][rows[0].index('version_modelo')] == "'@modelo"


# ───────── Permisos y auditoría ─────────

@pytest.mark.parametrize('dataset', list(export.DATASETS))
@pytest.mark.parametrize('role', ['client', 'support'])
def test_customers_and_support_cannot_download_any_report(world, dataset, role):
    _seed(world)
    assert world[role].get(f'/admin/fraude/exportar/{dataset}.csv').status_code == 403
    with world['app'].app_context():
        assert AuditLog.query.filter(AuditLog.action.like('FRAUD_EXPORT_%')).count() == 0


@pytest.mark.parametrize('dataset', list(export.DATASETS))
def test_anonymous_visitors_cannot_download_reports(world, dataset):
    response = world['anon'].get(f'/admin/fraude/exportar/{dataset}.csv')
    assert response.status_code == 302 and '/login' in response.headers['Location']


@pytest.mark.parametrize('dataset', list(export.DATASETS))
def test_every_administrative_download_is_audited_and_does_not_change_other_data(world, dataset):
    _seed(world)
    with world['app'].app_context():
        before = {model.__tablename__: model.query.count() for model in (RiskEvaluation, FraudIncident, HeldOperation, SecurityRestriction, FraudModelVersion)}
    response = world['admin'].get(f'/admin/fraude/exportar/{dataset}.csv')
    assert response.status_code == 200
    with world['app'].app_context():
        entry = AuditLog.query.filter_by(action=f'FRAUD_EXPORT_{dataset.upper()}').one()
        assert entry.user_id == world['admin_id'] and entry.module == 'FRAUDE'
        assert before == {model.__tablename__: model.query.count() for model in (RiskEvaluation, FraudIncident, HeldOperation, SecurityRestriction, FraudModelVersion)}


def test_unknown_datasets_and_invalid_filters_are_rejected_without_an_audit_entry(world):
    assert world['admin'].get('/admin/fraude/exportar/usuarios.csv').status_code == 404
    for query in ('nivel=FRAUDE', 'nivel=NADA', 'desde=ayer', 'desde=2026-02-01&hasta=2026-01-01', 'tipo=OTRO', 'modo=turbo'):
        assert world['admin'].get('/admin/fraude/exportar/evaluaciones.csv?' + query).status_code == 400
    assert world['admin'].get('/admin/fraude/exportar/estadisticas.csv?dias=0').status_code == 400
    assert world['admin'].get('/admin/fraude/exportar/estadisticas.csv?dias=abc').status_code == 400
    with world['app'].app_context():
        assert AuditLog.query.filter(AuditLog.action.like('FRAUD_EXPORT_%')).count() == 0


# ───────── Formato ─────────

def test_the_download_is_utf8_with_bom_attachment_headers_and_no_caching(world):
    _seed(world)
    response = world['admin'].get('/admin/fraude/exportar/evaluaciones.csv')
    assert response.data.startswith(b'\xef\xbb\xbf') and response.headers['Content-Type'] == 'text/csv; charset=utf-8'
    assert re.fullmatch(r'attachment; filename="fraude-evaluaciones-\d{8}-\d{6}\.csv"', response.headers['Content-Disposition'])
    assert 'no-store' in response.headers['Cache-Control'] and response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['X-Export-Rows'] == '3' and response.headers['X-Export-Truncated'] == 'false'
    response.data.decode('utf-8')                                                  # decodifica sin errores


def test_evaluations_have_clear_headers_and_consistent_formats(world):
    _seed(world)
    rows = _rows(world['admin'].get('/admin/fraude/exportar/evaluaciones.csv?nivel=INTERMEDIO'))
    header, row = rows[0], dict(zip(rows[0], rows[1]))
    assert header[:6] == ['id', 'fecha_utc', 'cliente_id', 'contexto', 'evento', 'nivel'] and len(rows) == 2
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', row['fecha_utc'])
    assert row['puntuacion'] == '30' and row['puntuacion_modelo'] == '-0.4321' and row['duracion_ms'] == '3.14'
    assert row['senales'] == 'MONTO_80_SALDO|DESTINATARIO_NUEVO' and row['modo'] == 'enforce' and row['cliente_id'] == str(world['customer'])


def test_held_operations_export_money_with_two_decimals_and_a_dot(world):
    _seed(world)
    row = dict(zip(*_rows(world['admin'].get('/admin/fraude/exportar/retenciones.csv'))[:2]))
    assert row['monto'] == '960.50' and row['estado'] == 'RETENIDA' and row['operacion'] == 'TRANSFERENCIA'


def test_the_statistics_and_model_reports_contain_the_expected_metrics(world):
    _seed(world)
    stats = {metric: value for metric, value in _rows(world['admin'].get('/admin/fraude/exportar/estadisticas.csv?dias=30'))[1:]}
    assert stats['dias_analizados'] == '30' and stats['evaluaciones_total'] == '3' and stats['evaluaciones_sospechoso'] == '1'
    assert stats['restricciones_activas'] == '1' and stats['operaciones_retenidas_vigentes'] == '1' and stats['incidentes_confirmados'] == '1'
    models = dict(zip(*_rows(world['admin'].get('/admin/fraude/exportar/modelos.csv'))[:2]))
    assert models['version'] == 'iforest-20260101000000000' and models['tasa_de_alertas_evaluacion'] == '0.0800' and models['metricas_supervisadas'] == 'NO'
    assert models['huella_datos_sha256'] == 'a' * 64


# ───────── Filtros ─────────

def test_evaluation_filters_by_level_type_mode_and_dates(world):
    _seed(world)
    count = lambda query: len(_rows(world['admin'].get('/admin/fraude/exportar/evaluaciones.csv' + query))) - 1
    assert count('') == 3 and count('?nivel=NORMAL') == 1 and count('?nivel=SOSPECHOSO') == 1
    assert count('?tipo=ACCESO') == 1 and count('?tipo=OPERACIONES') == 2 and count('?modo=enforce') == 1
    today, old = now_utc().date().isoformat(), (now_utc() - timedelta(days=3)).date().isoformat()
    assert count(f'?desde={today}') == 2 and count(f'?hasta={old}') == 1 and count(f'?desde={old}&hasta={old}') == 1


def test_incident_restriction_hold_and_model_filters(world):
    _seed(world)
    count = lambda dataset, query: len(_rows(world['admin'].get(f'/admin/fraude/exportar/{dataset}.csv{query}'))) - 1
    assert count('incidentes', '') == 2 and count('incidentes', '?estado=CONFIRMADO') == 1 and count('incidentes', '?nivel=FRAUDE') == 1
    assert count('incidentes', '?tipo=ACCESO') == 1 and count('incidentes', '?estado=DESCARTADO') == 0
    assert count('retenciones', '?estado=RETENIDA') == 1 and count('retenciones', '?estado=EJECUTADA') == 0 and count('retenciones', '?tipo=PAGO') == 0
    assert count('restricciones', '?tipo=OPERACIONES') == 1 and count('restricciones', '?tipo=ACCESO') == 0 and count('restricciones', '?estado=LEVANTADA') == 0
    assert count('modelos', '?estado=CANDIDATO') == 1 and count('modelos', '?estado=ACTIVO') == 0


def test_a_large_export_is_truncated_and_says_so(world, monkeypatch):
    monkeypatch.setattr(export, 'MAX_ROWS', 3)
    with world['app'].app_context():
        for _ in range(5):
            _evaluation(world)
    response = world['admin'].get('/admin/fraude/exportar/evaluaciones.csv')
    assert len(_rows(response)) - 1 == 3 and response.headers['X-Export-Truncated'] == 'true' and response.headers['X-Export-Rows'] == '3'


# ───────── Datos mínimos ─────────

@pytest.mark.parametrize('dataset', list(export.DATASETS))
def test_exports_never_contain_personal_data_secrets_or_free_text_written_by_administrators(world, dataset):
    _seed(world)
    body = world['admin'].get(f'/admin/fraude/exportar/{dataset}.csv').data.decode('utf-8-sig')
    for forbidden in ('privado@example.test', '55000001', 'ecustomer', 'Cliente ecustomer', 'MOTIVO-SECRETO-DEL-ADMIN', 'EVIDENCIA-PRIVADA',
                      'Motivo libre', 'password', 'otp', '127.0.0.1'):
        assert forbidden not in body
    header = body.splitlines()[0].lower()
    assert not re.search(r'correo|email|nombre|dni|telefono|(^|,)ip|contrase|clave|token|otp|(^|,)codigo(,|$)', header)


def test_the_panel_lists_one_form_per_dataset_with_the_expected_filters(world):
    _seed(world)
    html = world['admin'].get('/admin/fraude').get_data(as_text=True)
    for dataset in export.DATASETS:
        assert f'/admin/fraude/exportar/{dataset}.csv' in html
    assert 'Descargar CSV' in html and 'Sin nombres, correos ni datos de contacto' in html and 'iforest-20260101000000000' in html
