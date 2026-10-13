"""Panel administrativo de fraude. Se registra sobre el mismo blueprint ``main``.

Seguridad (se comprueba en el servidor, no solo ocultando botones):

* Solo el rol ``ADMINISTRADOR`` entra a cualquier ruta de este módulo (``SOPORTE`` y ``CLIENTE`` reciben 403, también en los POST).
* Todos los POST llevan CSRF (``CSRFProtect`` global) y se auditan; el servicio vuelve a comprobar el rol y que el administrador
  no actúe sobre su propio usuario.
* Un cliente nunca ve ni modifica su nivel de riesgo, sus incidentes ni sus restricciones.
* Los mensajes de error no incluyen datos personales: solo identificadores y nombres de usuario.
"""
import re
from datetime import date, datetime, time, timedelta
from functools import wraps

from flask import Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc

from . import bp
from ..fraud import anomaly, constants as c, export, metrics, service
from ..fraud.service import FraudAdminError, FraudPermissionError
from ..models import AuditLog, FraudIncident, FraudModelVersion, HeldOperation, RiskEvaluation, SecurityRestriction, db, now_utc

PAGE_LIMIT = 100


def admin_only(view):
    """Exige sesión y rol ADMINISTRADOR. Se comprueba en cada petición, GET o POST."""
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != c.ADMIN_ROLE:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def _date_arg(name):
    raw = request.args.get(name, '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        abort(400)


def _run(action, redirect_to):
    """Ejecuta una acción administrativa: errores de validación como aviso, falta de permiso como 403, y un solo commit."""
    try:
        action()
        db.session.commit()
    except FraudPermissionError:
        db.session.rollback()
        abort(403)
    except FraudAdminError as error:
        db.session.rollback()
        flash(str(error), 'error')
        return redirect(redirect_to)
    return None


@bp.route('/admin/fraude')
@admin_only
def fraud_dashboard():
    service.expire_stale()
    db.session.commit()
    status = request.args.get('estado', '').strip().upper()
    level = request.args.get('nivel', '').strip().upper()
    category = request.args.get('tipo', '').strip().upper()
    if (status and status not in c.INCIDENT_STATUSES) or (level and level not in (c.LEVEL_SOSPECHOSO, c.LEVEL_FRAUDE)) \
            or (category and category not in (c.CATEGORY_TRANSACTIONAL, c.CATEGORY_ACCESS)):
        abort(400)
    start, end = _date_arg('desde'), _date_arg('hasta')
    if start and end and start > end:
        abort(400)
    query = FraudIncident.query
    if status:
        query = query.filter(FraudIncident.status == status)
    if level:
        query = query.filter(FraudIncident.level == level)
    if category:
        query = query.filter(FraudIncident.category == category)
    if start:
        query = query.filter(FraudIncident.created_at >= datetime.combine(start, time.min))
    if end:
        query = query.filter(FraudIncident.created_at < datetime.combine(end + timedelta(days=1), time.min))
    incidents = query.order_by(desc(FraudIncident.created_at), desc(FraudIncident.id)).limit(PAGE_LIMIT).all()
    versions = FraudModelVersion.query.order_by(desc(FraudModelVersion.trained_at)).limit(20).all()
    return render_template('admin_fraud.html', incidents=incidents, summary=metrics.summary(7), versions=versions,
                           export_forms=export.FORMS, export_titles={key: value[1] for key, value in export.DATASETS.items()},
                           export_max_rows=export.MAX_ROWS,
                           filters={'estado': status, 'nivel': level, 'tipo': category,
                                    'desde': start.isoformat() if start else '', 'hasta': end.isoformat() if end else ''},
                           statuses=c.INCIDENT_STATUSES)


@bp.route('/admin/fraude/incidentes/<int:incident_id>')
@admin_only
def fraud_incident(incident_id):
    incident = db.session.get(FraudIncident, incident_id) or abort(404)
    restrictions = (SecurityRestriction.query.filter_by(incident_id=incident.id)
                    .order_by(desc(SecurityRestriction.created_at)).limit(PAGE_LIMIT).all())
    holds = HeldOperation.query.filter_by(incident_id=incident.id).order_by(desc(HeldOperation.created_at)).limit(PAGE_LIMIT).all()
    recent = (RiskEvaluation.query.filter_by(user_id=incident.user_id).order_by(desc(RiskEvaluation.created_at))
              .limit(10).all()) if incident.user_id else []
    return render_template('admin_fraud_incident.html', incident=incident, restrictions=restrictions, holds=holds, recent=recent,
                           suggested_days=service.suggested_sanction_days(incident.user_id, incident.id),
                           max_days=current_app.config['FRAUD_MAX_SANCTION_DAYS'], scopes=c.SCOPES,
                           own_user=incident.user_id == current_user.id)


def _incident_for_update(incident_id):
    return FraudIncident.query.filter_by(id=incident_id).with_for_update().first() or abort(404)


@bp.route('/admin/fraude/incidentes/<int:incident_id>/investigar', methods=['POST'])
@admin_only
def fraud_incident_investigate(incident_id):
    incident = _incident_for_update(incident_id)
    return _run(lambda: service.start_investigation(incident, current_user), url_for('main.fraud_incident', incident_id=incident_id)) \
        or redirect(url_for('main.fraud_incident', incident_id=incident_id))


@bp.route('/admin/fraude/incidentes/<int:incident_id>/comentar', methods=['POST'])
@admin_only
def fraud_incident_comment(incident_id):
    incident = _incident_for_update(incident_id)
    return _run(lambda: service.add_comment(incident, current_user, request.form.get('comentario')),
                url_for('main.fraud_incident', incident_id=incident_id)) or redirect(url_for('main.fraud_incident', incident_id=incident_id))


@bp.route('/admin/fraude/incidentes/<int:incident_id>/descartar', methods=['POST'])
@admin_only
def fraud_incident_discard(incident_id):
    incident = _incident_for_update(incident_id)
    outcome = _run(lambda: service.discard_incident(incident, current_user, request.form.get('motivo')),
                   url_for('main.fraud_incident', incident_id=incident_id))
    if outcome is None:
        flash('Incidente descartado como falso positivo. Se levantaron las restricciones preventivas.', 'success')
    return outcome or redirect(url_for('main.fraud_incident', incident_id=incident_id))


@bp.route('/admin/fraude/incidentes/<int:incident_id>/confirmar', methods=['POST'])
@admin_only
def fraud_incident_confirm(incident_id):
    incident = _incident_for_update(incident_id)
    outcome = _run(lambda: service.confirm_fraud(incident, current_user, request.form.get('motivo'),
                                                 request.form.getlist('alcance'), request.form.get('dias')),
                   url_for('main.fraud_incident', incident_id=incident_id))
    if outcome is None:
        flash('Fraude confirmado. La sanción es temporal y se puede levantar.', 'success')
    return outcome or redirect(url_for('main.fraud_incident', incident_id=incident_id))


@bp.route('/admin/fraude/restricciones')
@admin_only
def fraud_restrictions():
    service.expire_stale()
    db.session.commit()
    state = request.args.get('estado', 'ACTIVA').strip().upper()
    if state not in ('ACTIVA', 'VENCIDA', 'TODAS'):
        abort(400)
    now = now_utc()
    query = SecurityRestriction.query
    if state == 'ACTIVA':
        query = query.filter(SecurityRestriction.status == c.RESTRICTION_ACTIVE, SecurityRestriction.expires_at > now)
    elif state == 'VENCIDA':
        query = query.filter(SecurityRestriction.status != c.RESTRICTION_ACTIVE)
    items = query.order_by(desc(SecurityRestriction.created_at)).limit(PAGE_LIMIT).all()
    return render_template('admin_fraud_restrictions.html', restrictions=items, state=state, now=now)


@bp.route('/admin/fraude/restricciones/<int:restriction_id>/levantar', methods=['POST'])
@admin_only
def fraud_restriction_lift(restriction_id):
    restriction = (SecurityRestriction.query.filter_by(id=restriction_id).with_for_update().first()) or abort(404)
    target = url_for('main.fraud_restrictions')
    outcome = _run(lambda: service.lift_restriction(restriction, current_user, request.form.get('motivo')), target)
    if outcome is None:
        flash('Restricción levantada.', 'success')
    return outcome or redirect(target)


@bp.route('/admin/fraude/retenciones')
@admin_only
def fraud_holds():
    service.expire_stale()
    db.session.commit()
    items = HeldOperation.query.order_by(desc(HeldOperation.created_at)).limit(PAGE_LIMIT).all()
    return render_template('admin_fraud_holds.html', holds=items, now=now_utc())


def _review(hold_id, authorize):
    hold = (HeldOperation.query.filter_by(id=hold_id).with_for_update().first()) or abort(404)
    target = url_for('main.fraud_holds')
    outcome = _run(lambda: service.review_hold(hold, current_user, request.form.get('motivo'), authorize=authorize), target)
    if outcome is None:
        flash('Retención autorizada: el cliente puede repetir la operación.' if authorize else 'Retención rechazada.', 'success')
    return outcome or redirect(target)


@bp.route('/admin/fraude/retenciones/<int:hold_id>/autorizar', methods=['POST'])
@admin_only
def fraud_hold_authorize(hold_id):
    return _review(hold_id, True)


@bp.route('/admin/fraude/retenciones/<int:hold_id>/rechazar', methods=['POST'])
@admin_only
def fraud_hold_reject(hold_id):
    return _review(hold_id, False)


@bp.route('/admin/fraude/exportar/<dataset>.csv')
@admin_only
def fraud_export(dataset):
    """Descarga CSV (solo ADMINISTRADOR). Es de solo lectura sobre los datos, pero cada descarga queda auditada."""
    if dataset not in export.DATASETS:
        abort(404)
    try:
        body, rows, truncated = export.build(dataset, request.args)
    except ValueError:
        abort(400)
    service.audit(current_user, f'FRAUD_EXPORT_{dataset.upper()}')
    db.session.commit()
    response = Response(body, mimetype='text/csv')
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{export.filename(dataset)}"'
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Export-Rows'] = str(rows)
    response.headers['X-Export-Truncated'] = 'true' if truncated else 'false'
    return response


@bp.route('/admin/fraude/modelos/<version>')
@admin_only
def fraud_model(version):
    """Resultados de evaluación y trazabilidad de una versión del detector (solo lectura: la activación se hace por línea de órdenes)."""
    if not re.fullmatch(r'iforest(?:-[a-z]+)?-\d{14,20}', version):
        abort(404)
    row = FraudModelVersion.query.filter_by(version=version).first() or abort(404)
    return render_template('admin_fraud_model.html', model=row, gates=anomaly.activation_gates(row))


@bp.route('/admin/fraude/auditoria')
@admin_only
def fraud_audit():
    """Registro de auditoría de las acciones antifraude (decisiones, retenciones, modelos y descargas)."""
    start, end = _date_arg('desde'), _date_arg('hasta')
    if start and end and start > end:
        abort(400)
    action = request.args.get('accion', '').strip().upper()
    if action and not re.fullmatch(r'[A-Z_]{3,80}', action):
        abort(400)
    query = AuditLog.query.filter(AuditLog.module == 'FRAUDE')
    if action:
        query = query.filter(AuditLog.action == action)
    if start:
        query = query.filter(AuditLog.created_at >= datetime.combine(start, time.min))
    if end:
        query = query.filter(AuditLog.created_at < datetime.combine(end + timedelta(days=1), time.min))
    entries = query.order_by(desc(AuditLog.created_at), desc(AuditLog.id)).limit(PAGE_LIMIT).all()
    return render_template('admin_fraud_audit.html', entries=entries, filters={'accion': action, 'desde': start.isoformat() if start else '',
                                                                                'hasta': end.isoformat() if end else ''})
