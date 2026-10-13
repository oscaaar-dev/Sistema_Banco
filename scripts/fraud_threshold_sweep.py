"""Barrido de umbrales de las reglas antifraude sobre datos SINTÉTICOS (reproducible, sin base de datos ni ``data/``).

Uso (desde la raíz del proyecto)::

    python -m scripts.fraud_threshold_sweep

Genera operaciones sintéticas con anomalías inyectadas (vaciados y ráfagas), calcula las mismas características que usa el motor y
cuenta cuántas operaciones de cada tipo quedan en cada nivel con distintos umbrales. **Es una comprobación de comportamiento sobre
datos fabricados**: no estima la sensibilidad ni los falsos positivos de un fraude real, que no se pueden medir sin etiquetas
confirmadas. Sirve para ver cómo se mueve la proporción de alertas al cambiar los umbrales y para detectar umbrales absurdos.
"""
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.fraud import constants as c, rules, synthetic  # noqa: E402
from app.fraud.features import OperationFeatures  # noqa: E402

CONFIGS = {
    'A (25/55/65, vigente)': {'FRAUD_LEVEL_INTERMEDIO': 25, 'FRAUD_LEVEL_SOSPECHOSO': 55, 'FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE': 65},
    'B (20/45/55)': {'FRAUD_LEVEL_INTERMEDIO': 20, 'FRAUD_LEVEL_SOSPECHOSO': 45, 'FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE': 55},
    'C (35/65/75)': {'FRAUD_LEVEL_INTERMEDIO': 35, 'FRAUD_LEVEL_SOSPECHOSO': 65, 'FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE': 75},
    'D (45/75/85)': {'FRAUD_LEVEL_INTERMEDIO': 45, 'FRAUD_LEVEL_SOSPECHOSO': 75, 'FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE': 85},
}


def build_features(frame):
    """``OperationFeatures`` de cada fila usando solo lo anterior de la misma cuenta (como en producción)."""
    previous, out = {}, []
    for row in frame.sort_values('timestamp', kind='mergesort').itertuples():
        history = previous.setdefault(row.account_id, [])
        when = row.timestamp.to_pydatetime()
        same = [Decimal(str(a)) for t, k, a in history if k == row.kind and t >= when - timedelta(days=90)][-100:]
        recent = [t for t, _, _ in history if t >= when - timedelta(hours=1)]
        features = OperationFeatures(
            kind=row.kind, amount=Decimal(str(row.amount)), balance=Decimal(str(row.balance_before)), n_history=len(same),
            median_amount=Decimal(str(median(same))) if same else None, ops_1h=len(recent),
            ops_10m=sum(1 for t in recent if t >= when - timedelta(minutes=10)), ops_2m=sum(1 for t in recent if t >= when - timedelta(minutes=2)))
        out.append((row.label, features))
        history.append((when, row.kind, row.amount))
    return out


def main():
    frame = synthetic.generate(n_accounts=120, days=60, labelled=True, anomaly_rate=0.05, seed=11)
    rows = build_features(frame)
    totals = Counter(label for label, _ in rows)
    print(f'Operaciones sintéticas: {len(rows)} ({totals["LEGITIMA_VERIFICADA"]} habituales, {totals["FRAUDE_CONFIRMADO"]} anomalías inyectadas)')
    print('| Configuración (intermedio/sospechoso/baja confianza) | Habituales en INTERMEDIO | Habituales en SOSPECHOSO | Anomalías en INTERMEDIO | Anomalías en SOSPECHOSO |')
    print('|---|---|---|---|---|')
    for name, config in CONFIGS.items():
        counts = {(label, level): 0 for label in totals for level in c.ENGINE_LEVELS}
        for label, features in rows:
            confidence = 'SUFICIENTE' if features.n_history >= 3 else 'INSUFICIENTE'
            _, level, _ = rules.classify(rules.score_operation(features, {'FRAUD_MIN_HISTORY': 3, **config}), confidence, config)
            counts[(label, level)] += 1
        pct = lambda label, level: f'{counts[(label, level)] / totals[label]:.1%}'
        print(f'| {name} | {pct("LEGITIMA_VERIFICADA", c.LEVEL_INTERMEDIO)} | {pct("LEGITIMA_VERIFICADA", c.LEVEL_SOSPECHOSO)} | '
              f'{pct("FRAUDE_CONFIRMADO", c.LEVEL_INTERMEDIO)} | {pct("FRAUDE_CONFIRMADO", c.LEVEL_SOSPECHOSO)} |')


if __name__ == '__main__':
    main()
