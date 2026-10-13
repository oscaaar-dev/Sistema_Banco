"""Genera el dataset SINTÉTICO combinado de 12.000 eventos (8.000 transacciones y 4.000 accesos) y su manifiesto.

Uso (desde la raíz del proyecto)::

    python -m scripts.generate_antifraud_dataset [--output-dir datasets_sinteticos] [--seed 2026]

* Todo es ficticio: no lee ningún archivo del proyecto ni de ``data/`` (de hecho se niega a escribir dentro de ``data/``).
* **Nunca sobrescribe**: si ya existe un archivo con el mismo nombre y es idéntico, no hace nada; si es distinto, escribe una copia
  con sufijo ``_v2``, ``_v3``… y lo indica.
* Valida el resultado (exactamente 12.000 filas, 8.000/4.000, cronología, saldos, ausencia de datos personales…) **antes** de escribirlo.
"""
import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NAME = 'dataset_antifraude_completo_12000.csv'


def refuse_data_dir(path):
    resolved = Path(path).expanduser().resolve()
    forbidden = (ROOT / 'data').resolve()
    if resolved == forbidden or forbidden in resolved.parents:
        raise SystemExit('No se permite escribir dentro de la carpeta data/ del proyecto.')
    return resolved


def build(seed):
    import pandas as pd
    from app.fraud import dataset_validation as dv, synthetic_events as se
    frame = se.generate_combined(seed)
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator='\n')
    text = buffer.getvalue()
    # Se valida lo que realmente se escribirá: el CSV releído como texto, igual que lo leerá el cargador.
    reloaded = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    report = dv.validate_combined(reloaded, expected_rows=se.N_TRANSACTIONS + se.N_ACCESSES, expected_transactions=se.N_TRANSACTIONS,
                                  expected_accesses=se.N_ACCESSES)
    dv.assert_valid(report)
    return text, report, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir', default='datasets_sinteticos')
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()
    from app.fraud import synthetic_events as se
    directory = refuse_data_dir(ROOT / args.output_dir if not Path(args.output_dir).is_absolute() else args.output_dir)
    text, report, frame = build(args.seed)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    target = directory / NAME
    version = 1
    while target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            print(f'Ya existe un archivo idéntico, no se escribe nada: {target}')
            return
        version += 1
        target = directory / f'{Path(NAME).stem}_v{version}.csv'
    directory.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8', newline='')
    manifest = {
        'archivo': target.name, 'sha256': digest, 'bytes': target.stat().st_size, 'semilla': args.seed, 'esquema': se.SCHEMA_VERSION,
        'filas': int(len(frame)), 'columnas': list(frame.columns), 'distribucion': report['distribucion'], 'rango_temporal': report['rango_temporal'],
        'escenarios': {name: int((frame['scenario'] == name).sum()) for name, _, _ in se.SCENARIOS},
        'validacion': {'ok': report['ok'], 'comprobaciones': report['checks']},
        'separabilidad_univariante': report['separabilidad_univariante'],
        'puntuacion_descriptiva_auc_univariante': report['puntuacion_descriptiva_auc_univariante'],
        'aviso': 'Datos y etiquetas SINTÉTICOS. is_fraud lo decide el escenario de generación (no la puntuación). No equivalen a fraude confirmado.',
    }
    (directory / f'{target.stem}.manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Dataset escrito: {target} ({manifest["filas"]} filas, SHA-256 {digest[:16]}…)')
    if version > 1:
        print(f'Ya existía un archivo distinto con el nombre original; se escribió como versión {version} sin tocar el anterior.')


if __name__ == '__main__':
    main()
