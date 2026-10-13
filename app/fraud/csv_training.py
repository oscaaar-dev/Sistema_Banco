"""Carga, validación y entrenamiento del detector a partir de un archivo CSV.

El CSV es solo la fuente de **entrenamiento y evaluación**; lo operativo (evaluaciones, incidentes, restricciones y el registro de
versiones del modelo) vive en la base SQL. Reglas de este módulo:

* Un CSV arbitrario **nunca sustituye al modelo activo**: el resultado es un candidato que hay que activar de forma explícita.
* Nunca se inventan etiquetas. ``label`` admite FRAUDE_CONFIRMADO, LEGITIMA_VERIFICADA y SIN_VERIFICAR (vacío = SIN_VERIFICAR);
  cualquier otro valor invalida la fila. Una anomalía sin verificar NO es fraude ni operación legítima.
* Sin etiquetas fiables se usa detección de anomalías no supervisada (``anomaly``); las métricas supervisadas solo se calculan
  con etiquetas confirmadas suficientes.
* Se valida todo: columnas, tipos, faltantes, fechas, montos, duplicados, tamaño y codificación. La carpeta ``data/`` del
  proyecto está vedada: no se lee ni se escribe en ella.
"""
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

from flask import current_app

from . import anomaly

REQUIRED_COLUMNS = ('timestamp', 'account_id', 'amount', 'balance_before', 'kind')
OPTIONAL_COLUMNS = ('transaction_id', 'label')
KINDS = ('TRANSFERENCIA', 'PAGO', 'RECARGA')
LABELS = ('FRAUDE_CONFIRMADO', 'LEGITIMA_VERIFICADA', 'SIN_VERIFICAR')
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000
MAX_FILE_INVALID_RATIO = 0.5
MAX_AMOUNT = 10_000_000


class CsvValidationError(ValueError):
    """El archivo no se puede usar. El mensaje (en español) explica el motivo."""


def guard_path(path):
    """Resuelve la ruta y rechaza la carpeta ``data/`` del proyecto (lectura y escritura)."""
    resolved = Path(path).expanduser().resolve()
    forbidden = (Path(current_app.root_path).parent / 'data').resolve()
    if resolved == forbidden or forbidden in resolved.parents:
        raise CsvValidationError('No se permite leer ni escribir en la carpeta data/ del proyecto.')
    return resolved


def validate_csv_text(text, *, now=None):
    """Valida el texto de un CSV. Devuelve ``(DataFrame limpio, informe)`` o lanza ``CsvValidationError``."""
    import csv
    import numpy as np
    import pandas as pd
    # Las cabeceras repetidas se comprueban antes de leer: pandas las renombraría («timestamp.1») y ocultaría el problema.
    first_line = next(csv.reader(io.StringIO(text)), [])
    normalized = [str(name).strip().lower() for name in first_line]
    if len(set(normalized)) != len(normalized):
        raise CsvValidationError('El CSV tiene columnas con el mismo nombre.')
    try:
        raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise CsvValidationError(f'El CSV está mal formado: {str(error)[:150]}') from None
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    if len(set(raw.columns)) != len(raw.columns):
        raise CsvValidationError('El CSV tiene columnas con el mismo nombre.')
    missing = [column for column in REQUIRED_COLUMNS if column not in raw.columns]
    if missing:
        raise CsvValidationError(f'Faltan columnas obligatorias: {", ".join(missing)}.')
    total = len(raw)
    if total == 0:
        raise CsvValidationError('El CSV no tiene filas.')
    if total > MAX_ROWS:
        raise CsvValidationError(f'El CSV supera el máximo de {MAX_ROWS} filas.')
    ignored = [column for column in raw.columns if column not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
    work = raw[[column for column in REQUIRED_COLUMNS + OPTIONAL_COLUMNS if column in raw.columns]].apply(lambda col: col.str.strip())

    reasons = pd.Series([''] * total, index=work.index, dtype=object)

    def flag(mask, reason):
        nonlocal reasons
        reasons = reasons.where((reasons != '') | ~mask, reason)

    flag((work[list(REQUIRED_COLUMNS)] == '').any(axis=1), 'valores_faltantes')
    stamp = pd.to_datetime(work['timestamp'], errors='coerce', utc=True)
    flag(stamp.isna(), 'fecha_invalida')
    current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='UTC')
    current = current.tz_localize('UTC') if current.tzinfo is None else current.tz_convert('UTC')
    flag(stamp > current + pd.Timedelta(days=1), 'fecha_futura')
    amount = pd.to_numeric(work['amount'], errors='coerce')
    flag(~np.isfinite(amount) | (amount <= 0) | (amount > MAX_AMOUNT), 'monto_invalido')
    balance = pd.to_numeric(work['balance_before'], errors='coerce')
    flag(~np.isfinite(balance) | (balance < 0) | (balance > MAX_AMOUNT * 100), 'saldo_invalido')
    kind = work['kind'].str.upper()
    flag(~kind.isin(KINDS), 'tipo_desconocido')
    flag(work['account_id'].str.len() > 64, 'cuenta_invalida')
    if 'label' in work.columns:
        label = work['label'].str.upper().replace('', 'SIN_VERIFICAR')
        flag(~label.isin(LABELS), 'etiqueta_invalida')
    else:
        label = pd.Series('SIN_VERIFICAR', index=work.index)

    invalid = reasons != ''
    invalid_count = int(invalid.sum())
    ratio = invalid_count / total
    if ratio > MAX_FILE_INVALID_RATIO:
        raise CsvValidationError(f'El {ratio:.0%} de las filas es inválido; el archivo no es fiable. Motivos: {dict(Counter(reasons[invalid]))}.')

    clean = pd.DataFrame({'timestamp': stamp.dt.tz_convert('UTC').dt.tz_localize(None), 'account_id': work['account_id'],
                          'amount': amount, 'balance_before': balance, 'kind': kind, 'label': label})
    if 'transaction_id' in work.columns:
        clean['transaction_id'] = work['transaction_id']
    clean = clean[~invalid]
    before = len(clean)
    if 'transaction_id' in clean.columns:
        has_id = clean['transaction_id'] != ''
        clean = clean[~(has_id & clean.duplicated('transaction_id', keep='first'))]
        clean = clean.drop(columns=['transaction_id'])
    clean = clean.drop_duplicates(subset=['timestamp', 'account_id', 'amount', 'balance_before', 'kind'], keep='first')
    duplicates = before - len(clean)
    clean = clean.sort_values('timestamp', kind='mergesort').reset_index(drop=True)
    report = {
        'filas_leidas': int(total), 'filas_validas': int(len(clean)), 'invalidas': invalid_count,
        'proporcion_invalidas': round(ratio, 4), 'motivos_invalidas': {k: int(v) for k, v in Counter(reasons[invalid]).items()},
        'duplicados_eliminados': int(duplicates), 'columnas_ignoradas': ignored,
        'etiquetas': {k: int(v) for k, v in Counter(clean['label']).items()},
        'rango_fechas': [clean['timestamp'].min().isoformat(), clean['timestamp'].max().isoformat()] if len(clean) else None,
    }
    return clean, report


def load_csv(path):
    """Lee un CSV del disco con todas las comprobaciones. Devuelve ``(DataFrame, informe, sha256)``."""
    resolved = guard_path(path)
    if not resolved.is_file() or resolved.suffix.lower() != '.csv':
        raise CsvValidationError('La ruta debe ser un archivo .csv existente.')
    if resolved.stat().st_size > MAX_FILE_BYTES:
        raise CsvValidationError(f'El archivo supera el máximo de {MAX_FILE_BYTES // (1024 * 1024)} MB.')
    raw = resolved.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise CsvValidationError('El archivo no está codificado en UTF-8.') from None
    frame, report = validate_csv_text(text)
    return frame, report, hashlib.sha256(raw).hexdigest()


MAX_MANIFEST_BYTES = 64 * 1024


def load_manifest(path):
    """Manifiesto JSON de preparación del dataset (fuente, licencia, transformaciones, advertencias). Acotado y validado."""
    resolved = guard_path(path)
    if not resolved.is_file() or resolved.suffix.lower() != '.json' or resolved.stat().st_size > MAX_MANIFEST_BYTES:
        raise CsvValidationError('El manifiesto debe ser un archivo .json existente de menos de 64 KB.')
    try:
        manifest = json.loads(resolved.read_text(encoding='utf-8'))
    except ValueError:
        raise CsvValidationError('El manifiesto no es un JSON válido.') from None
    if not isinstance(manifest, dict):
        raise CsvValidationError('El manifiesto debe ser un objeto JSON.')
    return manifest


def train_from_csv(path, *, created_by=None, manifest_path=None, notes=None):
    """Valida el CSV, separa por tiempo, entrena y evalúa. Devuelve el registro CANDIDATO (no cambia el modelo activo).

    ``manifest_path`` (opcional) adjunta al registro la procedencia de los datos (fuente, licencia, transformaciones y advertencias,
    p. ej. «etiquetas simuladas») para que quede junto a las métricas.
    """
    frame, report, sha = load_csv(path)
    if manifest_path:
        manifest = load_manifest(manifest_path)
        report['manifiesto_de_preparacion'] = manifest
        notes = notes or str(manifest.get('advertencia', ''))[:300]
    needed = current_app.config['FRAUD_MODEL_MIN_SAMPLES'] + anomaly.MIN_EVAL_ROWS
    if len(frame) < needed:
        raise anomaly.InsufficientData(f'Tras validar quedan {len(frame)} filas; se necesitan al menos {needed}.')
    return anomaly.train_candidate(frame, source='CSV', validation=report, data_sha256=sha, created_by=created_by, notes=notes)
