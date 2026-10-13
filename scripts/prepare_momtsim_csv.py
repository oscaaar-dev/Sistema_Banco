"""Prepara el dataset público MoMTSim (dinero móvil sintético, CC BY 4.0) para el entrenamiento antifraude.

Fuente: «Synthetic Mobile Money Transaction Dataset» (Azamuke et al., Makerere University), Mendeley Data,
DOI 10.17632/zhj366m53p.2, generado con el simulador MoMTSim. Licencia CC BY 4.0: hay que citar la fuente y declarar que se
transformó (este script es esa transformación).

IMPORTANTE (honestidad de las etiquetas): los datos y la columna ``isFraud`` los produce un **simulador**. Son una verdad de
referencia *simulada*, no fraude real confirmado por personas. Se mapean al vocabulario del sistema (FRAUDE_CONFIRMADO y
LEGITIMA_VERIFICADA) porque es la única forma de calcular métricas supervisadas, pero el modelo y las métricas quedan marcados con
esa procedencia y no demuestran eficacia contra fraude real.

Uso (desde la raíz del proyecto; la carpeta de trabajo debe estar FUERA de ``data/`` y del repositorio)::

    python -m scripts.prepare_momtsim_csv download  --workdir RUTA
    python -m scripts.prepare_momtsim_csv profile   --workdir RUTA
    python -m scripts.prepare_momtsim_csv prepare   --workdir RUTA [--max-rows 180000] [--seed 7]

Solo usa la biblioteca estándar y pandas/NumPy (ya instalados). Solo descarga de ``data.mendeley.com`` por HTTPS, verifica el
tamaño y el SHA-256 publicados por la API pública y nunca ejecuta nada del archivo.
"""
import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATASET_ID = 'zhj366m53p'
DOI = '10.17632/zhj366m53p.2'
LICENSE = 'CC BY 4.0'
FILE_NAME = 'synthetic_mobile_money_transaction_dataset.csv'
API = f'https://data.mendeley.com/public-api/datasets/{DATASET_ID}/files?folder_id=root&version=2'
EXPECTED_HOST = 'data.mendeley.com'
EXPECTED_COLUMNS = ['step', 'transactionType', 'amount', 'initiator', 'oldBalInitiator', 'newBalInitiator', 'recipient',
                    'oldBalRecipient', 'newBalRecipient', 'isFraud']
# Solo las salidas de dinero con un equivalente claro en el sistema. El resto se excluye y se cuenta (no se fuerza un significado).
TYPE_MAP = {'TRANSFER': 'TRANSFERENCIA', 'PAYMENT': 'PAGO'}
EPOCH = datetime(2026, 1, 1)        # origen ARBITRARIO: el dataset solo trae el número de «step», no fechas reales
MAX_AMOUNT = 10_000_000
CHUNK = 250_000
USER_AGENT = 'Norte-academic-project dataset-preparation/1.0 (urllib; reproducible download with SHA-256 check)'


def refuse_forbidden(path):
    """La carpeta de trabajo no puede estar dentro de data/ ni dentro del repositorio."""
    resolved = Path(path).expanduser().resolve()
    for forbidden in ((ROOT / 'data').resolve(), ROOT.resolve()):
        if resolved == forbidden or forbidden in resolved.parents:
            raise SystemExit(f'La carpeta de trabajo no puede estar dentro de {forbidden}. Usa una ruta externa al repositorio y a data/.')
    return resolved


def remote_file_info():
    request = urllib.request.Request(API, headers={'Accept': 'application/json', 'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        listing = json.load(response)
    entry = next(item for item in listing if item['filename'] == FILE_NAME)
    url = entry['content_details']['download_url']
    host = urllib.request.urlparse(url).hostname
    if host != EXPECTED_HOST or not url.startswith('https://'):
        raise SystemExit(f'La URL de descarga no es la esperada ({host}).')
    return {'url': url, 'size': entry['content_details']['size'], 'sha256': entry['content_details']['sha256_hash']}


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(workdir):
    info = remote_file_info()
    raw_dir = workdir / 'raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / FILE_NAME
    if target.is_file() and target.stat().st_size == info['size'] and sha256_of(target) == info['sha256']:
        print(f'Ya descargado y verificado: {target}')
        return target
    print(f'Descargando {FILE_NAME} ({info["size"] / 1e6:.1f} MB) desde {EXPECTED_HOST} …')
    partial = target.with_suffix('.partial')
    request = urllib.request.Request(info['url'], headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(partial, 'wb') as out:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            out.write(block)
    if partial.stat().st_size != info['size'] or sha256_of(partial) != info['sha256']:
        partial.unlink()
        raise SystemExit('El archivo descargado no coincide con el tamaño y el SHA-256 publicados: descartado.')
    partial.replace(target)
    print(f'Descarga verificada (SHA-256 {info["sha256"][:16]}…): {target}')
    return target


def read_chunks(path):
    import pandas as pd
    reader = pd.read_csv(path, chunksize=CHUNK, dtype={'initiator': str, 'recipient': str, 'transactionType': str})
    for chunk in reader:
        if list(chunk.columns) != EXPECTED_COLUMNS:
            raise SystemExit(f'Columnas inesperadas: {list(chunk.columns)}')
        yield chunk


def profile(path):
    """Resumen del archivo completo, para decidir la transformación con datos (no a ciegas)."""
    import pandas as pd
    stats = {'rows': 0, 'fraud': 0, 'types': {}, 'fraud_by_type': {}, 'neg_old_balance': 0, 'zero_old_balance': 0, 'amount_max': 0.0,
             'step_min': None, 'step_max': None, 'missing': 0}
    initiators, fraud_steps = set(), pd.Series(dtype='int64')
    per_step_fraud = {}
    for chunk in read_chunks(path):
        stats['rows'] += len(chunk)
        stats['fraud'] += int(chunk['isFraud'].sum())
        stats['missing'] += int(chunk.isna().any(axis=1).sum())
        for name, count in chunk['transactionType'].value_counts().items():
            stats['types'][name] = stats['types'].get(name, 0) + int(count)
        for name, count in chunk[chunk['isFraud'] == 1]['transactionType'].value_counts().items():
            stats['fraud_by_type'][name] = stats['fraud_by_type'].get(name, 0) + int(count)
        stats['neg_old_balance'] += int((chunk['oldBalInitiator'] < 0).sum())
        stats['zero_old_balance'] += int((chunk['oldBalInitiator'] == 0).sum())
        stats['amount_max'] = max(stats['amount_max'], float(chunk['amount'].max()))
        low, high = int(chunk['step'].min()), int(chunk['step'].max())
        stats['step_min'] = low if stats['step_min'] is None else min(stats['step_min'], low)
        stats['step_max'] = high if stats['step_max'] is None else max(stats['step_max'], high)
        initiators.update(chunk['initiator'].unique())
        for step, count in chunk[chunk['isFraud'] == 1]['step'].value_counts().items():
            per_step_fraud[int(step)] = per_step_fraud.get(int(step), 0) + int(count)
    stats['unique_initiators'] = len(initiators)
    steps = sorted(per_step_fraud)
    stats['fraud_steps'] = {'distintos': len(steps), 'primeros': steps[:5], 'ultimos': steps[-5:]}
    stats['fraud_rate'] = stats['fraud'] / stats['rows']
    return stats


def pseudonym(series):
    """Identificador estable y no reversible: el dataset trae números de 16 dígitos con aspecto de tarjeta (sintéticos)."""
    import pandas as pd
    return pd.Series([hashlib.sha256(f'momtsim|{value}'.encode()).hexdigest()[:12] for value in series.unique()],
                     index=series.unique()).reindex(series).to_numpy()


def prepare(path, workdir, max_rows, seed):
    import numpy as np
    import pandas as pd
    manifest = {
        'fuente': {'titulo': 'Synthetic Mobile Money Transaction Dataset', 'doi': DOI, 'licencia': LICENSE,
                   'archivo_original': FILE_NAME, 'sha256_original': sha256_of(path),
                   'atribucion': 'Azamuke et al., Makerere University, Mendeley Data (simulador MoMTSim). Transformado por este script.'},
        'advertencia': ('Datos y etiquetas SIMULADOS. isFraud es la verdad de referencia del simulador, no fraude real confirmado. '
                        'Los resultados no demuestran eficacia contra fraude real.'),
        'parametros': {'max_filas': max_rows, 'semilla': seed, 'origen_temporal_arbitrario': EPOCH.isoformat(),
                       'resolucion_temporal': '1 paso (se asume 1 hora; no se verificó en la documentación accesible)'},
        'transformacion': {'transactionType': TYPE_MAP, 'excluidos': 'cualquier otro tipo', 'initiator': 'seudónimo SHA-256 truncado a 12 caracteres',
                           'timestamp': 'EPOCH + step horas', 'balance_before': 'oldBalInitiator (las filas con saldo negativo se descartan)',
                           'label': 'isFraud=1 → FRAUDE_CONFIRMADO (simulado); isFraud=0 → LEGITIMA_VERIFICADA (simulado)',
                           'se_ignoran': ['newBalInitiator', 'recipient', 'oldBalRecipient', 'newBalRecipient']},
        'conteos': {'leidas': 0, 'tipo_excluido': 0, 'saldo_negativo': 0, 'importe_invalido': 0, 'conservadas_antes_de_muestrear': 0},
    }
    frames = []
    for chunk in read_chunks(path):
        manifest['conteos']['leidas'] += len(chunk)
        known_type = chunk['transactionType'].isin(TYPE_MAP)
        manifest['conteos']['tipo_excluido'] += int((~known_type).sum())
        chunk = chunk[known_type]
        negative = chunk['oldBalInitiator'] < 0
        invalid = ~np.isfinite(chunk['amount']) | (chunk['amount'] <= 0) | (chunk['amount'] > MAX_AMOUNT) | ~np.isfinite(chunk['oldBalInitiator'])
        manifest['conteos']['saldo_negativo'] += int(negative.sum())
        manifest['conteos']['importe_invalido'] += int((invalid & ~negative).sum())
        chunk = chunk[~negative & ~invalid]
        frames.append(pd.DataFrame({
            'step': chunk['step'].astype('int64'), 'initiator': chunk['initiator'], 'amount': chunk['amount'].round(2),
            'balance_before': chunk['oldBalInitiator'].round(2), 'kind': chunk['transactionType'].map(TYPE_MAP),
            'is_fraud': chunk['isFraud'].astype(int)}))
    data = pd.concat(frames, ignore_index=True)
    manifest['conteos']['conservadas_antes_de_muestrear'] = int(len(data))
    manifest['conteos']['fraude_antes_de_muestrear'] = int(data['is_fraud'].sum())

    accounts = pd.Series(data['initiator'].unique())
    per_account = data.groupby('initiator').size()
    fraction = min(1.0, max_rows / len(data))
    rng = np.random.RandomState(seed)
    chosen = set(accounts[rng.rand(len(accounts)) < fraction])
    sampled = data[data['initiator'].isin(chosen)].copy()
    # Muestreo por CUENTA (todas las operaciones de cada cuenta elegida): conserva las secuencias que usan las características de
    # frecuencia y mantiene la prevalencia natural. Si por azar se pasa del tope, se recorta quitando cuentas completas.
    while len(sampled) > max_rows:
        drop = rng.choice(sorted(chosen), size=max(1, len(chosen) // 100), replace=False)
        chosen -= set(drop)
        sampled = sampled[sampled['initiator'].isin(chosen)]
    manifest['muestreo'] = {'tipo': 'por cuenta (aleatorio con semilla)', 'fraccion_de_cuentas': round(fraction, 6),
                            'cuentas_en_origen': int(len(accounts)), 'cuentas_en_muestra': int(len(chosen)),
                            'operaciones_por_cuenta_media': round(float(per_account.mean()), 3)}
    sampled['account_id'] = pseudonym(sampled['initiator'])
    sampled['timestamp'] = [EPOCH + pd.Timedelta(hours=int(step)) for step in sampled['step']]
    sampled['label'] = np.where(sampled['is_fraud'] == 1, 'FRAUDE_CONFIRMADO', 'LEGITIMA_VERIFICADA')
    sampled = sampled.sort_values(['timestamp', 'account_id'], kind='mergesort').reset_index(drop=True)
    sampled.insert(0, 'transaction_id', [f'MOMT-{i:07d}' for i in range(len(sampled))])
    out = sampled[['transaction_id', 'timestamp', 'account_id', 'amount', 'balance_before', 'kind', 'label']].copy()
    out['timestamp'] = out['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S')

    prepared_dir = workdir / 'prepared'
    prepared_dir.mkdir(parents=True, exist_ok=True)
    destination = prepared_dir / 'momtsim_prepared.csv'
    out.to_csv(destination, index=False, encoding='utf-8')
    manifest['resultado'] = {'archivo': destination.name, 'filas': int(len(out)), 'fraude_simulado': int((out['label'] == 'FRAUDE_CONFIRMADO').sum()),
                             'legitimas_simuladas': int((out['label'] == 'LEGITIMA_VERIFICADA').sum()),
                             'tamano_bytes': destination.stat().st_size, 'sha256': sha256_of(destination),
                             'rango_pasos': [int(sampled['step'].min()), int(sampled['step'].max())]}
    (prepared_dir / 'momtsim_prepared.manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(manifest['resultado'], indent=2))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=['download', 'profile', 'prepare'])
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--max-rows', type=int, default=180_000)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    workdir = refuse_forbidden(args.workdir)
    if args.stage == 'download':
        download(workdir)
        return
    raw = workdir / 'raw' / FILE_NAME
    if not raw.is_file():
        raise SystemExit('Primero ejecuta la etapa «download».')
    if args.stage == 'profile':
        print(json.dumps(profile(raw), indent=2, ensure_ascii=False))
    else:
        prepare(raw, workdir, args.max_rows, args.seed)


if __name__ == '__main__':
    main()
