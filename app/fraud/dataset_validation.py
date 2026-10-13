"""Validación automática del dataset sintético combinado (transacciones + accesos).

``validate_combined`` devuelve un informe con cada comprobación (``ok`` y detalle), la distribución de clases y la capacidad de
separación **univariante** de cada predictor (para demostrar que ninguna variable aislada revela la etiqueta). No lanza excepciones
por datos malos: informa; ``assert_valid`` sí lo hace para los flujos que deben detenerse.
"""
import re
from datetime import datetime

from . import synthetic_events as se

TX_NUMERIC = ['importe', 'saldo_anterior', 'saldo_posterior', 'beneficiario_nuevo', 'destinatarios_distintos_24h', 'operaciones_2m',
              'operaciones_10m', 'operaciones_1h', 'n_historial', 'fallos_login_30m']
TX_ONLY = TX_NUMERIC + ['tipo_operacion', 'importe_tipico']
ACCESS_ONLY = ['resultado_login', 'intentos_fallidos_recientes', 'solicitudes_otp_1h', 'viaje_imposible']
FLAGS = ['contexto_nuevo', 'dispositivo_nuevo', 'ubicacion_nueva', 'recuperacion_clave_reciente']
EVENT_TYPES = ('TRANSACCION', 'ACCESO')
LEVELS = ('NORMAL', 'INTERMEDIO', 'SOSPECHOSO')
MAX_UNIVARIATE_AUC = 0.90          # ninguna variable observable debe separar las clases mejor que esto por sí sola
TIME_FORMAT = '%Y-%m-%dT%H:%M:%S'

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_LONG_DIGITS = re.compile(r'\d{9,}')
_PHONE = re.compile(r'(?:\+?\d{2,3}[\s-]?)?\d{3}[\s-]\d{3}[\s-]?\d{3,4}')


def _luhn(number):
    total = 0
    for index, digit in enumerate(reversed(number)):
        value = int(digit) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _check(checks, name, ok, detail=''):
    checks.append({'comprobacion': name, 'ok': bool(ok), 'detalle': str(detail)})


def _numeric(series):
    import pandas as pd
    return pd.to_numeric(series, errors='coerce')


def _univariate(frame, columns):
    """Separabilidad univariante (max(AUC, 1-AUC)) de cada variable frente a is_fraud."""
    from sklearn.metrics import roc_auc_score
    out = {}
    target = _numeric(frame['is_fraud'])
    if not target.isin([0, 1]).all() or target.nunique() < 2:        # etiqueta corrupta: ya la señala otra comprobación
        return out
    for column in columns:
        values = _numeric(frame[column]).fillna(0)
        if values.nunique() < 2:
            continue
        auc = float(roc_auc_score(target.astype(int), values))
        out[column] = round(max(auc, 1 - auc), 4)
    return out


def validate_combined(frame, *, expected_rows=None, expected_transactions=None, expected_accesses=None):
    import numpy as np
    import pandas as pd
    checks = []
    columns = list(frame.columns)
    _check(checks, 'Sin columnas duplicadas', len(set(columns)) == len(columns), [c for c in set(columns) if columns.count(c) > 1])
    _check(checks, 'Columnas exactamente las del esquema', columns == se.COLUMNS,
           f'faltan={sorted(set(se.COLUMNS) - set(columns))} sobran={sorted(set(columns) - set(se.COLUMNS))}')
    if columns != se.COLUMNS:
        return {'ok': False, 'checks': checks}

    if expected_rows is not None:
        _check(checks, f'Exactamente {expected_rows} filas de datos', len(frame) == expected_rows, len(frame))
    counts = frame['event_type'].value_counts().to_dict()
    _check(checks, 'Tipos de evento válidos', set(frame['event_type']) <= set(EVENT_TYPES), sorted(set(frame['event_type']) - set(EVENT_TYPES)))
    if expected_transactions is not None:
        _check(checks, f'Exactamente {expected_transactions} transacciones', counts.get('TRANSACCION') == expected_transactions, counts.get('TRANSACCION'))
    if expected_accesses is not None:
        _check(checks, f'Exactamente {expected_accesses} accesos', counts.get('ACCESO') == expected_accesses, counts.get('ACCESO'))
    _check(checks, 'Identificadores únicos y con formato EVT-######', frame['event_id'].is_unique and frame['event_id'].str.fullmatch(r'EVT-\d{6}').all(),
           f'duplicados={int(frame["event_id"].duplicated().sum())}')

    stamps = pd.to_datetime(frame['timestamp'], format=TIME_FORMAT, errors='coerce')
    _check(checks, 'Fechas válidas (ISO 8601 AAAA-MM-DDThh:mm:ss)', stamps.notna().all(), int(stamps.isna().sum()))
    ordered = bool(stamps.notna().all() and stamps.is_monotonic_increasing)
    _check(checks, 'Orden cronológico no decreciente', ordered, '')
    if stamps.notna().all():
        _check(checks, 'hora_del_dia y dia_semana coinciden con la fecha',
               (stamps.dt.hour == _numeric(frame['hora_del_dia'])).all() and (stamps.dt.weekday == _numeric(frame['dia_semana'])).all(), '')
        span = f'{stamps.min().isoformat()} → {stamps.max().isoformat()}'
    else:
        span = ''
    label = _numeric(frame['is_fraud'])
    _check(checks, 'Etiqueta is_fraud binaria (0/1)', label.isin([0, 1]).all(), sorted(frame['is_fraud'].astype(str).unique())[:5])
    _check(checks, 'is_synthetic = 1 en todos los registros', (_numeric(frame['is_synthetic']) == 1).all(), '')
    score = _numeric(frame['puntuacion_riesgo_sintetica'])
    _check(checks, 'Puntuación descriptiva en [0, 100]', score.between(0, 100).all(), '')
    _check(checks, 'Nivel descriptivo válido', set(frame['nivel_riesgo_sintetico']) <= set(LEVELS), sorted(set(frame['nivel_riesgo_sintetico'])))
    for column in FLAGS:
        _check(checks, f'{column} binaria', _numeric(frame[column]).isin([0, 1]).all(), '')
    _check(checks, 'fallos_otp_recientes entero ≥ 0', (_numeric(frame['fallos_otp_recientes']) >= 0).all(), '')

    tx = frame[frame['event_type'] == 'TRANSACCION']
    ac = frame[frame['event_type'] == 'ACCESO']
    _check(checks, 'Las transacciones no traen columnas de acceso (esquema coherente por tipo)', (ac[TX_ONLY] == '').all().all() and (tx[ACCESS_ONLY] == '').all().all(), '')
    _check(checks, 'Tipos de operación válidos', set(tx['tipo_operacion']) <= set(se.ALL_KINDS), sorted(set(tx['tipo_operacion'])))
    amount, before, after = _numeric(tx['importe']), _numeric(tx['saldo_anterior']), _numeric(tx['saldo_posterior'])
    _check(checks, 'Importes finitos, positivos y razonables (≤ 1.000.000)', (np.isfinite(amount) & (amount > 0) & (amount <= 1_000_000)).all(), '')
    _check(checks, 'Saldos finitos y no negativos', (np.isfinite(before) & np.isfinite(after) & (before >= 0) & (after >= 0)).all(), '')
    credit = tx['tipo_operacion'] == 'DEPOSITO'
    expected_after = np.where(credit, before + amount, before - amount)
    _check(checks, 'saldo_posterior = saldo_anterior ± importe (ingresos suman, salidas restan)', np.allclose(after, expected_after, atol=0.011), '')
    _check(checks, 'Ninguna salida supera el saldo disponible', (amount[~credit] <= before[~credit] + 0.011).all(), '')
    continuity = True
    for _, group in tx.groupby('account_id'):
        a, b = _numeric(group['saldo_anterior']).to_numpy(), _numeric(group['saldo_posterior']).to_numpy()
        if len(group) > 1 and not np.allclose(a[1:], b[:-1], atol=0.011):
            continuity = False
            break
    _check(checks, 'Continuidad de saldos por cuenta (saldo_anterior = saldo_posterior previo)', continuity, '')
    ops = [_numeric(tx[c]) for c in ('operaciones_2m', 'operaciones_10m', 'operaciones_1h')]
    _check(checks, 'Contadores de ventana coherentes (2 min ≤ 10 min ≤ 1 h)', ((ops[0] <= ops[1]) & (ops[1] <= ops[2])).all(), '')
    _check(checks, 'Resultado de acceso válido', set(ac['resultado_login']) <= {'EXITOSO', 'FALLIDO'}, sorted(set(ac['resultado_login'])))
    _check(checks, 'Contadores de acceso enteros ≥ 0', ((_numeric(ac['intentos_fallidos_recientes']) >= 0) & (_numeric(ac['solicitudes_otp_1h']) >= 0)).all(), '')

    text_columns = ['event_id', 'account_id', 'scenario', 'tipo_operacion', 'resultado_login', 'event_type', 'nivel_riesgo_sintetico']
    flat = frame[text_columns].astype(str)
    pii = []
    if flat.apply(lambda col: col.str.contains(_EMAIL)).any().any():
        pii.append('correo electrónico')
    if flat.apply(lambda col: col.str.contains(_LONG_DIGITS)).any().any():
        pii.append('secuencia larga de dígitos')
    if flat.apply(lambda col: col.str.contains(_PHONE)).any().any():
        pii.append('número de teléfono')
    _check(checks, 'Cuentas seudónimas ACC-#### y sin datos personales ni bancarios reales', frame['account_id'].str.fullmatch(r'ACC-\d{4}').all() and not pii, pii)
    cards = [value for value in re.findall(r'\d{13,19}', ' '.join(flat.to_numpy().ravel())) if _luhn(value)]
    _check(checks, 'Ningún número con formato de tarjeta (Luhn)', not cards, len(cards))

    separability = {'TRANSACCION': _univariate(tx[tx['tipo_operacion'] != 'DEPOSITO'], ['importe', 'saldo_anterior', 'beneficiario_nuevo', 'destinatarios_distintos_24h',
                                                                                      'operaciones_2m', 'operaciones_10m', 'operaciones_1h', 'fallos_login_30m',
                                                                                      'fallos_otp_recientes', 'contexto_nuevo', 'recuperacion_clave_reciente',
                                                                                      'dispositivo_nuevo', 'ubicacion_nueva', 'hora_del_dia']),
                    'ACCESO': _univariate(ac, ['intentos_fallidos_recientes', 'solicitudes_otp_1h', 'fallos_otp_recientes', 'contexto_nuevo', 'recuperacion_clave_reciente',
                                               'dispositivo_nuevo', 'ubicacion_nueva', 'viaje_imposible', 'hora_del_dia'])}
    descriptive = {'TRANSACCION': _univariate(tx[tx['tipo_operacion'] != 'DEPOSITO'], ['puntuacion_riesgo_sintetica']),
                   'ACCESO': _univariate(ac, ['puntuacion_riesgo_sintetica'])}
    worst = {event: max(values.items(), key=lambda item: item[1]) if values else None for event, values in separability.items()}
    _check(checks, f'Ninguna variable observable individual separa las clases por sí sola (AUC univariante ≤ {MAX_UNIVARIATE_AUC})',
           all(w is None or w[1] <= MAX_UNIVARIATE_AUC for w in worst.values()), worst)

    distribution = {}
    for name, part in (('TRANSACCION', tx), ('ACCESO', ac)):
        positives = int(_numeric(part['is_fraud']).sum())
        distribution[name] = {'eventos': int(len(part)), 'is_fraud_1': positives, 'is_fraud_0': int(len(part) - positives),
                              'prevalencia': round(positives / len(part), 4) if len(part) else None}
    distribution['TOTAL'] = {'eventos': int(len(frame)), 'is_fraud_1': distribution['TRANSACCION']['is_fraud_1'] + distribution['ACCESO']['is_fraud_1']}
    return {'ok': all(check['ok'] for check in checks), 'checks': checks, 'distribucion': distribution, 'separabilidad_univariante': separability,
            'puntuacion_descriptiva_auc_univariante': descriptive, 'rango_temporal': span}


class DatasetInvalid(ValueError):
    pass


def assert_valid(report):
    failed = [check for check in report['checks'] if not check['ok']]
    if failed:
        raise DatasetInvalid('El dataset no supera la validación: ' + '; '.join(f'{c["comprobacion"]} ({c["detalle"]})' for c in failed))
    return report
