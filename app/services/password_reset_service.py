"""Recuperación de contraseña exclusiva para clientes: tokens de un solo uso con caducidad breve.

- El token (256 bits) solo viaja por correo; en la base de datos se guarda su HMAC-SHA256.
- Solo cuentas con rol CLIENTE y correo registrado son elegibles, tanto al emitir como al consumir el token.
- El consumo es atómico: ``UPDATE ... WHERE used_at IS NULL AND expires_at > now`` y el cambio de contraseña
  ocurren en la misma transacción. Si dos solicitudes llegan a la vez, solo una afecta una fila.
- Nunca se registran ni se devuelven tokens ni contraseñas.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import update

from ..models import AuditLog, LoginChallenge, PasswordResetToken, User, db, now_utc

CUSTOMER_ROLE = 'CLIENTE'
_TOKEN_FORMAT = re.compile(r'[A-Za-z0-9_-]{43}')  # secrets.token_urlsafe(32)


def token_hash(token):
    key = str(current_app.config['SECRET_KEY']).encode('utf-8')
    return hmac.new(key, ('password-reset:' + token).encode('utf-8'), hashlib.sha256).hexdigest()


def is_eligible(user):
    """Solo clientes con correo registrado pueden usar el flujo de recuperación."""
    return user is not None and user.role == CUSTOMER_ROLE and bool(user.email)


def create_token(user):
    """Crea un token nuevo y deja sin efecto los anteriores sin usar. Devuelve el token en claro (solo para el correo)."""
    if not is_eligible(user):
        raise ValueError('La cuenta no es elegible para la recuperación de contraseña.')
    now = now_utc()
    db.session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    db.session.add(PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash(token),
        created_at=now,
        expires_at=now + timedelta(seconds=current_app.config['PASSWORD_RESET_TTL_SECONDS']),
    ))
    return token


def find_valid_token(token):
    """Devuelve el registro si el token existe, no caducó, no se usó y su dueño sigue siendo un cliente."""
    if not isinstance(token, str) or not _TOKEN_FORMAT.fullmatch(token):
        return None
    record = PasswordResetToken.query.filter_by(token_hash=token_hash(token)).first()
    if record is None or record.used_at is not None or record.expires_at <= now_utc():
        return None
    return record if is_eligible(db.session.get(User, record.user_id)) else None


def reset_password(token, new_password):
    """Consume el token y fija la contraseña nueva de forma atómica. Devuelve True solo si se completó.

    La política de contraseña debe validarse antes de llamar a esta función.
    """
    if not isinstance(token, str) or not _TOKEN_FORMAT.fullmatch(token):
        return False
    now = now_utc()
    digest = token_hash(token)
    claimed = db.session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == digest,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        return False
    record = PasswordResetToken.query.filter_by(token_hash=digest).one()
    user = db.session.get(User, record.user_id)
    if not is_eligible(user):
        db.session.rollback()  # el token vuelve a su estado anterior; no sirve para otros roles
        return False
    user.set_password(new_password)
    user.password_changed_at = now  # invalida las sesiones abiertas (ver session_stamp)
    db.session.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    LoginChallenge.query.filter_by(user_id=user.id).delete()  # cualquier código de acceso pendiente deja de valer
    db.session.add(AuditLog(user_id=user.id, action='PASSWORD_RESET_COMPLETED', module='ACCESO',
                            result='COMPLETADO', created_at=now))
    db.session.commit()
    return True
