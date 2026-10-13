"""Gestión de cuentas administrativas. Se registra sobre el mismo blueprint ``main``.

Seguridad (se comprueba en el servidor, no solo ocultando botones):

* Todas las rutas de ``/admin/cuentas`` exigen ``ADMIN_GENERAL`` activo (``admins.requires(general_only=True)``): un ``ADMINISTRADOR``,
  ``SOPORTE`` o ``CLIENTE`` recibe 403, también en los POST. Los identificadores de cuentas que no son administrativas dan 404.
* Todos los POST llevan CSRF (``CSRFProtect`` global). Las acciones críticas (crear, permisos, estado, sesiones) exigen además la
  **contraseña del propio actor** y escribir el identificador de la cuenta afectada; los fallos se limitan y se auditan.
* Nada de lo que se muestra o se registra contiene contraseñas, tokens ni códigos. Esta interfaz no puede alterar ni borrar la auditoría.
* ``/admin/activar/<token>`` es público (la persona aún no tiene contraseña) pero el enlace es de un solo uso, caduca y no revela nada si es inválido.
"""
from flask import abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from . import _home_for_role, bp
from ..models import User, db
from ..services import admin_service as admins
from ..services.email_service import EmailDeliveryError, send_admin_activation_link
from ..services.password_policy import validate_new_password


def _private(response):
    """Páginas con token: sin caché y sin enviar el enlace como Referer."""
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def _target_or_404(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.role not in admins.ADMIN_ROLES:
        abort(404)                         # un identificador de cliente no se distingue de uno inexistente
    return user


def _run(action, redirect_to, success=None):
    """Ejecuta una acción administrativa: errores de validación como aviso, falta de permiso como 403 y un solo commit."""
    try:
        result = action()
        db.session.commit()
    except admins.AdminPermissionError:
        db.session.rollback()
        abort(403)
    except admins.AdminError as error:
        db.session.rollback()
        flash(str(error), 'error')
        return redirect(redirect_to), None
    if success:
        flash(success, 'success')
    return redirect(redirect_to), result


def _confirm(expected):
    """Confirmación reforzada de una acción crítica. Devuelve un mensaje de error o ``None``."""
    try:
        admins.confirm_critical(current_user, request.form.get('password'), request.form.get('confirmacion'), expected)
    except admins.AdminError as error:
        return str(error)
    return None


def _send_activation(user, token):
    """Envía el enlace. Devuelve False (sin dejar rastro del token) si no pudo enviarse."""
    try:
        send_admin_activation_link(user.email, admins.activation_link(token), current_app.config['ADMIN_ACTIVATION_TTL_SECONDS'] // 3600)
    except (EmailDeliveryError, admins.AdminError):
        current_app.logger.error('No se pudo enviar el enlace de activación administrativo (usuario %s).', user.id)
        return False
    return True


@bp.route('/admin/cuentas')
@admins.requires(general_only=True)
def admin_accounts():
    accounts = admins.list_admins()
    return render_template('admin_accounts.html', accounts=accounts, permissions=admins.permissions_by_user(accounts), catalog=admins.PERMISSIONS)


@bp.route('/admin/cuentas/nueva', methods=['GET', 'POST'])
@admins.requires(general_only=True)
def admin_account_new():
    if request.method == 'GET':
        return render_template('admin_account_new.html', catalog=admins.PERMISSIONS, form={})
    form = {'full_name': request.form.get('full_name', ''), 'email': request.form.get('email', ''), 'permisos': request.form.getlist('permisos')}
    error = _confirm(form['email'].strip().lower())
    if error:
        flash(error, 'error')
        return render_template('admin_account_new.html', catalog=admins.PERMISSIONS, form=form), 400
    try:
        user, token = admins.create_admin(current_user, full_name=form['full_name'], email=form['email'], permissions=form['permisos'])
        db.session.flush()
        if not _send_activation(user, token):
            db.session.rollback()               # sin correo no se crea una cuenta que nadie podría activar
            flash('No se pudo enviar el correo de activación. La cuenta no se creó: revisa la configuración de correo.', 'error')
            return render_template('admin_account_new.html', catalog=admins.PERMISSIONS, form=form), 503
        db.session.commit()
    except admins.AdminPermissionError:
        db.session.rollback()
        abort(403)
    except admins.AdminError as error:
        db.session.rollback()
        flash(str(error), 'error')
        return render_template('admin_account_new.html', catalog=admins.PERMISSIONS, form=form), 400
    flash('Cuenta creada en estado pendiente. Se envió un enlace de activación al correo indicado; la persona define su propia contraseña.', 'success')
    return redirect(url_for('main.admin_account_detail', user_id=user.id))


@bp.route('/admin/cuentas/<int:user_id>')
@admins.requires(general_only=True)
def admin_account_detail(user_id):
    target = _target_or_404(user_id)
    return render_template('admin_account_detail.html', target=target, granted=set(admins.stored_permissions(target)), catalog=admins.PERMISSIONS,
                           history=admins.change_history(50, target_id=target.id), actions=admins.ACTIONS, is_self=target.id == current_user.id)


@bp.route('/admin/cuentas/<int:user_id>/permisos', methods=['POST'])
@admins.requires(general_only=True)
def admin_account_permissions(user_id):
    target = _target_or_404(user_id)
    back = url_for('main.admin_account_detail', user_id=target.id)
    error = _confirm(target.username)
    if error:
        flash(error, 'error')
        return redirect(back)
    response, _ = _run(lambda: admins.set_permissions(current_user, target, request.form.getlist('permisos')), back, 'Permisos actualizados.')
    return response


@bp.route('/admin/cuentas/<int:user_id>/estado', methods=['POST'])
@admins.requires(general_only=True)
def admin_account_status(user_id):
    target = _target_or_404(user_id)
    back = url_for('main.admin_account_detail', user_id=target.id)
    error = _confirm(target.username)
    if error:
        flash(error, 'error')
        return redirect(back)
    action = request.form.get('accion', '')
    response, _ = _run(lambda: admins.set_status(current_user, target, action), back, 'Estado actualizado. Las sesiones abiertas de la cuenta se revocaron si correspondía.')
    return response


@bp.route('/admin/cuentas/<int:user_id>/sesiones', methods=['POST'])
@admins.requires(general_only=True)
def admin_account_sessions(user_id):
    target = _target_or_404(user_id)
    back = url_for('main.admin_account_detail', user_id=target.id)
    error = _confirm(target.username)
    if error:
        flash(error, 'error')
        return redirect(back)
    response, _ = _run(lambda: admins.revoke_sessions_of(current_user, target), back, 'Sesiones revocadas: la persona deberá volver a iniciar sesión.')
    return response


@bp.route('/admin/cuentas/<int:user_id>/reenviar', methods=['POST'])
@admins.requires(general_only=True)
def admin_account_resend(user_id):
    target = _target_or_404(user_id)
    back = url_for('main.admin_account_detail', user_id=target.id)
    error = _confirm(target.username)
    if error:
        flash(error, 'error')
        return redirect(back)
    try:
        token = admins.resend_activation(current_user, target)
        db.session.flush()
        if not _send_activation(target, token):
            db.session.rollback()
            flash('No se pudo enviar el correo de activación.', 'error')
            return redirect(back)
        db.session.commit()
    except admins.AdminPermissionError:
        db.session.rollback()
        abort(403)
    except admins.AdminError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(back)
    flash('Enlace de activación reenviado.', 'success')
    return redirect(back)


@bp.route('/admin/cuentas/<int:user_id>/restablecer', methods=['POST'])
@admins.requires(general_only=True)
def admin_account_reset(user_id):
    target = _target_or_404(user_id)
    back = url_for('main.admin_account_detail', user_id=target.id)
    error = _confirm(target.username)
    if error:
        flash(error, 'error')
        return redirect(back)
    try:
        token = admins.reset_access(current_user, target)
        db.session.flush()
        if not _send_activation(target, token):
            db.session.rollback()               # sin correo no se restablece: la cuenta sigue como estaba
            flash('No se pudo enviar el correo de activación: no se cambió nada.', 'error')
            return redirect(back)
        db.session.commit()
    except admins.AdminPermissionError:
        db.session.rollback()
        abort(403)
    except admins.AdminError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return redirect(back)
    flash('Acceso restablecido: la contraseña anterior ya no sirve y se enviaron las instrucciones a la persona. Cuando defina su contraseña, activa la cuenta.', 'success')
    return redirect(back)


@bp.route('/admin/cuentas/historial')
@admins.requires(general_only=True)
def admin_account_history():
    return render_template('admin_account_history.html', changes=admins.change_history(200), activity=admins.admin_activity(100))


@bp.route('/admin/activar/<token>', methods=['GET', 'POST'])
def admin_activate(token):
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    if admins.find_valid_activation(token) is None:
        return _private(make_response(render_template('admin_activate.html', invalid=True), 400))
    if request.method != 'POST':
        return _private(make_response(render_template('admin_activate.html', invalid=False)))
    password, confirmation = request.form.get('password', ''), request.form.get('password_confirmation', '')
    errors, failed = validate_new_password(password, confirmation)
    if errors:
        for message in errors:
            flash(message, 'error')
        return _private(make_response(render_template('admin_activate.html', invalid=False, password_failed=failed), 400))
    user = admins.complete_activation(token, password)
    if user is None:
        return _private(make_response(render_template('admin_activate.html', invalid=True), 400))
    if user.account_status == admins.STATUS_ACTIVE:
        flash('Tu contraseña quedó definida y tu cuenta está activa. Inicia sesión: te enviaremos un código de seguridad por correo.', 'success')
    else:
        flash('Tu contraseña quedó definida. Un Administrador General debe activar tu cuenta antes de que puedas iniciar sesión.', 'success')
    return redirect(url_for('main.login'))
