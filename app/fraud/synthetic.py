"""Datos SINTÉTICOS para pruebas y demostraciones del detector. Nunca se mezclan con datos reales.

Se generan de forma determinista (``seed``) y sin leer ninguna fuente externa ni la carpeta ``data/``. Si ``labelled=True``, las
anomalías inyectadas llevan la etiqueta FRAUDE_CONFIRMADO y el resto LEGITIMA_VERIFICADA: es una «verdad de referencia» que solo
existe porque la fabricamos; sirve para probar el código, **no** demuestra eficacia contra fraude real. Sin ``labelled``, todas las
filas son SIN_VERIFICAR (el caso realista).
"""
from datetime import datetime, timedelta

from .csv_training import KINDS, guard_path

BASE = datetime(2026, 1, 1)
COLUMNS = ['transaction_id', 'timestamp', 'account_id', 'amount', 'balance_before', 'kind', 'label']


def generate(*, n_accounts=40, days=30, seed=7, anomaly_rate=0.01, labelled=False):
    import numpy as np
    import pandas as pd
    rng = np.random.RandomState(seed)
    rows = []
    for account in range(n_accounts):
        typical = float(np.exp(rng.normal(np.log(80), 0.5)))
        base_balance = float(rng.uniform(1500, 9000))
        for _ in range(int(rng.poisson(days * 0.8))):
            when = BASE + timedelta(seconds=int(rng.uniform(0, days * 86400)))
            balance = base_balance * float(rng.uniform(0.8, 1.2))
            amount = min(max(typical * float(np.exp(rng.normal(0, 0.25))), 1.0), 0.35 * balance)
            rows.append([when, f'A{account:03d}', round(amount, 2), round(balance, 2), KINDS[int(rng.choice(3, p=[0.4, 0.4, 0.2]))],
                         'LEGITIMA_VERIFICADA' if labelled else 'SIN_VERIFICAR'])
    injected = max(1, int(round(len(rows) * anomaly_rate))) if anomaly_rate > 0 else 0
    fraud_label = 'FRAUDE_CONFIRMADO' if labelled else 'SIN_VERIFICAR'
    for index in range(injected):
        account = f'A{int(rng.randint(0, n_accounts)):03d}'
        when = BASE + timedelta(seconds=int(rng.uniform(0, days * 86400)))
        balance = float(rng.uniform(1500, 9000))
        if index % 2 == 0:       # vaciado: monto cercano al saldo
            rows.append([when, account, round(0.92 * balance, 2), round(balance, 2), 'TRANSFERENCIA', fraud_label])
        else:                    # ráfaga: varias salidas en pocos segundos
            for step in range(5):
                rows.append([when + timedelta(seconds=15 * step), account, round(float(rng.uniform(150, 400)), 2),
                             round(balance, 2), 'TRANSFERENCIA', fraud_label])
    frame = pd.DataFrame(rows, columns=['timestamp', 'account_id', 'amount', 'balance_before', 'kind', 'label'])
    frame = frame.sort_values('timestamp', kind='mergesort').reset_index(drop=True)
    frame.insert(0, 'transaction_id', [f'SYN-{i:06d}' for i in range(len(frame))])
    return frame[COLUMNS]


def write_csv(frame, path):
    """Escribe el CSV (UTF-8, fechas ISO). Rechaza cualquier ruta dentro de ``data/``."""
    target = guard_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out['timestamp'] = out['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S')
    out.to_csv(target, index=False, encoding='utf-8')
    return target
