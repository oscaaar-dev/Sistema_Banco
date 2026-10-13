"""Detector de anomalías transaccionales no supervisado (Isolation Forest, de scikit-learn, ya instalado).

Es una **herramienta de observación**: su puntuación se guarda en ``risk_evaluations.model_score`` junto con la versión del
modelo, pero **no cambia el nivel ni dispara medidas**. No hay etiquetas fiables de fraude, así que no se afirma ninguna
precisión ni sensibilidad: solo sirve para comparar, con el tiempo, lo que dicen las reglas y lo que «ve» el modelo.

Arquitectura híbrida (ver ``docs/fraude.md``):

* **CSV** para entrenar y evaluar (``csv_training``): se valida, se separa por tiempo y se entrena fuera de la aplicación web.
* **SQL** para lo operativo: evaluaciones, incidentes, restricciones y el registro de versiones del modelo (``FraudModelVersion``).
* **Exportación CSV** de resultados desde el panel (``export``).

Ciclo de vida de un modelo (nunca se entrena con cada transacción):

1. ``save_candidate``: guarda el artefacto y registra la versión como ``CANDIDATO``. No cambia el modelo activo.
2. ``activate_version``: activación explícita que solo procede si se cumplen las puertas de validación (``activation_gates``).
   Son comprobaciones de cordura (datos suficientes, entorno compatible, artefacto íntegro, tasa de alertas razonable): **no**
   demuestran capacidad de detección de fraude.
3. ``score``: inferencia que tolera ausencia de modelo, versiones distintas y errores sin afectar a la operación.

Seguridad de la persistencia: el artefacto es un archivo de ``joblib`` (pickle) que solo escribe este módulo en ``FRAUD_MODEL_DIR``
(``instance/``, fuera de Git). Antes de cargarlo se comprueba su SHA-256 y que scikit-learn, Python y las características
coincidan. No se carga nunca un archivo recibido de un usuario ni de una ruta externa.
"""
import hashlib
import json
import math
import sys
from datetime import timedelta
from pathlib import Path

from flask import current_app

from ..models import AuditLog, FraudModelVersion, Transaction, db, now_utc

FEATURE_NAMES = ('log_monto', 'proporcion_saldo', 'salidas_10m', 'salidas_1h')
TRAINING_KINDS = ('TRANSFERENCIA', 'PAGO', 'RECARGA')
TRAINING_WINDOW_DAYS = 180
TRAINING_MAX_ROWS = 5000
MIN_EVAL_ROWS = 20
MAX_INVALID_RATIO = 0.20
MAX_EVAL_ANOMALY_RATE = 0.35

STATUS_CANDIDATE, STATUS_ACTIVE, STATUS_ARCHIVED, STATUS_REJECTED = 'CANDIDATO', 'ACTIVO', 'ARCHIVADO', 'RECHAZADO'


class InsufficientData(RuntimeError):
    """No hay operaciones suficientes para entrenar de forma útil."""


class ActivationRefused(ValueError):
    """La versión no supera las puertas de validación; el mensaje enumera los motivos."""


# ───────── Características ─────────

def vector_from_features(features):
    share = features.balance_share
    share = 0.0 if share is None else min(max(share, 0.0), 1.5)
    return [math.log1p(float(features.amount)), share, float(features.ops_10m), float(features.ops_1h)]


def build_feature_frame(frame):
    """Características del modelo a partir de ``timestamp, account_id, amount, balance_before``.

    Las ventanas (10 min y 1 h) solo miran **hacia atrás** en cada cuenta, así que ninguna fila usa información futura.
    """
    import numpy as np
    import pandas as pd
    ordered = frame.sort_values(['account_id', 'timestamp'], kind='mergesort')
    ts = ordered['timestamp'].to_numpy(dtype='datetime64[s]').astype('int64')
    accounts = ordered['account_id'].to_numpy()
    ops_10m = np.zeros(len(ordered), dtype=float)
    ops_1h = np.zeros(len(ordered), dtype=float)
    first_1h = first_10m = 0
    for i in range(len(ordered)):
        if i == 0 or accounts[i] != accounts[i - 1]:
            first_1h = first_10m = i                    # nueva cuenta: la ventana empieza en su primera operación
        while ts[first_1h] < ts[i] - 3600:
            first_1h += 1
        while ts[first_10m] < ts[i] - 600:
            first_10m += 1
        ops_1h[i], ops_10m[i] = i - first_1h, i - first_10m      # operaciones previas de la misma cuenta dentro de la ventana
    amount = ordered['amount'].astype(float).to_numpy()
    before = ordered['balance_before'].astype(float).to_numpy()
    share = np.where(before > 0, np.minimum(amount / np.where(before > 0, before, 1.0), 1.5), 0.0)
    out = pd.DataFrame({'log_monto': np.log1p(amount), 'proporcion_saldo': share, 'salidas_10m': ops_10m, 'salidas_1h': ops_1h},
                       index=ordered.index)
    return out.reindex(frame.index)


def database_frame(max_rows=TRAINING_MAX_ROWS, window_days=TRAINING_WINDOW_DAYS):
    """Operaciones de salida recientes de la base, con el mismo formato que el CSV de entrenamiento (consulta acotada)."""
    import pandas as pd
    since = now_utc() - timedelta(days=window_days)
    rows = (db.session.query(Transaction.account_id, Transaction.amount, Transaction.balance_after, Transaction.created_at,
                             Transaction.operation_type)
            .filter(Transaction.amount < 0, Transaction.operation_type.in_(TRAINING_KINDS), Transaction.created_at >= since)
            .order_by(Transaction.created_at.desc()).limit(max_rows).all())
    records = [{'timestamp': when, 'account_id': str(account_id), 'amount': abs(float(amount)),
                'balance_before': float(balance_after) + abs(float(amount)), 'kind': kind, 'label': 'SIN_VERIFICAR'}
               for account_id, amount, balance_after, when, kind in rows]
    frame = pd.DataFrame(records, columns=['timestamp', 'account_id', 'amount', 'balance_before', 'kind', 'label'])
    frame['timestamp'] = pd.to_datetime(frame['timestamp'])
    return frame.sort_values('timestamp').reset_index(drop=True)


# ───────── Entrenamiento y evaluación ─────────

def fit_model(features, *, minimum=None, random_state=42):
    """Entrena un Isolation Forest con un ``DataFrame`` de características. No toca la base de datos ni el disco."""
    minimum = current_app.config['FRAUD_MODEL_MIN_SAMPLES'] if minimum is None else minimum
    if len(features) < minimum:
        raise InsufficientData(f'Hay {len(features)} operaciones de entrenamiento; se necesitan al menos {minimum}.')
    from sklearn.ensemble import IsolationForest
    clean = features[list(FEATURE_NAMES)].astype(float).replace([float('inf'), float('-inf')], 0).fillna(0)
    return IsolationForest(n_estimators=100, contamination='auto', random_state=random_state).fit(clean.to_numpy())


def evaluate_model(model, train_features, eval_features, eval_labels=None, eval_kinds=None):
    """Métricas de observación. Las supervisadas solo se calculan si hay etiquetas confirmadas suficientes; nunca se inventan."""
    import numpy as np
    columns = list(FEATURE_NAMES)
    train_x = train_features[columns].astype(float).to_numpy()
    eval_x = eval_features[columns].astype(float).to_numpy()
    train_scores, eval_scores = model.decision_function(train_x), model.decision_function(eval_x)
    eval_flagged = model.predict(eval_x) == -1

    def describe(values):
        return {'media': round(float(np.mean(values)), 4), 'p05': round(float(np.percentile(values, 5)), 4),
                'p50': round(float(np.percentile(values, 50)), 4)}

    spread = train_x.std(axis=0)
    spread[spread == 0] = 1.0
    drift = np.abs(eval_x.mean(axis=0) - train_x.mean(axis=0)) / spread
    metrics = {
        'entrenamiento': {'filas': int(len(train_x)), 'puntuacion': describe(train_scores)},
        'evaluacion': {'filas': int(len(eval_x)), 'tasa_de_alertas': round(float(eval_flagged.mean()), 4),
                       'puntuacion': describe(eval_scores)},
        'deriva_maxima_estandarizada': round(float(drift.max()), 4),
        'supervisadas': {'disponibles': False,
                         'motivo': 'No hay etiquetas confirmadas suficientes (se necesitan 20 fraudes confirmados y 100 operaciones legítimas verificadas en la evaluación).'},
        'aviso': 'Aprendizaje no supervisado: la puntuación mide qué tan atípica es una operación, no la probabilidad de fraude.',
    }
    if eval_labels is not None:
        labels = np.asarray(eval_labels)
        fraud, legit = labels == 'FRAUDE_CONFIRMADO', labels == 'LEGITIMA_VERIFICADA'
        counts = {'fraude_confirmado': int(fraud.sum()), 'legitima_verificada': int(legit.sum()),
                  'sin_verificar': int((labels == 'SIN_VERIFICAR').sum())}
        metrics['etiquetas_en_evaluacion'] = counts
        if fraud.sum() >= 20 and legit.sum() >= 100:
            from sklearn.metrics import average_precision_score, roc_auc_score

            def supervised(selection):
                target = fraud[selection].astype(int)
                return {'auc_roc': round(float(roc_auc_score(target, -eval_scores[selection])), 4),
                        'precision_media': round(float(average_precision_score(target, -eval_scores[selection])), 4),
                        'prevalencia_fraude': round(float(target.mean()), 4), 'fraudes': int(target.sum()), 'legitimas': int(len(target) - target.sum())}

            both = fraud | legit
            metrics['supervisadas'] = {'disponibles': True, **supervised(both),
                                       'nota': 'Solo sobre filas con etiqueta confirmada; las SIN_VERIFICAR no se usan como legítimas ni como fraude. '
                                               'La precisión media debe leerse frente a la prevalencia de fraude (un clasificador al azar obtiene ese valor).'}
            if eval_kinds is not None:
                kinds = np.asarray(eval_kinds)
                by_kind = {}
                for kind in sorted(set(kinds)):
                    selection = both & (kinds == kind)
                    if (fraud & (kinds == kind)).sum() >= 20 and (legit & (kinds == kind)).sum() >= 100:
                        by_kind[str(kind)] = supervised(selection)
                metrics['supervisadas']['por_tipo'] = by_kind
                metrics['supervisadas']['lectura_por_tipo'] = ('El valor global puede inflarse por diferencias entre tipos de operación '
                                                               '(p. ej. pagos pequeños frente a transferencias grandes). Compare con «por_tipo».')
    return metrics


# ───────── Persistencia versionada ─────────

def _model_dir():
    return Path(current_app.config['FRAUD_MODEL_DIR'])


def _runtime_versions():
    import sklearn
    return {'sklearn': '.'.join(sklearn.__version__.split('.')[:2]), 'python': f'{sys.version_info.major}.{sys.version_info.minor}'}


def save_candidate(model, *, source, n_train, n_eval, metrics, validation, data_sha256=None, created_by=None, notes=None,
                   features=None, tag='iforest', algorithm='IsolationForest', extra_metadata=None):
    """Guarda el artefacto y registra la versión como CANDIDATO. **No cambia el modelo activo.**

    ``features``, ``tag`` y ``algorithm`` permiten registrar modelos con otro conjunto de variables (p. ej. el de accesos o el de
    transacciones ampliado). Si las variables no son las del inferidor actual (``FEATURE_NAMES``), la versión se guarda pero
    **las puertas de activación la rechazan**: el motor actual no sabría calcularlas."""
    import joblib
    directory = _model_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime('%Y%m%d%H%M%S%f')[:20]
    version = f'{tag}-{stamp}'
    artifact = directory / f'{version}.joblib'
    joblib.dump(model, artifact)
    runtime = _runtime_versions()
    metadata = {'version': version, 'trained_at': now_utc().isoformat(), 'n_train': int(n_train), 'n_eval': int(n_eval),
                'features': list(features or FEATURE_NAMES), 'source': source, 'data_sha256': data_sha256, 'algorithm': algorithm,
                'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), **runtime, **(extra_metadata or {})}
    (directory / f'{version}.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    row = FraudModelVersion(version=version, source=source, status=STATUS_CANDIDATE, n_train=int(n_train), n_eval=int(n_eval), algorithm=algorithm,
                            features=list(features or FEATURE_NAMES), metrics=metrics, validation=validation, data_sha256=data_sha256,
                            sklearn_version=runtime['sklearn'], python_version=runtime['python'],
                            created_by_id=created_by.id if created_by is not None else None, notes=(notes or '')[:300] or None)
    db.session.add(row)
    db.session.commit()
    return row


def train_candidate(frame, *, source, validation, data_sha256=None, created_by=None, eval_fraction=0.2, notes=None, cutoff=None,
                    exclude_confirmed_from_training=True):
    """Entrena sobre ``frame`` (``timestamp, account_id, amount, balance_before, kind, label``) y registra un CANDIDATO.

    División **temporal**: se entrena con lo anterior a un instante de corte y se evalúa con lo posterior. Los fraudes
    confirmados no se usan para entrenar (el detector aprende cómo es lo habitual). Las etiquetas jamás se infieren.
    """
    if len(frame) < 2:
        raise InsufficientData('No hay filas suficientes.')
    features = build_feature_frame(frame)
    if cutoff is None:
        ordered = frame.sort_values('timestamp', kind='mergesort')
        cutoff = ordered['timestamp'].iloc[min(len(ordered) - 1, max(1, int(len(ordered) * (1 - eval_fraction))))]
    train_mask = frame['timestamp'] < cutoff
    eval_mask = ~train_mask
    # Por defecto los fraudes confirmados no se usan para entrenar. Con ``exclude_confirmed_from_training=False`` las etiquetas no
    # intervienen en el entrenamiento en absoluto (solo en la evaluación), como exige un detector estrictamente no supervisado.
    train_rows = train_mask & (frame['label'] != 'FRAUDE_CONFIRMADO') if exclude_confirmed_from_training else train_mask
    if int(eval_mask.sum()) < MIN_EVAL_ROWS:
        raise InsufficientData(f'La evaluación tendría {int(eval_mask.sum())} filas; se necesitan al menos {MIN_EVAL_ROWS}.')
    model = fit_model(features[train_rows])
    metrics = evaluate_model(model, features[train_rows], features[eval_mask], frame.loc[eval_mask, 'label'].to_numpy(),
                             eval_kinds=frame.loc[eval_mask, 'kind'].to_numpy() if 'kind' in frame.columns else None)
    metrics['corte_temporal'] = cutoff.isoformat()
    return save_candidate(model, source=source, n_train=int(train_rows.sum()), n_eval=int(eval_mask.sum()), metrics=metrics,
                          validation=validation, data_sha256=data_sha256, created_by=created_by, notes=notes)


def train_from_database(created_by=None):
    frame = database_frame()
    return train_candidate(frame, source='BASE_DE_DATOS', created_by=created_by,
                           validation={'filas_leidas': int(len(frame)), 'invalidas': 0, 'proporcion_invalidas': 0.0,
                                       'nota': 'Datos de la tabla transactions (consulta acotada); sin etiquetas.'})


def list_versions():
    return [row.version for row in FraudModelVersion.query.order_by(FraudModelVersion.trained_at.desc()).all()]


def activation_gates(row):
    """Motivos por los que una versión NO puede activarse (lista vacía = supera las puertas). Son comprobaciones de cordura."""
    problems = []
    if row.status not in (STATUS_CANDIDATE, STATUS_ARCHIVED):
        problems.append(f'El estado actual ({row.status}) no permite activarla.')
    directory = _model_dir()
    artifact, metadata_file = directory / f'{row.version}.joblib', directory / f'{row.version}.json'
    if not artifact.is_file() or not metadata_file.is_file():
        problems.append('Falta el artefacto del modelo o sus metadatos.')
    else:
        try:
            metadata = json.loads(metadata_file.read_text(encoding='utf-8'))
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != metadata.get('sha256'):
                problems.append('El artefacto no coincide con su huella SHA-256 (alterado o dañado).')
        except (OSError, ValueError):
            problems.append('Los metadatos del modelo no se pueden leer.')
    runtime = _runtime_versions()
    if (row.sklearn_version, row.python_version) != (runtime['sklearn'], runtime['python']):
        problems.append(f"Entorno distinto al del entrenamiento (scikit-learn {row.sklearn_version}/Python {row.python_version} "
                        f"frente a {runtime['sklearn']}/{runtime['python']}).")
    if tuple(row.features or ()) != FEATURE_NAMES:
        problems.append('Las características del modelo no coinciden con las actuales.')
    if row.n_train < current_app.config['FRAUD_MODEL_MIN_SAMPLES']:
        problems.append(f"Entrenamiento con {row.n_train} filas; mínimo {current_app.config['FRAUD_MODEL_MIN_SAMPLES']}.")
    if row.n_eval < MIN_EVAL_ROWS:
        problems.append(f'Evaluación con {row.n_eval} filas; mínimo {MIN_EVAL_ROWS}.')
    if (row.validation or {}).get('proporcion_invalidas', 0) > MAX_INVALID_RATIO:
        problems.append('Demasiados registros inválidos en los datos de entrenamiento.')
    rate = ((row.metrics or {}).get('evaluacion') or {}).get('tasa_de_alertas')
    if rate is None or not 0 <= rate <= MAX_EVAL_ANOMALY_RATE:
        problems.append(f'La tasa de alertas en la evaluación ({rate}) no es razonable (máximo {MAX_EVAL_ANOMALY_RATE}).')
    return problems


def activate_version(version, actor=None):
    """Activación explícita y validada de una versión. Archiva la anterior y deja constancia en la auditoría."""
    row = FraudModelVersion.query.filter_by(version=version).with_for_update().first()
    if row is None:
        raise ValueError('La versión indicada no existe.')
    problems = activation_gates(row)
    if problems:
        raise ActivationRefused(' '.join(problems))
    for active in FraudModelVersion.query.filter_by(status=STATUS_ACTIVE).all():
        active.status = STATUS_ARCHIVED
    row.status, row.activated_at, row.activated_by_id = STATUS_ACTIVE, now_utc(), actor.id if actor is not None else None
    (_model_dir() / 'current.txt').write_text(version, encoding='utf-8')
    db.session.add(AuditLog(user_id=actor.id if actor is not None else None, action='FRAUD_MODEL_ACTIVATED', module='FRAUDE',
                            result='COMPLETADA'))
    db.session.commit()
    reset_cache()
    return row


def reject_version(version, reason, actor=None):
    row = FraudModelVersion.query.filter_by(version=version).first()
    if row is None or row.status != STATUS_CANDIDATE:
        raise ValueError('Solo se puede rechazar una versión candidata existente.')
    row.status, row.notes = STATUS_REJECTED, (reason or '')[:300] or None
    db.session.add(AuditLog(user_id=actor.id if actor is not None else None, action='FRAUD_MODEL_REJECTED', module='FRAUDE',
                            result='COMPLETADA'))
    db.session.commit()


def deactivate(actor=None):
    """Retira el modelo activo (el motor sigue solo con reglas). Reversión rápida ante un modelo defectuoso."""
    pointer = _model_dir() / 'current.txt'
    if pointer.is_file():
        pointer.unlink()
    for active in FraudModelVersion.query.filter_by(status=STATUS_ACTIVE).all():
        active.status = STATUS_ARCHIVED
    db.session.add(AuditLog(user_id=actor.id if actor is not None else None, action='FRAUD_MODEL_DEACTIVATED', module='FRAUDE',
                            result='COMPLETADA'))
    db.session.commit()
    reset_cache()


def load_candidate(version):
    """Carga un artefacto **sin activarlo**, con las mismas verificaciones de integridad. Devuelve ``(modelo, metadatos)``.

    No exige las variables del inferidor actual (un candidato con otro conjunto de variables también se puede cargar para
    evaluarlo); sí exige que el SHA-256, scikit-learn y Python coincidan con los del entrenamiento.
    """
    if not version.startswith('iforest') or '/' in version or '\\' in version:
        raise ValueError('Nombre de versión no válido.')
    directory = _model_dir()
    artifact, metadata_file = directory / f'{version}.joblib', directory / f'{version}.json'
    if not artifact.is_file() or not metadata_file.is_file():
        raise ValueError('Falta el artefacto o sus metadatos.')
    metadata = json.loads(metadata_file.read_text(encoding='utf-8'))
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != metadata.get('sha256'):
        raise ValueError('El artefacto no coincide con su huella SHA-256.')
    runtime = _runtime_versions()
    if {k: metadata.get(k) for k in ('sklearn', 'python')} != runtime:
        raise ValueError('El entorno no coincide con el del entrenamiento.')
    import joblib
    # Seguridad: mismo criterio que en _load (archivo propio, ruta del sistema, SHA-256 y versiones verificados).
    return joblib.load(artifact), metadata


# ───────── Inferencia ─────────

_cache = {'key': None, 'model': None, 'version': None, 'reason': 'sin_cargar'}


def _load():
    """(modelo, versión, motivo de indisponibilidad). Nunca lanza: un fallo del modelo no debe afectar a la operación."""
    try:
        pointer = _model_dir() / 'current.txt'
        if not pointer.is_file():
            return None, None, 'sin_modelo_activo'
        version = pointer.read_text(encoding='utf-8').strip()
        if not version.startswith('iforest-') or '/' in version or '\\' in version:
            return None, None, 'puntero_invalido'
        metadata_file, artifact = _model_dir() / f'{version}.json', _model_dir() / f'{version}.joblib'
        key = (version, artifact.stat().st_mtime_ns if artifact.is_file() else None)
        if _cache['key'] == key and _cache['model'] is not None:
            return _cache['model'], version, None
        metadata = json.loads(metadata_file.read_text(encoding='utf-8'))
        if tuple(metadata.get('features', ())) != FEATURE_NAMES:
            return None, None, 'caracteristicas_distintas'
        if {k: metadata.get(k) for k in ('sklearn', 'python')} != _runtime_versions():
            return None, None, 'version_de_entorno_distinta'
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != metadata.get('sha256'):
            return None, None, 'artefacto_alterado'
        import joblib
        # Seguridad: joblib/pickle solo es seguro con archivos de confianza. Este lo escribió save_candidate en FRAUD_MODEL_DIR
        # (instance/, fuera de Git); la ruta no viene de ningún usuario y ya se verificaron su SHA-256 y las versiones.
        model = joblib.load(artifact)
        _cache.update(key=key, model=model, version=version, reason=None)
        return model, version, None
    except Exception:
        current_app.logger.warning('Detector de anomalías no disponible por un error al cargar el modelo.')
        return None, None, 'error_de_carga'


def status():
    model, version, reason = _load()
    return {'available': model is not None, 'active_version': version, 'reason': reason, 'versions': list_versions()}


def reset_cache():
    _cache.update(key=None, model=None, version=None, reason='sin_cargar')


def score(features):
    """(versión, puntuación) o (None, None). Menor puntuación = más atípico; sin umbral: es solo observación."""
    if not current_app.config.get('FRAUD_MODEL_ENABLED', True):
        return None, None
    model, version, _ = _load()
    if model is None:
        return None, None
    try:
        value = float(model.decision_function([vector_from_features(features)])[0])
    except Exception:                       # modelo roto, con otra forma de entrada o con datos extraños: se ignora, nunca se propaga
        current_app.logger.warning('Detector de anomalías: no se pudo puntuar la operación; se ignora la puntuación.')
        return None, None
    if not math.isfinite(value):
        return None, None
    return version, round(value, 4)
