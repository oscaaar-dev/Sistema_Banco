"""Motor de evaluación: reglas + (opcional) detector de anomalías en modo observación + política por modo.

Garantías de diseño:

* **El motor nunca autoriza nada.** Solo puede añadir una retención. Las validaciones bancarias (autenticación, autorización,
  importe, saldo, idempotencia, OTP y cámara) se ejecutan siempre y por separado.
* **La retención ocurre antes de tocar saldos.** ``evaluate_operation`` se llama tras validar y *antes* de modificar cuentas; si
  retiene, confirma solo sus propios registros (evaluación, incidente, retención, auditoría) y lanza ``OperationHeld``.
* **Un fallo del motor no es «sin riesgo».** Se registra ``NO_EVALUADO``. En ``enforce``, continúan solo las operaciones de hasta
  ``FRAUD_FAILSAFE_MAX_AMOUNT`` (que ya exigen OTP y cámara); las mayores se retienen. En ``shadow`` el fallo no afecta a nada.
* Las escrituras de observación van en un *savepoint*: si fallan, la operación bancaria continúa intacta.
"""
import time
from decimal import Decimal

from flask import current_app

from ..models import RiskEvaluation, db
from . import anomaly, constants as c, features as feature_extraction, rules, service

HELD_MESSAGE = ('Por seguridad, esta operación quedó retenida para revisión antes de ejecutarse. No se realizó ningún cargo ni '
                'movimiento. Contacta a soporte para continuar.')


class Decision:
    """Resultado de evaluar una operación. ``authorization`` es una retención autorizada que se consumirá al completarse."""

    def __init__(self, evaluation=None, level=None, score=0, authorization=None):
        self.evaluation, self.level, self.score, self.authorization = evaluation, level, score, authorization


def _recommended(level):
    return {c.LEVEL_NORMAL: c.ACTION_ALLOW, c.LEVEL_INTERMEDIO: c.ACTION_VERIFY,
            c.LEVEL_SOSPECHOSO: c.ACTION_HOLD}.get(level, c.ACTION_MANUAL)


def _persist(evaluation):
    """Guarda la evaluación sin poder dañar la transacción en curso. Devuelve la evaluación o ``None``."""
    try:
        with db.session.begin_nested():
            db.session.add(evaluation)
            db.session.flush()
        return evaluation
    except Exception:
        current_app.logger.warning('Fraude: no se pudo guardar la evaluación de riesgo; la operación continúa.')
        return None


def _model_score(features):
    try:
        return anomaly.score(features)
    except Exception:
        current_app.logger.warning('Fraude: el detector de anomalías falló; se ignora su puntuación.')
        return None, None


def evaluate_operation(user, account, kind, amount, *, beneficiary=None, description='', ip_hash=None):
    """Evalúa una operación de salida de dinero ANTES de modificar saldos. Puede lanzar ``OperationHeld``."""
    config = current_app.config
    mode = config['FRAUD_MODE']
    if mode == c.MODE_OFF:
        return Decision()
    started = time.perf_counter()
    value = Decimal(str(amount))
    level, score, signals, confidence, error_code = c.LEVEL_NO_EVALUADO, 0, [], 'SUFICIENTE', None
    model_version = model_value = None
    try:
        features = feature_extraction.operation_features(user, account, kind, value, beneficiary=beneficiary, ip_hash=ip_hash)
        signals = rules.score_operation(features, config)
        confidence = 'SUFICIENTE' if features.n_history >= config['FRAUD_MIN_HISTORY'] else 'INSUFICIENTE'
        score, level, _ = rules.classify(signals, confidence, config)
        model_version, model_value = _model_score(features)
    except Exception as exc:
        current_app.logger.warning('Fraude: falló la evaluación de una operación (%s).', type(exc).__name__)
        error_code = type(exc).__name__[:60]
        level, score, signals = c.LEVEL_NO_EVALUADO, 0, []

    target = beneficiary.id if beneficiary is not None else (description or '')[:180]
    fingerprint = service.operation_fingerprint(user.id, account.id, kind, value, target)
    held, authorization, applied = False, None, c.APPLIED_NONE
    if mode == c.MODE_ENFORCE:
        applied = {c.LEVEL_NORMAL: c.APPLIED_ALLOW, c.LEVEL_INTERMEDIO: c.APPLIED_ALERT, c.LEVEL_SOSPECHOSO: c.APPLIED_ALERT,
                   c.LEVEL_NO_EVALUADO: c.APPLIED_FAILSAFE_ALLOW}[level]
        failed_over_limit = level == c.LEVEL_NO_EVALUADO and value > Decimal(str(config['FRAUD_FAILSAFE_MAX_AMOUNT']))
        if kind in c.HOLDABLE_KINDS and (level == c.LEVEL_SOSPECHOSO or failed_over_limit):
            authorization = service.find_authorization(user.id, fingerprint)
            if authorization is not None:
                applied = c.APPLIED_ADMIN_AUTHORIZED
            else:
                held, applied = True, c.APPLIED_HELD

    evaluation = _persist(RiskEvaluation(
        user_id=user.id, account_id=account.id, context=kind, event=c.EVENT_OPERATION, level=level, score=score,
        confidence=confidence, signals=[signal.as_dict() for signal in signals], rules_version=config['FRAUD_RULES_VERSION'],
        model_version=model_version, model_score=model_value, mode=mode, recommended_action=_recommended(level),
        applied_action=applied, status='ERROR' if error_code else 'OK', error_code=error_code,
        duration_ms=round((time.perf_counter() - started) * 1000, 2)))

    incident = None
    if level == c.LEVEL_SOSPECHOSO and evaluation is not None:
        try:
            with db.session.begin_nested():
                incident, _ = service.open_or_update_incident(
                    evaluation, user, c.CATEGORY_TRANSACTIONAL, f'Operación {kind} con señales de riesgo (puntuación {score}).')
        except Exception:
            incident = None
            current_app.logger.warning('Fraude: no se pudo abrir el incidente; la evaluación queda registrada.')

    if held:
        reason = 'Operación sospechosa retenida antes de ejecutarse.' if level == c.LEVEL_SOSPECHOSO \
            else 'No se pudo evaluar el riesgo y el monto supera el límite de continuidad.'
        service.create_hold(user, account, kind, value, fingerprint, reason, evaluation, incident)
        service.audit(user, 'FRAUD_OPERATION_HELD', result='RETENIDA')
        db.session.commit()          # solo los registros de la retención: aún no se tocó ningún saldo ni movimiento
        raise service.OperationHeld(HELD_MESSAGE)
    return Decision(evaluation, level, score, authorization)


def finalize_operation(decision, transaction):
    """Vincula la evaluación con el movimiento creado y consume la autorización usada (en la misma transacción)."""
    if decision is None:
        return
    if decision.evaluation is not None:
        decision.evaluation.transaction_id = transaction.id
    if decision.authorization is not None:
        service.mark_executed(decision.authorization, transaction)


def evaluate_access(user, event, *, ip_hash=None):
    """Evalúa un evento de acceso. Nunca lanza: es un registro de seguridad, no puede romper el inicio de sesión.

    Debe llamarse DESPUÉS de confirmar los registros del propio evento (fallo de clave o de OTP, acceso correcto).
    """
    config = current_app.config
    mode = config['FRAUD_MODE']
    if mode == c.MODE_OFF or user is None:
        return None
    started = time.perf_counter()
    level, score, signals, confidence, error_code = c.LEVEL_NO_EVALUADO, 0, [], 'SUFICIENTE', None
    try:
        features = feature_extraction.access_features(user, ip_hash=ip_hash)
        signals = rules.score_access(features, config)
        confidence = 'SUFICIENTE' if features.n_prior_access_events >= 3 else 'INSUFICIENTE'
        score, level, _ = rules.classify(signals, confidence, config)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning('Fraude: falló la evaluación de un acceso (%s).', type(exc).__name__)
        error_code = type(exc).__name__[:60]
    try:
        applied = c.APPLIED_NONE
        if mode == c.MODE_ENFORCE:
            applied = c.APPLIED_ACCESS_RESTRICTED if level == c.LEVEL_SOSPECHOSO else (
                c.APPLIED_ALERT if level == c.LEVEL_INTERMEDIO else c.APPLIED_ALLOW)
        evaluation = RiskEvaluation(
            user_id=user.id, context=c.CONTEXT_ACCESS, event=event, level=level, score=score, confidence=confidence,
            signals=[signal.as_dict() for signal in signals], rules_version=config['FRAUD_RULES_VERSION'], mode=mode,
            recommended_action=_recommended(level), applied_action=applied, status='ERROR' if error_code else 'OK',
            error_code=error_code, duration_ms=round((time.perf_counter() - started) * 1000, 2))
        db.session.add(evaluation)
        if level == c.LEVEL_SOSPECHOSO:
            incident, _ = service.open_or_update_incident(
                evaluation, user, c.CATEGORY_ACCESS, f'Actividad de acceso con señales de riesgo (puntuación {score}).')
            if mode == c.MODE_ENFORCE:
                service.apply_preventive_access_restriction(user, incident)
                service.audit(user, 'FRAUD_ACCESS_RESTRICTED', result='RESTRINGIDO')
        db.session.commit()
        return evaluation
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Fraude: no se pudo registrar la evaluación de un acceso.')
        return None
