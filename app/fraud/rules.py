"""Reglas de riesgo versionadas (``FRAUD_RULES_VERSION``) y clasificación en niveles.

Importante, y así se presenta en el panel y en la documentación:

* La puntuación 0-100 es una **herramienta interna de clasificación**, NO una probabilidad de fraude calibrada.
* Los umbrales son **iniciales**: se eligieron conservadores a partir de las reglas ya existentes y se contrastan con escenarios
  sintéticos; no están validados con datos reales etiquetados (no los hay). Se ajustan observando el modo «shadow».
* Ninguna señal aislada puede llevar a SOSPECHOSO: hacen falta dos *grupos* de señales independientes (cada grupo tiene un tope).
* Con historial insuficiente solo se usan reglas conservadoras y el umbral de SOSPECHOSO sube. La falta de datos no es fraude.
"""
from dataclasses import dataclass

from .constants import LEVEL_INTERMEDIO, LEVEL_NORMAL, LEVEL_SOSPECHOSO

GROUP_CAPS = {'monto': 40, 'frecuencia': 30, 'automatizacion': 25, 'destinatario': 25, 'acceso': 45, 'contexto': 24}
RELEVANT_GROUP_POINTS = 10        # un grupo cuenta como «señal independiente» a partir de estos puntos


@dataclass(frozen=True)
class Signal:
    code: str
    group: str
    points: int
    detail: str

    def as_dict(self):
        return {'code': self.code, 'group': self.group, 'points': self.points, 'detail': self.detail}


def score_operation(f, config):
    """Señales de una operación de salida de dinero. ``f`` es un ``OperationFeatures``."""
    signals = []
    enough_history = f.n_history >= config['FRAUD_MIN_HISTORY']

    if enough_history and f.median_amount and f.median_amount > 0:
        ratio = f.amount / f.median_amount
        if ratio >= 6:
            signals.append(Signal('MONTO_6X_HISTORIAL', 'monto', 25, 'El monto es al menos 6 veces el monto típico de esta operación.'))
        elif ratio >= 3:
            signals.append(Signal('MONTO_3X_HISTORIAL', 'monto', 15, 'El monto es al menos 3 veces el monto típico de esta operación.'))

    share = f.balance_share
    if share is not None:
        if share >= 0.95:
            signals.append(Signal('VACIADO_DE_CUENTA', 'monto', 30, 'El monto equivale al 95 % o más del saldo disponible.'))
        elif share >= 0.80:
            signals.append(Signal('MONTO_80_SALDO', 'monto', 20, 'El monto equivale al 80 % o más del saldo disponible.'))
        elif share >= 0.50:
            signals.append(Signal('MONTO_50_SALDO', 'monto', 10, 'El monto equivale a la mitad o más del saldo disponible.'))

    if f.ops_1h >= 5:
        signals.append(Signal('FRECUENCIA_1H', 'frecuencia', 20, f'{f.ops_1h} salidas de dinero en la última hora.'))
    elif f.ops_10m >= 3:
        signals.append(Signal('FRECUENCIA_10M', 'frecuencia', 15, f'{f.ops_10m} salidas de dinero en los últimos 10 minutos.'))
    if f.ops_2m >= 3:
        signals.append(Signal('RAFAGA_2M', 'automatizacion', 25, f'{f.ops_2m} salidas de dinero en los últimos 2 minutos (posible automatización).'))

    if f.first_time_beneficiary:
        signals.append(Signal('DESTINATARIO_NUEVO', 'destinatario', 8, 'Primera transferencia a este destinatario.'))
    if f.distinct_beneficiaries_24h >= 3:
        signals.append(Signal('VARIOS_DESTINATARIOS_24H', 'destinatario', 15,
                              f'{f.distinct_beneficiaries_24h} destinatarios distintos en 24 horas.'))
    if f.same_amount_repeats_1h >= 2:
        signals.append(Signal('MISMO_MONTO_REPETIDO', 'destinatario', 12, 'El mismo monto al mismo destinatario varias veces en una hora.'))

    if f.failed_logins_30m >= 3:
        signals.append(Signal('FALLOS_LOGIN_RECIENTES', 'acceso', 15, f'{f.failed_logins_30m} intentos de acceso fallidos en los últimos 30 minutos.'))
    if f.otp_failures_30m >= 2:
        signals.append(Signal('FALLOS_OTP_RECIENTES', 'acceso', 15, f'{f.otp_failures_30m} códigos de seguridad incorrectos en los últimos 30 minutos.'))

    if f.password_changed_recently:
        signals.append(Signal('CLAVE_CAMBIADA_24H', 'contexto', 12, 'La contraseña cambió en las últimas 24 horas.'))
    if f.new_context:
        signals.append(Signal('CONTEXTO_NUEVO', 'contexto', 8, 'La operación se confirma desde una conexión no vista antes en este cliente.'))
    return signals


def score_access(f, config):
    """Señales de un evento de acceso. ``f`` es un ``AccessFeatures``."""
    signals = []
    if f.failed_logins_30m >= 5:
        signals.append(Signal('FALLOS_LOGIN_5', 'acceso', 30, f'{f.failed_logins_30m} intentos de acceso fallidos en los últimos 30 minutos.'))
    elif f.failed_logins_30m >= 3:
        signals.append(Signal('FALLOS_LOGIN_3', 'acceso', 15, f'{f.failed_logins_30m} intentos de acceso fallidos en los últimos 30 minutos.'))
    if f.otp_failures_30m >= 3:
        signals.append(Signal('FALLOS_OTP_3', 'acceso', 25, f'{f.otp_failures_30m} códigos de seguridad incorrectos en los últimos 30 minutos.'))
    elif f.otp_failures_30m >= 2:
        signals.append(Signal('FALLOS_OTP_2', 'acceso', 15, f'{f.otp_failures_30m} códigos de seguridad incorrectos en los últimos 30 minutos.'))
    if f.new_context:
        signals.append(Signal('CONTEXTO_NUEVO', 'contexto', 12, 'Conexión no vista antes en los accesos anteriores de este cliente.'))
    if f.otp_sent_60m >= 3:
        signals.append(Signal('SOLICITUDES_OTP_REPETIDAS', 'contexto', 12, f'{f.otp_sent_60m} códigos solicitados en la última hora.'))
    return signals


def classify(signals, confidence, config):
    """(puntuación, nivel, grupos relevantes). Aplica los topes por grupo y exige dos grupos para SOSPECHOSO."""
    by_group = {}
    for signal in signals:
        by_group[signal.group] = by_group.get(signal.group, 0) + signal.points
    capped = {group: min(points, GROUP_CAPS.get(group, 20)) for group, points in by_group.items()}
    score = min(100, sum(capped.values()))
    relevant = sorted(group for group, points in capped.items() if points >= RELEVANT_GROUP_POINTS)
    suspicious_cutoff = config['FRAUD_LEVEL_SOSPECHOSO'] if confidence == 'SUFICIENTE' else config['FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE']
    if score >= suspicious_cutoff and len(relevant) >= 2:
        level = LEVEL_SOSPECHOSO
    elif score >= config['FRAUD_LEVEL_INTERMEDIO']:
        level = LEVEL_INTERMEDIO
    else:
        level = LEVEL_NORMAL
    return score, level, relevant
