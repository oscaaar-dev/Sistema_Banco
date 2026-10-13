"""Dataset sintético combinado (8.000 transacciones + 4.000 accesos): generación, esquema, validación y reproducibilidad.

Todo ficticio y en memoria o en ``tmp_path``; nada lee ni escribe ``data/``.
"""
import hashlib
import inspect
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.fraud import dataset_validation as dv, synthetic_events as se
from scripts import generate_antifraud_dataset as gen

ROOT = Path(__file__).resolve().parents[1]
REAL_DATASET = ROOT / 'datasets_sinteticos' / 'dataset_antifraude_completo_12000.csv'


@pytest.fixture(scope='module')
def generated():
    frame = se.generate_combined(2026)
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator='\n')
    return frame, buffer.getvalue()


@pytest.fixture
def table(generated):
    """El CSV releído como lo lee el cargador (todo texto). Cada prueba recibe su propia copia."""
    return pd.read_csv(io.StringIO(generated[1]), dtype=str, keep_default_na=False)


def _validate(frame):
    return dv.validate_combined(frame, expected_rows=12000, expected_transactions=8000, expected_accesses=4000)


# ───────── Generación y validación completa ─────────

def test_the_generated_dataset_has_the_exact_size_split_and_passes_every_check(generated, table):
    frame, _ = generated
    assert len(frame) == 12000 and frame['event_type'].value_counts().to_dict() == {'TRANSACCION': 8000, 'ACCESO': 4000}
    report = _validate(table)
    assert report['ok'], [c for c in report['checks'] if not c['ok']]
    assert len(report['checks']) >= 30 and all(c['ok'] for c in report['checks'])
    assert list(frame.columns) == se.COLUMNS and len(set(se.COLUMNS)) == len(se.COLUMNS)


def test_generation_is_deterministic_and_the_seed_matters(generated):
    again = se.generate_combined(2026)
    assert again.equals(generated[0])
    assert not se.generate_combined(7).equals(generated[0])


def test_class_distribution_and_scenarios_are_documented_and_complete(generated):
    frame, _ = generated
    distribution = _validate(pd.read_csv(io.StringIO(generated[1]), dtype=str, keep_default_na=False))['distribucion']
    assert distribution['TRANSACCION']['is_fraud_1'] == int(frame[frame.event_type == 'TRANSACCION'].is_fraud.sum()) > 400
    assert 0.05 < distribution['TRANSACCION']['prevalencia'] < 0.12 and 0.07 < distribution['ACCESO']['prevalencia'] < 0.15
    documented = {name: fraud for name, fraud, _ in se.SCENARIOS}
    assert set(frame['scenario']) == set(documented)                       # todo escenario generado está documentado y al revés
    for name, fraud in documented.items():
        assert set(frame.loc[frame.scenario == name, 'is_fraud']) == {fraud}      # la etiqueta la fija el escenario, sin excepciones
    assert all(description.strip() for _, _, description in se.SCENARIOS)


def test_labels_do_not_come_from_the_descriptive_score_and_classes_overlap(generated):
    frame, _ = generated
    source = inspect.getsource(se._describe).split('"""')[2]                                       # solo el código, sin el docstring
    assert "['fraud']" not in source and "['scenario']" not in source and 'is_fraud' not in source      # la puntuación jamás ve la etiqueta ni el escenario
    tx = frame[frame.event_type == 'TRANSACCION']
    fraud, legit = tx[tx.is_fraud == 1], tx[tx.is_fraud == 0]
    assert (fraud.nivel_riesgo_sintetico == 'NORMAL').any() and (legit.nivel_riesgo_sintetico != 'NORMAL').any()   # solapamiento en ambos sentidos
    assert fraud.puntuacion_riesgo_sintetica.astype(float).mean() > legit.puntuacion_riesgo_sintetica.astype(float).mean()      # informativa, sin ser la etiqueta
    assert fraud.scenario.nunique() >= 4 and (frame[frame.event_type == 'ACCESO'].is_fraud == 1).sum() > 300


def test_no_single_observable_variable_reveals_the_label(table):
    report = _validate(table)
    for event, values in report['separabilidad_univariante'].items():
        assert values and max(values.values()) <= dv.MAX_UNIVARIATE_AUC, (event, max(values.items(), key=lambda item: item[1]))
    for event in ('TRANSACCION', 'ACCESO'):
        assert report['puntuacion_descriptiva_auc_univariante'][event]['puntuacion_riesgo_sintetica'] < 1.0      # informativa, pero no perfecta
    assert sum(1 for tx_fraud in table.loc[(table.event_type == 'TRANSACCION') & (table.is_fraud == '1'), 'contexto_nuevo'] if tx_fraud == '0') > 50   # fraudes sin la señal «clásica»


def test_chronology_and_balances_are_coherent_per_account(generated):
    frame, _ = generated
    assert pd.to_datetime(frame.timestamp).is_monotonic_increasing and frame.event_id.is_unique
    tx = frame[frame.event_type == 'TRANSACCION'].copy()
    for column in ('importe', 'saldo_anterior', 'saldo_posterior'):
        tx[column] = tx[column].astype(float)
    assert (tx.saldo_posterior >= 0).all() and (tx.saldo_anterior >= 0).all()
    debit = tx.tipo_operacion != 'DEPOSITO'
    assert ((tx.saldo_anterior - tx.importe)[debit] - tx.saldo_posterior[debit]).abs().max() < 0.011
    assert ((tx.saldo_anterior + tx.importe)[~debit] - tx.saldo_posterior[~debit]).abs().max() < 0.011
    assert (tx.importe[debit] <= tx.saldo_anterior[debit] + 0.011).all()
    for _, group in tx.groupby('account_id'):
        if len(group) > 1:
            assert abs(group.saldo_anterior.to_numpy()[1:] - group.saldo_posterior.to_numpy()[:-1]).max() < 0.011      # el saldo encadena de una operación a la siguiente


def test_window_counters_only_look_backwards_and_match_the_timeline(generated):
    frame, _ = generated
    tx = frame[(frame.event_type == 'TRANSACCION') & (frame.tipo_operacion != 'DEPOSITO')].copy()
    tx['t'] = pd.to_datetime(tx.timestamp)
    sample = tx[tx.account_id.isin(tx.account_id.drop_duplicates().sample(25, random_state=3))]
    for account, group in sample.groupby('account_id'):
        times = group.t.reset_index(drop=True)
        for position, (_, row) in enumerate(group.iterrows()):
            prior = times[:position]
            assert int(row.operaciones_10m) == int(((times[position] - prior) <= pd.Timedelta(minutes=10)).sum())
            assert int(row.operaciones_1h) == int(((times[position] - prior) <= pd.Timedelta(hours=1)).sum())


def test_there_is_no_personal_or_bank_data_and_no_real_looking_identifiers(generated, table):
    assert table.account_id.str.fullmatch(r'ACC-\d{4}').all() and table.event_id.str.fullmatch(r'EVT-\d{6}').all()
    text = generated[1]
    assert '@' not in text and 'http' not in text.lower()
    assert not any(column in se.COLUMNS for column in ('nombre', 'dni', 'correo', 'email', 'telefono', 'iban', 'tarjeta', 'ip', 'password', 'otp_codigo'))
    assert (table.is_synthetic == '1').all()


# ───────── Cada validación detecta la corrupción que dice detectar ─────────

def _broken(table, mutate):
    frame = table.copy()
    mutate(frame)
    report = _validate(frame)
    assert not report['ok']
    return [c['comprobacion'] for c in report['checks'] if not c['ok']]


@pytest.mark.parametrize('name,mutate,expected', [
    ('fila_de_menos', lambda f: f.drop(index=f.index[-1], inplace=True), 'Exactamente 12000 filas'),
    ('id_duplicado', lambda f: f.__setitem__('event_id', f['event_id'].where(f.index != 5, f.loc[4, 'event_id'])), 'Identificadores únicos'),
    ('fecha_invalida', lambda f: f.__setitem__('timestamp', f['timestamp'].where(f.index != 9, '2026-13-45T99:00:00')), 'Fechas válidas'),
    ('desorden', lambda f: f.__setitem__('timestamp', f['timestamp'].where(f.index != 100, '2025-01-01T00:00:00')), 'Orden cronológico'),
    ('tipo_evento', lambda f: f.__setitem__('event_type', f['event_type'].where(f.index != 3, 'OTRO')), 'Tipos de evento válidos'),
    ('etiqueta_2', lambda f: f.__setitem__('is_fraud', f['is_fraud'].where(f.index != 3, '2')), 'Etiqueta is_fraud binaria'),
    ('no_sintetico', lambda f: f.__setitem__('is_synthetic', f['is_synthetic'].where(f.index != 3, '0')), 'is_synthetic'),
    ('saldo_negativo', lambda f: f.__setitem__('saldo_posterior', f['saldo_posterior'].where(f.index != 0, '-5.00')), 'Saldos finitos y no negativos'),
    ('saldo_incoherente', lambda f: f.__setitem__('saldo_posterior', f['saldo_posterior'].where(f.index != 0, '999999.99')), 'saldo_posterior = saldo_anterior'),
    ('importe_extremo', lambda f: f.__setitem__('importe', f['importe'].where(f.index != 0, '9999999999')), 'Importes finitos'),
    ('correo', lambda f: f.__setitem__('account_id', f['account_id'].where(f.index != 1, 'ana@example.com')), 'sin datos personales'),
    ('tarjeta', lambda f: f.__setitem__('scenario', f['scenario'].where(f.index != 1, '4539578763621486')), 'Ningún número con formato de tarjeta'),
    ('mezcla_de_tipos', lambda f: f.__setitem__('resultado_login', f['resultado_login'].where(f.index != 0, 'EXITOSO')), 'columnas de acceso'),
    ('resultado_login', lambda f: f.__setitem__('resultado_login', f['resultado_login'].where(f.event_type != 'ACCESO', 'TAL VEZ')), 'Resultado de acceso válido'),
    ('ventanas', lambda f: f.__setitem__('operaciones_2m', f['operaciones_2m'].where(f.index != 0, '50')), 'Contadores de ventana'),
])
def test_the_validator_catches_each_kind_of_corruption(table, name, mutate, expected):
    assert any(expected in failed for failed in _broken(table, mutate)), name


def test_the_validator_reports_schema_problems_without_crashing(table):
    extra = table.copy()
    extra['columna_nueva'] = 'x'
    assert 'Columnas exactamente las del esquema' in [c['comprobacion'] for c in _validate(extra)['checks'] if not c['ok']]
    missing = table.drop(columns=['importe'])
    assert not _validate(missing)['ok']
    duplicated = pd.concat([table, table[['importe']]], axis=1)
    assert 'Sin columnas duplicadas' in [c['comprobacion'] for c in _validate(duplicated)['checks'] if not c['ok']]
    with pytest.raises(dv.DatasetInvalid, match='no supera'):
        dv.assert_valid(_validate(missing))


def test_leakage_is_flagged_when_one_variable_would_reveal_the_label(table):
    leaky = table.copy()
    leaky['operaciones_2m'] = leaky['is_fraud'].where(leaky['event_type'] == 'TRANSACCION', '')       # variable observable = etiqueta
    leaky['operaciones_10m'] = leaky['operaciones_2m']
    leaky['operaciones_1h'] = leaky['operaciones_2m']
    failed = [c['comprobacion'] for c in _validate(leaky)['checks'] if not c['ok']]
    assert any('Ninguna variable observable individual' in name for name in failed)


# ───────── Escritura segura, sin sobrescribir ─────────

def test_the_script_writes_validates_and_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['gen', '--output-dir', str(tmp_path), '--seed', '2026'])
    gen.main()
    target = tmp_path / gen.NAME
    first = target.read_bytes()
    manifest = json.loads((tmp_path / 'dataset_antifraude_completo_12000.manifest.json').read_text(encoding='utf-8'))
    assert manifest['filas'] == 12000 and manifest['validacion']['ok'] and manifest['sha256'] == hashlib.sha256(first).hexdigest()
    assert manifest['distribucion']['TRANSACCION']['eventos'] == 8000 and manifest['esquema'] == se.SCHEMA_VERSION and 'SINTÉTICOS' in manifest['aviso']
    gen.main()                                                                                    # idéntico: no escribe nada
    assert 'no se escribe nada' in capsys.readouterr().out and sorted(p.name for p in tmp_path.iterdir()) == sorted([gen.NAME, 'dataset_antifraude_completo_12000.manifest.json'])
    target.write_text('trabajo previo del usuario\n', encoding='utf-8')                         # un archivo distinto con el mismo nombre
    gen.main()
    assert target.read_text(encoding='utf-8') == 'trabajo previo del usuario\n'                   # no se tocó
    assert (tmp_path / 'dataset_antifraude_completo_12000_v2.csv').read_bytes() == first


def test_the_script_refuses_to_write_inside_data(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['gen', '--output-dir', str(gen.ROOT / 'data' / 'x')])
    with pytest.raises(SystemExit, match='data/'):
        gen.main()
    assert not (gen.ROOT / 'data' / 'x').exists()


# ───────── Reproducibilidad del archivo entregado ─────────

@pytest.mark.skipif(not REAL_DATASET.is_file(), reason='el dataset entregado no está en esta copia de trabajo')
def test_the_delivered_csv_can_be_validated_again_and_is_reproducible_from_its_seed():
    text = REAL_DATASET.read_text(encoding='utf-8')
    frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    report = _validate(frame)
    assert report['ok'], [c for c in report['checks'] if not c['ok']]
    manifest = json.loads(REAL_DATASET.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    assert manifest['sha256'] == hashlib.sha256(REAL_DATASET.read_bytes()).hexdigest()
    regenerated, _, _ = gen.build(manifest['semilla'])
    assert hashlib.sha256(regenerated.encode('utf-8')).hexdigest() == manifest['sha256']            # misma semilla → mismo archivo, bit a bit
