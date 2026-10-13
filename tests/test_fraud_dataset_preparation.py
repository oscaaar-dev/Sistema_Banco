"""Preparación de un dataset público (MoMTSim) y registro de su procedencia. No descarga nada: usa un archivo falso con el mismo formato."""
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from app import create_app
from app.fraud import anomaly, csv_training, synthetic
from app.models import FraudModelVersion
from scripts import prepare_momtsim_csv as prep

COLUMNS = prep.EXPECTED_COLUMNS


@pytest.fixture
def world(tmp_path):
    app = create_app({'SECRET_KEY': 'k' * 40, 'FRAUD_MODEL_DIR': str(tmp_path / 'models')})
    return {'app': app, 'tmp': tmp_path, 'runner': app.test_cli_runner()}


def _fake_raw(path, accounts=60, per_account=12):
    """CSV con el formato del original: tarjetas de 16 dígitos como iniciador, tipos variados y saldos a veces negativos."""
    rows = []
    for account in range(accounts):
        initiator = f'45{account:014d}'
        for index in range(per_account):
            kind = ['PAYMENT', 'TRANSFER', 'DEPOSIT', 'WITHDRAWAL', 'DEBIT'][index % 5]
            fraud = int(kind == 'TRANSFER' and (account + index) % 3 == 0)
            balance = -50.0 if (account, index) == (0, 1) else 1000.0 + account
            amount = 19000.0 if kind == 'TRANSFER' else 500.0 + index
            rows.append([index // 2, kind, amount, initiator, balance, balance - amount, f'99{account:012d}', 5.0, 5.0 + amount, fraud])
    rows.append([1, 'TRANSFER', 0.0, '4599999999999999', 100.0, 100.0, 'x', 0, 0, 0])             # importe inválido
    rows.append([1, 'PAYMENT', 2e9, '4599999999999998', 100.0, 100.0, 'x', 0, 0, 0])               # importe desmesurado
    pd.DataFrame(rows, columns=COLUMNS).to_csv(path, index=False)


def test_the_working_folder_can_never_be_inside_data_or_the_repository(tmp_path):
    assert prep.refuse_forbidden(tmp_path) == tmp_path.resolve()
    for forbidden in (prep.ROOT / 'data', prep.ROOT / 'data' / 'sub', prep.ROOT, prep.ROOT / 'tests'):
        with pytest.raises(SystemExit, match='no puede estar dentro'):
            prep.refuse_forbidden(forbidden)


def test_preparation_maps_the_schema_pseudonymizes_ids_and_records_everything_in_a_manifest(tmp_path):
    raw = tmp_path / 'raw.csv'
    _fake_raw(raw)
    destination = prep.prepare(raw, tmp_path, max_rows=100_000, seed=7)
    frame = pd.read_csv(destination, dtype=str, keep_default_na=False)
    assert list(frame.columns) == ['transaction_id', 'timestamp', 'account_id', 'amount', 'balance_before', 'kind', 'label']
    assert set(frame['kind']) == {'TRANSFERENCIA', 'PAGO'}                                        # solo los tipos con equivalente claro
    assert set(frame['label']) == {'FRAUDE_CONFIRMADO', 'LEGITIMA_VERIFICADA'}
    assert set(frame.loc[frame['label'] == 'FRAUDE_CONFIRMADO', 'kind']) == {'TRANSFERENCIA'}      # el simulador solo defrauda transferencias
    assert all(re.fullmatch(r'[0-9a-f]{12}', value) for value in frame['account_id'])              # seudónimo; ningún número de 16 dígitos
    assert not frame.apply(lambda column: column.str.contains(r'\d{13,}', regex=True)).any().any()
    assert all(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value) for value in frame['timestamp'])
    assert (frame['amount'].astype(float) > 0).all() and (frame['balance_before'].astype(float) >= 0).all()
    manifest = json.loads((tmp_path / 'prepared' / 'momtsim_prepared.manifest.json').read_text(encoding='utf-8'))
    assert manifest['fuente']['doi'] == prep.DOI and manifest['fuente']['licencia'] == 'CC BY 4.0'
    assert 'SIMULADOS' in manifest['advertencia'] and manifest['transformacion']['label'].startswith('isFraud=1')
    counts = manifest['conteos']
    assert counts['saldo_negativo'] == 1 and counts['importe_invalido'] == 2 and counts['tipo_excluido'] > 0
    assert counts['leidas'] == 60 * 12 + 2 and manifest['resultado']['filas'] == len(frame)
    assert manifest['resultado']['sha256'] and manifest['parametros']['origen_temporal_arbitrario'].startswith('2026-01-01')


def test_the_prepared_file_passes_the_systems_own_validation_without_dropping_rows(tmp_path):
    raw = tmp_path / 'raw.csv'
    _fake_raw(raw)
    destination = prep.prepare(raw, tmp_path, max_rows=100_000, seed=7)
    frame, report = csv_training.validate_csv_text(destination.read_text(encoding='utf-8'))
    assert report['invalidas'] == 0 and report['filas_validas'] == report['filas_leidas'] - report['duplicados_eliminados']
    assert set(report['etiquetas']) == {'FRAUDE_CONFIRMADO', 'LEGITIMA_VERIFICADA'}


def test_sampling_is_deterministic_by_whole_account_and_respects_the_row_cap(tmp_path):
    raw = tmp_path / 'raw.csv'
    _fake_raw(raw, accounts=200)
    first = prep.prepare(raw, tmp_path / 'a', max_rows=400, seed=3)
    second = prep.prepare(raw, tmp_path / 'b', max_rows=400, seed=3)
    other = prep.prepare(raw, tmp_path / 'c', max_rows=400, seed=4)
    assert first.read_bytes() == second.read_bytes() and first.read_bytes() != other.read_bytes()
    frame = pd.read_csv(first)
    assert 0 < len(frame) <= 400
    per_account = frame.groupby('account_id').size()
    assert per_account.nunique() <= 2                                                         # cuentas completas (todas las operaciones de cada una)
    manifest = json.loads((tmp_path / 'a' / 'prepared' / 'momtsim_prepared.manifest.json').read_text(encoding='utf-8'))
    assert manifest['muestreo']['tipo'].startswith('por cuenta') and manifest['muestreo']['cuentas_en_muestra'] < manifest['muestreo']['cuentas_en_origen']


def test_the_raw_file_must_have_the_expected_columns(tmp_path):
    bad = tmp_path / 'bad.csv'
    bad.write_text('a,b,c\n1,2,3\n')
    with pytest.raises(SystemExit, match='Columnas inesperadas'):
        list(prep.read_chunks(bad))


def test_the_download_url_is_only_accepted_from_the_expected_https_host(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return json.dumps([{'filename': prep.FILE_NAME, 'content_details': {'download_url': 'https://malo.example/file', 'size': 1, 'sha256_hash': 'x'}}]).encode()

    monkeypatch.setattr(prep.urllib.request, 'urlopen', lambda *a, **k: Response())
    monkeypatch.setattr(prep.json, 'load', lambda response: json.loads(response.read()))
    with pytest.raises(SystemExit, match='no es la esperada'):
        prep.remote_file_info()


# ───────── Procedencia y métricas por tipo en el entrenamiento ─────────

def _labelled_csv(world):
    with world['app'].app_context():
        return synthetic.write_csv(synthetic.generate(n_accounts=140, days=60, labelled=True, anomaly_rate=0.06, seed=11), world['tmp'] / 'etiquetado.csv')


def _manifest(world, **extra):
    path = world['tmp'] / 'manifiesto.json'
    path.write_text(json.dumps({'fuente': {'doi': '10.0/prueba', 'licencia': 'CC BY 4.0'}, 'advertencia': 'Datos y etiquetas SIMULADOS de prueba.', **extra}), encoding='utf-8')
    return path


def test_supervised_metrics_are_stratified_by_operation_type_and_show_the_prevalence_baseline(world):
    csv_path = _labelled_csv(world)
    with world['app'].app_context():
        row = csv_training.train_from_csv(csv_path)
        supervised = row.metrics['supervisadas']
        assert supervised['disponibles'] is True and 0 < supervised['prevalencia_fraude'] < 0.5 and supervised['fraudes'] >= 20
        assert 'prevalencia' in supervised['nota'] and 'por_tipo' in supervised and 'lectura_por_tipo' in supervised
        assert set(supervised['por_tipo']) == {'TRANSFERENCIA'}                                   # solo hay fraude en transferencias
        by_kind = supervised['por_tipo']['TRANSFERENCIA']
        assert {'auc_roc', 'precision_media', 'prevalencia_fraude', 'fraudes', 'legitimas'} <= set(by_kind) and by_kind['fraudes'] >= 20


def test_a_preparation_manifest_travels_with_the_candidate_so_the_provenance_is_never_lost(world):
    csv_path, manifest = _labelled_csv(world), _manifest(world)
    with world['app'].app_context():
        row = csv_training.train_from_csv(csv_path, manifest_path=manifest)
        assert row.validation['manifiesto_de_preparacion']['advertencia'] == 'Datos y etiquetas SIMULADOS de prueba.'
        assert row.notes == 'Datos y etiquetas SIMULADOS de prueba.' and row.status == anomaly.STATUS_CANDIDATE
        explicit = csv_training.train_from_csv(csv_path, manifest_path=manifest, notes='Nota explícita')
        assert explicit.notes == 'Nota explícita'


@pytest.mark.parametrize('content,name,message', [
    ('{no es json', 'm.json', 'JSON válido'), ('[1, 2]', 'm.json', 'objeto JSON'), ('{}', 'm.txt', r'\.json'), ('{"x": "' + 'a' * 70000 + '"}', 'm.json', '64 KB'),
], ids=['json_roto', 'lista', 'extension', 'demasiado_grande'])
def test_invalid_manifests_are_rejected_before_training(world, content, name, message):
    csv_path = _labelled_csv(world)
    bad = world['tmp'] / name
    bad.write_text(content, encoding='utf-8')
    with world['app'].app_context():
        with pytest.raises(csv_training.CsvValidationError, match=message):
            csv_training.train_from_csv(csv_path, manifest_path=bad)
        with pytest.raises(csv_training.CsvValidationError, match='manifiesto'):
            csv_training.train_from_csv(csv_path, manifest_path=world['tmp'] / 'no-existe.json')
        assert FraudModelVersion.query.count() == 0


def test_the_cli_accepts_a_manifest_and_a_note_and_still_only_registers_a_candidate(world):
    csv_path, manifest = _labelled_csv(world), _manifest(world)
    result = world['runner'].invoke(args=['fraud-train', '--csv', str(csv_path), '--manifest', str(manifest), '--notes', 'prueba con datos sintéticos'])
    assert result.exit_code == 0 and 'Candidato iforest-' in result.output
    with world['app'].app_context():
        row = FraudModelVersion.query.one()
        assert row.status == anomaly.STATUS_CANDIDATE and row.notes == 'prueba con datos sintéticos'
        assert row.validation['manifiesto_de_preparacion']['fuente']['licencia'] == 'CC BY 4.0'
        assert anomaly.status()['available'] is False                                          # entrenar no activa nada
    forbidden = Path(world['app'].root_path).parent / 'data' / 'm.json'
    result = world['runner'].invoke(args=['fraud-train', '--csv', str(csv_path), '--manifest', str(forbidden)])
    assert result.exit_code == 1 and 'data/' in result.output
