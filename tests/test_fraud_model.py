"""Flujo híbrido de Machine Learning: CSV sintético → validación → entrenamiento → candidato → activación explícita → inferencia.

Solo datos sintéticos generados aquí (``app.fraud.synthetic``) y archivos temporales de ``tmp_path``; nada lee ni escribe ``data/``.
La evaluación sobre datos sintéticos NO demuestra eficacia contra fraude real: solo comprueba que el código funciona.
"""
import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.fraud import anomaly, constants as c, csv_training, features as feature_extraction, synthetic
from app.models import AuditLog, FraudModelVersion, RiskEvaluation, Transaction, User, db, now_utc
from app.services.banking_service import process_operation
from fraud_helpers import add_debits, make_beneficiary, make_client

HEADER = 'timestamp,account_id,amount,balance_before,kind,label\n'
GOOD = '2026-01-05T10:00:00,A1,50.00,1000,PAGO,SIN_VERIFICAR\n'


@pytest.fixture
def world(tmp_path):
    app = create_app({'SECRET_KEY': 'k' * 40, 'FRAUD_MODEL_DIR': str(tmp_path / 'models')})
    return {'app': app, 'tmp': tmp_path, 'runner': app.test_cli_runner()}


def _csv(world, name='datos.csv', **kwargs):
    with world['app'].app_context():
        return synthetic.write_csv(synthetic.generate(**kwargs), world['tmp'] / name)


def _candidate(world, name='datos.csv', **kwargs):
    path = _csv(world, name, **kwargs)
    with world['app'].app_context():
        row = csv_training.train_from_csv(path)
        return row.version, path


# ───────── Validación del CSV ─────────

def _validate(text):
    return csv_training.validate_csv_text(text, now=None)


def test_a_valid_csv_is_loaded_with_a_complete_report():
    frame, report = _validate(HEADER + GOOD + '2026-01-05T11:00:00,A1,70,900,transferencia,\n')
    assert list(frame.columns) == ['timestamp', 'account_id', 'amount', 'balance_before', 'kind', 'label']
    assert report['filas_leidas'] == 2 and report['filas_validas'] == 2 and report['invalidas'] == 0 and report['proporcion_invalidas'] == 0
    assert list(frame['kind']) == ['PAGO', 'TRANSFERENCIA'] and list(frame['label']) == ['SIN_VERIFICAR', 'SIN_VERIFICAR']   # vacío = sin verificar
    assert report['rango_fechas'] == ['2026-01-05T10:00:00', '2026-01-05T11:00:00']


def test_headers_are_normalized_and_extra_columns_are_ignored_and_reported():
    frame, report = _validate(' Timestamp , ACCOUNT_ID,Amount,Balance_Before,KIND,nota_libre\n2026-01-05T10:00:00,A1,50,1000,PAGO,hola\n')
    assert len(frame) == 1 and report['columnas_ignoradas'] == ['nota_libre']


def test_a_missing_label_column_means_every_row_is_unverified_and_no_label_is_ever_invented():
    frame, report = _validate('timestamp,account_id,amount,balance_before,kind\n2026-01-05T10:00:00,A1,50,1000,PAGO\n')
    assert set(frame['label']) == {'SIN_VERIFICAR'} and report['etiquetas'] == {'SIN_VERIFICAR': 1}


@pytest.mark.parametrize('missing', ['timestamp', 'account_id', 'amount', 'balance_before', 'kind'])
def test_each_required_column_is_mandatory(missing):
    columns = [name for name in ('timestamp', 'account_id', 'amount', 'balance_before', 'kind') if name != missing]
    with pytest.raises(csv_training.CsvValidationError, match=missing):
        _validate(','.join(columns) + '\n' + ','.join('x' for _ in columns) + '\n')


@pytest.mark.parametrize('row,reason', [
    ('no-es-fecha,A1,50,1000,PAGO,SIN_VERIFICAR', 'fecha_invalida'),
    ('2099-01-01T00:00:00,A1,50,1000,PAGO,SIN_VERIFICAR', 'fecha_futura'),
    ('2026-01-05T10:00:00,A1,abc,1000,PAGO,SIN_VERIFICAR', 'monto_invalido'),
    ('2026-01-05T10:00:00,A1,-5,1000,PAGO,SIN_VERIFICAR', 'monto_invalido'),
    ('2026-01-05T10:00:00,A1,0,1000,PAGO,SIN_VERIFICAR', 'monto_invalido'),
    ('2026-01-05T10:00:00,A1,inf,1000,PAGO,SIN_VERIFICAR', 'monto_invalido'),
    ('2026-01-05T10:00:00,A1,nan,1000,PAGO,SIN_VERIFICAR', 'monto_invalido'),
    ('2026-01-05T10:00:00,A1,99999999999,1000,PAGO,SIN_VERIFICAR', 'monto_invalido'),
    ('2026-01-05T10:00:00,A1,50,-1,PAGO,SIN_VERIFICAR', 'saldo_invalido'),
    ('2026-01-05T10:00:00,A1,50,mucho,PAGO,SIN_VERIFICAR', 'saldo_invalido'),
    ('2026-01-05T10:00:00,A1,50,1000,DEPOSITO,SIN_VERIFICAR', 'tipo_desconocido'),
    ('2026-01-05T10:00:00,A1,50,1000,PAGO,FRAUDE_PROBABLE', 'etiqueta_invalida'),
    ('2026-01-05T10:00:00,A1,50,1000,PAGO,fraude', 'etiqueta_invalida'),
    ('2026-01-05T10:00:00,,50,1000,PAGO,SIN_VERIFICAR', 'valores_faltantes'),
    (',A1,50,1000,PAGO,SIN_VERIFICAR', 'valores_faltantes'),
    ('2026-01-05T10:00:00,' + 'X' * 65 + ',50,1000,PAGO,SIN_VERIFICAR', 'cuenta_invalida'),
])
def test_each_kind_of_invalid_row_is_dropped_counted_and_never_silently_repaired(row, reason):
    frame, report = _validate(HEADER + GOOD * 1 + '2026-01-05T09:00:00,A2,10,500,PAGO,SIN_VERIFICAR\n' * 3 + row + '\n')
    assert report['invalidas'] == 1 and report['motivos_invalidas'] == {reason: 1} and report['filas_leidas'] == 5
    assert len(frame) == 2          # el duplicado exacto de las filas válidas también se elimina


def test_duplicates_by_transaction_id_and_by_content_are_removed_and_counted():
    text = ('transaction_id,timestamp,account_id,amount,balance_before,kind\n'
            'T1,2026-01-05T10:00:00,A1,50,1000,PAGO\nT1,2026-01-05T10:05:00,A1,60,990,PAGO\n'
            'T2,2026-01-05T11:00:00,A1,70,900,PAGO\nT3,2026-01-05T11:00:00,A1,70,900,PAGO\n')
    frame, report = _validate(text)
    assert report['duplicados_eliminados'] == 2 and len(frame) == 2


def test_a_file_that_is_mostly_invalid_is_refused_as_unreliable():
    text = HEADER + GOOD + 'mala,A1,x,y,Z,W\n' * 5
    with pytest.raises(csv_training.CsvValidationError, match='no es fiable'):
        _validate(text)


@pytest.mark.parametrize('text,match', [
    ('', 'mal formado|filas'), (HEADER, 'no tiene filas'), ('timestamp,timestamp,amount,balance_before,kind\n1,2,3,4,5\n', 'mismo nombre'),
    (HEADER + '"2026-01-05T10:00:00,A1,50,1000,PAGO,SIN_VERIFICAR\n', 'mal formado|inválido|filas'),
])
def test_unusable_files_are_rejected_with_a_clear_message(text, match):
    with pytest.raises(csv_training.CsvValidationError, match=match):
        _validate(text)


def test_file_level_limits_encoding_extension_and_the_forbidden_data_folder(world, monkeypatch):
    with world['app'].app_context():
        good = world['tmp'] / 'ok.csv'
        good.write_text(HEADER + GOOD, encoding='utf-8-sig')                       # el BOM de Excel se acepta
        assert csv_training.load_csv(good)[1]['filas_validas'] == 1
        latin = world['tmp'] / 'latin.csv'
        latin.write_bytes((HEADER + GOOD.replace('A1', 'Ñ1')).encode('latin-1'))
        with pytest.raises(csv_training.CsvValidationError, match='UTF-8'):
            csv_training.load_csv(latin)
        (world['tmp'] / 'dato.txt').write_text(HEADER + GOOD)
        for bad in (world['tmp'] / 'dato.txt', world['tmp'] / 'no-existe.csv', world['tmp']):
            with pytest.raises(csv_training.CsvValidationError, match='archivo .csv'):
                csv_training.load_csv(bad)
        monkeypatch.setattr(csv_training, 'MAX_FILE_BYTES', 10)
        with pytest.raises(csv_training.CsvValidationError, match='máximo'):
            csv_training.load_csv(good)
        monkeypatch.setattr(csv_training, 'MAX_FILE_BYTES', 10_000_000)
        monkeypatch.setattr(csv_training, 'MAX_ROWS', 1)
        with pytest.raises(csv_training.CsvValidationError, match='filas'):
            _validate(HEADER + GOOD * 3)
        forbidden = Path(world['app'].root_path).parent / 'data' / 'secreto.csv'          # solo se evalúa la ruta; no se accede a nada
        with pytest.raises(csv_training.CsvValidationError, match='data/'):
            csv_training.load_csv(forbidden)
        with pytest.raises(csv_training.CsvValidationError, match='data/'):
            synthetic.write_csv(synthetic.generate(n_accounts=2, days=2), forbidden)
        assert not forbidden.exists()


# ───────── Datos sintéticos ─────────

def test_synthetic_data_is_deterministic_labelled_only_on_request_and_free_of_personal_data(world):
    a, b = synthetic.generate(seed=3), synthetic.generate(seed=3)
    assert a.equals(b) and not a.equals(synthetic.generate(seed=4))
    assert set(synthetic.generate(labelled=False)['label']) == {'SIN_VERIFICAR'}
    assert set(synthetic.generate(labelled=True)['label']) == {'LEGITIMA_VERIFICADA', 'FRAUDE_CONFIRMADO'}
    assert list(a.columns) == synthetic.COLUMNS and a['transaction_id'].is_unique
    path = _csv(world)
    with world['app'].app_context():
        assert csv_training.load_csv(path)[1]['invalidas'] == 0                      # el CSV generado supera la validación propia


# ───────── Separación temporal y características sin fugas ─────────

def test_window_features_only_look_backwards_so_future_rows_cannot_leak(world):
    import pandas as pd
    base = pd.Timestamp('2026-01-05 10:00:00')
    rows = [(base, 'A', 10), (base + pd.Timedelta(minutes=5), 'A', 10), (base + pd.Timedelta(minutes=8), 'A', 10),
            (base + pd.Timedelta(minutes=45), 'A', 10), (base + pd.Timedelta(minutes=3), 'B', 10)]
    frame = pd.DataFrame(rows, columns=['timestamp', 'account_id', 'amount']).assign(balance_before=1000.0)
    features = anomaly.build_feature_frame(frame)
    assert list(features['salidas_10m']) == [0, 1, 2, 0, 0] and list(features['salidas_1h']) == [0, 1, 2, 3, 0]
    extended = pd.concat([frame, pd.DataFrame([(base + pd.Timedelta(minutes=50), 'A', 99999)],
                                              columns=['timestamp', 'account_id', 'amount']).assign(balance_before=1.0)], ignore_index=True)
    again = anomaly.build_feature_frame(extended).iloc[:5]
    assert features.equals(again)                                                   # añadir una fila futura no cambia las anteriores


def test_the_split_is_temporal_the_evaluation_is_strictly_later_and_confirmed_fraud_is_not_used_to_train(world, monkeypatch):
    seen = {}
    real_fit = anomaly.fit_model

    def spy_fit(features, **kwargs):
        seen['train_index'] = list(features.index)
        return real_fit(features, **kwargs)

    monkeypatch.setattr(anomaly, 'fit_model', spy_fit)
    frame = synthetic.generate(n_accounts=60, days=40, labelled=True, anomaly_rate=0.05)
    with world['app'].app_context():
        row = anomaly.train_candidate(frame, source='CSV', validation={'proporcion_invalidas': 0})
        cutoff = pd_timestamp(row.metrics['corte_temporal'])
        train = frame.loc[seen['train_index']]
        assert train['timestamp'].max() < cutoff <= frame[frame['timestamp'] >= cutoff]['timestamp'].min()
        assert 'FRAUDE_CONFIRMADO' not in set(train['label'])                        # el detector aprende lo habitual
        assert row.n_eval == int((frame['timestamp'] >= cutoff).sum()) and row.n_train == len(train)


def pd_timestamp(text):
    import pandas as pd
    return pd.Timestamp(text)


# ───────── Entrenamiento: siempre candidato, con trazabilidad ─────────

def test_training_from_csv_registers_a_candidate_with_version_date_features_metrics_and_source_hash(world):
    version, path = _candidate(world)
    with world['app'].app_context():
        row = FraudModelVersion.query.filter_by(version=version).one()
        assert (row.status, row.source, row.algorithm) == (anomaly.STATUS_CANDIDATE, 'CSV', 'IsolationForest')
        assert re.fullmatch(r'iforest-\d{20}', row.version) and row.trained_at <= now_utc()
        assert row.features == list(anomaly.FEATURE_NAMES) and row.n_train >= 200 and row.n_eval >= anomaly.MIN_EVAL_ROWS
        assert row.data_sha256 == hashlib.sha256(Path(path).read_bytes()).hexdigest()
        assert row.validation['filas_leidas'] == row.validation['filas_validas'] and row.sklearn_version and row.python_version
        assert {'entrenamiento', 'evaluacion', 'deriva_maxima_estandarizada', 'supervisadas', 'aviso'} <= set(row.metrics)
        assert row.metrics['supervisadas']['disponibles'] is False                  # sin etiquetas confirmadas no hay métricas supervisadas
        assert 'no supervisado' in row.metrics['aviso'].lower() and row.activated_at is None
        directory = Path(world['app'].config['FRAUD_MODEL_DIR'])
        assert (directory / f'{version}.joblib').is_file() and (directory / f'{version}.json').is_file()
        assert not (directory / 'current.txt').exists()                            # entrenar NO activa nada
        assert anomaly.status()['available'] is False and anomaly.score(_features(world)) == (None, None)


def _features(world):
    from app.fraud.features import OperationFeatures
    return OperationFeatures(kind='PAGO', amount=Decimal('50'), balance=Decimal('1000'), ops_10m=0, ops_1h=0)


def test_supervised_metrics_are_computed_only_when_there_are_enough_confirmed_labels(world):
    frame = synthetic.generate(n_accounts=140, days=60, labelled=True, anomaly_rate=0.06, seed=11)
    with world['app'].app_context():
        row = anomaly.train_candidate(frame, source='CSV', validation={'proporcion_invalidas': 0})
        labels = row.metrics['etiquetas_en_evaluacion']
        assert labels['fraude_confirmado'] >= 20 and labels['legitima_verificada'] >= 100
        supervised = row.metrics['supervisadas']
        assert supervised['disponibles'] is True and 0.0 <= supervised['auc_roc'] <= 1.0 and 0.0 <= supervised['precision_media'] <= 1.0
        assert supervised['auc_roc'] > 0.7                       # verificación de cordura SOBRE DATOS SINTÉTICOS, no eficacia real
        assert 'sin_verificar' in labels and 'SIN_VERIFICAR' in supervised['nota']


def test_too_little_data_is_refused_and_no_candidate_is_registered(world):
    path = world['tmp'] / 'poco.csv'
    path.write_text(HEADER + GOOD * 1 + ''.join(f'2026-01-05T10:{m:02d}:00,A1,{m + 1},1000,PAGO,SIN_VERIFICAR\n' for m in range(40)), encoding='utf-8')
    with world['app'].app_context():
        with pytest.raises(anomaly.InsufficientData, match='al menos'):
            csv_training.train_from_csv(path)
        assert FraudModelVersion.query.count() == 0


def test_training_from_the_database_registers_a_candidate_with_the_database_as_source(world):
    with world['app'].app_context():
        with pytest.raises(anomaly.InsufficientData):
            anomaly.train_from_database()
        owner, account = make_client('mdbuser', '56000001', '560000000001')
        now = now_utc()
        for index in range(420):
            db.session.add(Transaction(account_id=account, operation_type='PAGO', description='Pago', amount=-Decimal(f'{10 + index % 40}.00'),
                                       balance_after=Decimal('900.00'), created_at=now - timedelta(minutes=index * 7)))
        db.session.commit()
        row = anomaly.train_from_database()
        assert row.source == 'BASE_DE_DATOS' and row.status == anomaly.STATUS_CANDIDATE and row.n_train + row.n_eval >= 420   # + las operaciones de muestra del demo
        assert row.validation['nota'].startswith('Datos de la tabla transactions')


# ───────── Activación explícita y validada ─────────

def test_activation_makes_the_candidate_active_archives_the_previous_one_and_is_audited(world):
    first, _ = _candidate(world, 'a.csv', seed=1)
    second, _ = _candidate(world, 'b.csv', seed=2)
    with world['app'].app_context():
        anomaly.activate_version(first)
        anomaly.activate_version(second)
        rows = {row.version: row for row in FraudModelVersion.query.all()}
        assert rows[first].status == anomaly.STATUS_ARCHIVED and rows[second].status == anomaly.STATUS_ACTIVE and rows[second].activated_at
        assert (Path(world['app'].config['FRAUD_MODEL_DIR']) / 'current.txt').read_text() == second
        assert AuditLog.query.filter_by(action='FRAUD_MODEL_ACTIVATED', module='FRAUDE').count() == 2
        status = anomaly.status()
        assert status['available'] and status['active_version'] == second
        version, value = anomaly.score(_features(world))
        assert version == second and isinstance(value, float)


def test_a_new_arbitrary_csv_never_replaces_the_active_model_by_itself(world):
    active, _ = _candidate(world, 'a.csv', seed=1)
    with world['app'].app_context():
        anomaly.activate_version(active)
    other, _ = _candidate(world, 'b.csv', seed=5, anomaly_rate=0.2)
    with world['app'].app_context():
        assert db.session.query(FraudModelVersion).filter_by(version=other).one().status == anomaly.STATUS_CANDIDATE
        assert anomaly.status()['active_version'] == active
        assert FraudModelVersion.query.filter_by(status=anomaly.STATUS_ACTIVE).count() == 1


def _break(world, version, **changes):
    with world['app'].app_context():
        row = FraudModelVersion.query.filter_by(version=version).one()
        for key, value in changes.items():
            setattr(row, key, value)
        db.session.commit()


@pytest.mark.parametrize('changes,message', [
    ({'n_train': 10}, 'Entrenamiento con'), ({'n_eval': 3}, 'Evaluación con'), ({'sklearn_version': '0.1'}, 'Entorno distinto'),
    ({'python_version': '2.7'}, 'Entorno distinto'), ({'features': ['otra']}, 'características'),
    ({'validation': {'proporcion_invalidas': 0.5}}, 'inválidos'),
    ({'metrics': {'evaluacion': {'tasa_de_alertas': 0.9}}}, 'tasa de alertas'), ({'metrics': {}}, 'tasa de alertas'),
    ({'status': anomaly.STATUS_REJECTED}, 'estado actual'),
])
def test_activation_gates_refuse_candidates_that_do_not_pass_the_sanity_checks(world, changes, message):
    version, _ = _candidate(world)
    _break(world, version, **changes)
    with world['app'].app_context():
        with pytest.raises(anomaly.ActivationRefused, match=message):
            anomaly.activate_version(version)
        assert FraudModelVersion.query.filter_by(status=anomaly.STATUS_ACTIVE).count() == 0
        assert not (Path(world['app'].config['FRAUD_MODEL_DIR']) / 'current.txt').exists()
        assert AuditLog.query.filter_by(action='FRAUD_MODEL_ACTIVATED').count() == 0


def test_a_tampered_or_missing_artifact_cannot_be_activated(world):
    version, _ = _candidate(world)
    artifact = Path(world['app'].config['FRAUD_MODEL_DIR']) / f'{version}.joblib'
    artifact.write_bytes(artifact.read_bytes() + b'x')
    with world['app'].app_context():
        with pytest.raises(anomaly.ActivationRefused, match='SHA-256'):
            anomaly.activate_version(version)
    artifact.unlink()
    with world['app'].app_context():
        with pytest.raises(anomaly.ActivationRefused, match='Falta el artefacto'):
            anomaly.activate_version(version)
        with pytest.raises(ValueError, match='no existe'):
            anomaly.activate_version('iforest-inexistente')


def test_a_model_corrupted_after_activation_fails_safe_and_never_breaks_inference(world):
    version, _ = _candidate(world)
    with world['app'].app_context():
        anomaly.activate_version(version)
        assert anomaly.score(_features(world))[0] == version
    artifact = Path(world['app'].config['FRAUD_MODEL_DIR']) / f'{version}.joblib'
    artifact.write_bytes(b'corrupto')
    with world['app'].app_context():
        anomaly.reset_cache()
        assert anomaly.score(_features(world)) == (None, None) and anomaly.status()['reason'] == 'artefacto_alterado'


def test_a_model_trained_for_another_environment_is_ignored_at_inference(world):
    version, _ = _candidate(world)
    with world['app'].app_context():
        anomaly.activate_version(version)
    metadata_file = Path(world['app'].config['FRAUD_MODEL_DIR']) / f'{version}.json'
    metadata = json.loads(metadata_file.read_text())
    metadata['sklearn'] = '0.20'
    metadata_file.write_text(json.dumps(metadata))
    with world['app'].app_context():
        anomaly.reset_cache()
        assert anomaly.score(_features(world)) == (None, None) and anomaly.status()['reason'] == 'version_de_entorno_distinta'


def test_rollback_to_a_previous_version_rejecting_candidates_and_retiring_the_model(world):
    first, _ = _candidate(world, 'a.csv', seed=1)
    second, _ = _candidate(world, 'b.csv', seed=2)
    third, _ = _candidate(world, 'c.csv', seed=3)
    with world['app'].app_context():
        anomaly.activate_version(first)
        anomaly.activate_version(second)
        anomaly.activate_version(first)                                          # reversión: una versión archivada vuelve a ser la activa
        assert anomaly.status()['active_version'] == first
        assert FraudModelVersion.query.filter_by(version=second).one().status == anomaly.STATUS_ARCHIVED
        anomaly.reject_version(third, 'Deriva alta en la evaluación')
        assert FraudModelVersion.query.filter_by(version=third).one().status == anomaly.STATUS_REJECTED
        with pytest.raises(ValueError):
            anomaly.reject_version(first, 'No se puede rechazar el activo')
        with pytest.raises(anomaly.ActivationRefused):
            anomaly.activate_version(third)
        anomaly.deactivate()
        assert anomaly.status() == {'available': False, 'active_version': None, 'reason': 'sin_modelo_activo', 'versions': anomaly.status()['versions']}
        assert FraudModelVersion.query.filter_by(status=anomaly.STATUS_ACTIVE).count() == 0
        assert AuditLog.query.filter_by(action='FRAUD_MODEL_DEACTIVATED').count() == 1


# ───────── El modelo es observación: no decide nada y no entrena durante operaciones ─────────

@pytest.fixture
def banking(world):
    with world['app'].app_context():
        sender, account = make_client('mbank', '57000001', '570000000001', balance='1000.00')
        make_client('mbankto', '57000002', '570000000002', balance='0.00')
        beneficiary = make_beneficiary(sender, '570000000002')
    return {**world, 'sender': sender, 'account': account, 'beneficiary': beneficiary}


def _pay(banking, amount, key):
    user = db.session.get(User, banking['sender'])
    return process_operation(user, 'TRANSFERENCIA', Decimal(str(amount)), 'Transferencia a Destino', beneficiary_id=banking['beneficiary'],
                             idempotency_key=f'clave-modelo-{key:04d}-000000000000')


def test_the_active_model_score_is_stored_for_observation_but_does_not_change_the_level(banking):
    version, _ = _candidate(banking)
    with banking['app'].app_context():
        anomaly.activate_version(version)
        _pay(banking, 20, 1)
        evaluation = RiskEvaluation.query.one()
        assert evaluation.model_version == version and isinstance(evaluation.model_score, float)
        assert evaluation.level == c.LEVEL_NORMAL and evaluation.status == 'OK'
        assert db.session.get(User, banking['sender']) is not None


def test_a_missing_model_means_rules_only_without_errors(banking):
    with banking['app'].app_context():
        _pay(banking, 20, 1)
        evaluation = RiskEvaluation.query.one()
        assert evaluation.model_version is None and evaluation.model_score is None and evaluation.level == c.LEVEL_NORMAL


def test_models_are_never_trained_or_registered_during_a_bank_operation(banking, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No se debe entrenar durante una operación bancaria.')

    monkeypatch.setattr(anomaly, 'fit_model', forbidden)
    monkeypatch.setattr(anomaly, 'train_candidate', forbidden)
    monkeypatch.setattr(anomaly, 'train_from_database', forbidden)
    with banking['app'].app_context():
        for key in range(1, 6):
            _pay(banking, 5, key)
        assert FraudModelVersion.query.count() == 0 and RiskEvaluation.query.count() == 5


# ───────── Comandos de línea de órdenes ─────────

def test_cli_end_to_end_synthetic_csv_train_activate_status_report_and_deactivate(world):
    runner, target = world['runner'], world['tmp'] / 'cli.csv'
    result = runner.invoke(args=['fraud-synthetic-csv', str(target)])
    assert result.exit_code == 0 and target.is_file() and 'sintético' in result.output
    result = runner.invoke(args=['fraud-train', '--csv', str(target)])
    assert result.exit_code == 0 and 'Candidato iforest-' in result.output and 'fraud-model-activate' in result.output
    version = re.search(r'iforest-\d+', result.output).group(0)
    assert runner.invoke(args=['fraud-model-status']).exit_code == 0 and '"available": false' in runner.invoke(args=['fraud-model-status']).output
    assert runner.invoke(args=['fraud-model-activate', version]).exit_code == 0
    assert f'"active_version": "{version}"' in runner.invoke(args=['fraud-model-status']).output
    report = runner.invoke(args=['fraud-report', '--days', '3'])
    assert report.exit_code == 0 and '"total"' in report.output
    maintenance = runner.invoke(args=['fraud-maintenance', '--dry-run'])
    assert maintenance.exit_code == 0 and 'marcadas como vencidas' in maintenance.output
    assert runner.invoke(args=['fraud-model-deactivate']).exit_code == 0
    assert '"available": false' in runner.invoke(args=['fraud-model-status']).output


def test_cli_refuses_bad_input_with_a_clear_message_and_a_nonzero_exit(world):
    runner = world['runner']
    for args in (['fraud-train'], ['fraud-train', '--from-db', '--csv', str(world['tmp'] / 'x.csv')]):
        result = runner.invoke(args=args)
        assert result.exit_code == 1 and 'exactamente una fuente' in result.output
    bad = world['tmp'] / 'malo.csv'
    bad.write_text('a,b\n1,2\n')
    result = runner.invoke(args=['fraud-train', '--csv', str(bad)])
    assert result.exit_code == 1 and 'Faltan columnas obligatorias' in result.output
    assert runner.invoke(args=['fraud-model-activate', 'iforest-no-existe']).exit_code == 1
    forbidden = Path(world['app'].root_path).parent / 'data' / 'x.csv'
    result = runner.invoke(args=['fraud-synthetic-csv', str(forbidden)])
    assert result.exit_code == 1 and 'data/' in result.output and not forbidden.exists()


# ───────── Retención de datos ─────────

def test_old_evaluations_are_purged_but_those_linked_to_incidents_or_holds_are_kept(world):
    from app.fraud import metrics
    from app.models import FraudIncident, HeldOperation
    with world['app'].app_context():
        user, account = make_client('purgeuser', '58000001', '580000000001')
        old = now_utc() - timedelta(days=400)
        evaluations = []
        for index in range(4):
            item = RiskEvaluation(user_id=user, context='PAGO', event='OPERACION', level=c.LEVEL_NORMAL, score=0, signals=[], rules_version='r', mode='shadow',
                                  recommended_action='PERMITIR', applied_action='NINGUNA_OBSERVACION', created_at=old if index < 3 else now_utc())
            db.session.add(item)
            evaluations.append(item)
        db.session.flush()
        db.session.add(FraudIncident(user_id=user, evaluation_id=evaluations[0].id, category=c.CATEGORY_TRANSACTIONAL, status=c.INCIDENT_OPEN, title='x'))
        db.session.add(HeldOperation(user_id=user, account_id=account, evaluation_id=evaluations[1].id, kind='PAGO', amount=Decimal('1'), fingerprint='f' * 64,
                                     reason='x', expires_at=now_utc() + timedelta(hours=1)))
        db.session.commit()
        assert metrics.purge_old_evaluations(180) == 1                                  # solo la antigua sin vínculo
        assert RiskEvaluation.query.count() == 3
