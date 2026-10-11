"""Rutas de recuperación de contraseña (solo clientes). Se registran sobre el mismo blueprint ``main``.

- ``/recuperar``: el cliente indica su correo. La respuesta pública es idéntica exista o no la cuenta, sea cliente
  o no, y esté o no limitada la solicitud (evita enumerar cuentas y roles).
- ``/recuperar/<token>``: valida el enlace y permite fijar una contraseña nueva con la misma política que el registro.
"""
import re
from datetime import timedelta

from flask import current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from . import _home_for_role, _recent_audit_count, _request_ip_hash, bp
from ..models import AuditLog, User, db, now_utc
from ..services import password_reset_service as resets
from ..services.email_service import EmailDeliveryError, send_password_reset_link
from ..services.password_policy import validate_new_password

GENERIC_REQUEST_MESSAGE = ('Si el correo corresponde a una cuenta de cliente de Norte, te enviaremos un enlace para '
                           'restablecer la contraseña. Revisa tu bandeja de entrada y la carpeta de correo no deseado. '
                           'Si ya pediste uno hace poco, espera unos minutos antes de intentarlo otra vez.')
INVALID_LINK_MESSAGE = 'El enlace no es válido, ya se usó o venció. Solicita uno nuevo para continuar.'
_EMAIL_FORMAT = re.compile(r"[^@\s<>\"]{1,64}@[^@\s<>\"]{1,255}\.[^@\s<>\"]{2,}")


def _private_page(response):
    """Páginas con token: sin caché y sin enviar el enlace como Referer."""
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _reset_link(token):
    path = url_for('main.reset_password', token=token)
    base = current_app.config.get('PUBLIC_BASE_URL')
    if base:
        return base + path
    if current_app.config.get('APP_ENV') == 'production':
        return None  # sin base pública no se construye el enlace a partir del encabezado Host
    return url_for('main.reset_password', token=token, _external=True)


def _deliver_reset_email(user):
    """Crea el token y envía el correo. Devuelve False si no pudo enviarse (el token se descarta)."""
    token = resets.create_token(user)
    link = _reset_link(token)
    if link is None:
        db.session.rollback()
        current_app.logger.error('Recuperación de contraseña omitida: falta PUBLIC_BASE_URL en producción.')
        return False
    try:
        db.session.flush()
        send_password_reset_link(user.email, link, current_app.config['PASSWORD_RESET_TTL_SECONDS'] // 60)
    except EmailDeliveryError:
        db.session.rollback()
        current_app.logger.error('No se pudo enviar el correo de recuperación (usuario %s).', user.id)
        return False
    db.session.add(AuditLog(user_id=user.id, action='PASSWORD_RESET_REQUESTED', module='ACCESO', result='ENVIADO',
                            ip_local=_request_ip_hash('password-reset-ip'), created_at=now_utc()))
    return True


@bp.route('/recuperar', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    if request.method != 'POST':
        return render_template('forgot_password.html', sent=False)

    email = request.form.get('email', '').strip().lower()
    if len(email) > 254 or not _EMAIL_FORMAT.fullmatch(email):
        flash('Ingresa una dirección de correo electrónico válida.', 'error')
        return render_template('forgot_password.html', sent=False), 400

    now = now_utc()
    window = timedelta(seconds=current_app.config['PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS'])
    ip_hash = _request_ip_hash('password-reset-ip')
    # Todas las solicitudes cuentan contra la conexión, incluidas las de cuentas inexistentes.
    attempts = _recent_audit_count('PASSWORD_RESET_ATTEMPT', now - window, ip_hash=ip_hash)
    db.session.add(AuditLog(action='PASSWORD_RESET_ATTEMPT', module='ACCESO', result='RECIBIDA',
                            ip_local=ip_hash, created_at=now))
    db.session.commit()  # el intento queda registrado aunque el envío falle
    if attempts < current_app.config['PASSWORD_RESET_RATE_LIMIT_IP']:
        user = User.query.filter_by(email=email).first()
        if resets.is_eligible(user) and _recent_audit_count(
            'PASSWORD_RESET_REQUESTED', now - window, user_id=user.id
        ) < current_app.config['PASSWORD_RESET_RATE_LIMIT_USER']:
            _deliver_reset_email(user)
    db.session.commit()
    flash(GENERIC_REQUEST_MESSAGE, 'info')
    return render_template('forgot_password.html', sent=True)


@bp.route('/recuperar/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    if resets.find_valid_token(token) is None:
        return _private_page(make_response(render_template('reset_password.html', invalid=True), 400))
    if request.method != 'POST':
        return _private_page(make_response(render_template('reset_password.html', invalid=False)))

    password = request.form.get('password', '')
    confirmation = request.form.get('password_confirmation', '')
    errors, failed = validate_new_password(password, confirmation)
    if errors:
        for error in errors:
            flash(error, 'error')
        return _private_page(make_response(
            render_template('reset_password.html', invalid=False, password_failed=failed), 400))
    if not resets.reset_password(token, password):
        return _private_page(make_response(render_template('reset_password.html', invalid=True), 400))
    flash('Tu contraseña se actualizó. Inicia sesión con la nueva contraseña; te enviaremos un código de seguridad.',
          'success')
    return redirect(url_for('main.login'))
