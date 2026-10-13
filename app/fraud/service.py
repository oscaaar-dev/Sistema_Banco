"""Incidentes, restricciones y retenciones: el estado que deja el motor y las acciones de un administrador.

Reglas que este módulo hace cumplir (además de las rutas, por defensa en profundidad):

* Solo un ``ADMINISTRADOR`` puede confirmar, descartar, levantar o autorizar; nunca sobre su propio usuario.
* Toda restricción tiene fecha de expiración (no hay bloqueos permanentes) y se puede levantar.
* Solo los incidentes CONFIRMADOS cuentan para la reincidencia; las alertas sin verificar no.
* Las funciones no hacen ``commit``: lo hace quien las llama (así cada acción es atómica con su auditoría).
"""
import hashlib
import hmac
import re
from datetime import timedelta
from decimal import Decimal

from flask import current_app
from sqlalchemy import func

from ..models import AuditLog, FraudIncident, HeldOperation, IncidentEvent, SecurityRestriction, User, db, now_utc
from ..services import admin_service as admins
from . import constants as c


class OperationRestricted(ValueError):
    """El cliente tiene una restricción activa sobre sus operaciones. El mensaje se muestra tal cual al usuario."""


class OperationHeld(ValueError):
    """La operación quedó retenida antes de ejecutarse: no se modificó ningún saldo."""


class FraudAdminError(ValueError):
    """Dato inválido en una acción administrativa; el mensaje se puede mostrar al administrador."""


class FraudPermissionError(PermissionError):
    """El actor no tiene permiso para la acción."""


def audit(actor, action, result='COMPLETADA'):
    db.session.add(AuditLog(user_id=actor.id if actor is not None else None, action=action, module='FRAUDE', result=result))


def _cfg(key):
    return current_app.config[key]


# ───────── Restricciones ─────────

def active_restriction(user_id, scope, now=None):
    now = now or now_utc()
    return (SecurityRestriction.query
            .filter(SecurityRestriction.user_id == user_id, SecurityRestriction.scope == scope,
                    SecurityRestriction.status == c.RESTRICTION_ACTIVE,
                    SecurityRestriction.starts_at <= now, SecurityRestriction.expires_at > now)
            .order_by(SecurityRestriction.expires_at.desc()).first())


def guard_operations(user):
    """Impide operar a un cliente con una restricción activa. Se aplica en cualquier modo, también en ``off``."""
    if active_restriction(user.id, c.SCOPE_OPERATIONS) is not None:
        raise OperationRestricted('Tus operaciones están restringidas temporalmente por seguridad. '
                                  'Contacta a soporte para revisar tu caso.')


def access_restriction(user):
    """Restricción de acceso vigente del cliente, o ``None``."""
    return active_restriction(user.id, c.SCOPE_ACCESS)


def revoke_sessions(user):
    """Invalida las sesiones ya emitidas del usuario (cambia el sello de sesión). No borra datos."""
    user.sessions_revoked_at = now_utc()


def apply_preventive_access_restriction(user, incident):
    """Bloqueo preventivo y temporal del acceso, más revocación de sesiones. Idempotente mientras siga vigente."""
    existing = active_restriction(user.id, c.SCOPE_ACCESS)
    if existing is not None:
        return existing
    now = now_utc()
    restriction = SecurityRestriction(
        user_id=user.id, incident_id=incident.id if incident is not None else None, scope=c.SCOPE_ACCESS,
        origin=c.ORIGIN_PREVENTIVE, reason='Bloqueo preventivo temporal por señales de acceso sospechoso.',
        starts_at=now, expires_at=now + timedelta(minutes=_cfg('FRAUD_PREVENTIVE_ACCESS_MINUTES')),
        rules_version=_cfg('FRAUD_RULES_VERSION'))
    db.session.add(restriction)
    revoke_sessions(user)
    if incident is not None:
        add_event(incident, None, 'RESTRICCION_PREVENTIVA', 'Acceso restringido temporalmente y sesiones revocadas.')
    return restriction


def expire_stale(now=None):
    """Marca como EXPIRADAS las restricciones y retenciones vencidas. Devuelve (restricciones, retenciones)."""
    now = now or now_utc()
    restrictions = (SecurityRestriction.query
                    .filter(SecurityRestriction.status == c.RESTRICTION_ACTIVE, SecurityRestriction.expires_at <= now)
                    .update({'status': c.RESTRICTION_EXPIRED}, synchronize_session=False))
    holds = (HeldOperation.query
             .filter(HeldOperation.status.in_((c.HOLD_HELD, c.HOLD_AUTHORIZED)), HeldOperation.expires_at <= now)
             .update({'status': c.HOLD_EXPIRED}, synchronize_session=False))
    return restrictions, holds


# ───────── Incidentes ─────────

def add_event(incident, actor, event_type, detail=''):
    db.session.add(IncidentEvent(incident_id=incident.id, actor_id=actor.id if actor is not None else None,
                                 event_type=event_type, detail=(detail or '')[:500]))


def open_or_update_incident(evaluation, user, category, title):
    """Abre un incidente para una evaluación SOSPECHOSA o suma la evaluación a uno reciente del mismo cliente y categoría."""
    now = now_utc()
    db.session.flush()
    incident = (FraudIncident.query
                .filter(FraudIncident.user_id == user.id, FraudIncident.category == category,
                        FraudIncident.status.in_(c.INCIDENT_ACTIVE_STATUSES), FraudIncident.created_at >= now - timedelta(hours=24))
                .order_by(FraudIncident.created_at.desc()).first())
    if incident is not None:
        incident.updated_at = now
        add_event(incident, None, 'NUEVA_EVALUACION_SOSPECHOSA',
                  f'Evaluación {evaluation.id}: puntuación {evaluation.score}, modo {evaluation.mode}.' if evaluation is not None else '')
        return incident, False
    incident = FraudIncident(user_id=user.id, account_id=evaluation.account_id if evaluation is not None else None,
                             evaluation_id=evaluation.id if evaluation is not None else None, origin='AUTOMATICO',
                             category=category, status=c.INCIDENT_OPEN, level=c.LEVEL_SOSPECHOSO, title=title[:200])
    db.session.add(incident)
    db.session.flush()
    add_event(incident, None, 'ABIERTO', title)
    return incident, True


# ───────── Retenciones ─────────

def operation_fingerprint(user_id, account_id, kind, amount, target):
    """Huella de los datos de una operación: permite reconocer la misma operación al repetirla tras una autorización."""
    key = str(current_app.config['SECRET_KEY']).encode('utf-8')
    message = f'fraud-hold|{user_id}|{account_id}|{kind}|{Decimal(str(amount)):.2f}|{target}'.encode('utf-8')
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def create_hold(user, account, kind, amount, fingerprint, reason, evaluation, incident):
    now = now_utc()
    existing = (HeldOperation.query
                .filter(HeldOperation.user_id == user.id, HeldOperation.fingerprint == fingerprint,
                        HeldOperation.status == c.HOLD_HELD, HeldOperation.expires_at > now).first())
    if existing is not None:
        return existing
    hold = HeldOperation(user_id=user.id, account_id=account.id, evaluation_id=evaluation.id if evaluation is not None else None,
                         incident_id=incident.id if incident is not None else None, kind=kind,
                         amount=Decimal(str(amount)).quantize(Decimal('0.01')), fingerprint=fingerprint, reason=reason[:300],
                         expires_at=now + timedelta(hours=_cfg('FRAUD_HOLD_TTL_HOURS')))
    db.session.add(hold)
    db.session.flush()
    return hold


def find_authorization(user_id, fingerprint, now=None):
    """Autorización de un administrador vigente para esta misma operación (se consume una sola vez)."""
    now = now or now_utc()
    return (HeldOperation.query
            .filter(HeldOperation.user_id == user_id, HeldOperation.fingerprint == fingerprint,
                    HeldOperation.status == c.HOLD_AUTHORIZED, HeldOperation.expires_at > now)
            .order_by(HeldOperation.id.desc()).with_for_update().first())


def mark_executed(hold, transaction):
    hold.status = c.HOLD_EXECUTED
    hold.transaction_id = transaction.id


# ───────── Acciones administrativas ─────────

def _require_admin(actor, subject_user_id=None, *, permission):
    """El actor debe ser un administrador activo con el permiso concreto (ADMIN_GENERAL los tiene todos). Denegar por defecto."""
    if actor is None or not admins.has_permission(actor, permission):
        raise FraudPermissionError('No tienes permiso para realizar esta acción.')
    if subject_user_id is not None and actor.id == subject_user_id:
        raise FraudPermissionError('No puedes actuar sobre tu propio usuario.')


def clean_text(text, *, minimum=10, maximum=500, label='El motivo'):
    cleaned = re.sub(r'\s+', ' ', re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', ' ', str(text or ''))).strip()
    if len(cleaned) < minimum:
        raise FraudAdminError(f'{label} debe tener al menos {minimum} caracteres.')
    if len(cleaned) > maximum:
        raise FraudAdminError(f'{label} no puede superar {maximum} caracteres.')
    return cleaned


def _require_active_incident(incident):
    if incident.status not in c.INCIDENT_ACTIVE_STATUSES:
        raise FraudAdminError('Este incidente ya está cerrado.')


def start_investigation(incident, actor):
    _require_admin(actor, incident.user_id, permission=admins.P_FRAUD_INVESTIGATE)
    if incident.status != c.INCIDENT_OPEN:
        raise FraudAdminError('Solo se puede iniciar la investigación de un incidente abierto.')
    incident.status = c.INCIDENT_INVESTIGATING
    incident.updated_at = now_utc()
    add_event(incident, actor, 'INVESTIGACION_INICIADA', 'Incidente en investigación.')
    audit(actor, 'FRAUD_INVESTIGATION_STARTED')


def add_comment(incident, actor, text):
    _require_admin(actor, incident.user_id, permission=admins.P_FRAUD_INVESTIGATE)
    comment = clean_text(text, minimum=3, maximum=500, label='El comentario')
    incident.updated_at = now_utc()
    add_event(incident, actor, 'COMENTARIO', comment)
    audit(actor, 'FRAUD_COMMENT_ADDED')


def discard_incident(incident, actor, reason):
    """Descarta el incidente (falso positivo): levanta las restricciones preventivas y autoriza repetir lo retenido."""
    _require_admin(actor, incident.user_id, permission=admins.P_FRAUD_DECIDE)
    _require_active_incident(incident)
    reason = clean_text(reason)
    now = now_utc()
    incident.status = c.INCIDENT_DISCARDED
    incident.closed_at, incident.closed_by_id, incident.closure_reason = now, actor.id, reason
    incident.updated_at = now
    for restriction in SecurityRestriction.query.filter_by(incident_id=incident.id, origin=c.ORIGIN_PREVENTIVE,
                                                           status=c.RESTRICTION_ACTIVE).all():
        _lift(restriction, actor, f'Incidente descartado: {reason}'[:300], now)
    for hold in HeldOperation.query.filter_by(incident_id=incident.id, status=c.HOLD_HELD).all():
        hold.status, hold.reviewed_by_id, hold.reviewed_at = c.HOLD_AUTHORIZED, actor.id, now
        hold.review_reason = 'Incidente descartado (falso positivo): el cliente puede repetir la operación.'
        hold.expires_at = now + timedelta(hours=_cfg('FRAUD_HOLD_TTL_HOURS'))
    add_event(incident, actor, 'DESCARTADO', reason)
    audit(actor, 'FRAUD_INCIDENT_DISCARDED')


def suggested_sanction_days(user_id, exclude_incident_id=None):
    """Duración sugerida según la reincidencia **confirmada**. Las alertas sin verificar no cuentan."""
    query = db.session.query(func.count(FraudIncident.id)).filter(FraudIncident.user_id == user_id,
                                                                  FraudIncident.status == c.INCIDENT_CONFIRMED)
    if exclude_incident_id is not None:
        query = query.filter(FraudIncident.id != exclude_incident_id)
    previous = query.scalar() or 0
    steps = _cfg('FRAUD_SANCTION_DAYS_BY_RECIDIVISM')
    return steps[min(previous, len(steps) - 1)]


def confirm_fraud(incident, actor, reason, scopes, days):
    """Confirma el fraude (decisión humana) y aplica una sanción **temporal** con alcance y duración definidos."""
    _require_admin(actor, incident.user_id, permission=admins.P_FRAUD_DECIDE)
    _require_active_incident(incident)
    reason = clean_text(reason)
    scopes = sorted(set(scopes or ()))
    if not scopes or any(scope not in c.SCOPES for scope in scopes):
        raise FraudAdminError('Elige al menos un alcance válido para la sanción.')
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise FraudAdminError('La duración de la sanción no es válida.') from None
    if not 1 <= days <= _cfg('FRAUD_MAX_SANCTION_DAYS'):
        raise FraudAdminError(f'La sanción debe durar entre 1 y {_cfg("FRAUD_MAX_SANCTION_DAYS")} días; no hay sanciones permanentes.')
    user = db.session.get(User, incident.user_id)
    if user is None:
        raise FraudAdminError('El cliente del incidente ya no existe.')
    now = now_utc()
    incident.status, incident.level = c.INCIDENT_CONFIRMED, c.LEVEL_FRAUDE
    incident.confirmed_at, incident.confirmed_by_id, incident.confirmation_reason = now, actor.id, reason
    incident.updated_at = now
    restrictions = []
    for scope in scopes:
        restriction = SecurityRestriction(user_id=user.id, incident_id=incident.id, scope=scope, origin=c.ORIGIN_SANCTION,
                                          reason=reason[:300], starts_at=now, expires_at=now + timedelta(days=days),
                                          created_by_id=actor.id, rules_version=_cfg('FRAUD_RULES_VERSION'))
        db.session.add(restriction)
        restrictions.append(restriction)
    if c.SCOPE_ACCESS in scopes:
        revoke_sessions(user)
    for hold in HeldOperation.query.filter_by(incident_id=incident.id, status=c.HOLD_HELD).all():
        hold.status, hold.reviewed_by_id, hold.reviewed_at = c.HOLD_REJECTED, actor.id, now
        hold.review_reason = 'Fraude confirmado.'
    add_event(incident, actor, 'FRAUDE_CONFIRMADO', f'{reason} | Sanción: {", ".join(scopes)} durante {days} días.')
    audit(actor, 'FRAUD_INCIDENT_CONFIRMED')
    return restrictions


def _lift(restriction, actor, reason, now=None):
    restriction.status = c.RESTRICTION_LIFTED
    restriction.lifted_by_id, restriction.lifted_at, restriction.lift_reason = actor.id, now or now_utc(), reason[:300]
    if restriction.incident_id:
        incident = db.session.get(FraudIncident, restriction.incident_id)
        if incident is not None:
            add_event(incident, actor, 'RESTRICCION_LEVANTADA', f'{restriction.scope}: {reason}')


def lift_restriction(restriction, actor, reason):
    _require_admin(actor, restriction.user_id, permission=admins.P_FRAUD_RESTRICTIONS)
    if restriction.status != c.RESTRICTION_ACTIVE:
        raise FraudAdminError('La restricción ya no está activa.')
    _lift(restriction, actor, clean_text(reason, maximum=300))
    audit(actor, 'FRAUD_RESTRICTION_LIFTED')


def review_hold(hold, actor, reason, *, authorize):
    """Autoriza (el cliente podrá repetir la operación con todas sus validaciones) o rechaza una operación retenida."""
    _require_admin(actor, hold.user_id, permission=admins.P_FRAUD_HOLDS)
    if hold.status != c.HOLD_HELD:
        raise FraudAdminError('Esta retención ya fue revisada o venció.')
    now = now_utc()
    hold.status = c.HOLD_AUTHORIZED if authorize else c.HOLD_REJECTED
    hold.reviewed_by_id, hold.reviewed_at, hold.review_reason = actor.id, now, clean_text(reason, maximum=300)
    if authorize:
        hold.expires_at = now + timedelta(hours=_cfg('FRAUD_HOLD_TTL_HOURS'))
    if hold.incident_id:
        incident = db.session.get(FraudIncident, hold.incident_id)
        if incident is not None:
            add_event(incident, actor, 'RETENCION_AUTORIZADA' if authorize else 'RETENCION_RECHAZADA', hold.review_reason)
    audit(actor, 'FRAUD_HOLD_AUTHORIZED' if authorize else 'FRAUD_HOLD_REJECTED')
