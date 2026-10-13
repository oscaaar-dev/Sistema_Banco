"""Entrenamiento y evaluación de candidatos a partir del CSV combinado de transacciones y accesos.

El CSV combinado se conserva como **fuente original**; el procesamiento lo separa internamente por tipo de evento (no se crean
datasets fuente independientes). Se entrenan **dos modelos distintos** porque transacciones y accesos no comparten variables:

* transacciones (solo salidas de dinero; los depósitos no se modelan) → 12 variables observables que el motor ya sabe calcular;
* accesos → 6 variables observables.

Además se registra un candidato **clásico** por el flujo existente (``anomaly.train_candidate``, 4 variables) para comparar.

Reglas que este módulo hace cumplir (y que el informe verifica y documenta):

* Algoritmo **no supervisado** (Isolation Forest): las etiquetas ``is_fraud`` NO intervienen en el entrenamiento ni en el umbral; solo
  se usan para evaluar. Hiperparámetros y tasa de alertas fijados **a priori**, no ajustados con la evaluación.
* División **cronológica**: entrenan los eventos anteriores a un instante de corte y se evalúa con los posteriores (sin mezcla aleatoria).
* El umbral de alerta es un percentil de las puntuaciones del **periodo de entrenamiento**; el tramo de evaluación nunca lo toca.
* Predictores: solo variables observables y calculables por el sistema. Quedan fuera identificadores, ``scenario`` (procedencia),
  ``is_fraud``, ``is_synthetic``, la puntuación y el nivel descriptivos, las variables que el sistema no recopila (dispositivo,
  ubicación, viaje imposible) y la hora (el motor no la usa).
* Las etiquetas son sintéticas: no equivalen a fraudes confirmados por analistas.
"""
import hashlib
import io
import json
import platform
import sys
import time
from pathlib import Path

from flask import current_app

from ..models import FraudModelVersion
from . import anomaly, csv_training, dataset_validation as dv, rules, synthetic_events as se
from .features import AccessFeatures, OperationFeatures

TX_FEATURES = ('log_importe', 'proporcion_saldo', 'ratio_importe_tipico', 'operaciones_2m', 'operaciones_10m', 'operaciones_1h',
               'beneficiario_nuevo', 'destinatarios_distintos_24h', 'fallos_login_30m', 'fallos_otp_recientes',
               'recuperacion_clave_reciente', 'contexto_nuevo')
ACCESS_FEATURES = ('fallido', 'intentos_fallidos_recientes', 'fallos_otp_recientes', 'solicitudes_otp_1h', 'contexto_nuevo',
                   'recuperacion_clave_reciente')
EXCLUDED = {
    'event_id': 'identificador', 'account_id': 'identificador', 'timestamp': 'solo para ordenar y dividir cronológicamente',
    'event_type': 'solo para separar los modelos', 'scenario': 'procedencia de la generación: revela la etiqueta',
    'is_fraud': 'etiqueta: solo evaluación', 'is_synthetic': 'procedencia', 'puntuacion_riesgo_sintetica': 'resume las demás variables (atajo) y es descriptiva',
    'nivel_riesgo_sintetico': 'derivado de la puntuación descriptiva', 'dispositivo_nuevo': 'el sistema actual no la recopila',
    'ubicacion_nueva': 'el sistema actual no la recopila', 'viaje_imposible': 'el sistema actual no la recopila',
    'hora_del_dia': 'el motor no usa la hora (zona horaria no fiable)', 'dia_semana': 'idem',
    'saldo_posterior': 'resultado de la operación: redundante con saldo_anterior e importe', 'tipo_operacion': 'se usa para estratificar la evaluación',
    'importe / saldo_anterior / n_historial / importe_tipico': 'entran transformados (log_importe, proporcion_saldo, ratio_importe_tipico)',
}
NUMERIC = ['importe', 'saldo_anterior', 'saldo_posterior', 'beneficiario_nuevo', 'destinatarios_distintos_24h', 'operaciones_2m', 'operaciones_10m',
           'operaciones_1h', 'n_historial', 'importe_tipico', 'fallos_login_30m', 'intentos_fallidos_recientes', 'solicitudes_otp_1h', 'viaje_imposible',
           'contexto_nuevo', 'dispositivo_nuevo', 'ubicacion_nueva', 'recuperacion_clave_reciente', 'fallos_otp_recientes', 'is_fraud',
           'puntuacion_riesgo_sintetica', 'hora_del_dia', 'dia_semana']
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000
HYPERPARAMETERS = {'n_estimators': 100, 'contamination': 'auto', 'random_state': 42}


class EventDatasetError(ValueError):
    """El CSV combinado no se puede usar; el mensaje (en español) explica el motivo."""


def load_combined(path):
    """Lee y valida el CSV combinado. Devuelve ``(DataFrame tipado, informe de validación, sha256)``."""
    import pandas as pd
    resolved = csv_training.guard_path(path)
    if not resolved.is_file() or resolved.suffix.lower() != '.csv':
        raise EventDatasetError('La ruta debe ser un archivo .csv existente.')
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise EventDatasetError('El archivo supera el tamaño máximo permitido.')
    raw = resolved.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise EventDatasetError('El archivo no está codificado en UTF-8.') from None
    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise EventDatasetError(f'El CSV está mal formado: {str(error)[:150]}') from None
    if len(frame) > MAX_ROWS:
        raise EventDatasetError(f'El CSV supera el máximo de {MAX_ROWS} filas.')
    columns = list(frame.columns)
    if columns != se.COLUMNS:
        raise EventDatasetError(f'Esquema distinto al esperado ({se.SCHEMA_VERSION}): faltan {sorted(set(se.COLUMNS) - set(columns))} y sobran '
                                f'{sorted(set(columns) - set(se.COLUMNS))}.')
    present = set(frame['event_type']) if len(frame) else set()
    if not {'TRANSACCION', 'ACCESO'} <= present:
        raise EventDatasetError('El CSV debe contener transacciones y accesos; se entrenan dos modelos distintos.')
    report = dv.validate_combined(frame)
    blocking = [c for c in report['checks'] if not c['ok'] and 'separa' not in c['comprobacion']]
    if blocking:
        raise EventDatasetError('El CSV no supera la validación: ' + '; '.join(f'{c["comprobacion"]} ({c["detalle"]})' for c in blocking))
    typed = frame.copy()
    typed['timestamp'] = pd.to_datetime(typed['timestamp'], format=dv.TIME_FORMAT)
    for column in NUMERIC:
        typed[column] = pd.to_numeric(typed[column], errors='coerce')
    typed['n_accesos_previos'] = (typed.assign(_a=(typed['event_type'] == 'ACCESO').astype(int)).groupby('account_id')['_a'].cumsum()
                                  - (typed['event_type'] == 'ACCESO').astype(int))
    return typed, report, hashlib.sha256(raw).hexdigest()


# ───────── Variables observables ─────────

def tx_matrix(tx):
    import numpy as np
    import pandas as pd
    amount, before = tx['importe'].astype(float), tx['saldo_anterior'].astype(float)
    share = np.where(before > 0, np.minimum(amount / np.where(before > 0, before, 1.0), 1.5), 0.0)
    typical = tx['importe_tipico'].astype(float).fillna(0.0)
    ratio = np.where((tx['n_historial'] >= 3) & (typical > 0), np.minimum(amount / np.where(typical > 0, typical, 1.0), 20.0), 1.0)
    return pd.DataFrame({'log_importe': np.log1p(amount), 'proporcion_saldo': share, 'ratio_importe_tipico': ratio,
                         'operaciones_2m': tx['operaciones_2m'], 'operaciones_10m': tx['operaciones_10m'], 'operaciones_1h': tx['operaciones_1h'],
                         'beneficiario_nuevo': tx['beneficiario_nuevo'], 'destinatarios_distintos_24h': tx['destinatarios_distintos_24h'],
                         'fallos_login_30m': tx['fallos_login_30m'], 'fallos_otp_recientes': tx['fallos_otp_recientes'],
                         'recuperacion_clave_reciente': tx['recuperacion_clave_reciente'], 'contexto_nuevo': tx['contexto_nuevo']},
                        index=tx.index, columns=list(TX_FEATURES)).astype(float)


def access_matrix(ac):
    import pandas as pd
    return pd.DataFrame({'fallido': (ac['resultado_login'] == 'FALLIDO').astype(float), 'intentos_fallidos_recientes': ac['intentos_fallidos_recientes'],
                         'fallos_otp_recientes': ac['fallos_otp_recientes'], 'solicitudes_otp_1h': ac['solicitudes_otp_1h'],
                         'contexto_nuevo': ac['contexto_nuevo'], 'recuperacion_clave_reciente': ac['recuperacion_clave_reciente']},
                        index=ac.index, columns=list(ACCESS_FEATURES)).astype(float)


# ───────── Métricas ─────────

def _ratio(numerator, denominator):
    return None if denominator == 0 else round(numerator / denominator, 4)


def binary_metrics(y_true, flagged):
    """Matriz de confusión y tasas. Una tasa es ``None`` (indefinida) cuando su denominador es cero; nunca se inventa."""
    import numpy as np
    y, f = np.asarray(y_true).astype(bool), np.asarray(flagged).astype(bool)
    tp, fp, tn, fn = int((y & f).sum()), int((~y & f).sum()), int((~y & ~f).sum()), int((y & ~f).sum())
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = None if precision is None or recall is None or precision + recall == 0 else round(2 * precision * recall / (precision + recall), 4)
    return {'matriz_confusion': {'VP': tp, 'FP': fp, 'VN': tn, 'FN': fn}, 'precision': precision, 'sensibilidad_recall': recall, 'f1': f1,
            'tasa_falsos_positivos': _ratio(fp, fp + tn), 'eventos_marcados': tp + fp, 'normales_marcadas_como_sospechosas': fp,
            'fraudes_sinteticos_detectados': tp, 'fraudes_sinteticos_no_detectados': fn, 'fraudes_sinteticos_en_evaluacion': tp + fn,
            'eventos_normales_en_evaluacion': fp + tn}


def threshold_free(y_true, scores):
    from sklearn.metrics import average_precision_score, roc_auc_score
    y = [int(v) for v in y_true]
    if len(set(y)) < 2:
        return {'auc_roc': None, 'precision_media': None, 'prevalencia': _ratio(sum(y), len(y)),
                'nota': 'Indefinidas: el conjunto de evaluación no contiene las dos clases.'}
    return {'auc_roc': round(float(roc_auc_score(y, scores)), 4), 'precision_media': round(float(average_precision_score(y, scores)), 4),
            'prevalencia': round(sum(y) / len(y), 4)}


def _scenario_table(scenarios, y_true, flagged):
    table = {}
    for name, fraud, _ in se.SCENARIOS:
        mask = [s == name for s in scenarios]
        total = sum(mask)
        if not total:
            continue
        caught = sum(1 for m, f in zip(mask, flagged) if m and f)
        table[name] = {'es_fraude_sintetico': bool(fraud), 'eventos': total, 'marcados': caught,
                       'proporcion_marcada': round(caught / total, 4)}
    return table


# ───────── Reglas del motor sobre el mismo conjunto de evaluación ─────────

def _rule_config():
    keys = ('FRAUD_LEVEL_INTERMEDIO', 'FRAUD_LEVEL_SOSPECHOSO', 'FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE', 'FRAUD_MIN_HISTORY')
    return {key: current_app.config[key] for key in keys}


def rule_levels(tx_eval, ac_eval):
    """Nivel (NORMAL/INTERMEDIO/SOSPECHOSO) que asignarían las reglas del motor a cada evento de evaluación."""
    config = _rule_config()
    levels = {}
    for index, row in tx_eval.iterrows():
        kind = row['tipo_operacion']
        features = OperationFeatures(
            kind=kind, amount=_dec(row['importe']), balance=_dec(row['saldo_anterior']), n_history=int(row['n_historial']),
            median_amount=_dec(row['importe_tipico']) if row['importe_tipico'] == row['importe_tipico'] else None, ops_2m=int(row['operaciones_2m']),
            ops_10m=int(row['operaciones_10m']), ops_1h=int(row['operaciones_1h']),
            first_time_beneficiary=bool(row['beneficiario_nuevo']) if kind == 'TRANSFERENCIA' else None,
            distinct_beneficiaries_24h=int(row['destinatarios_distintos_24h']), password_changed_recently=bool(row['recuperacion_clave_reciente']),
            failed_logins_30m=int(row['fallos_login_30m']), otp_failures_30m=int(row['fallos_otp_recientes']), new_context=bool(row['contexto_nuevo']))
        confidence = 'SUFICIENTE' if features.n_history >= config['FRAUD_MIN_HISTORY'] else 'INSUFICIENTE'
        levels[index] = rules.classify(rules.score_operation(features, config), confidence, config)[1]
    for index, row in ac_eval.iterrows():
        prior = int(row['n_accesos_previos'])
        features = AccessFeatures(
            failed_logins_30m=int(row['intentos_fallidos_recientes']) + (1 if row['resultado_login'] == 'FALLIDO' else 0),
            otp_failures_30m=int(row['fallos_otp_recientes']), otp_sent_60m=int(row['solicitudes_otp_1h']),
            new_context=bool(row['contexto_nuevo']) if prior >= 3 else None, n_prior_access_events=prior)
        confidence = 'SUFICIENTE' if prior >= 3 else 'INSUFICIENTE'
        levels[index] = rules.classify(rules.score_access(features, config), confidence, config)[1]
    return levels


def _dec(value):
    from decimal import Decimal
    return Decimal(str(value))


def _levels_report(index, levels, y_true, scenarios):
    order = ('NORMAL', 'INTERMEDIO', 'SOSPECHOSO')
    got = [levels[i] for i in index]
    distribution = {level: {'fraude_sintetico': sum(1 for g, y in zip(got, y_true) if g == level and y),
                            'normal': sum(1 for g, y in zip(got, y_true) if g == level and not y)} for level in order}
    suspicious = [g == 'SOSPECHOSO' for g in got]
    broad = [g in ('INTERMEDIO', 'SOSPECHOSO') for g in got]
    return {'distribucion_por_nivel': distribution, 'alerta_estricta_SOSPECHOSO': binary_metrics(y_true, suspicious),
            'alerta_amplia_INTERMEDIO_o_mas': binary_metrics(y_true, broad), 'por_escenario_INTERMEDIO_o_mas': _scenario_table(scenarios, y_true, broad)}


# ───────── Entrenamiento por tipo de evento ─────────

def _train_and_evaluate(name, x_train, x_eval, y_eval, scenarios, kinds, *, flag_rate):
    import numpy as np
    from sklearn.ensemble import IsolationForest
    if len(x_train) < current_app.config['FRAUD_MODEL_MIN_SAMPLES']:
        raise EventDatasetError(f'{name}: {len(x_train)} eventos de entrenamiento; se necesitan al menos {current_app.config["FRAUD_MODEL_MIN_SAMPLES"]}.')
    model = IsolationForest(**HYPERPARAMETERS).fit(x_train.to_numpy())          # sin etiquetas: aprendizaje no supervisado
    train_scores = model.decision_function(x_train.to_numpy())
    threshold = float(np.percentile(train_scores, flag_rate * 100))             # umbral fijado SOLO con el periodo de entrenamiento
    started = time.perf_counter()
    eval_scores = model.decision_function(x_eval.to_numpy())
    elapsed = (time.perf_counter() - started) * 1000
    flagged = eval_scores < threshold
    metrics = {'umbral_puntuacion': round(threshold, 6), **binary_metrics(y_eval, flagged),
               'independiente_del_umbral': threshold_free(y_eval, -eval_scores),
               'por_escenario': _scenario_table(scenarios, y_eval, flagged),
               'tiempo_evaluacion_ms': round(elapsed, 2), 'tiempo_por_evento_us': round(elapsed * 1000 / max(len(x_eval), 1), 2)}
    if kinds is not None:
        metrics['por_tipo_operacion'] = {kind: {'eventos': int(sum(k == kind for k in kinds)),
                                                'fraudes_sinteticos': int(sum(1 for k, y in zip(kinds, y_eval) if k == kind and y)),
                                                **{key: value for key, value in binary_metrics([y for k, y in zip(kinds, y_eval) if k == kind],
                                                                                                  [f for k, f in zip(kinds, flagged) if k == kind]).items()
                                                   if key in ('precision', 'sensibilidad_recall', 'f1', 'tasa_falsos_positivos', 'matriz_confusion')}}
                                         for kind in sorted(set(kinds))}
    return model, metrics, train_scores, eval_scores, flagged


def classic_frame(tx):
    """Transformación hacia el esquema del cargador clásico (solo salidas de dinero). Etiquetas SIMULADAS."""
    import pandas as pd
    out = tx[tx['tipo_operacion'].isin(se.OUT_KINDS)]
    return pd.DataFrame({'timestamp': out['timestamp'], 'account_id': out['account_id'], 'amount': out['importe'].astype(float),
                         'balance_before': out['saldo_anterior'].astype(float), 'kind': out['tipo_operacion'],
                         'label': out['is_fraud'].map({1: 'FRAUDE_CONFIRMADO', 0: 'LEGITIMA_VERIFICADA'})}).reset_index(drop=True)


VOLATILE_KEYS = frozenset({'version', 'tiempo_evaluacion_ms', 'tiempo_por_evento_us'})


def _stable(value):
    """Copia sin los datos que cambian entre ejecuciones idénticas (nombres de versión con marca de tiempo y tiempos medidos)."""
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in value.items() if key not in VOLATILE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def results_fingerprint(models, rules_report):
    """Huella de las MÉTRICAS: iguales para los mismos datos y la misma configuración, aunque cambie la versión registrada o el reloj."""
    return hashlib.sha256(json.dumps(_stable({'modelos': models, 'reglas': rules_report}), sort_keys=True, default=str).encode()).hexdigest()


def run_pipeline(csv_path, *, report_dir=None, created_by=None, flag_rate=0.05, eval_fraction=0.25):
    """Valida, divide cronológicamente, entrena los candidatos y evalúa. No activa nada. Devuelve el informe."""
    import numpy as np
    import pandas as pd
    import sklearn
    report_dir = csv_training.guard_path(report_dir) if report_dir else None          # nunca se escribe en data/
    frame, validation, sha = load_combined(csv_path)
    ordered = frame.sort_values('timestamp', kind='mergesort')
    cutoff = ordered['timestamp'].iloc[int(len(ordered) * (1 - eval_fraction))]
    train_mask, eval_mask = frame['timestamp'] < cutoff, frame['timestamp'] >= cutoff
    tx_all = frame[(frame['event_type'] == 'TRANSACCION') & frame['tipo_operacion'].isin(se.OUT_KINDS)]
    ac_all = frame[frame['event_type'] == 'ACCESO']
    parts = {}
    integrity = {'corte_temporal': cutoff.isoformat(), 'fraccion_evaluacion': eval_fraction,
                 'umbral_calculado_con': 'puntuaciones del periodo de entrenamiento (percentil fijado a priori)',
                 'etiquetas_usadas_en_entrenamiento': False, 'etiquetas_usadas_para_el_umbral': False,
                 'hiperparametros_fijados_a_priori': HYPERPARAMETERS, 'tasa_de_alertas_a_priori': flag_rate}
    candidates = []
    notes = 'Datos y etiquetas SINTÉTICOS (generador documentado): no equivalen a fraude confirmado por analistas.'
    for name, part, matrix, features, tag in (('transacciones', tx_all, tx_matrix, TX_FEATURES, 'iforest-tx'), ('accesos', ac_all, access_matrix, ACCESS_FEATURES, 'iforest-acc')):
        x = matrix(part)
        x_train, x_eval = x[train_mask.reindex(part.index)], x[eval_mask.reindex(part.index)]
        eval_part = part[eval_mask.reindex(part.index)]
        y_eval = eval_part['is_fraud'].astype(int).tolist()
        kinds = eval_part['tipo_operacion'].tolist() if name == 'transacciones' else None
        model, metrics, train_scores, eval_scores, flagged = _train_and_evaluate(name, x_train, x_eval, y_eval, eval_part['scenario'].tolist(), kinds, flag_rate=flag_rate)
        train_part = part[train_mask.reindex(part.index)]
        integrity[f'{name}_solapamiento_de_ids_entrenamiento_evaluacion'] = int(len(set(train_part['event_id']) & set(eval_part['event_id'])))
        integrity[f'{name}_ultimo_evento_de_entrenamiento'] = train_part['timestamp'].max().isoformat()
        integrity[f'{name}_primer_evento_de_evaluacion'] = eval_part['timestamp'].min().isoformat()
        period = {'entrenamiento': [train_part['timestamp'].min().isoformat(), train_part['timestamp'].max().isoformat()],
                  'evaluacion': [eval_part['timestamp'].min().isoformat(), eval_part['timestamp'].max().isoformat()]}
        stored = {'entrenamiento': {'filas': int(len(x_train)), 'puntuacion': {'media': round(float(train_scores.mean()), 4)}},
                  'evaluacion': {'filas': int(len(x_eval)), 'tasa_de_alertas': round(float(flagged.mean()), 4)},
                  'supervisadas': {'disponibles': metrics['independiente_del_umbral']['auc_roc'] is not None, **metrics['independiente_del_umbral'],
                                   'nota': 'Etiquetas SINTÉTICAS solo para evaluar.'},
                  'periodos': period, 'variables': list(features), 'variables_excluidas': EXCLUDED, **metrics}
        row = anomaly.save_candidate(
            model, source='CSV', n_train=len(x_train), n_eval=len(x_eval), metrics=stored,
            validation={'proporcion_invalidas': 0.0, 'filas_leidas': int(len(frame)), 'tipo_de_evento': name, 'esquema': se.SCHEMA_VERSION,
                        'comprobaciones_ok': int(sum(c['ok'] for c in validation['checks'])), 'comprobaciones_total': len(validation['checks'])},
            data_sha256=sha, created_by=created_by, notes=notes, features=features, tag=tag,
            extra_metadata={'tipo_de_evento': name, 'umbral_puntuacion': metrics['umbral_puntuacion'], 'tasa_de_alertas_a_priori': flag_rate,
                            'hiperparametros': HYPERPARAMETERS, 'periodos': period, 'esquema_del_dataset': se.SCHEMA_VERSION})
        loaded, metadata = anomaly.load_candidate(row.version)          # comprueba que el artefacto se puede cargar en este entorno
        check = loaded.decision_function(x_eval.to_numpy()[:5])
        parts[name] = {'version': row.version, 'variables': list(features), 'periodos': period, 'filas_entrenamiento': int(len(x_train)),
                       'filas_evaluacion': int(len(x_eval)), 'metricas': metrics, 'carga_verificada': bool(np.isfinite(check).all()),
                       'puertas_de_activacion': anomaly.activation_gates(row)}
        candidates.append(row.version)
        parts[name]['_levels'] = (eval_part, flagged)

    rules_report = {}
    levels = rule_levels(parts['transacciones']['_levels'][0], parts['accesos']['_levels'][0])
    for name in ('transacciones', 'accesos'):
        eval_part = parts[name].pop('_levels')[0]
        rules_report[name] = _levels_report(eval_part.index, levels, eval_part['is_fraud'].astype(int).tolist(), eval_part['scenario'].tolist())

    # Candidato clásico por el flujo existente (4 variables), con el MISMO corte temporal y sin usar etiquetas para entrenar
    classic = classic_frame(frame[frame['event_type'] == 'TRANSACCION'])
    classic_row = anomaly.train_candidate(classic, source='CSV', cutoff=cutoff, exclude_confirmed_from_training=False, notes=notes + ' Esquema clásico (4 variables).',
                                          validation={'proporcion_invalidas': 0.0, 'filas_leidas': int(len(classic)), 'esquema': 'clasico', 'procedencia_etiquetas': 'SINTÉTICAS'},
                                          data_sha256=sha, created_by=created_by)
    classic_features = anomaly.build_feature_frame(classic)
    classic_model, _ = anomaly.load_candidate(classic_row.version)
    c_train, c_eval = classic['timestamp'] < cutoff, classic['timestamp'] >= cutoff
    scores_train = classic_model.decision_function(classic_features[c_train][list(anomaly.FEATURE_NAMES)].to_numpy())
    threshold = float(np.percentile(scores_train, flag_rate * 100))
    scores_eval = classic_model.decision_function(classic_features[c_eval][list(anomaly.FEATURE_NAMES)].to_numpy())
    y_classic = (classic.loc[c_eval, 'label'] == 'FRAUDE_CONFIRMADO').astype(int).tolist()
    parts['candidato_clasico'] = {'version': classic_row.version, 'variables': list(anomaly.FEATURE_NAMES), 'filas_entrenamiento': int(c_train.sum()),
                                  'filas_evaluacion': int(c_eval.sum()),
                                  'metricas': {'umbral_puntuacion': round(threshold, 6), **binary_metrics(y_classic, scores_eval < threshold),
                                               'independiente_del_umbral': threshold_free(y_classic, -scores_eval)},
                                  'puertas_de_activacion': anomaly.activation_gates(classic_row)}
    candidates.append(classic_row.version)

    report = {
        'informe': 'Evaluación de modelos candidatos (datos SINTÉTICOS)', 'esquema_del_dataset': se.SCHEMA_VERSION,
        'dataset': {'archivo': Path(csv_path).name, 'sha256': sha, 'filas': int(len(frame)), 'por_tipo': frame['event_type'].value_counts().to_dict(),
                    'validacion': {'ok': validation['ok'], 'comprobaciones': len(validation['checks']), 'distribucion': validation['distribucion']}},
        'entorno': {'python': sys.version.split()[0], 'scikit_learn': sklearn.__version__, 'pandas': pd.__version__, 'numpy': np.__version__,
                    'plataforma': platform.platform()},
        'integridad_temporal_y_sin_fugas': integrity, 'modelos': parts, 'reglas_del_motor_sobre_la_misma_evaluacion': rules_report,
        'candidatos_registrados': candidates, 'definiciones': DEFINITIONS, 'limitaciones': LIMITATIONS,
    }
    report['resultados_sha256'] = results_fingerprint(parts, rules_report)
    if report_dir:
        write_report(report, Path(report_dir))
    return report


DEFINITIONS = {
    'positivo': 'Evento con is_fraud=1 (fraude SINTÉTICO del escenario de prueba, no un fraude confirmado por analistas).',
    'marcado': 'Evento cuya puntuación del modelo es menor que el umbral. El umbral es el percentil (tasa de alertas a priori) de las puntuaciones del periodo de entrenamiento.',
    'precision': 'VP / (VP + FP). Denominador: eventos marcados. Indefinida si no hay eventos marcados.',
    'sensibilidad_recall': 'VP / (VP + FN). Denominador: fraudes sintéticos del conjunto de evaluación. Indefinida si no hay positivos.',
    'f1': '2·precisión·sensibilidad / (precisión + sensibilidad). Indefinida si falta alguna.',
    'tasa_falsos_positivos': 'FP / (FP + VN). Denominador: eventos normales (is_fraud=0) del conjunto de evaluación. Indefinida si no hay negativos.',
    'auc_roc': 'Probabilidad de que un fraude sintético reciba una puntuación más atípica que un evento normal (independiente del umbral).',
    'precision_media': 'Área bajo la curva precisión-sensibilidad; debe leerse frente a la prevalencia (un clasificador al azar obtiene la prevalencia).',
    'reglas_del_motor': 'Se aplican a la misma evaluación las reglas versionadas del motor: «estricta» = nivel SOSPECHOSO; «amplia» = INTERMEDIO o SOSPECHOSO.',
}
LIMITATIONS = [
    'Las etiquetas y los eventos son sintéticos y salen de escenarios definidos por nosotros: un buen resultado solo demuestra el comportamiento frente a ese escenario, no eficacia bancaria real.',
    'La prevalencia de fraude (8 % de transacciones y 10 % de accesos) es mucho mayor que la real; las precisiones no son extrapolables.',
    'Las variables dispositivo, ubicación y viaje imposible participan en los escenarios pero el sistema actual no las recopila: quedan fuera de los modelos y de las reglas.',
    'Los candidatos usan variables que el motor actual no calcula todas (p. ej. ratio frente al importe típico en el modelo): no se pueden activar hasta ampliar la inferencia; las puertas de activación lo impiden.',
    'Aprendizaje no supervisado: la puntuación mide qué tan atípico es un evento, no la probabilidad de fraude. El umbral procede de una tasa de alertas fijada a priori.',
    'Un único corte cronológico (60 días) y una sola semilla: no se estima la variabilidad entre muestras ni la deriva a largo plazo.',
]


def write_report(report, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'informe_evaluacion_modelos_candidatos.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    (directory / 'informe_evaluacion_modelos_candidatos.md').write_text(render_markdown(report), encoding='utf-8')


def _pct(value):
    return '—' if value is None else f'{value:.1%}'


def render_markdown(report):
    lines = [f'# {report["informe"]}', '', '> Datos y etiquetas **sintéticos**. Los resultados no demuestran eficacia bancaria real.', '',
             '## Dataset', f'- Archivo: `{report["dataset"]["archivo"]}`', f'- SHA-256: `{report["dataset"]["sha256"]}`',
             f'- Filas: {report["dataset"]["filas"]} ({report["dataset"]["por_tipo"]})',
             f'- Validación: {"superada" if report["dataset"]["validacion"]["ok"] else "CON FALLOS"} ({report["dataset"]["validacion"]["comprobaciones"]} comprobaciones)',
             f'- Entorno: {report["entorno"]}', '', '## Integridad temporal y ausencia de fugas']
    lines += [f'- {key}: {value}' for key, value in report['integridad_temporal_y_sin_fugas'].items()]
    for name, part in report['modelos'].items():
        metrics = part['metricas']
        lines += ['', f'## Modelo: {name}', f'- Versión: `{part["version"]}`', f'- Variables: {", ".join(part["variables"])}',
                  f'- Filas de entrenamiento / evaluación: {part["filas_entrenamiento"]} / {part["filas_evaluacion"]}']
        if 'periodos' in part:
            lines.append(f'- Periodos: {part["periodos"]}')
        c = metrics['matriz_confusion']
        lines += [f'- Umbral de puntuación (del periodo de entrenamiento): {metrics["umbral_puntuacion"]}',
                  f'- Matriz de confusión: VP={c["VP"]} FP={c["FP"]} VN={c["VN"]} FN={c["FN"]}',
                  f'- Precisión {_pct(metrics["precision"])} · Sensibilidad {_pct(metrics["sensibilidad_recall"])} · F1 {_pct(metrics["f1"])} · Tasa de falsos positivos {_pct(metrics["tasa_falsos_positivos"])}',
                  f'- Independiente del umbral: {metrics["independiente_del_umbral"]}']
        if 'tiempo_evaluacion_ms' in metrics:
            lines.append(f'- Tiempo de evaluación: {metrics["tiempo_evaluacion_ms"]} ms ({metrics["tiempo_por_evento_us"]} µs por evento)')
        lines.append(f'- Puertas de activación: {part["puertas_de_activacion"] or "superadas"} (NO se activó)')
        if 'por_escenario' in metrics:
            lines += ['', '| Escenario | Fraude sintético | Eventos | Marcados | Proporción |', '|---|---|---|---|---|']
            lines += [f'| {k} | {"sí" if v["es_fraude_sintetico"] else "no"} | {v["eventos"]} | {v["marcados"]} | {_pct(v["proporcion_marcada"])} |' for k, v in metrics['por_escenario'].items()]
    lines += ['', '## Reglas del motor sobre la misma evaluación']
    for name, part in report['reglas_del_motor_sobre_la_misma_evaluacion'].items():
        lines += [f'### {name}', f'- Distribución por nivel: {part["distribucion_por_nivel"]}',
                  f'- Alerta estricta (SOSPECHOSO): {part["alerta_estricta_SOSPECHOSO"]}', f'- Alerta amplia (INTERMEDIO o más): {part["alerta_amplia_INTERMEDIO_o_mas"]}']
    lines += ['', '## Definiciones'] + [f'- **{k}**: {v}' for k, v in report['definiciones'].items()]
    lines += ['', '## Limitaciones'] + [f'- {text}' for text in report['limitaciones']]
    lines += ['', f'Resultados reproducibles (huella): `{report["resultados_sha256"]}`', '']
    return '\n'.join(lines)
