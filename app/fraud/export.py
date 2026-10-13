"""Exportación CSV de los resultados antifraude (solo administradores; cada descarga queda en la auditoría).

Formato consistente: UTF-8 (con BOM para que Excel respete las tildes), cabeceras en español, fechas ``AAAA-MM-DD HH:MM:SS`` en UTC,
importes con dos decimales y punto decimal, y puntuaciones con cuatro.

Seguridad:

* **Inyección de fórmulas CSV**: toda celda de texto cuyo primer carácter útil sea ``=``, ``+``, ``-``, ``@``, tabulador o retorno de
  carro se neutraliza anteponiendo un apóstrofo, para que Excel/Calc/Sheets no la interpreten como fórmula. Solo se salta esa
  neutralización en los números y fechas que formatea este módulo (``Raw``), nunca en texto de origen no confiable.
* **Datos mínimos**: no hay nombres, correos, DNI, contraseñas, OTP ni IP; los clientes se identifican por su número interno.
  Los motivos escritos a mano por administradores no se exportan.
* Máximo ``MAX_ROWS`` filas por descarga (la respuesta indica si se truncó).
"""
import csv
import io
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import desc

from ..models import AuditLog, FraudIncident, FraudModelVersion, HeldOperation, RiskEvaluation, SecurityRestriction, now_utc
from . import constants as c, metrics

MAX_ROWS = 10_000
FORMULA_START = ('=', '+', '-', '@', '\t', '\r', '\n')
EVALUATION_LEVELS = (c.LEVEL_NORMAL, c.LEVEL_INTERMEDIO, c.LEVEL_SOSPECHOSO, c.LEVEL_NO_EVALUADO)
INCIDENT_LEVELS = (c.LEVEL_SOSPECHOSO, c.LEVEL_FRAUDE)


class Raw(str):
    """Texto ya formateado por este módulo (número o fecha); no se neutraliza."""


def neutralize(value):
    """Texto seguro para hojas de cálculo: si pudiera interpretarse como fórmula, se antepone un apóstrofo."""
    text = str(value)
    return "'" + text if text.lstrip().startswith(FORMULA_START) else text


def _ts(value):
    return Raw(value.strftime('%Y-%m-%d %H:%M:%S')) if value else ''


def _money(value):
    return Raw(f'{Decimal(str(value)):.2f}') if value is not None else ''


def _num(value, places=4):
    return Raw(f'{float(value):.{places}f}') if value is not None else ''


def _int(value):
    return Raw(str(int(value))) if value is not None else ''


def cell(value):
    if value is None:
        return ''
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, bool):
        return 'SI' if value else 'NO'
    if isinstance(value, (int,)):
        return str(value)
    return neutralize(value)


def to_csv_bytes(headers, rows):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(headers)
    for row in rows:
        writer.writerow([cell(value) for value in row])
    return buffer.getvalue().encode('utf-8-sig')


def parse_filters(args):
    """Filtros comunes desde la URL. Lanza ``ValueError`` si alguno no es válido."""
    def parse_date(name):
        raw = (args.get(name) or '').strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f'La fecha «{name}» no es válida (use AAAA-MM-DD).') from None
    filters = {'desde': parse_date('desde'), 'hasta': parse_date('hasta'),
               'nivel': (args.get('nivel') or '').strip().upper(), 'estado': (args.get('estado') or '').strip().upper(),
               'tipo': (args.get('tipo') or '').strip().upper(), 'modo': (args.get('modo') or '').strip().lower()}
    if filters['desde'] and filters['hasta'] and filters['desde'] > filters['hasta']:
        raise ValueError('La fecha inicial es posterior a la final.')
    try:
        filters['dias'] = int(args.get('dias') or 7)
    except ValueError:
        raise ValueError('El número de días no es válido.') from None
    if not 1 <= filters['dias'] <= 365:
        raise ValueError('El número de días debe estar entre 1 y 365.')
    return filters


def _check(value, allowed, label):
    if value and value not in allowed:
        raise ValueError(f'El valor de «{label}» no es válido.')


def _range(query, column, filters):
    if filters['desde']:
        query = query.filter(column >= datetime.combine(filters['desde'], time.min))
    if filters['hasta']:
        query = query.filter(column < datetime.combine(filters['hasta'] + timedelta(days=1), time.min))
    return query


def _limited(query, order_column):
    rows = query.order_by(desc(order_column)).limit(MAX_ROWS + 1).all()
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def evaluations(filters):
    _check(filters['nivel'], EVALUATION_LEVELS, 'nivel')
    _check(filters['tipo'], ('ACCESO', 'OPERACIONES'), 'tipo')
    _check(filters['modo'], (c.MODE_SHADOW, c.MODE_ENFORCE), 'modo')
    query = _range(RiskEvaluation.query, RiskEvaluation.created_at, filters)
    if filters['nivel']:
        query = query.filter(RiskEvaluation.level == filters['nivel'])
    if filters['tipo'] == 'ACCESO':
        query = query.filter(RiskEvaluation.context == c.CONTEXT_ACCESS)
    elif filters['tipo'] == 'OPERACIONES':
        query = query.filter(RiskEvaluation.context != c.CONTEXT_ACCESS)
    if filters['modo']:
        query = query.filter(RiskEvaluation.mode == filters['modo'])
    items, truncated = _limited(query, RiskEvaluation.id)
    headers = ['id', 'fecha_utc', 'cliente_id', 'contexto', 'evento', 'nivel', 'puntuacion', 'confianza', 'senales', 'version_reglas',
               'version_modelo', 'puntuacion_modelo', 'modo', 'accion_recomendada', 'accion_aplicada', 'estado_evaluacion',
               'codigo_error', 'duracion_ms', 'movimiento_id']
    rows = [[_int(e.id), _ts(e.created_at), _int(e.user_id), e.context, e.event, e.level, _int(e.score), e.confidence,
             '|'.join(str(s.get('code', '')) for s in (e.signals or [])), e.rules_version, e.model_version, _num(e.model_score),
             e.mode, e.recommended_action, e.applied_action, e.status, e.error_code, _num(e.duration_ms, 2), _int(e.transaction_id)]
            for e in items]
    return headers, rows, truncated


def incidents(filters):
    _check(filters['nivel'], INCIDENT_LEVELS, 'nivel')
    _check(filters['estado'], c.INCIDENT_STATUSES, 'estado')
    _check(filters['tipo'], (c.CATEGORY_TRANSACTIONAL, c.CATEGORY_ACCESS), 'tipo')
    query = _range(FraudIncident.query, FraudIncident.created_at, filters)
    for column, key in ((FraudIncident.level, 'nivel'), (FraudIncident.status, 'estado'), (FraudIncident.category, 'tipo')):
        if filters[key]:
            query = query.filter(column == filters[key])
    items, truncated = _limited(query, FraudIncident.id)
    headers = ['id', 'fecha_apertura_utc', 'cliente_id', 'categoria', 'origen', 'nivel', 'estado', 'titulo', 'evaluacion_id',
               'confirmado_utc', 'confirmado_por_id', 'cerrado_utc', 'cerrado_por_id', 'ultima_actualizacion_utc']
    rows = [[_int(i.id), _ts(i.created_at), _int(i.user_id), i.category, i.origin, i.level, i.status, i.title, _int(i.evaluation_id),
             _ts(i.confirmed_at), _int(i.confirmed_by_id), _ts(i.closed_at), _int(i.closed_by_id), _ts(i.updated_at)] for i in items]
    return headers, rows, truncated


def held_operations(filters):
    """«Operaciones sospechosas»: las retenidas antes de ejecutarse (no modificaron saldos)."""
    _check(filters['estado'], (c.HOLD_HELD, c.HOLD_AUTHORIZED, c.HOLD_REJECTED, c.HOLD_EXECUTED, c.HOLD_EXPIRED), 'estado')
    _check(filters['tipo'], c.HOLDABLE_KINDS, 'tipo')
    query = _range(HeldOperation.query, HeldOperation.created_at, filters)
    if filters['estado']:
        query = query.filter(HeldOperation.status == filters['estado'])
    if filters['tipo']:
        query = query.filter(HeldOperation.kind == filters['tipo'])
    items, truncated = _limited(query, HeldOperation.id)
    headers = ['id', 'fecha_utc', 'cliente_id', 'cuenta_id', 'operacion', 'monto', 'estado', 'vence_utc', 'revisado_por_id',
               'revisado_utc', 'evaluacion_id', 'incidente_id', 'movimiento_id']
    rows = [[_int(h.id), _ts(h.created_at), _int(h.user_id), _int(h.account_id), h.kind, _money(h.amount), h.status, _ts(h.expires_at),
             _int(h.reviewed_by_id), _ts(h.reviewed_at), _int(h.evaluation_id), _int(h.incident_id), _int(h.transaction_id)] for h in items]
    return headers, rows, truncated


def restrictions(filters):
    _check(filters['estado'], (c.RESTRICTION_ACTIVE, c.RESTRICTION_LIFTED, c.RESTRICTION_EXPIRED), 'estado')
    _check(filters['tipo'], c.SCOPES, 'tipo')
    query = _range(SecurityRestriction.query, SecurityRestriction.created_at, filters)
    if filters['estado']:
        query = query.filter(SecurityRestriction.status == filters['estado'])
    if filters['tipo']:
        query = query.filter(SecurityRestriction.scope == filters['tipo'])
    items, truncated = _limited(query, SecurityRestriction.id)
    headers = ['id', 'cliente_id', 'incidente_id', 'alcance', 'origen', 'estado', 'desde_utc', 'vence_utc', 'creada_por_id',
               'levantada_por_id', 'levantada_utc', 'version_reglas']
    rows = [[_int(r.id), _int(r.user_id), _int(r.incident_id), r.scope, r.origin, r.status, _ts(r.starts_at), _ts(r.expires_at),
             _int(r.created_by_id), _int(r.lifted_by_id), _ts(r.lifted_at), r.rules_version] for r in items]
    return headers, rows, truncated


def models(filters):
    _check(filters['estado'], ('CANDIDATO', 'ACTIVO', 'ARCHIVADO', 'RECHAZADO'), 'estado')
    query = _range(FraudModelVersion.query, FraudModelVersion.trained_at, filters)
    if filters['estado']:
        query = query.filter(FraudModelVersion.status == filters['estado'])
    items, truncated = _limited(query, FraudModelVersion.id)
    headers = ['version', 'algoritmo', 'origen', 'estado', 'entrenado_utc', 'filas_entrenamiento', 'filas_evaluacion', 'caracteristicas',
               'tasa_de_alertas_evaluacion', 'deriva_maxima', 'metricas_supervisadas', 'huella_datos_sha256', 'scikit_learn', 'python',
               'activado_utc']
    rows = [[m.version, m.algorithm, m.source, m.status, _ts(m.trained_at), _int(m.n_train), _int(m.n_eval), '|'.join(m.features or []),
             _num(((m.metrics or {}).get('evaluacion') or {}).get('tasa_de_alertas')), _num((m.metrics or {}).get('deriva_maxima_estandarizada')),
             'SI' if ((m.metrics or {}).get('supervisadas') or {}).get('disponibles') else 'NO', m.data_sha256, m.sklearn_version,
             m.python_version, _ts(m.activated_at)] for m in items]
    return headers, rows, truncated


def audit_log(filters):
    """Auditoría de las acciones antifraude (módulo FRAUDE). Sin texto libre: solo identificadores y códigos de acción."""
    if filters['tipo'] and not re.fullmatch(r'[A-Z_]{3,80}', filters['tipo']):
        raise ValueError('El valor de «tipo» no es válido.')
    query = _range(AuditLog.query.filter(AuditLog.module == 'FRAUDE'), AuditLog.created_at, filters)
    if filters['tipo']:
        query = query.filter(AuditLog.action == filters['tipo'])
    items, truncated = _limited(query, AuditLog.id)
    headers = ['id', 'fecha_utc', 'usuario_id', 'accion', 'resultado']
    rows = [[_int(a.id), _ts(a.created_at), _int(a.user_id), a.action, a.result] for a in items]
    return headers, rows, truncated


def statistics(filters):
    summary = metrics.summary(filters['dias'])
    rows = [['dias_analizados', _int(summary['days'])], ['evaluaciones_total', _int(summary['total'])]]
    for level in EVALUATION_LEVELS:
        rows.append([f'evaluaciones_{level.lower()}', _int(summary['by_level'].get(level, 0))])
    rows += [['tasa_de_fallos_de_evaluacion', _num(summary['error_rate'])],
             ['proporcion_que_requiere_verificacion', _num(summary['pct_needing_verification'])],
             ['latencia_media_ms', _num(summary['latency_ms']['mean'], 2)], ['latencia_p95_ms', _num(summary['latency_ms']['p95'], 2)],
             ['latencia_maxima_ms', _num(summary['latency_ms']['max'], 2)], ['restricciones_activas', _int(summary['active_restrictions'])],
             ['operaciones_retenidas_vigentes', _int(summary['held_operations'])],
             ['incidentes_confirmados', _int(summary['confirmed'])], ['falsos_positivos_revisados', _int(summary['false_positives_reviewed'])]]
    for status, count in sorted(summary['incidents_by_status'].items()):
        rows.append([f'incidentes_{status.lower()}', _int(count)])
    for bucket, count in summary['score_buckets'].items():
        rows.append([f'puntuacion_{bucket}', _int(count)])
    rows += [['versiones_de_reglas', '|'.join(summary['rules_versions'])], ['versiones_de_modelo', '|'.join(summary['model_versions'])],
             ['modos', '|'.join(summary['modes'])]]
    return ['metrica', 'valor'], rows, False


DATASETS = {
    'evaluaciones': (evaluations, 'Evaluaciones de riesgo'),
    'incidentes': (incidents, 'Incidentes'),
    'retenciones': (held_operations, 'Operaciones sospechosas retenidas'),
    'restricciones': (restrictions, 'Restricciones'),
    'estadisticas': (statistics, 'Estadísticas'),
    'modelos': (models, 'Versiones del detector de anomalías'),
    'auditoria': (audit_log, 'Auditoría de acciones antifraude'),
}


def build(dataset, args):
    """CSV de un conjunto de datos con los filtros de la URL. Devuelve ``(bytes, filas, truncado)``."""
    builder = DATASETS[dataset][0]
    headers, rows, truncated = builder(parse_filters(args))
    return to_csv_bytes(headers, rows), len(rows), truncated


def filename(dataset):
    return f'fraude-{dataset}-{now_utc().strftime("%Y%m%d-%H%M%S")}.csv'


# Filtros que admite cada conjunto de datos: (nombre, etiqueta, opciones o None para fechas/números).
FORMS = [
    ('evaluaciones', [('desde', 'Desde', None), ('hasta', 'Hasta', None), ('nivel', 'Nivel', EVALUATION_LEVELS),
                      ('tipo', 'Tipo', ('ACCESO', 'OPERACIONES')), ('modo', 'Modo', (c.MODE_SHADOW, c.MODE_ENFORCE))]),
    ('incidentes', [('desde', 'Desde', None), ('hasta', 'Hasta', None), ('nivel', 'Nivel', INCIDENT_LEVELS),
                    ('estado', 'Estado', c.INCIDENT_STATUSES), ('tipo', 'Tipo', (c.CATEGORY_TRANSACTIONAL, c.CATEGORY_ACCESS))]),
    ('retenciones', [('desde', 'Desde', None), ('hasta', 'Hasta', None),
                     ('estado', 'Estado', (c.HOLD_HELD, c.HOLD_AUTHORIZED, c.HOLD_REJECTED, c.HOLD_EXECUTED, c.HOLD_EXPIRED)),
                     ('tipo', 'Operación', c.HOLDABLE_KINDS)]),
    ('restricciones', [('desde', 'Desde', None), ('hasta', 'Hasta', None),
                       ('estado', 'Estado', (c.RESTRICTION_ACTIVE, c.RESTRICTION_LIFTED, c.RESTRICTION_EXPIRED)),
                       ('tipo', 'Alcance', c.SCOPES)]),
    ('modelos', [('desde', 'Desde', None), ('hasta', 'Hasta', None), ('estado', 'Estado', ('CANDIDATO', 'ACTIVO', 'ARCHIVADO', 'RECHAZADO'))]),
    ('auditoria', [('desde', 'Desde', None), ('hasta', 'Hasta', None), ('tipo', 'Acción', ('FRAUD_OPERATION_HELD', 'FRAUD_ACCESS_RESTRICTED',
                  'FRAUD_INCIDENT_CONFIRMED', 'FRAUD_INCIDENT_DISCARDED', 'FRAUD_RESTRICTION_LIFTED', 'FRAUD_HOLD_AUTHORIZED', 'FRAUD_HOLD_REJECTED',
                  'FRAUD_MODEL_ACTIVATED', 'FRAUD_MODEL_REJECTED', 'FRAUD_MODEL_DEACTIVATED'))]),
    ('estadisticas', [('dias', 'Días a resumir (1-365)', 'numero')]),
]
