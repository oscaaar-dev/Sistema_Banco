"""Entrenamiento y evaluación de candidatos desde el CSV combinado: esquema estricto, división cronológica, ausencia de fugas,
métricas, reproducibilidad y carga de los candidatos.

Solo datos sintéticos generados aquí, base SQLite en memoria y carpetas temporales; nada lee ni escribe ``data/``. Los resultados sobre
datos sintéticos NO demuestran eficacia contra fraude real: estas pruebas comprueban que el código es correcto y honesto.
"""
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import IsolationForest

from app import create_app
from app.fraud import anomaly, csv_training, event_training as et, synthetic_events as se
from app.models import FraudModelVersion, db


@pytest.fixture(scope='module')
def dataset(tmp_path_factory):
    frame = se.generate_combined(2026)
    folder = tmp_path_factory.mktemp('eventos')
    path = folder / 'combinado.csv'
    frame.to_csv(path, index=False, lineterminator='\n')
    return {'path': path, 'frame': frame, 'dir': folder}


def _app(tmp_path):
    return create_app({'SECRET_KEY': 'k' * 40, 'FRAUD_MODEL_DIR': str(tmp_path / 'modelos')})


@pytest.fixture(scope='module')
def trained(dataset, tmp_path_factory):
    """Una ejecución completa del pipeline (compartida): app aislada, modelos en carpeta temporal."""
    root = tmp_path_factory.mktemp('entrenamiento')
    app = _app(root)
    with app.app_context():
        report = et.run_pipeline(dataset['path'], report_dir=root / 'informe')
        rows = {row.version: (row.status, row.source, list(row.features or [])) for row in FraudModelVersion.query.all()}
        status = anomaly.status()
    return {'app': app, 'report': report, 'root': root, 'rows': rows, 'status': status}


def _write(tmp_path, frame, name='x.csv'):
    path = tmp_path / name
    frame.to_csv(path, index=False, lineterminator='\n')
    return path


def _load_error(app, path, match):
    with app.app_context(), pytest.raises(et.EventDatasetError, match=match):
        et.load_combined(path)


# ───────── Esquema y archivo estrictos ─────────

@pytest.fixture
def small(dataset):
    return dataset['frame'].copy()


def test_the_loader_accepts_the_valid_file_and_types_it(dataset, tmp_path):
    with _app(tmp_path).app_context():
        frame, report, sha = et.load_combined(dataset['path'])
    assert len(frame) == 12000 and report['ok'] and len(sha) == 64 and pd.api.types.is_datetime64_any_dtype(frame['timestamp'])
    assert pd.api.types.is_numeric_dtype(frame['importe']) and 'n_accesos_previos' in frame.columns
    assert frame.loc[frame.event_type == 'ACCESO', 'n_accesos_previos'].min() == 0


@pytest.mark.parametrize('mutate,match', [
    (lambda f: f.drop(columns=['importe']), 'Esquema distinto'),
    (lambda f: f.assign(extra='x'), 'Esquema distinto'),
    (lambda f: f[list(reversed(f.columns))], 'Esquema distinto'),
    (lambda f: f.assign(importe=f['importe'].astype(str).where(f.index != 0, 'abc')), 'validación'),
    (lambda f: f.assign(importe=f['importe'].astype(str).where(f.index != 0, '9999999999')), 'validación'),
    (lambda f: f.assign(timestamp=f['timestamp'].where(f.index != 7, '31/02/2026 99:99')), 'validación'),
    (lambda f: f.assign(is_fraud=f['is_fraud'].astype(str).where(f.index != 7, '2')), 'validación'),
    (lambda f: f.assign(event_type=f['event_type'].where(f.index != 7, 'RETIRADA')), 'validación'),
    (lambda f: f.assign(account_id=f['account_id'].where(f.index != 7, 'cliente@example.com')), 'validación'),
])
def test_the_loader_rejects_a_schema_or_content_that_does_not_match(small, tmp_path, mutate, match):
    _load_error(_app(tmp_path), _write(tmp_path, mutate(small)), match)


def test_the_loader_rejects_bad_files_before_trusting_them(tmp_path, monkeypatch):
    app = _app(tmp_path)
    _load_error(app, tmp_path / 'no_existe.csv', 'archivo .csv existente')
    texto = tmp_path / 'datos.txt'
    texto.write_text('a,b\n1,2\n', encoding='utf-8')
    _load_error(app, texto, 'archivo .csv existente')
    latin = tmp_path / 'latin.csv'
    latin.write_bytes('event_id,é\n'.encode('latin-1') + bytes([0xe9, 0xff]))
    _load_error(app, latin, 'UTF-8')
    vacio = tmp_path / 'vacio.csv'
    vacio.write_text('', encoding='utf-8')
    _load_error(app, vacio, 'mal formado')
    solo_cabecera = tmp_path / 'cabecera.csv'
    solo_cabecera.write_text(','.join(se.COLUMNS) + '\n', encoding='utf-8')
    _load_error(app, solo_cabecera, 'transacciones y accesos')
    monkeypatch.setattr(et, 'MAX_ROWS', 10)
    grande = tmp_path / 'grande.csv'
    grande.write_text(','.join(se.COLUMNS) + '\n' + '\n'.join(['x,' * (len(se.COLUMNS) - 1) + 'x'] * 11) + '\n', encoding='utf-8')
    _load_error(app, grande, 'máximo de 10 filas')
    monkeypatch.setattr(et, 'MAX_ROWS', 200_000)
    monkeypatch.setattr(et, 'MAX_FILE_BYTES', 100)
    _load_error(app, grande, 'tamaño máximo')


def test_the_pipeline_never_reads_or_writes_the_data_folder(dataset, tmp_path):
    app = _app(tmp_path)
    protected = Path(app.root_path).parent / 'data'
    with app.app_context():
        with pytest.raises(csv_training.CsvValidationError, match='data/'):
            et.run_pipeline(protected / 'secreto.csv')
        with pytest.raises(csv_training.CsvValidationError, match='data/'):
            et.run_pipeline(dataset['path'], report_dir=protected / 'informes')
        assert FraudModelVersion.query.count() == 0                       # no se registró nada antes de rechazar
    assert not (protected / 'informes').exists()


def test_a_training_period_that_is_too_small_is_refused(dataset, tmp_path):
    app = _app(tmp_path)
    app.config['FRAUD_MODEL_MIN_SAMPLES'] = 10_000_000
    with app.app_context(), pytest.raises(et.EventDatasetError, match='se necesitan al menos'):
        et.run_pipeline(dataset['path'])
    with app.app_context():
        assert FraudModelVersion.query.count() == 0
        assert not (tmp_path / 'modelos').exists() or not list((tmp_path / 'modelos').glob('*.joblib'))


# ───────── Variables: solo observables, sin identificadores ni etiquetas ─────────

def test_predictors_are_observable_only_and_forbidden_columns_are_excluded():
    forbidden = {'event_id', 'account_id', 'timestamp', 'scenario', 'is_fraud', 'is_synthetic', 'puntuacion_riesgo_sintetica', 'nivel_riesgo_sintetico',
                 'dispositivo_nuevo', 'ubicacion_nueva', 'viaje_imposible', 'hora_del_dia', 'dia_semana', 'saldo_posterior'}
    assert not forbidden & set(et.TX_FEATURES) and not forbidden & set(et.ACCESS_FEATURES)
    assert forbidden <= set(et.EXCLUDED) and all(reason.strip() for reason in et.EXCLUDED.values())


def test_the_feature_matrices_ignore_labels_scenarios_scores_and_identifiers(dataset, tmp_path):
    with _app(tmp_path).app_context():
        frame, _, _ = et.load_combined(dataset['path'])
    tx = frame[(frame.event_type == 'TRANSACCION') & frame.tipo_operacion.isin(se.OUT_KINDS)]
    ac = frame[frame.event_type == 'ACCESO']
    base_tx, base_ac = et.tx_matrix(tx), et.access_matrix(ac)
    assert list(base_tx.columns) == list(et.TX_FEATURES) and list(base_ac.columns) == list(et.ACCESS_FEATURES)
    assert np.isfinite(base_tx.to_numpy()).all() and np.isfinite(base_ac.to_numpy()).all()
    scrambled_tx, scrambled_ac = tx.copy(), ac.copy()
    for part in (scrambled_tx, scrambled_ac):
        part['is_fraud'] = 1 - part['is_fraud']
        part['scenario'] = 'otro'
        part['puntuacion_riesgo_sintetica'] = 99.0
        part['nivel_riesgo_sintetico'] = 'SOSPECHOSO'
        part['event_id'] = 'EVT-000000'
        part['account_id'] = 'ACC-0000'
        part['dispositivo_nuevo'] = 1 - part['dispositivo_nuevo']
        part['ubicacion_nueva'] = 1 - part['ubicacion_nueva']
    pd.testing.assert_frame_equal(base_tx, et.tx_matrix(scrambled_tx))
    pd.testing.assert_frame_equal(base_ac, et.access_matrix(scrambled_ac))


def test_deposits_are_not_modelled_as_outgoing_money(dataset, tmp_path):
    frame = dataset['frame']
    deposits = int((frame.tipo_operacion == 'DEPOSITO').sum())
    assert deposits > 0 and not set(se.OUT_KINDS) & {'DEPOSITO'}


# ───────── Pipeline completo ─────────

def test_the_pipeline_registers_three_candidates_and_activates_none(trained):
    report, rows, status = trained['report'], trained['rows'], trained['status']
    assert len(report['candidatos_registrados']) == 3 == len(rows)
    assert all(state == 'CANDIDATO' for state, _, _ in rows.values())
    assert status['available'] is False and status['active_version'] is None                # nada activo → el detector sigue sin modelo
    assert not (trained['root'] / 'modelos' / 'current.txt').exists()                      # ninguna versión apuntada como activa
    assert all(part['carga_verificada'] for name, part in report['modelos'].items() if name != 'candidato_clasico')
    assert {part['version'] for part in report['modelos'].values()} == set(report['candidatos_registrados'])


def test_candidates_cannot_be_activated_by_accident_and_mismatched_ones_are_refused(trained):
    with trained['app'].app_context():
        for name in ('transacciones', 'accesos'):
            version = trained['report']['modelos'][name]['version']
            assert anomaly.activation_gates(FraudModelVersion.query.filter_by(version=version).one())       # el inferidor actual usa otras 4 variables
            with pytest.raises(anomaly.ActivationRefused):
                anomaly.activate_version(version)
        assert FraudModelVersion.query.filter_by(status='ACTIVO').count() == 0
        assert anomaly.status()['active_version'] is None


def test_the_split_is_chronological_with_no_overlap_and_no_label_use(trained):
    integrity = trained['report']['integridad_temporal_y_sin_fugas']
    assert integrity['etiquetas_usadas_en_entrenamiento'] is False and integrity['etiquetas_usadas_para_el_umbral'] is False
    for name in ('transacciones', 'accesos'):
        assert integrity[f'{name}_solapamiento_de_ids_entrenamiento_evaluacion'] == 0
        assert integrity[f'{name}_ultimo_evento_de_entrenamiento'] < integrity[f'{name}_primer_evento_de_evaluacion']
        assert integrity[f'{name}_ultimo_evento_de_entrenamiento'] < integrity['corte_temporal'] <= integrity[f'{name}_primer_evento_de_evaluacion']
        period = trained['report']['modelos'][name]['periodos']
        assert period['entrenamiento'][1] < period['evaluacion'][0]
    assert integrity['hiperparametros_fijados_a_priori'] == et.HYPERPARAMETERS and integrity['tasa_de_alertas_a_priori'] == 0.05
    sizes = trained['report']['modelos']
    assert sizes['transacciones']['filas_entrenamiento'] > sizes['transacciones']['filas_evaluacion'] > 0


def test_each_model_reports_its_configuration_variables_periods_and_metrics(trained):
    with trained['app'].app_context():
        for name, features in (('transacciones', et.TX_FEATURES), ('accesos', et.ACCESS_FEATURES)):
            part = trained['report']['modelos'][name]
            row = FraudModelVersion.query.filter_by(version=part['version']).one()
            assert row.algorithm == 'IsolationForest' and row.source == 'CSV' and list(row.features) == list(features)
            assert row.n_train == part['filas_entrenamiento'] and row.n_eval == part['filas_evaluacion'] and row.data_sha256 == trained['report']['dataset']['sha256']
            stored = row.metrics
            assert stored['periodos'] == part['periodos'] and stored['variables'] == list(features) and 'variables_excluidas' in stored
            assert stored['matriz_confusion'] == part['metricas']['matriz_confusion']
            metadata = json.loads((trained['root'] / 'modelos' / f'{row.version}.json').read_text(encoding='utf-8'))
            assert metadata['sklearn'] and metadata['python'] and metadata['tipo_de_evento'] == name and metadata['hiperparametros'] == et.HYPERPARAMETERS


def test_the_reported_metrics_are_arithmetically_consistent(trained):
    for name in ('transacciones', 'accesos'):
        m = trained['report']['modelos'][name]['metricas']
        c = m['matriz_confusion']
        assert c['VP'] + c['FP'] + c['VN'] + c['FN'] == trained['report']['modelos'][name]['filas_evaluacion']
        assert m['eventos_marcados'] == c['VP'] + c['FP'] and m['fraudes_sinteticos_detectados'] == c['VP'] and m['fraudes_sinteticos_no_detectados'] == c['FN']
        assert m['normales_marcadas_como_sospechosas'] == c['FP'] and m['eventos_normales_en_evaluacion'] == c['FP'] + c['VN']
        if c['VP'] + c['FP']:
            assert m['precision'] == round(c['VP'] / (c['VP'] + c['FP']), 4)
        assert m['sensibilidad_recall'] == round(c['VP'] / (c['VP'] + c['FN']), 4) and m['tasa_falsos_positivos'] == round(c['FP'] / (c['FP'] + c['VN']), 4)
        assert sum(v['eventos'] for v in m['por_escenario'].values()) == trained['report']['modelos'][name]['filas_evaluacion']
        assert m['tiempo_evaluacion_ms'] >= 0 and 0 <= m['independiente_del_umbral']['auc_roc'] <= 1
    kinds = trained['report']['modelos']['transacciones']['metricas']['por_tipo_operacion']
    assert set(kinds) <= set(se.OUT_KINDS) and sum(v['eventos'] for v in kinds.values()) == trained['report']['modelos']['transacciones']['filas_evaluacion']


def test_the_engine_rules_are_evaluated_on_the_same_reserved_set(trained):
    for name in ('transacciones', 'accesos'):
        rules = trained['report']['reglas_del_motor_sobre_la_misma_evaluacion'][name]
        total = sum(v['fraude_sintetico'] + v['normal'] for v in rules['distribucion_por_nivel'].values())
        assert total == trained['report']['modelos'][name]['filas_evaluacion'] and set(rules['distribucion_por_nivel']) == {'NORMAL', 'INTERMEDIO', 'SOSPECHOSO'}
        strict, broad = rules['alerta_estricta_SOSPECHOSO'], rules['alerta_amplia_INTERMEDIO_o_mas']
        assert strict['eventos_marcados'] <= broad['eventos_marcados']


def test_the_reports_name_the_csv_without_any_local_path(trained, dataset):
    folder = trained['root'] / 'informe'
    stored = json.loads((folder / 'informe_evaluacion_modelos_candidatos.json').read_text(encoding='utf-8'))
    text = (folder / 'informe_evaluacion_modelos_candidatos.md').read_text(encoding='utf-8')
    assert stored['dataset']['archivo'] == 'combinado.csv' and 'ruta' not in stored['dataset']
    assert '- Archivo: `combinado.csv`' in text
    for private in (str(dataset['dir']), str(dataset['dir']).replace('\\', '\\\\'), str(trained['root'])):
        assert private not in json.dumps(stored) and private not in text                  # ni la carpeta del CSV ni la de trabajo
    assert trained['report']['dataset']['archivo'] == 'combinado.csv'


def test_the_report_files_are_written_and_state_the_limitations(trained):
    folder = trained['root'] / 'informe'
    stored = json.loads((folder / 'informe_evaluacion_modelos_candidatos.json').read_text(encoding='utf-8'))
    text = (folder / 'informe_evaluacion_modelos_candidatos.md').read_text(encoding='utf-8')
    assert stored['resultados_sha256'] == trained['report']['resultados_sha256'] and stored['limitaciones'] == et.LIMITATIONS
    assert 'SINTÉTIC' in text.upper() and 'no demuestra' in text.lower().replace('\n', ' ')
    for term in ('Matriz de confusión', 'Sensibilidad', 'falsos positivos'):
        assert term.lower() in text.lower()
    assert '"secret' not in json.dumps(stored).lower() and 'password' not in json.dumps(stored).lower()
    assert stored['entorno']['scikit_learn'] and stored['entorno']['python'] and stored['entorno']['pandas'] and stored['entorno']['numpy']


# ───────── Reproducibilidad y ausencia de fugas (comprobadas, no declaradas) ─────────

def test_the_same_data_gives_the_same_results_and_only_the_registered_version_changes(trained, dataset, tmp_path):
    app = _app(tmp_path)
    with app.app_context():
        again = et.run_pipeline(dataset['path'])
    assert again['resultados_sha256'] == trained['report']['resultados_sha256']
    for name in ('transacciones', 'accesos'):
        first, second = trained['report']['modelos'][name], again['modelos'][name]
        assert first['metricas']['matriz_confusion'] == second['metricas']['matriz_confusion'] and first['metricas']['umbral_puntuacion'] == second['metricas']['umbral_puntuacion']
        assert first['version'] != second['version']                                      # cada ejecución registra su propia versión


def test_model_and_threshold_depend_only_on_features_never_on_labels(trained, dataset, tmp_path):
    flipped = dataset['frame'].copy()
    flipped['is_fraud'] = 1 - flipped['is_fraud']                                        # etiquetas invertidas: el modelo no debe notarlo
    path = _write(tmp_path, flipped, 'invertido.csv')
    with _app(tmp_path).app_context():
        other = et.run_pipeline(path)
    for name in ('transacciones', 'accesos'):
        first, second = trained['report']['modelos'][name]['metricas'], other['modelos'][name]['metricas']
        assert first['umbral_puntuacion'] == second['umbral_puntuacion']                  # mismo modelo, mismo umbral
        flagged_first = first['eventos_marcados']
        assert flagged_first == second['eventos_marcados']                                 # mismos eventos marcados…
        assert first['matriz_confusion'] != second['matriz_confusion']                     # …pero las métricas sí dependen de la etiqueta evaluada
    assert other['resultados_sha256'] != trained['report']['resultados_sha256']


def test_fit_never_receives_the_label_or_evaluation_rows(dataset, tmp_path, monkeypatch):
    seen = []
    original = IsolationForest.fit

    def spy(self, X, y=None, **kwargs):
        seen.append((np.asarray(X).shape, y))
        return original(self, X, y, **kwargs)

    monkeypatch.setattr(IsolationForest, 'fit', spy)
    with _app(tmp_path).app_context():
        report = et.run_pipeline(dataset['path'])
    assert len(seen) >= 3 and all(y is None for _, y in seen)                              # aprendizaje no supervisado
    train_sizes = {report['modelos'][n]['filas_entrenamiento'] for n in ('transacciones', 'accesos')}
    assert {shape[0] for shape, _ in seen[:2]} == train_sizes                                # solo filas del periodo de entrenamiento
    assert seen[0][0][1] == len(et.TX_FEATURES) and seen[1][0][1] == len(et.ACCESS_FEATURES)


# ───────── Métricas: casos límite ─────────

def test_metrics_are_undefined_not_invented_when_a_denominator_is_zero():
    none_positive = et.binary_metrics([0, 0, 0, 0], [0, 0, 1, 0])
    assert none_positive['sensibilidad_recall'] is None and none_positive['f1'] is None and none_positive['precision'] == 0.0
    assert none_positive['tasa_falsos_positivos'] == 0.25 and none_positive['matriz_confusion'] == {'VP': 0, 'FP': 1, 'VN': 3, 'FN': 0}
    nothing_flagged = et.binary_metrics([1, 0, 1, 0], [0, 0, 0, 0])
    assert nothing_flagged['precision'] is None and nothing_flagged['sensibilidad_recall'] == 0.0 and nothing_flagged['f1'] is None
    only_positive = et.binary_metrics([1, 1], [1, 0])
    assert only_positive['tasa_falsos_positivos'] is None and only_positive['sensibilidad_recall'] == 0.5
    perfect = et.binary_metrics([1, 0, 1, 0], [1, 0, 1, 0])
    assert perfect['precision'] == perfect['sensibilidad_recall'] == perfect['f1'] == 1.0 and perfect['tasa_falsos_positivos'] == 0.0
    empty = et.binary_metrics([], [])
    assert empty['precision'] is None and empty['sensibilidad_recall'] is None and empty['matriz_confusion'] == {'VP': 0, 'FP': 0, 'VN': 0, 'FN': 0}
    assert et.threshold_free([0, 0, 0], [0.1, 0.2, 0.3])['auc_roc'] is None and et.threshold_free([1, 1], [0.1, 0.2])['precision_media'] is None
    ranked = et.threshold_free([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9])
    assert ranked['auc_roc'] == 1.0 and ranked['prevalencia'] == 0.5


# ───────── Carga de candidatos ─────────

def test_a_candidate_can_be_loaded_without_activation_and_a_tampered_one_is_refused(trained):
    with trained['app'].app_context():
        version = trained['report']['modelos']['transacciones']['version']
        model, metadata = anomaly.load_candidate(version)
        assert metadata['sha256'] and hasattr(model, 'decision_function') and model.n_features_in_ == len(et.TX_FEATURES)
        assert np.isfinite(model.decision_function(np.zeros((2, len(et.TX_FEATURES))))).all()
        artifact = trained['root'] / 'modelos' / f'{version}.joblib'
        original = artifact.read_bytes()
        try:
            artifact.write_bytes(original + b'manipulado')
            with pytest.raises(ValueError, match='SHA-256'):
                anomaly.load_candidate(version)
        finally:
            artifact.write_bytes(original)
        anomaly.load_candidate(version)                                                    # restaurado: vuelve a cargar
        for bad in ('../etc/passwd', 'otra-cosa', 'iforest/../x', 'iforest\\x'):
            with pytest.raises(ValueError):
                anomaly.load_candidate(bad)
        with pytest.raises(ValueError, match='Falta'):
            anomaly.load_candidate('iforest-tx-99999999999999')


def test_an_environment_mismatch_blocks_loading(trained, monkeypatch):
    with trained['app'].app_context():
        version = trained['report']['modelos']['accesos']['version']
        monkeypatch.setattr(anomaly, '_runtime_versions', lambda: {'sklearn': '0.0.1', 'python': '2.7.0'})
        with pytest.raises(ValueError, match='entorno'):
            anomaly.load_candidate(version)


# ───────── Orden de línea de comandos ─────────

def test_the_cli_trains_candidates_reports_and_activates_nothing(dataset, tmp_path):
    app = _app(tmp_path)
    runner = app.test_cli_runner()
    result = runner.invoke(args=['fraud-train-events', str(dataset['path']), '--report-dir', str(tmp_path / 'informe')])
    assert result.exit_code == 0, result.output
    assert 'Ninguno está activo' in result.output and result.output.count('Candidato ') == 3
    assert (tmp_path / 'informe' / 'informe_evaluacion_modelos_candidatos.json').is_file()
    with app.app_context():
        assert FraudModelVersion.query.filter_by(status='ACTIVO').count() == 0 and FraudModelVersion.query.count() == 3


def test_the_cli_refuses_bad_input_with_a_clear_message(dataset, tmp_path):
    app = _app(tmp_path)
    runner = app.test_cli_runner()
    broken = _write(tmp_path, dataset['frame'].drop(columns=['importe']), 'roto.csv')
    result = runner.invoke(args=['fraud-train-events', str(broken)])
    assert result.exit_code != 0 and 'Esquema distinto' in result.output and 'Traceback' not in result.output
    protected = Path(app.root_path).parent / 'data' / 'x.csv'
    result = runner.invoke(args=['fraud-train-events', str(protected)])
    assert result.exit_code != 0 and 'data/' in result.output
    result = runner.invoke(args=['fraud-train-events', str(dataset['path']), '--flag-rate', '0.9'])
    assert result.exit_code != 0
    with app.app_context():
        assert FraudModelVersion.query.count() == 0
