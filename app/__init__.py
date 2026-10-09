from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf.csrf import CSRFProtect
from pathlib import Path
from sqlalchemy import inspect
from config import Config
from .models import db, User
login_manager=LoginManager()
login_manager.login_view='main.login'
login_manager.login_message='Inicia sesión para continuar.'
login_manager.login_message_category='info'
csrf=CSRFProtect()

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

def _validate_existing_schema():
    inspector=inspect(db.engine)
    existing_tables=set(inspector.get_table_names())
    missing_tables=[]
    missing_columns=[]
    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            missing_tables.append(table.name)
            continue
        existing_columns={column['name'] for column in inspector.get_columns(table.name)}
        missing_columns.extend(f'{table.name}.{column.name}' for column in table.columns
                               if column.name not in existing_columns)
    if missing_tables or missing_columns:
        details=[]
        if missing_tables: details.append('tablas: '+', '.join(missing_tables))
        if missing_columns: details.append('columnas: '+', '.join(missing_columns))
        raise RuntimeError('El esquema de la base requiere una migración aprobada ('+'; '.join(details)+'). '
                           'La aplicación no crea ni altera esquemas persistentes al iniciar.')

def create_app(config_overrides=None):
    app=Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app); login_manager.init_app(app); csrf.init_app(app)
    from .routes import bp
    app.register_blueprint(bp)
    from .api import api
    app.register_blueprint(api)

    @login_manager.unauthorized_handler
    def unauthorized_api_response():
        if request.path.startswith('/api/v1/'):
            response=jsonify(error='Debes iniciar sesión para consultar este recurso.')
            response.status_code=401
            response.headers['Cache-Control']='no-store'
            return response
        flash(login_manager.login_message,'info')
        return redirect(url_for(login_manager.login_view, next=request.url))

    @app.context_processor
    def inject_brand_context():
        return {'norte_legal_notice':app.config['LEGAL_NOTICE']}

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options','nosniff')
        response.headers.setdefault('X-Frame-Options','DENY')
        response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy','camera=(self), geolocation=(), microphone=()')
        if app.config.get('SESSION_COOKIE_SECURE') and request.headers.get('X-Forwarded-Proto')=='https':
            response.headers.setdefault('Strict-Transport-Security','max-age=31536000')
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def render_error_page(error):
        status=getattr(error,'code',500)
        messages={
            400:('No pudimos procesar esta solicitud.','Revisa la información e inténtalo nuevamente.'),
            403:('No tienes acceso a esta sección.','Inicia sesión con una cuenta que tenga los permisos necesarios.'),
            404:('No encontramos esta página.','Es posible que la dirección haya cambiado o ya no esté disponible.'),
            500:('Ocurrió un problema temporal.','Inténtalo nuevamente en unos minutos.'),
        }
        title,detail=messages.get(status,messages[500])
        return render_template('error.html',status=status,title=title,detail=detail),status

    @app.before_request
    def require_email_second_factor():
        if current_user.is_authenticated and not session.get('login_2fa_verified'):
            logout_user()
            session.clear()
            flash('Tu sesión debe volver a autenticarse con el código de seguridad.','error')
            return redirect(url_for('main.login'))

    with app.app_context():
        if app.config.get('APP_ENV')=='testing':
            db.create_all()
            from .services.seed_service import seed_demo
            seed_demo()
        else:
            _validate_existing_schema()
    return app
