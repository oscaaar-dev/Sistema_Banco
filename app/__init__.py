from flask import Flask, flash, redirect, render_template, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf.csrf import CSRFProtect
from pathlib import Path
from sqlalchemy import inspect, text
from config import Config
from .models import db, User
login_manager=LoginManager()
login_manager.login_view='main.login'
login_manager.login_message='Inicia sesión para continuar.'
login_manager.login_message_category='info'
csrf=CSRFProtect()

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

def create_app():
    app=Flask(__name__)
    app.config.from_object(Config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app); login_manager.init_app(app); csrf.init_app(app)
    from .routes import bp
    app.register_blueprint(bp)
    @app.context_processor
    def inject_brand_context():
        return {'norte_legal_notice':app.config['LEGAL_NOTICE']}

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
        if inspect(db.engine).has_table('users'):
            existing_columns={column['name'] for column in inspect(db.engine).get_columns('users')}
            with db.engine.begin() as connection:
                if 'email' not in existing_columns:
                    connection.execute(text('ALTER TABLE users ADD COLUMN email VARCHAR(254)'))
                if 'dni' not in existing_columns:
                    connection.execute(text('ALTER TABLE users ADD COLUMN dni VARCHAR(8)'))
        db.create_all()
        existing_indexes={index['name'] for index in inspect(db.engine).get_indexes('users')}
        with db.engine.begin() as connection:
            if 'uq_users_email' not in existing_indexes:
                connection.execute(text('CREATE UNIQUE INDEX uq_users_email ON users (email)'))
            if 'uq_users_dni' not in existing_indexes:
                connection.execute(text('CREATE UNIQUE INDEX uq_users_dni ON users (dni)'))
        from .services.seed_service import seed_demo
        seed_demo()
    return app
