"""Roles administrativos, permisos y gestión de cuentas privilegiadas.

Roles (columna ``users.role``; no se crean roles duplicados):

* ``ADMIN_GENERAL``: responsable de las cuentas administrativas. Tiene todos los permisos por definición del rol, crea cuentas
  ``ADMINISTRADOR``, concede y retira permisos, y suspende, reactiva o desactiva cuentas. No puede alterar ni borrar la auditoría.
* ``ADMINISTRADOR`` (el «ADMIN» del encargo; se conserva el nombre existente): solo hace lo que sus permisos le autorizan.
  **Denegar por defecto**: sin permiso concedido no puede nada salvo entrar a la vista general. No crea administradores, no cambia roles ni permisos
  y no se los puede conceder a sí mismo.
* ``CLIENTE``: sin ninguna capacidad administrativa. ``SOPORTE`` (preexistente) tampoco tiene permisos administrativos.

Una cuenta administrativa solo es efectiva si está ``ACTIVA``. Todas las comprobaciones se hacen **en el servidor**, en cada ruta y en cada acción;
ocultar botones en la interfaz es solo comodidad. Nadie se promociona automáticamente: el primer ``ADMIN_GENERAL`` se aprovisiona con
``flask admin-bootstrap`` (ver ``docs/roles-administrativos.md``) y los ``ADMINISTRADOR`` los crea un ``ADMIN_GENERAL``.

Nunca se registran ni se devuelven contraseñas, tokens ni códigos. Cada cambio queda en ``admin_change_log`` (solo se inserta) y en la auditoría.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, current_app
from flask_login import current_user, login_required
from sqlalchemy import func, update

from ..models import AdminActivationToken, AdminChangeLog, AdminPermission, AuditLog, LoginChallenge, User, db, now_utc

ROLE_GENERAL = 'ADMIN_GENERAL'
ROLE_ADMIN = 'ADMINISTRADOR'
ROLE_CLIENT = 'CLIENTE'
ADMIN_ROLES = (ROLE_GENERAL, ROLE_ADMIN)

STATUS_ACTIVE = 'ACTIVA'
STATUS_PENDING = 'PENDIENTE'
STATUS_SUSPENDED = 'SUSPENDIDA'
STATUS_DEACTIVATED = 'DESACTIVADA'

# Permisos asignables a un ADMINISTRADOR. Los de acción implican poder consultar el panel antifraude.
P_FRAUD_VIEW = 'fraude.consultar'
P_FRAUD_INVESTIGATE = 'fraude.investigar'
P_FRAUD_DECIDE = 'fraude.decidir'
P_FRAUD_HOLDS = 'fraude.resolver_retenciones'
P_FRAUD_RESTRICTIONS = 'fraude.gestionar_restricciones'
P_FRAUD_EXPORT = 'fraude.exportar'
P_AUDIT = 'auditoria.consultar'
P_CAPTURES = 'capturas.consultar'

PERMISSIONS = {
    P_FRAUD_VIEW: 'Consultar el panel antifraude: incidentes, evaluaciones, restricciones, retenciones y modelos',
    P_FRAUD_INVESTIGATE: 'Investigar incidentes antifraude y comentar',
    P_FRAUD_DECIDE: 'Descartar o confirmar un incidente (la confirmación impone una sanción temporal)',
    P_FRAUD_HOLDS: 'Autorizar o rechazar operaciones retenidas',
    P_FRAUD_RESTRICTIONS: 'Levantar restricciones temporales',
    P_FRAUD_EXPORT: 'Descargar los informes CSV antifraude',
    P_AUDIT: 'Consultar la auditoría antifraude y descargarla',
    P_CAPTURES: 'Ver las capturas de seguridad de las operaciones (datos sensibles de privacidad)',
}
_IMPLY_VIEW = frozenset({P_FRAUD_INVESTIGATE, P_FRAUD_DECIDE, P_FRAUD_HOLDS, P_FRAUD_RESTRICTIONS, P_FRAUD_EXPORT})

ACTIONS = {
    'activar': ({STATUS_PENDING}, STATUS_ACTIVE),
    'suspender': ({STATUS_ACTIVE}, STATUS_SUSPENDED),
    'reactivar': ({STATUS_SUSPENDED}, STATUS_ACTIVE),
    'desactivar': ({STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_PENDING}, STATUS_DEACTIVATED),
}
_TOKEN_FORMAT = re.compile(r'[A-Za-z0-9_-]{43}')                       # secrets.token_urlsafe(32)
_EMAIL_FORMAT = re.compile(r"[^@\s<>\"]{1,64}@[^@\s<>\"]{1,255}\.[^@\s<>\"]{2,}")
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


class AdminError(ValueError):
    """Dato o estado inválido en una acción administrativa; el mensaje se puede mostrar a quien la pidió."""


class AdminPermissionError(PermissionError):
    """El actor no está autorizado para la acción."""


def _cfg(key):
    return current_app.config[key]


# ───────── Roles y permisos ─────────

def _is_active(user):
    return user is not None and getattr(user, 'account_status', None) == STATUS_ACTIVE


def is_general(user):
    """ADMIN_GENERAL con la cuenta activa."""
    return user is not None and getattr(user, 'role', None) == ROLE_GENERAL and _is_active(user)


def is_admin(user):
    """Cualquier cuenta administrativa activa (ADMIN_GENERAL o ADMINISTRADOR)."""
    return user is not None and getattr(user, 'role', None) in ADMIN_ROLES and _is_active(user)


def stored_permissions(user):
    return {row.permission for row in AdminPermission.query.filter_by(user_id=user.id).all()}


def effective_permissions(user):
    if is_general(user):
        return set(PERMISSIONS)
    if not is_admin(user):
        return set()
    granted = stored_permissions(user) & set(PERMISSIONS)
    if granted & _IMPLY_VIEW:
        granted.add(P_FRAUD_VIEW)
    return granted


def has_permission(user, permission):
    return permission in effective_permissions(user)


def requires(permission=None, *, general_only=False):
    """Decorador de rutas: sesión + comprobación en el servidor en cada petición (GET o POST). Deniega por defecto con 403."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if general_only:
                allowed = is_general(current_user)
            elif permission is None:
                allowed = is_admin(current_user)
            else:
                allowed = has_permission(current_user, permission)
            if not allowed:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def require_general(actor):
    if not is_general(actor):
        raise AdminPermissionError('Solo un Administrador General activo puede realizar esta acción.')


def normalize_permissions(codes):
    values = sorted({str(code).strip() for code in (codes or []) if str(code).strip()})
    unknown = [code for code in values if code not in PERMISSIONS]
    if unknown:
        raise AdminError('Permiso desconocido: ' + ', '.join(unknown)[:80])
    return values


# ───────── Registro de cambios ─────────

def record_change(actor, target, action, detail=None, result='COMPLETADA'):
    """Historial de cambios administrativos (solo inserta) y entrada de auditoría. ``detail`` no admite secretos."""
    db.session.add(AdminChangeLog(actor_id=actor.id if actor is not None else None, target_id=target.id if target is not None else None,
                                  action=action, detail=detail or {}))
    db.session.add(AuditLog(user_id=actor.id if actor is not None else None, action='ADMIN_' + action, module='ADMIN', result=result))


def confirm_critical(actor, password, typed, expected):
    """Confirmación reforzada de una acción crítica: contraseña del propio actor **y** escribir el identificador de la cuenta afectada.

    Los fallos se registran y se limitan (5 en 15 minutos por actor) para impedir tantear la contraseña desde una sesión robada."""
    window = now_utc() - timedelta(seconds=_cfg('ADMIN_STEPUP_WINDOW_SECONDS'))
    failures = AuditLog.query.filter(AuditLog.user_id == actor.id, AuditLog.action == 'ADMIN_STEPUP_FAILED', AuditLog.created_at >= window).count()
    if failures >= _cfg('ADMIN_STEPUP_MAX_FAILURES'):
        raise AdminError('Demasiados intentos de confirmación fallidos. Inténtalo más tarde.')
    ok_password = isinstance(password, str) and bool(password) and actor.check_password(password)
    ok_typed = isinstance(typed, str) and typed.strip().lower() == str(expected).strip().lower() and bool(typed.strip())
    if not (ok_password and ok_typed):
        db.session.add(AuditLog(user_id=actor.id, action='ADMIN_STEPUP_FAILED', module='ADMIN', result='RECHAZADA'))
        db.session.commit()                       # el fallo queda registrado aunque la acción se cancele
        raise AdminError('La confirmación no coincide: revisa tu contraseña y el identificador escrito.')


# ───────── Altas y activación ─────────

def _clean_name(value):
    name = ' '.join(_CONTROL.sub(' ', str(value or '')).split())
    if not 2 <= len(name) <= 100:
        raise AdminError('El nombre debe tener entre 2 y 100 caracteres.')
    return name


def _clean_email(value):
    email = str(value or '').strip().lower()
    if len(email) > 254 or not _EMAIL_FORMAT.fullmatch(email):
        raise AdminError('Ingresa una dirección de correo electrónico válida.')
    return email


def _new_username():
    while True:
        candidate = 'adm-' + secrets.token_hex(4)
        if User.query.filter_by(username=candidate).first() is None:
            return candidate


def token_hash(token):
    key = str(_cfg('SECRET_KEY')).encode('utf-8')
    return hmac.new(key, ('admin-activation:' + token).encode('utf-8'), hashlib.sha256).hexdigest()


def issue_activation_token(user, created_by, *, auto_activate=False):
    """Crea un enlace de un solo uso (deja sin efecto los anteriores). Devuelve el token en claro: solo debe viajar por correo."""
    now = now_utc()
    db.session.execute(update(AdminActivationToken).where(AdminActivationToken.user_id == user.id, AdminActivationToken.used_at.is_(None)).values(used_at=now))
    token = secrets.token_urlsafe(32)
    db.session.add(AdminActivationToken(user_id=user.id, token_hash=token_hash(token), created_by_id=created_by.id if created_by is not None else None,
                                        auto_activate=auto_activate, created_at=now, expires_at=now + timedelta(seconds=_cfg('ADMIN_ACTIVATION_TTL_SECONDS'))))
    return token


def activation_link(token):
    """Enlace público de activación. En producción exige PUBLIC_BASE_URL (no se construye a partir del encabezado Host)."""
    base = current_app.config.get('PUBLIC_BASE_URL')
    if not base:
        if current_app.config.get('APP_ENV') == 'production':
            raise AdminError('Falta PUBLIC_BASE_URL: no se puede construir el enlace de activación.')
        base = 'http://localhost:5000'
    return f'{base}/admin/activar/{token}'


def find_valid_activation(token):
    if not isinstance(token, str) or not _TOKEN_FORMAT.fullmatch(token):
        return None
    record = AdminActivationToken.query.filter_by(token_hash=token_hash(token)).first()
    if record is None or record.used_at is not None or record.expires_at <= now_utc():
        return None
    user = db.session.get(User, record.user_id)
    return record if user is not None and user.role in ADMIN_ROLES and user.account_status == STATUS_PENDING else None


def complete_activation(token, new_password):
    """Consume el enlace y fija la contraseña elegida por la propia persona, de forma atómica. Devuelve el usuario o ``None``.

    La política de contraseña debe validarse antes. Si el enlace era de aprovisionamiento inicial (``auto_activate``) la cuenta pasa a ACTIVA;
    en los demás casos queda PENDIENTE hasta que un ADMIN_GENERAL la active (control de dos personas)."""
    if not isinstance(token, str) or not _TOKEN_FORMAT.fullmatch(token):
        return None
    now = now_utc()
    digest = token_hash(token)
    claimed = db.session.execute(update(AdminActivationToken).where(AdminActivationToken.token_hash == digest, AdminActivationToken.used_at.is_(None),
                                                                    AdminActivationToken.expires_at > now).values(used_at=now))
    if claimed.rowcount != 1:
        db.session.rollback()
        return None
    record = AdminActivationToken.query.filter_by(token_hash=digest).one()
    user = db.session.get(User, record.user_id)
    if user is None or user.role not in ADMIN_ROLES or user.account_status != STATUS_PENDING:
        db.session.rollback()
        return None
    user.set_password(new_password)
    user.password_changed_at = now                       # invalida cualquier sesión previa (ver User.session_stamp)
    db.session.execute(update(AdminActivationToken).where(AdminActivationToken.user_id == user.id, AdminActivationToken.used_at.is_(None)).values(used_at=now))
    LoginChallenge.query.filter_by(user_id=user.id).delete()
    if record.auto_activate:
        user.account_status = STATUS_ACTIVE
        record_change(user, user, 'ACTIVADA_AL_DEFINIR_CONTRASENA', {'rol': user.role})
    else:
        record_change(user, user, 'CONTRASENA_DEFINIDA', {'estado': user.account_status})
    db.session.commit()
    return user


def create_admin(actor, *, full_name, email, permissions=()):
    """ADMIN_GENERAL crea una cuenta ADMINISTRADOR en estado PENDIENTE. Nadie conoce su contraseña: la persona la define desde un enlace por correo.

    Devuelve ``(usuario, token)``; el token solo se usa para el correo y no se registra."""
    require_general(actor)
    name, address, codes = _clean_name(full_name), _clean_email(email), normalize_permissions(permissions)
    if User.query.filter(func.lower(User.email) == address).first() is not None:
        raise AdminError('Ya existe una cuenta con ese correo.')
    user = User(username=_new_username(), full_name=name, email=address, role=ROLE_ADMIN, account_status=STATUS_PENDING)
    user.set_password(secrets.token_urlsafe(32))         # contraseña aleatoria que nadie conoce: no sirve para entrar
    db.session.add(user)
    db.session.flush()
    for code in codes:
        db.session.add(AdminPermission(user_id=user.id, permission=code, granted_by_id=actor.id))
    token = issue_activation_token(user, actor)
    record_change(actor, user, 'CUENTA_CREADA', {'rol': ROLE_ADMIN, 'permisos': codes})
    return user, token


def resend_activation(actor, target):
    require_general(actor)
    target = _admin_target(target)
    if target.account_status != STATUS_PENDING:
        raise AdminError('Solo se puede reenviar el enlace a una cuenta pendiente.')
    token = issue_activation_token(target, actor)
    record_change(actor, target, 'ENLACE_REENVIADO', {})
    return token


def bootstrap_general(full_name, email):
    """Aprovisionamiento inicial (solo desde la línea de órdenes del servidor): crea el primer ADMIN_GENERAL en estado PENDIENTE.

    No promociona a ningún usuario existente (el correo no puede pertenecer a una cuenta previa) y se niega si ya hay un ADMIN_GENERAL
    activo o pendiente. La propia persona define su contraseña desde el enlace que recibe en su correo; al hacerlo la cuenta pasa a ACTIVA."""
    name, address = _clean_name(full_name), _clean_email(email)
    if User.query.filter(User.role == ROLE_GENERAL, User.account_status.in_((STATUS_ACTIVE, STATUS_PENDING))).first() is not None:
        raise AdminError('Ya existe un Administrador General activo o pendiente: el aprovisionamiento inicial no se repite.')
    if User.query.filter(func.lower(User.email) == address).first() is not None:
        raise AdminError('Ya existe una cuenta con ese correo: no se promueven cuentas existentes.')
    user = User(username=_new_username(), full_name=name, email=address, role=ROLE_GENERAL, account_status=STATUS_PENDING)
    user.set_password(secrets.token_urlsafe(32))
    db.session.add(user)
    db.session.flush()
    token = issue_activation_token(user, None, auto_activate=True)
    record_change(None, user, 'ADMIN_GENERAL_APROVISIONADO', {'rol': ROLE_GENERAL})
    return user, token


# ───────── Permisos, estado y sesiones ─────────

def _admin_target(target):
    user = db.session.get(User, target) if isinstance(target, int) else target
    if user is None or user.role not in ADMIN_ROLES:
        raise AdminError('La cuenta indicada no es una cuenta administrativa.')
    return user


def _not_self(actor, target, what):
    if actor.id == target.id:
        raise AdminPermissionError(f'No puedes {what} sobre tu propia cuenta.')


def set_permissions(actor, target, permissions):
    """Sustituye el conjunto de permisos de un ADMINISTRADOR. El ADMIN_GENERAL ya los tiene todos y nadie se los cambia a sí mismo."""
    require_general(actor)
    target = _admin_target(target)
    _not_self(actor, target, 'cambiar permisos')
    if target.role != ROLE_ADMIN:
        raise AdminError('El Administrador General tiene todos los permisos por definición del rol.')
    new, old = set(normalize_permissions(permissions)), stored_permissions(target)
    for code in sorted(new - old):
        db.session.add(AdminPermission(user_id=target.id, permission=code, granted_by_id=actor.id))
    if old - new:
        AdminPermission.query.filter(AdminPermission.user_id == target.id, AdminPermission.permission.in_(sorted(old - new))).delete(synchronize_session=False)
    if new != old:
        record_change(actor, target, 'PERMISOS_ACTUALIZADOS', {'antes': sorted(old), 'despues': sorted(new)})
    return sorted(new)


def revoke_sessions(target):
    """Invalida todas las sesiones abiertas de la cuenta (la comprobación ocurre en cada petición) y cualquier código de acceso pendiente."""
    target.sessions_revoked_at = now_utc()
    LoginChallenge.query.filter_by(user_id=target.id).delete()


def _other_active_generals_query(target):
    """Los demás ADMIN_GENERAL activos, con las filas bloqueadas (``FOR UPDATE``) para que dos suspensiones simultáneas no dejen el sistema sin ninguno.

    No se usa ``.count()``: PostgreSQL rechaza ``SELECT count(*) ... FOR UPDATE`` (SQLite lo ignora, por eso lo comprueba una prueba con el dialecto de PostgreSQL)."""
    return User.query.filter(User.role == ROLE_GENERAL, User.account_status == STATUS_ACTIVE, User.id != target.id).with_for_update()


def ensure_another_active_general(target):
    """Nunca se deja el sistema sin un ADMIN_GENERAL activo."""
    others = _other_active_generals_query(target).all()
    if not others:
        raise AdminError('No se puede suspender ni desactivar al último Administrador General activo.')


def set_status(actor, target, action):
    require_general(actor)
    target = _admin_target(target)
    _not_self(actor, target, 'cambiar el estado')
    if action not in ACTIONS:
        raise AdminError('Acción desconocida.')
    allowed_from, new_status = ACTIONS[action]
    if target.account_status not in allowed_from:
        raise AdminError(f'No se puede {action} una cuenta {target.account_status.lower()}.')
    if action == 'activar' and target.password_changed_at is None:
        raise AdminError('La persona aún no definió su contraseña con el enlace de activación; no se puede activar todavía.')
    if target.role == ROLE_GENERAL and action in ('suspender', 'desactivar') and target.account_status == STATUS_ACTIVE:
        ensure_another_active_general(target)
    previous = target.account_status
    target.account_status = new_status
    if new_status in (STATUS_SUSPENDED, STATUS_DEACTIVATED):
        revoke_sessions(target)
    if new_status == STATUS_DEACTIVATED:
        db.session.execute(update(AdminActivationToken).where(AdminActivationToken.user_id == target.id, AdminActivationToken.used_at.is_(None)).values(used_at=now_utc()))
    record_change(actor, target, 'ESTADO_' + new_status, {'accion': action, 'antes': previous, 'despues': new_status})
    return new_status


def reset_access(actor, target):
    """Restablece el acceso de una cuenta (contraseña olvidada o cuenta comprometida) sin crear otra: vuelve a PENDIENTE, la contraseña anterior deja de servir,
    se revocan las sesiones y se emite un enlace para que la propia persona defina una nueva. Un ADMIN_GENERAL debe volver a activarla (control de dos personas).

    Devuelve el token (solo para el correo)."""
    require_general(actor)
    target = _admin_target(target)
    _not_self(actor, target, 'restablecer el acceso')
    if target.account_status not in (STATUS_ACTIVE, STATUS_SUSPENDED):
        raise AdminError('Solo se puede restablecer el acceso de una cuenta activa o suspendida (a una pendiente se le reenvía el enlace).')
    if target.role == ROLE_GENERAL and target.account_status == STATUS_ACTIVE:
        ensure_another_active_general(target)
    previous = target.account_status
    target.account_status = STATUS_PENDING
    target.set_password(secrets.token_urlsafe(32))              # la contraseña anterior deja de servir
    target.password_changed_at = None                           # hasta que la persona defina una nueva no se puede reactivar
    revoke_sessions(target)
    token = issue_activation_token(target, actor)
    record_change(actor, target, 'ACCESO_RESTABLECIDO', {'antes': previous})
    return token


def revoke_sessions_of(actor, target):
    require_general(actor)
    target = _admin_target(target)
    _not_self(actor, target, 'revocar sesiones (usa «Cerrar sesión»)')
    revoke_sessions(target)
    record_change(actor, target, 'SESIONES_REVOCADAS', {})


# ───────── Consultas para los paneles ─────────

def list_admins():
    return User.query.filter(User.role.in_(ADMIN_ROLES)).order_by(User.created_at.desc(), User.id.desc()).all()


def permissions_by_user(users):
    ids = [user.id for user in users]
    result = {user_id: [] for user_id in ids}
    if ids:
        for row in AdminPermission.query.filter(AdminPermission.user_id.in_(ids)).order_by(AdminPermission.permission).all():
            result[row.user_id].append(row.permission)
    return result


def change_history(limit=200, target_id=None):
    query = AdminChangeLog.query
    if target_id is not None:
        query = query.filter(AdminChangeLog.target_id == target_id)
    return query.order_by(AdminChangeLog.created_at.desc(), AdminChangeLog.id.desc()).limit(limit).all()


def admin_activity(limit=100):
    """Acciones auditadas realizadas por cuentas administrativas (para revisar lo que hacen otros administradores)."""
    return (AuditLog.query.join(User, User.id == AuditLog.user_id).filter(User.role.in_(ADMIN_ROLES))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all())
