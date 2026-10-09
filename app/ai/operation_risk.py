from decimal import Decimal
from statistics import median


def assess_operation_risk(
    amount,
    balance,
    comparable_amounts,
    recent_operation_count,
    duration_seconds=None,
):
    """Return an explainable 0-100 risk score, not a calibrated probability."""
    score = 0
    reasons = []

    if balance > 0:
        balance_ratio = Decimal(str(amount)) / Decimal(str(balance))
        if balance_ratio >= Decimal('0.50'):
            score += 35
            reasons.append('El monto representa al menos la mitad del saldo disponible.')
        elif balance_ratio >= Decimal('0.25'):
            score += 20
            reasons.append('El monto representa al menos una cuarta parte del saldo disponible.')
        elif balance_ratio >= Decimal('0.10'):
            score += 10
            reasons.append('El monto representa al menos el 10 % del saldo disponible.')

    if len(comparable_amounts) >= 3:
        typical_amount = Decimal(str(median(comparable_amounts)))
        if typical_amount > 0:
            ratio = Decimal(str(amount)) / typical_amount
            if ratio >= 4:
                score += 40
                reasons.append('El monto es al menos 4 veces el monto típico de esta operación.')
            elif ratio >= 3:
                score += 30
                reasons.append('El monto es al menos 3 veces el monto típico de esta operación.')
            elif ratio >= 2:
                score += 20
                reasons.append('El monto es al menos 2 veces el monto típico de esta operación.')

    if recent_operation_count >= 5:
        score += 35
        reasons.append('Se registraron 5 o más operaciones en la última hora.')
    elif recent_operation_count >= 3:
        score += 20
        reasons.append('Se registraron 3 o más operaciones en la última hora.')

    if duration_seconds is not None and duration_seconds >= 180:
        score += 15
        reasons.append('La operación tomó 3 minutos o más en completarse.')

    if not reasons:
        reasons.append('No se activaron las reglas de alerta configuradas.')

    enough_history = len(comparable_amounts) >= 3 or recent_operation_count >= 3
    if not enough_history:
        reasons.append('Historial limitado: el puntaje se basa principalmente en el monto frente al saldo.')

    score = min(score, 100)
    if score >= 60:
        level = 'Alto'
    elif score >= 20:
        level = 'Moderado'
    else:
        level = 'Bajo'

    return {
        'score': score,
        'level': level,
        'reasons': reasons,
        'enough_history': enough_history,
    }
