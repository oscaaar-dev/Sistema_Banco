"""Higiene de publicación del trabajo antifraude: sin rutas personales en lo que se versiona y sin modelos generados en Git.

Solo lee archivos del proyecto que se publican; nada toca ``data/``.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLISHABLE = [
    'docs/fraude.md', 'docs/dataset-antifraude-sintetico.md', 'docs/informe-final-antifraude.md',
    'datasets_sinteticos/dataset_antifraude_completo_12000.manifest.json',
    'datasets_sinteticos/informe_evaluacion_modelos_candidatos.json', 'datasets_sinteticos/informe_evaluacion_modelos_candidatos.md',
]
PERSONAL_PATH = re.compile(r'[A-Za-z]:[\\/]+(?:Users|Usuarios)[\\/]|/Users/|/home/[a-z]|/c/Users/', re.IGNORECASE)
SECRET = re.compile(r'(?:sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abp]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{30,})')


@pytest.mark.parametrize('relative', PUBLISHABLE)
def test_publishable_files_have_no_personal_paths_or_secrets(relative):
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(f'{relative} no está en esta copia de trabajo')
    text = path.read_text(encoding='utf-8')
    assert not PERSONAL_PATH.search(text), f'{relative} contiene una ruta personal'
    assert not SECRET.search(text), f'{relative} parece contener un secreto'


def test_the_evaluation_reports_keep_their_metrics_after_redaction():
    path = ROOT / 'datasets_sinteticos' / 'informe_evaluacion_modelos_candidatos.json'
    if not path.is_file():
        pytest.skip('el informe no está en esta copia de trabajo')
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['dataset']['archivo'] == 'dataset_antifraude_completo_12000.csv' and 'ruta' not in report['dataset']
    assert report['modelos']['transacciones']['metricas']['matriz_confusion'] == {'VP': 87, 'FP': 25, 'VN': 1644, 'FN': 73}
    assert report['modelos']['accesos']['metricas']['matriz_confusion'] == {'VP': 26, 'FP': 25, 'VN': 899, 'FN': 106}
    assert report['modelos']['candidato_clasico']['metricas']['matriz_confusion'] == {'VP': 64, 'FP': 27, 'VN': 1642, 'FN': 96}
    assert report['resultados_sha256'].startswith('a563e7f7dff458f2')


def test_gitignore_keeps_generated_models_and_local_evidence_out_of_git():
    rules = {line.strip() for line in (ROOT / '.gitignore').read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.startswith('#')}
    for expected in ('*.joblib', 'datasets_sinteticos/modelos_candidatos/', 'instance/fraud_models/', 'fraud-e2e-*', '*.log'):
        assert expected in rules, expected
    assert not any('datasets_sinteticos' == rule.strip('/') or rule in ('*.csv', '*.json', '*.md') for rule in rules)      # el CSV y los informes sí se publican
