import hashlib
import hmac
import importlib
import secrets
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re
from flask import Blueprint, Response, current_app, render_template, request, redirect, url_for, flash, abort, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import desc, or_
from sqlalchemy.exc import IntegrityError
from ..models import db, User, LoginChallenge, LoginAttemptLimit, OperationChallenge, Account, BankCard, Beneficiary, Transaction, InteractionSession, OperationCapture, AuditLog, now_utc
from ..services.banking_service import account_for, process_operation, process_internal_transfer, log, operation_risk_for
from ..services.statement_service import MOVEMENT_TYPES, account_statement_pdf
from ..services.email_service import EmailDeliveryError, send_login_code, send_operation_code
from ..services.password_policy import validate_new_password
from ..services.validation import parse_amount, parse_duration_seconds
from ..services.capture_service import read_capture
from ..services.account_service import account_overview, parse_month
from ..services.contact_service import active_beneficiaries, delete_contact, find_client_account, get_active_beneficiary, mask_name, normalize_identifier, save_contact
bp=Blueprint('main',__name__)

LOGIN_FAILURE_LIMIT=5
LOGIN_FAILURE_WINDOW=timedelta(minutes=15)
LOGIN_LOCK_DURATION=timedelta(minutes=15)
OPERATION_CODE_TTL=timedelta(minutes=5)
OPERATION_CODE_RESEND=timedelta(seconds=60)
OPERATION_CODE_MAX_ATTEMPTS=5
CAMERA_CONSENT_VERSION='2026-10-v2'
CAMERA_KINDS=('transfer','payment','recharge','internal')

def _home_for_role(user):
    if user.role=='ADMINISTRADOR':
        return url_for('main.admin')
    if user.role=='SOPORTE':
        return url_for('main.support')
    return url_for('main.dashboard')

def _require_client():
    if current_user.role!='CLIENTE':
        abort(403)

def _camera_receipt_ok(kind,operation_key):
    """La cámara es obligatoria: hace falta haber aceptado el aviso vigente para esta operación concreta."""
    receipt=session.get('camera_consent_receipt') or {}
    return bool(receipt.get('version')==CAMERA_CONSENT_VERSION and receipt.get('kind')==kind
                and operation_key and receipt.get('idempotency_key')==operation_key)

def _capture_meta():
    return {'consent_version':CAMERA_CONSENT_VERSION,'ip_hash':_request_ip_hash('operation-capture'),
            'user_agent':request.headers.get('User-Agent','')[:255]}

CAMERA_REQUIRED_MESSAGE='Para confirmar la operación debes aceptar el aviso y activar la cámara.'
PENDING_FIELDS=('beneficiary_id','amount','service','phone')

@bp.app_template_global('camera_acknowledged')
def _camera_acknowledged(kind,operation_key):
    return _camera_receipt_ok(kind,operation_key)

def _login_attempt_hash(identifier):
    key=str(current_app.config['SECRET_KEY']).encode('utf-8')
    normalized=(identifier or '').strip().casefold()
    return hmac.new(key,('login-attempt:'+normalized).encode('utf-8'),hashlib.sha256).hexdigest()

def _request_ip_hash(purpose):
    key=str(current_app.config['SECRET_KEY']).encode('utf-8')
    if current_app.config.get('RAILWAY_ENVIRONMENT_NAME'):
        address=request.headers.get('X-Real-IP') or request.remote_addr or 'unknown'
    else:
        address=request.remote_addr or 'unknown'
    return hmac.new(key,(purpose+':'+address.strip()).encode('utf-8'),hashlib.sha256).hexdigest()

def _recent_audit_count(action, since, *, user_id=None, ip_hash=None):
    query=AuditLog.query.filter_by(action=action).filter(AuditLog.created_at>=since)
    if user_id is not None:
        query=query.filter_by(user_id=user_id)
    if ip_hash is not None:
        query=query.filter_by(ip_local=ip_hash)
    return query.count()

def _login_code_rate_limited(user):
    now=now_utc()
    since=now-timedelta(seconds=current_app.config['LOGIN_CODE_RATE_LIMIT_WINDOW_SECONDS'])
    ip_hash=_request_ip_hash('login-otp-ip')
    return (
        _recent_audit_count('LOGIN_OTP_SENT',since,ip_hash=ip_hash)>=current_app.config['LOGIN_CODE_RATE_LIMIT_IP']
        or _recent_audit_count('LOGIN_OTP_SENT',since,user_id=user.id)>=current_app.config['LOGIN_CODE_RATE_LIMIT_USER']
    )

def _login_is_blocked(identifier):
    attempt=LoginAttemptLimit.query.filter_by(identifier_hash=_login_attempt_hash(identifier)).first()
    if attempt is None:
        return False
    now=now_utc()
    if attempt.locked_until and attempt.locked_until>now:
        return True
    if (attempt.locked_until and attempt.locked_until<=now) or attempt.window_started_at+LOGIN_FAILURE_WINDOW<=now:
        db.session.delete(attempt)
        db.session.commit()
    return False

def _record_failed_login(identifier):
    key=_login_attempt_hash(identifier)
    now=now_utc()
    for _ in range(2):
        attempt=LoginAttemptLimit.query.filter_by(identifier_hash=key).with_for_update().first()
        if attempt is None:
            attempt=LoginAttemptLimit(identifier_hash=key,failure_count=1,window_started_at=now)
            db.session.add(attempt)
        elif attempt.window_started_at+LOGIN_FAILURE_WINDOW<=now:
            attempt.failure_count=1
            attempt.window_started_at=now
            attempt.locked_until=None
        else:
            attempt.failure_count+=1
        if attempt.failure_count>=LOGIN_FAILURE_LIMIT:
            attempt.locked_until=now+LOGIN_LOCK_DURATION
        log(None,'LOGIN_FAILURE','SEGURIDAD',result='RECHAZADA')
        try:
            db.session.commit()
            return
        except IntegrityError:
            db.session.rollback()
    raise RuntimeError('No se pudo actualizar el control de intentos de acceso.')

def _clear_login_failures(identifier):
    attempt=LoginAttemptLimit.query.filter_by(identifier_hash=_login_attempt_hash(identifier)).first()
    if attempt is not None:
        db.session.delete(attempt)
        db.session.commit()

def _login_code_hash(user_id, code):
    key=str(current_app.config['SECRET_KEY']).encode('utf-8')
    message=f'{user_id}:{code}'.encode('utf-8')
    return hmac.new(key,message,hashlib.sha256).hexdigest()

def _operation_code_hash(user_id, code):
    key=str(current_app.config['SECRET_KEY']).encode('utf-8')
    message=f'operation:{user_id}:{code}'.encode('utf-8')
    return hmac.new(key,message,hashlib.sha256).hexdigest()

def _operation_details_hash(operation_type, details):
    key=str(current_app.config['SECRET_KEY']).encode('utf-8')
    message=f'operation-details:{operation_type}:{details}'.encode('utf-8')
    return hmac.new(key,message,hashlib.sha256).hexdigest()

def _masked_email(address):
    local,separator,domain=address.partition('@')
    if not separator:
        return 'correo registrado'
    return f'{local[:1]}***@{domain}'

def _issue_login_code(user):
    now=now_utc()
    challenge=LoginChallenge.query.filter_by(user_id=user.id).first()
    cooldown=timedelta(seconds=current_app.config['LOGIN_CODE_RESEND_SECONDS'])
    if challenge and now-challenge.last_sent_at<cooldown:
        session['pending_login_user_id']=user.id
        return 'cooldown'
    if _login_code_rate_limited(user):
        session['pending_login_user_id']=user.id
        return 'rate_limited'

    code=f'{secrets.randbelow(1_000_000):06d}'
    if challenge is None:
        challenge=LoginChallenge(user_id=user.id)
        db.session.add(challenge)
    challenge.code_hash=_login_code_hash(user.id,code)
    challenge.expires_at=now+timedelta(seconds=current_app.config['LOGIN_CODE_TTL_SECONDS'])
    challenge.last_sent_at=now
    challenge.attempts=0
    try:
        db.session.flush()
        send_login_code(user.email,code)
        db.session.add(AuditLog(
            user_id=user.id,action='LOGIN_OTP_SENT',module='SEGURIDAD',result='ENVIADO',
            ip_local=_request_ip_hash('login-otp-ip'),created_at=now,
        ))
        db.session.commit()
    except EmailDeliveryError:
        db.session.rollback()
        current_app.logger.exception('No se pudo enviar el código de acceso para el usuario %s.',user.id)
        return 'delivery_error'

    session.clear()
    session['pending_login_user_id']=user.id
    return 'sent'

def _pending_login():
    user_id=session.get('pending_login_user_id')
    if not user_id:
        return None,None
    user=db.session.get(User,user_id)
    challenge=LoginChallenge.query.filter_by(user_id=user_id).first()
    return user,challenge

@bp.route('/')
def index():
    return render_template('home.html')

PUBLIC_INFO_PAGES = {
    'personas': {
        'eyebrow': 'PERSONAS · ROLES DEL EQUIPO',
        'title': 'Detrás de cada servicio digital hay un equipo.',
        'intro': 'Conoce los perfiles que administran, supervisan y mantienen una plataforma como Norte, y qué funciones están disponibles en esta demostración.',
        'icon': 'bi-people',
        'highlights': [
            {'icon': 'bi-person-gear', 'title': 'Administrador del sistema', 'description': 'En Norte puede consultar indicadores del entorno, sesiones recientes y registros de auditoría.', 'action': 'Acceso de administración', 'destination': 'login', 'image': 'team/administrador.jpg', 'image_alt': 'Modelo en retrato profesional para ilustrar el rol de administración', 'photo_credit': 'Antoni Shkraba · Pexels', 'photo_url': 'https://www.pexels.com/photo/portrait-of-a-man-standing-in-an-office-8192053/'},
            {'icon': 'bi-briefcase', 'title': 'Gerencia', 'description': 'Perfil de referencia para revisar indicadores de gestión. Esta demostración aún no tiene un rol ni un tablero gerencial independiente.', 'action': 'Conocer el proyecto', 'destination': 'acerca', 'image': 'team/gerencia.jpg', 'image_alt': 'Modelo en retrato profesional para ilustrar el rol de gerencia', 'photo_credit': 'Kampus Production · Pexels', 'photo_url': 'https://www.pexels.com/photo/portrait-of-smiling-woman-in-an-office-8463168/'},
            {'icon': 'bi-pc-display', 'title': 'Sistemas y soporte', 'description': 'La vista de soporte muestra conteos y eventos técnicos; no permite operar cuentas ni muestra datos personales.', 'action': 'Acceso de soporte', 'destination': 'login', 'image': 'team/sistemas-soporte.jpg', 'image_alt': 'Modelo trabajando frente a una computadora para ilustrar sistemas y soporte', 'photo_credit': 'cottonbro studio · Pexels', 'photo_url': 'https://www.pexels.com/photo/portrait-of-a-man-at-his-desk-7504748/'},
        ],
        'cta_label': 'Explorar herramientas',
        'cta_endpoint': 'main.herramientas',
    },
    'herramientas': {
        'eyebrow': 'HERRAMIENTAS · RECORRIDOS DISPONIBLES',
        'title': 'Encuentra la función que necesitas explorar.',
        'intro': 'Entra a cada sección para conocer sus pantallas y el recorrido. El sistema usa datos de demostración; no se conecta a bancos ni a empresas de servicios.',
        'icon': 'bi-grid',
        'highlights': [
            {'icon': 'bi-wallet2', 'title': 'Resumen y cuentas', 'description': 'Consulta el saldo ficticio y los datos de la cuenta de demostración.', 'action': 'Ver resumen', 'destination': 'login'},
            {'icon': 'bi-clock-history', 'title': 'Movimientos', 'description': 'Revisa el historial y abre el detalle de una operación de ejemplo.', 'action': 'Ver movimientos', 'destination': 'login'},
            {'icon': 'bi-arrow-left-right', 'title': 'Transferencias internas', 'description': 'Conoce el formulario y los pasos de confirmación dentro de Norte.', 'action': 'Explorar transferencias', 'destination': 'login'},
            {'icon': 'bi-receipt-cutoff', 'title': 'Pagos y recargas', 'description': 'Explora formularios de demostración; no se envían pagos a terceros.', 'action': 'Explorar operaciones', 'destination': '#operaciones'},
            {'icon': 'bi-credit-card-2-front', 'title': 'Tarjetas de muestra', 'description': 'Consulta tarjetas ficticias asociadas a una cuenta de demostración.', 'action': 'Conocer las tarjetas', 'destination': 'login'},
        ],
        'note': 'Las funciones no se conectan a cuentas externas. Los pagos, recargas y transferencias son recorridos de demostración sin efectos financieros fuera de Norte.',
        'cta_label': 'Conocer la seguridad',
        'cta_endpoint': 'main.seguridad',
    },
    'seguridad': {
        'eyebrow': 'SEGURIDAD Y TRANSPARENCIA',
        'title': 'Saber qué ocurre con tus datos también es seguridad.',
        'intro': 'Antes de crear una cuenta o probar una función, aquí puedes revisar cómo funciona el acceso y qué límites tiene Norte.',
        'icon': 'bi-shield-check',
        'highlights': [
            {'icon': 'bi-envelope-check', 'title': 'Acceso verificado por correo', 'description': 'Al iniciar sesión, Norte envía un código de verificación al correo registrado.', 'action': 'Ir al acceso', 'destination': 'login'},
            {'icon': 'bi-key', 'title': 'Elige una clave exclusiva', 'description': 'No reutilices la contraseña de tu correo, banco u otros servicios.', 'action': 'Crear perfil', 'destination': 'register'},
            {'icon': 'bi-person-lock', 'title': 'Conoce el uso de tus datos', 'description': 'Revisa qué información se solicita al crear una cuenta de demostración.', 'action': 'Leer privacidad', 'destination': 'privacy'},
            {'icon': 'bi-shield-exclamation', 'title': 'Reconoce sus límites', 'description': 'Norte no es una entidad financiera ni procesa dinero real o pagos externos.', 'action': 'Leer términos', 'destination': 'terms'},
        ],
        'note': 'Usa datos de acceso que no emplees en otros servicios. No ingreses claves bancarias ni números de tarjeta reales. Norte no procesa dinero real ni pagos externos.',
        'cta_label': 'Acerca del proyecto',
        'cta_endpoint': 'main.acerca',
    },
    'acerca': {
        'eyebrow': 'ACERCA DE NORTE',
        'title': 'Un proyecto independiente para aprender explorando.',
        'intro': 'Norte es una experiencia académica de banca digital en desarrollo. Reúne pantallas y recorridos de demostración para explicar cómo se organiza un sistema de este tipo.',
        'icon': 'bi-compass',
        'highlights': [
            {'icon': 'bi-lightbulb', 'title': 'Por qué existe', 'description': 'Para mostrar de forma sencilla cómo se conectan el resumen, los movimientos y los recorridos digitales.', 'action': 'Conocer las herramientas', 'destination': 'herramientas'},
            {'icon': 'bi-window', 'title': 'Qué puedes explorar', 'description': 'Perfiles de demostración, vistas adaptables y operaciones internas con datos ficticios.', 'action': 'Ver la experiencia', 'destination': '#operaciones'},
            {'icon': 'bi-info-circle', 'title': 'Qué no es Norte', 'description': 'No es BCP ni está afiliado a esa entidad; tampoco ofrece productos bancarios reales.', 'action': 'Leer términos', 'destination': 'terms'},
        ],
        'timeline': [
            ('La idea', 'Hacer más claros los pasos de una experiencia financiera digital.'),
            ('El prototipo', 'Construir una interfaz navegable con datos ficticios.'),
            ('La exploración', 'Probar las pantallas desde computadora, tablet o celular.'),
        ],
        'note': 'Norte es independiente y no está afiliado, patrocinado ni respaldado por el BCP. Sus nombres, pantallas, datos y operaciones pertenecen a un proyecto de demostración.',
        'cta_label': 'Volver al inicio',
        'cta_endpoint': 'main.index',
    },
}


def _public_info_page(page_key):
    page=dict(PUBLIC_INFO_PAGES[page_key])
    page['highlights']=[dict(card) for card in page['highlights']]
    for card in page['highlights']:
        destination=card['destination']
        if destination.startswith('#'):
            card['href']=url_for('main.index')+destination
        else:
            card['href']=url_for('main.'+destination)
    return render_template('public_info.html', page=page, page_key=page_key)


@bp.route('/personas')
def personas():
    return _public_info_page('personas')


@bp.route('/herramientas')
def herramientas():
    return _public_info_page('herramientas')


@bp.route('/seguridad')
def seguridad():
    return _public_info_page('seguridad')


@bp.route('/acerca')
def acerca():
    return _public_info_page('acerca')


@bp.route('/terminos')
def terms():
    return render_template('legal.html',page_key='terminos')


@bp.route('/privacidad')
def privacy():
    return render_template('legal.html',page_key='privacidad')


@bp.route('/login',methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    if request.method=='GET' and session.get('pending_login_user_id'):
        return redirect(url_for('main.verify_login'))
    if request.method=='POST':
        identifier=request.form.get('username','').strip()
        if _login_is_blocked(identifier):
            flash('Usuario o clave incorrectos.','error')
            return render_template('login.html'),429
        # El acceso acepta el usuario heredado, el DNI registrado o el correo.
        # Consultar el DNI de forma explícita evita que el texto de la interfaz
        # prometa una alternativa que no funcione para cuentas migradas.
        user=User.query.filter(or_(
            User.username==identifier,
            User.dni==identifier,
            User.email==identifier.lower(),
        )).first()
        if user and user.check_password(request.form.get('password','')):
            _clear_login_failures(identifier)
            if not user.email:
                flash('Esta cuenta no tiene un correo de seguridad configurado. Contacta a la administración.','error')
                return redirect(url_for('main.login'))
            result=_issue_login_code(user)
            if result=='delivery_error':
                flash('No se pudo enviar el código de seguridad. Revisa la configuración de correo o contacta a la administración.','error')
                return redirect(url_for('main.login'))
            if result=='rate_limited':
                flash('Se alcanzó el límite temporal de códigos. Inténtalo más tarde.','error')
                return render_template('login.html'),429
            if result=='cooldown':
                flash('Ya se envió un código recientemente. Revisa el correo registrado o espera antes de solicitar otro.','info')
            else:
                flash('Enviamos un código de seguridad al correo registrado.','success')
            return redirect(url_for('main.verify_login'))
        _record_failed_login(identifier)
        flash('Usuario o clave incorrectos.','error')
    return render_template('login.html')

@bp.route('/register',methods=['GET','POST'])
def register():
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    if request.method=='POST':
        now=now_utc()
        since=now-timedelta(seconds=current_app.config['REGISTRATION_RATE_LIMIT_WINDOW_SECONDS'])
        registration_ip=_request_ip_hash('registration-ip')
        if _recent_audit_count('CUSTOMER_REGISTERED',since,ip_hash=registration_ip)>=current_app.config['REGISTRATION_RATE_LIMIT_IP']:
            flash('Se alcanzó el límite temporal de registros desde esta conexión. Inténtalo más tarde.','error')
            return render_template('register.html'),429
        full_name=' '.join(request.form.get('full_name','').split())
        dni=request.form.get('dni','').strip()
        email=request.form.get('email','').strip().lower()
        password=request.form.get('password','')
        password_confirmation=request.form.get('password_confirmation','')
        errors=[]
        if not 2<=len(full_name)<=100 or any(
            not (character.isalpha() or character in " '-")
            for character in full_name
        ):
            errors.append('Escribe tu nombre completo usando letras, espacios, apóstrofes o guiones.')
        if len(dni)!=8 or not dni.isascii() or not dni.isdigit():
            errors.append('El DNI debe contener exactamente 8 dígitos.')
        email_pattern=r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
        if len(email)>254 or not re.fullmatch(email_pattern,email):
            errors.append('Ingresa una dirección de correo electrónico válida.')
        password_errors,password_failed=validate_new_password(password,password_confirmation)
        errors.extend(password_errors)
        if errors:
            for error in errors:
                flash(error,'error')
            return render_template('register.html',password_failed=password_failed),400

        duplicate=User.query.filter(or_(
            User.username==dni,
            User.dni==dni,
            User.email==email,
        )).first()
        if duplicate:
            flash('Ya existe una cuenta con ese DNI o correo. Inicia sesión o usa otros datos.','error')
            return render_template('register.html'),409

        user=User(username=dni,full_name=full_name,email=email,dni=dni,role='CLIENTE')
        user.set_password(password)
        try:
            db.session.add(user)
            db.session.flush()
            account_number=None
            while account_number is None:
                candidate=f'{100000000000+secrets.randbelow(900000000000):012d}'
                if not Account.query.filter_by(number=candidate).first():
                    account_number=candidate
            db.session.add(Account(
                user_id=user.id,
                account_type='Cuenta de ahorros',
                number=account_number,
                balance=Decimal('0.00'),
            ))
            db.session.add(AuditLog(
                user_id=user.id,action='CUSTOMER_REGISTERED',module='ACCESO',result='COMPLETADO',
                ip_local=registration_ip,created_at=now,
            ))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Ya existe una cuenta con ese DNI o correo. Inicia sesión o usa otros datos.','error')
            return render_template('register.html'),409

        session.pop('pending_login_user_id',None)
        flash('Cuenta creada. Inicia sesión con tu DNI o correo y contraseña; te enviaremos un código de seguridad.','success')
        return redirect(url_for('main.login'))
    return render_template('register.html')

@bp.route('/login/verify',methods=['GET','POST'])
def verify_login():
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    user,challenge=_pending_login()
    if not user or not challenge:
        session.pop('pending_login_user_id',None)
        flash('Inicia sesión con tu usuario y clave para solicitar un código.','error')
        return redirect(url_for('main.login'))

    if now_utc()>=challenge.expires_at:
        db.session.delete(challenge)
        db.session.commit()
        session.pop('pending_login_user_id',None)
        flash('El código venció. Vuelve a ingresar tus credenciales para solicitar otro.','error')
        return redirect(url_for('main.login'))

    if request.method=='POST':
        code=request.form.get('code','').strip()
        if len(code)!=6 or not code.isascii() or not code.isdigit() or not hmac.compare_digest(
            challenge.code_hash,_login_code_hash(user.id,code)
        ):
            challenge.attempts+=1
            attempts_left=max(0,current_app.config['LOGIN_CODE_MAX_ATTEMPTS']-challenge.attempts)
            if attempts_left==0:
                db.session.delete(challenge)
                db.session.commit()
                session.pop('pending_login_user_id',None)
                flash('Se agotaron los intentos. Vuelve a ingresar tus credenciales para solicitar otro código.','error')
                return redirect(url_for('main.login'))
            db.session.commit()
            flash(f'Código incorrecto. Te quedan {attempts_left} intentos.','error')
        else:
            db.session.delete(challenge)
            db.session.commit()
            session.clear()
            login_user(user)
            session.permanent=True
            session['login_2fa_verified']=True
            session['pwd_stamp']=user.session_stamp()
            log(user,'LOGIN','SEGURIDAD')
            db.session.commit()
            return redirect(_home_for_role(user))

    return render_template('login_verify.html',masked_email=_masked_email(user.email))

@bp.route('/login/resend',methods=['POST'])
def resend_login_code():
    if current_user.is_authenticated:
        return redirect(_home_for_role(current_user))
    user,challenge=_pending_login()
    if not user or not challenge:
        session.pop('pending_login_user_id',None)
        flash('Inicia sesión con tu usuario y clave para solicitar un código.','error')
        return redirect(url_for('main.login'))

    cooldown=timedelta(seconds=current_app.config['LOGIN_CODE_RESEND_SECONDS'])
    remaining=int((challenge.last_sent_at+cooldown-now_utc()).total_seconds())
    if remaining>0:
        flash(f'Espera {remaining} segundos antes de solicitar otro código.','error')
        return redirect(url_for('main.verify_login'))

    result=_issue_login_code(user)
    if result=='delivery_error':
        flash('No se pudo enviar el código de seguridad. Revisa la configuración de correo o contacta a la administración.','error')
        return redirect(url_for('main.verify_login'))
    if result=='rate_limited':
        flash('Se alcanzó el límite temporal de códigos. Inténtalo más tarde.','error')
        return render_template('login_verify.html',masked_email=_masked_email(user.email)),429
    flash('Enviamos un nuevo código al correo registrado.','success')
    return redirect(url_for('main.verify_login'))

@bp.route('/login/cancel',methods=['POST'])
def cancel_login():
    _,challenge=_pending_login()
    if challenge:
        db.session.delete(challenge)
        db.session.commit()
    session.pop('pending_login_user_id',None)
    flash('Verificación cancelada.','info')
    return redirect(url_for('main.login'))
@bp.route('/logout',methods=['POST'])
@login_required
def logout():
    log(current_user,'LOGOUT','SEGURIDAD'); db.session.commit(); logout_user(); return redirect(url_for('main.login'))
@bp.route('/dashboard')
@login_required
def dashboard():
    _require_client()
    accounts=Account.query.filter_by(user_id=current_user.id,status='ACTIVA').order_by(Account.id).all()
    account_ids=[item.id for item in accounts]
    account=accounts[0] if accounts else None
    recent=(Transaction.query.filter(Transaction.account_id.in_(account_ids))
            .order_by(desc(Transaction.created_at),desc(Transaction.id)).limit(5).all() if account_ids else [])
    cards=BankCard.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html',account=account,accounts=accounts,recent=recent,cards=cards)
@bp.route('/accounts')
@login_required
def accounts():
    _require_client()
    owned=Account.query.filter_by(user_id=current_user.id).order_by(Account.id).all()
    selected_raw=request.args.get('account_id')
    if selected_raw:
        try:
            selected_id=int(selected_raw)
        except (TypeError,ValueError):
            abort(400)
        account=next((item for item in owned if item.id==selected_id),None)
        if account is None:
            abort(404)
    else:
        account=owned[0] if owned else None
    month_raw=request.args.get('month')
    month=parse_month(month_raw) if month_raw else None
    if month_raw and month is None:
        abort(400)
    overview=account_overview(account,month) if account else None
    return render_template('accounts.html',accounts=owned,overview=overview)

@bp.route('/movements')
@login_required
def movements():
    _require_client()
    accounts=Account.query.filter_by(user_id=current_user.id,status='ACTIVA').order_by(Account.id).all()
    selected_account_raw=request.args.get('account_id')
    if selected_account_raw:
        try:
            selected_account_id=int(selected_account_raw)
        except (TypeError,ValueError):
            abort(400)
        account=Account.query.filter_by(id=selected_account_id,user_id=current_user.id,status='ACTIVA').first_or_404()
    else:
        account=accounts[0] if accounts else None
    page_raw=request.args.get('page','1')
    try:
        page=int(page_raw)
        start=date.fromisoformat(request.args['from']) if request.args.get('from') else None
        end=date.fromisoformat(request.args['to']) if request.args.get('to') else None
    except (TypeError,ValueError):
        abort(400)
    operation_type=request.args.get('type','').strip().upper()
    valid_types=set(MOVEMENT_TYPES)
    if page<1 or (operation_type and operation_type not in valid_types) or (start and end and start>end):
        abort(400)
    query=Transaction.query.filter_by(account_id=account.id) if account else Transaction.query.filter(db.false())
    if operation_type:
        query=query.filter_by(operation_type=operation_type)
    if start:
        query=query.filter(Transaction.created_at>=datetime.combine(start,time.min))
    if end:
        query=query.filter(Transaction.created_at<datetime.combine(end+timedelta(days=1),time.min))
    per_page=20
    total=query.count()
    pages=max(1,(total+per_page-1)//per_page)
    if page>pages:
        page=pages
    items=query.order_by(desc(Transaction.created_at),desc(Transaction.id)).offset((page-1)*per_page).limit(per_page).all()
    return render_template('movements.html',items=items,account=account,accounts=accounts,page=page,pages=pages,total=total,
                           selected_type=operation_type,start_date=start.isoformat() if start else '',
                           end_date=end.isoformat() if end else '')

@bp.route('/statements/<int:account_id>.pdf')
@login_required
def account_statement(account_id):
    _require_client()
    account=Account.query.filter_by(id=account_id,user_id=current_user.id).first_or_404()
    try:
        start=date.fromisoformat(request.args['from']) if request.args.get('from') else None
        end=date.fromisoformat(request.args['to']) if request.args.get('to') else None
    except (TypeError,ValueError):
        abort(400)
    operation_type=request.args.get('type','').strip().upper()
    if (start and end and start>end) or (operation_type and operation_type not in MOVEMENT_TYPES):
        abort(400)
    pdf=account_statement_pdf(account,current_user,start,end,operation_type)
    response=Response(pdf,mimetype='application/pdf')
    response.headers['Content-Disposition']='attachment; filename="norte-estado-de-cuenta.pdf"'
    response.headers['Cache-Control']='no-store'
    return response
@bp.route('/cards')
@login_required
def cards():
    _require_client()
    return render_template('cards.html',cards=BankCard.query.filter_by(user_id=current_user.id).all())

@bp.route('/cards/<int:card_id>/status',methods=['POST'])
@login_required
def update_card_status(card_id):
    _require_client()
    card=BankCard.query.filter_by(id=card_id,user_id=current_user.id).first_or_404()
    target=request.form.get('status','')
    if target not in ('Activa','Bloqueada') or target==card.status:
        abort(400)
    card.status=target
    log(current_user,'CARD_'+('LOCKED' if target=='Bloqueada' else 'UNLOCKED'),'TARJETAS')
    db.session.commit()
    flash('Tarjeta bloqueada.' if target=='Bloqueada' else 'Tarjeta desbloqueada.','success')
    return redirect(url_for('main.cards'))

@bp.route('/operation-risk')
@login_required
def operation_risk():
    _require_client()
    kinds={'transfer':'TRANSFERENCIA','payment':'PAGO','recharge':'RECARGA'}
    kind=kinds.get(request.args.get('kind',''))
    if not kind: abort(400)
    try:
        amount=parse_amount(request.args.get('amount',''))
        return jsonify(operation_risk_for(current_user,kind,amount))
    except (ValueError,InvalidOperation):
        return jsonify(error='Ingresa un monto entre S/ 0.01 y S/ 10,000.00.'),400

@bp.route('/camera/consent', methods=['POST'])
@login_required
def record_camera_consent():
    _require_client()
    data=request.get_json(silent=True) or request.form
    kind=data.get('kind','')
    operation_key=data.get('idempotency_key','').strip()
    if (data.get('version')!=CAMERA_CONSENT_VERSION
            or kind not in CAMERA_KINDS
            or not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',operation_key)):
        return jsonify(error='No se pudo validar el aviso para esta operación. Recarga la página e inténtalo de nuevo.'),400
    action=f"CAMERA_CONSENT_ACCEPTED_{CAMERA_CONSENT_VERSION.replace('-', '_').upper()}_{kind.upper()}"
    log(current_user,action,'PRIVACIDAD')
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('No se pudo registrar el consentimiento de cámara.')
        return jsonify(error='No se pudo registrar tu aceptación. Inténtalo de nuevo en unos segundos.'),503
    session['camera_consent_receipt']={
        'version':CAMERA_CONSENT_VERSION,
        'kind':kind,
        'idempotency_key':operation_key,
    }
    return jsonify(recorded=True,version=CAMERA_CONSENT_VERSION)

@bp.route('/camera/analyze', methods=['POST'])
@login_required
def camera_analyze():
    _require_client()
    return jsonify(error='El análisis de cámara del servidor está deshabilitado. La vista previa es local al navegador.'),410

@bp.route('/operation/<kind>',methods=['GET','POST'])
@login_required
def operation(kind):
    _require_client()
    if kind not in ('transfer','payment','recharge'): abort(404)
    recipients=active_beneficiaries(current_user.id).all(); account=account_for(current_user)
    submitted_idempotency_key=request.form.get('idempotency_key','').strip() if request.method=='POST' else ''
    idempotency_key=(submitted_idempotency_key if re.fullmatch(r'[A-Za-z0-9_-]{22,64}',submitted_idempotency_key)
                     else secrets.token_urlsafe(24))
    prefill={name:request.form.get(name,'') for name in PENDING_FIELDS} if request.method=='POST' else {}
    if request.method=='GET':
        pending=session.get('pending_operation')
        if (isinstance(pending,dict) and pending.get('kind')==kind
                and re.fullmatch(r'[A-Za-z0-9_-]{22,64}',str(pending.get('key','')))):
            # Volvemos de "Enviar código": se conserva la clave de la operación (el código está ligado a ella) y los datos.
            idempotency_key=pending['key']
            prefill={name:str(pending.get(name,'')) for name in PENDING_FIELDS}
        else:
            session.pop('camera_consent_receipt',None)
    if request.method=='POST':
        camera_consent=bool(request.form.get('camera_consent')=='yes' and _camera_receipt_ok(kind,submitted_idempotency_key))
        if not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',submitted_idempotency_key):
            flash('El formulario venció. Vuelve a cargarlo antes de confirmar.','error')
            return render_template('operation.html',kind=kind,recipients=recipients,account=account,
                                   idempotency_key=idempotency_key,
                                   masked_email=_masked_email(current_user.email or ''),
                                   camera_consent_version=CAMERA_CONSENT_VERSION,prefill=prefill), 400
        try:
            amount=parse_amount(request.form.get('amount'))
            duration_seconds=parse_duration_seconds(request.form.get('form_duration_seconds'))
            beneficiary_id=request.form.get('beneficiary_id',type=int)
            if kind=='transfer':
                if account is None:
                    raise ValueError('No se encontró una cuenta activa.')
                recipient=get_active_beneficiary(current_user.id,beneficiary_id)
                if not recipient: raise ValueError('Destinatario no válido.')
                description='Transferencia a '+recipient.name; op='TRANSFERENCIA'
                details=f'beneficiary:{recipient.id}'
            elif kind=='payment':
                service=request.form.get('service','').strip()
                if service not in {'Electricidad','Agua','Internet','Tarjeta académica'}:
                    raise ValueError('Selecciona un servicio válido.')
                description='Pago: '+service[:70]; op='PAGO'; details='service:'+service
            else:
                phone=request.form.get('phone','').strip()
                if not re.fullmatch(r'[0-9]{7,15}',phone):
                    raise ValueError('Ingresa un número de celular válido, solo con dígitos.')
                description='Recarga: '+phone[:40]; op='RECARGA'; details='phone:'+phone

            # Cámara obligatoria: se valida antes de gastar el código de un solo uso, para poder reintentar con el mismo código.
            if not camera_consent:
                raise ValueError(CAMERA_REQUIRED_MESSAGE)
            capture=read_capture(request.files.get('capture'),current_app.config['CAPTURE_MAX_BYTES'])

            code=request.form.get('otp_code','').strip()
            must_use_otp=True
            if must_use_otp:
                if not current_user.email:
                    raise ValueError('Esta cuenta no tiene un correo de seguridad configurado. Contacta a la administración.')
                challenge=OperationChallenge.query.filter_by(
                    user_id=current_user.id,operation_type=op
                ).with_for_update().first()
                now=now_utc()
                if (not challenge or challenge.consumed_at is not None
                        or now>=challenge.expires_at or challenge.details_hash is None):
                    if challenge and challenge.consumed_at is None:
                        challenge.consumed_at=now
                        db.session.commit()
                    raise ValueError('Solicita un nuevo código de seguridad antes de confirmar la operación.')
                expected_details_hash=_operation_details_hash(op,details)
                if (challenge.account_id!=account.id
                        or challenge.beneficiary_id!=(recipient.id if kind=='transfer' else None)
                        or challenge.amount!=amount.quantize(Decimal('0.01'))
                        or challenge.idempotency_key!=submitted_idempotency_key
                        or not hmac.compare_digest(challenge.details_hash,expected_details_hash)):
                    challenge.consumed_at=now
                    db.session.commit()
                    raise ValueError('Los datos cambiaron después de enviar el código. Solicita uno nuevo.')
                if (len(code)!=6 or not code.isascii() or not code.isdigit()
                        or not hmac.compare_digest(challenge.code_hash,_operation_code_hash(current_user.id,code))):
                    challenge.attempts+=1
                    if challenge.attempts>=OPERATION_CODE_MAX_ATTEMPTS:
                        challenge.consumed_at=now
                    log(current_user,'OPERATION_OTP_FAILURE','SEGURIDAD',result='RECHAZADA')
                    db.session.commit()
                    raise ValueError('El código de confirmación no es válido o venció.')
                challenge.consumed_at=now
                log(current_user,'OPERATION_OTP_VERIFIED','SEGURIDAD')
            tx=process_operation(current_user,op,amount,description,beneficiary_id,request.form.get('service'),camera_consent,duration_seconds=duration_seconds,idempotency_key=idempotency_key,capture=capture,capture_meta=_capture_meta())
            session.pop('camera_consent_receipt',None)
            session.pop('pending_operation',None)
            flash('Operación registrada. No tiene efectos financieros.','success')
            return redirect(url_for('main.receipt',transaction_id=tx.id))
        except InvalidOperation:
            db.session.rollback()
            flash('Revisa los datos ingresados e inténtalo de nuevo.','error')
        except ValueError as e:
            db.session.rollback()
            flash(str(e),'error')
        except Exception:
            db.session.rollback()
            current_app.logger.exception('No se pudo completar la operación %s del usuario %s.',kind,current_user.id)
            flash('No se pudo completar la operación ni guardar la foto de seguridad. No se registró ningún movimiento; inténtalo de nuevo.','error')
    return render_template('operation.html',kind=kind,recipients=recipients,account=account,
                           idempotency_key=idempotency_key,masked_email=_masked_email(current_user.email or ''),
                           camera_consent_version=CAMERA_CONSENT_VERSION,prefill=prefill)

CONTACT_PENDING_TTL=timedelta(minutes=10)
CONTACT_NOT_FOUND_MESSAGE='No pudimos encontrar un cliente de Norte con esos datos. Revisa el DNI o el número de cuenta e inténtalo de nuevo.'

def _contacts_page(status=200,candidate=None,identifier='',return_to=''):
    contacts=active_beneficiaries(current_user.id).all()
    return render_template('contacts.html',contacts=contacts,candidate=candidate,identifier=identifier,return_to=return_to),status

def _contact_return_to():
    return 'transfer' if request.values.get('from')=='transfer' else ''

def _contact_lookup_rate_limited():
    since=now_utc()-timedelta(seconds=current_app.config['CONTACT_LOOKUP_RATE_LIMIT_WINDOW_SECONDS'])
    return (_recent_audit_count('CONTACT_LOOKUP',since,user_id=current_user.id)>=current_app.config['CONTACT_LOOKUP_RATE_LIMIT_USER']
            or _recent_audit_count('CONTACT_LOOKUP',since,ip_hash=_request_ip_hash('contact-lookup-ip'))>=current_app.config['CONTACT_LOOKUP_RATE_LIMIT_IP'])

@bp.route('/contacts')
@login_required
def contacts():
    _require_client()
    session.pop('pending_contact',None)
    return _contacts_page(return_to=_contact_return_to())

@bp.route('/contacts/lookup',methods=['POST'])
@login_required
def contact_lookup():
    _require_client()
    return_to=_contact_return_to()
    session.pop('pending_contact',None)
    raw=request.form.get('identifier','')
    identifier=normalize_identifier(raw)
    if identifier is None:
        flash('Escribe un DNI de 8 dígitos o un número de cuenta de 10 a 20 dígitos, solo con números.','error')
        return _contacts_page(400,identifier=raw[:30],return_to=return_to)
    if _contact_lookup_rate_limited():
        flash('Hiciste varias búsquedas seguidas. Por seguridad, espera unos minutos antes de volver a intentarlo.','error')
        return _contacts_page(429,identifier=raw[:30],return_to=return_to)
    account=find_client_account(identifier)
    log(current_user,'CONTACT_LOOKUP','CONTACTOS',result='ENCONTRADO' if account else 'SIN_RESULTADO',ip_local=_request_ip_hash('contact-lookup-ip'))
    db.session.commit()
    if account is None:
        flash(CONTACT_NOT_FOUND_MESSAGE,'error')
        return _contacts_page(404,identifier=raw[:30],return_to=return_to)
    if account.user_id==current_user.id:
        flash('Ese número corresponde a tus propias cuentas. Para moverte saldo entre ellas usa un traslado interno.','error')
        return _contacts_page(400,identifier=raw[:30],return_to=return_to)
    if any(item.account_number==account.number for item in active_beneficiaries(current_user.id).all()):
        flash('Ya tienes a este cliente en tus contactos.','info')
        return _contacts_page(409,identifier=raw[:30],return_to=return_to)
    holder=db.session.get(User,account.user_id)
    session['pending_contact']={'account_id':account.id,'at':now_utc().isoformat()}
    candidate={'name':mask_name(holder.full_name),'last4':account.number[-4:]}
    return _contacts_page(candidate=candidate,return_to=return_to)

@bp.route('/contacts/confirm',methods=['POST'])
@login_required
def contact_confirm():
    _require_client()
    return_to=_contact_return_to()
    pending=session.pop('pending_contact',None)
    try:
        if not isinstance(pending,dict) or now_utc()-datetime.fromisoformat(pending['at'])>CONTACT_PENDING_TTL:
            raise ValueError('La búsqueda venció. Vuelve a ingresar el DNI o el número de cuenta para agregar el contacto.')
        account=Account.query.filter_by(id=pending['account_id'],status='ACTIVA').first()
        if account is None:
            raise ValueError(CONTACT_NOT_FOUND_MESSAGE)
        contact=save_contact(current_user,account)
        log(current_user,'CONTACT_ADDED','CONTACTOS')
        db.session.commit()
    except (ValueError,KeyError,TypeError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error,ValueError) else 'La búsqueda venció. Vuelve a ingresar el DNI o el número de cuenta para agregar el contacto.','error')
        return _contacts_page(400,return_to=return_to)
    except IntegrityError:
        db.session.rollback()
        flash('No se pudo guardar el contacto. Inténtalo nuevamente.','error')
        return _contacts_page(409,return_to=return_to)
    flash(f'Agregaste a {contact.name} a tus contactos.','success')
    return redirect(url_for('main.operation',kind='transfer') if return_to=='transfer' else url_for('main.contacts'))

@bp.route('/contacts/<int:contact_id>/delete',methods=['POST'])
@login_required
def contact_delete(contact_id):
    _require_client()
    contact=get_active_beneficiary(current_user.id,contact_id)
    if contact is None:
        abort(404)
    name=contact.name
    delete_contact(contact)
    log(current_user,'CONTACT_REMOVED','CONTACTOS')
    db.session.commit()
    flash(f'Eliminaste a {name} de tus contactos.','success')
    return redirect(url_for('main.operation',kind='transfer') if _contact_return_to()=='transfer' else url_for('main.contacts'))

@bp.route('/operation/<kind>/send-code',methods=['POST'])
@login_required
def send_transfer_code(kind):
    _require_client()
    if kind not in ('transfer','payment','recharge'): abort(404)
    if not current_user.email:
        flash('Esta cuenta no tiene un correo de seguridad configurado. Contacta a la administración.','error')
        return redirect(url_for('main.operation',kind=kind))
    try:
        required={'transfer':('beneficiary_id','el destinatario'),'payment':('service','el servicio'),'recharge':('phone','el número de celular')}[kind]
        if not request.form.get(required[0],'').strip() or not request.form.get('amount','').strip():
            raise ValueError(f'Completa {required[1]} y el monto antes de enviar el código.')
        amount=parse_amount(request.form.get('amount'))
        account=account_for(current_user)
        if account is None or account.balance<amount:
            raise ValueError('No hay saldo suficiente en la cuenta seleccionada.')
        beneficiary_id=request.form.get('beneficiary_id',type=int)
        if kind=='transfer':
            beneficiary=get_active_beneficiary(current_user.id,beneficiary_id)
            if beneficiary is None:
                raise ValueError('Selecciona un destinatario válido antes de pedir el código.')
            op='TRANSFERENCIA'; details=f'beneficiary:{beneficiary.id}'
            readable=f'S/ {amount:.2f} a {beneficiary.name}, cuenta terminada en {beneficiary.account_number[-4:]}'
        elif kind=='payment':
            service=request.form.get('service','').strip()
            if service not in {'Electricidad','Agua','Internet','Tarjeta académica'}:
                raise ValueError('Selecciona un servicio válido antes de pedir el código.')
            beneficiary=None; op='PAGO'; details='service:'+service
            readable=f'S/ {amount:.2f} para el servicio {service}'
        else:
            phone=request.form.get('phone','').strip()
            if not re.fullmatch(r'[0-9]{7,15}',phone):
                raise ValueError('Ingresa un número de celular válido antes de pedir el código.')
            beneficiary=None; op='RECARGA'; details='phone:'+phone
            readable=f'S/ {amount:.2f} para la recarga terminada en {phone[-4:]}'
        idempotency_key=request.form.get('idempotency_key','').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',idempotency_key):
            raise ValueError('El formulario venció. Vuelve a cargarlo antes de pedir un código.')

        challenge=OperationChallenge.query.filter_by(
            user_id=current_user.id,operation_type=op
        ).with_for_update().first()
        now=now_utc()
        if challenge and now-challenge.last_sent_at<OPERATION_CODE_RESEND:
            flash('Espera antes de solicitar otro código de seguridad.','error')
            return redirect(url_for('main.operation',kind=kind))
        code=f'{secrets.randbelow(1_000_000):06d}'
        if challenge is None:
            challenge=OperationChallenge(user_id=current_user.id,operation_type=op)
            db.session.add(challenge)
        challenge.account_id=account.id
        challenge.beneficiary_id=beneficiary.id if beneficiary else None
        challenge.amount=amount
        challenge.idempotency_key=idempotency_key
        challenge.details_hash=_operation_details_hash(op,details)
        challenge.code_hash=_operation_code_hash(current_user.id,code)
        challenge.expires_at=now+OPERATION_CODE_TTL
        challenge.last_sent_at=now
        challenge.attempts=0
        challenge.consumed_at=None
        send_operation_code(current_user.email,code,readable)
        log(current_user,'OPERATION_OTP_SENT','SEGURIDAD')
        db.session.commit()
        session['pending_operation']={'kind':kind,'key':idempotency_key,'amount':f'{amount:.2f}',
                                      'beneficiary_id':str(beneficiary.id) if beneficiary else '',
                                      'service':request.form.get('service','').strip() if kind=='payment' else '',
                                      'phone':request.form.get('phone','').strip() if kind=='recharge' else ''}
        flash(f'Enviamos un código para confirmar {readable} al correo { _masked_email(current_user.email) }.','success')
    except EmailDeliveryError:
        db.session.rollback()
        current_app.logger.exception('No se pudo enviar el código de transferencia para el usuario %s.',current_user.id)
        flash('No se pudo entregar el código de seguridad. Revisa el correo configurado o contacta a la administración.','error')
    except InvalidOperation:
        db.session.rollback()
        flash('Revisa los datos ingresados e inténtalo de nuevo.','error')
    except ValueError as error:
        db.session.rollback()
        flash(str(error),'error')
    return redirect(url_for('main.operation',kind=kind))

@bp.route('/internal-transfer',methods=['GET','POST'])
@login_required
def internal_transfer():
    _require_client()
    accounts=Account.query.filter_by(user_id=current_user.id,status='ACTIVA').order_by(Account.id).all()
    key=request.form.get('idempotency_key','').strip() if request.method=='POST' else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',key):
        key=secrets.token_urlsafe(24)
    source_id=request.form.get('source_account_id',type=int) if request.method=='POST' else (accounts[0].id if accounts else None)
    destination_id=request.form.get('destination_account_id',type=int) if request.method=='POST' else (accounts[1].id if len(accounts)>1 else None)
    amount_raw=request.form.get('amount','') if request.method=='POST' else ''
    code_sent=False
    def render_transfer(status=200):
        return render_template('internal_transfer.html',accounts=accounts,source_id=source_id,
                               destination_id=destination_id,amount=amount_raw,idempotency_key=key,
                               code_sent=code_sent,masked_email=_masked_email(current_user.email or ''),
                               camera_consent_version=CAMERA_CONSENT_VERSION,kind='internal'),status
    if request.method=='GET':
        session.pop('camera_consent_receipt',None)
        return render_transfer()
    if len(accounts)<2:
        flash('Necesitas dos cuentas activas a tu nombre para hacer un traslado interno.','error')
        return render_transfer(400)
    source=Account.query.filter_by(id=source_id,user_id=current_user.id,status='ACTIVA').first()
    destination=Account.query.filter_by(id=destination_id,user_id=current_user.id,status='ACTIVA').first()
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',key):
            raise ValueError('El formulario venció. Vuelve a cargarlo antes de continuar.')
        if source is None or destination is None or source.id==destination.id:
            raise ValueError('Selecciona dos cuentas activas distintas que estén a tu nombre.')
        if not amount_raw.strip():
            raise ValueError('Completa las cuentas y el monto antes de continuar.')
        amount=parse_amount(amount_raw)
        if source.currency!=destination.currency:
            raise ValueError('Las cuentas deben tener la misma moneda.')
        duration_seconds=parse_duration_seconds(request.form.get('form_duration_seconds'),default=None)
        if source.balance<amount:
            raise ValueError('Saldo insuficiente en la cuenta de origen.')
        action=request.form.get('action')
        if action=='send_code':
            if not current_user.email:
                raise ValueError('Configura un correo de seguridad antes de solicitar el código.')
            challenge=OperationChallenge.query.filter_by(user_id=current_user.id,operation_type='TRASLADO_PROPIO').with_for_update().first()
            now=now_utc()
            if challenge and now-challenge.last_sent_at<OPERATION_CODE_RESEND:
                code_sent=True
                raise ValueError('Espera antes de solicitar otro código de seguridad.')
            code=f'{secrets.randbelow(1_000_000):06d}'
            if challenge is None:
                challenge=OperationChallenge(user_id=current_user.id,operation_type='TRASLADO_PROPIO')
                db.session.add(challenge)
            challenge.account_id=source.id
            challenge.beneficiary_id=None
            challenge.amount=amount
            challenge.idempotency_key=key
            challenge.details_hash=_operation_details_hash('TRASLADO_PROPIO',f'destination:{destination.id}')
            challenge.code_hash=_operation_code_hash(current_user.id,code)
            challenge.expires_at=now+OPERATION_CODE_TTL
            challenge.last_sent_at=now
            challenge.attempts=0
            challenge.consumed_at=None
            send_operation_code(current_user.email,code,f'S/ {amount:.2f} entre tus cuentas terminadas en {source.number[-4:]} y {destination.number[-4:]}')
            log(current_user,'INTERNAL_TRANSFER_OTP_SENT','SEGURIDAD')
            db.session.commit()
            code_sent=True
            flash('Enviamos un código de seguridad al correo registrado.','success')
        elif action=='confirm':
            previous=Transaction.query.filter_by(account_id=source.id,idempotency_key=key,operation_type='TRANSFERENCIA').first()
            if previous:
                return redirect(url_for('main.receipt',transaction_id=previous.id))
            if not _camera_receipt_ok('internal',key):
                code_sent=True
                raise ValueError(CAMERA_REQUIRED_MESSAGE)
            capture=read_capture(request.files.get('capture'),current_app.config['CAPTURE_MAX_BYTES'])
            challenge=OperationChallenge.query.filter_by(user_id=current_user.id,operation_type='TRASLADO_PROPIO').with_for_update().first()
            now=now_utc()
            if (not challenge or challenge.consumed_at is not None or now>=challenge.expires_at
                    or challenge.account_id!=source.id or challenge.amount!=amount
                    or challenge.idempotency_key!=key
                    or not hmac.compare_digest(challenge.details_hash or '',_operation_details_hash('TRASLADO_PROPIO',f'destination:{destination.id}'))):
                raise ValueError('Solicita un nuevo código para estas cuentas y este monto.')
            code=request.form.get('otp_code','').strip()
            if len(code)!=6 or not code.isascii() or not code.isdigit() or not hmac.compare_digest(
                    challenge.code_hash,_operation_code_hash(current_user.id,code)):
                challenge.attempts+=1
                if challenge.attempts>=OPERATION_CODE_MAX_ATTEMPTS:
                    challenge.consumed_at=now
                log(current_user,'INTERNAL_TRANSFER_OTP_FAILURE','SEGURIDAD',result='RECHAZADA')
                db.session.commit()
                raise ValueError('El código de confirmación no es válido o venció.')
            challenge.consumed_at=now
            tx=process_internal_transfer(current_user,source.id,destination.id,amount,
                                         duration_seconds=duration_seconds,idempotency_key=key,
                                         capture=capture,capture_meta=_capture_meta())
            session.pop('camera_consent_receipt',None)
            flash('Traslado entre tus cuentas registrado. No tiene efectos financieros externos.','success')
            return redirect(url_for('main.receipt',transaction_id=tx.id))
        else:
            raise ValueError('Selecciona una acción válida.')
    except EmailDeliveryError:
        db.session.rollback()
        flash('No se pudo entregar el código de seguridad. Revisa el correo configurado o contacta a administración.','error')
    except InvalidOperation:
        db.session.rollback()
        flash('Revisa los datos ingresados e inténtalo de nuevo.','error')
    except ValueError as error:
        db.session.rollback()
        flash(str(error),'error')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('No se pudo registrar el traslado entre cuentas.')
        flash('No se pudo completar el traslado. Inténtalo nuevamente.','error')
    if request.form.get('action')=='confirm':
        code_sent=True
    return render_transfer()

@bp.route('/receipt/<int:transaction_id>')
@login_required
def receipt(transaction_id):
    _require_client()
    tx=Transaction.query.join(Account).filter(Transaction.id==transaction_id,Account.user_id==current_user.id).first_or_404()
    return render_template('receipt.html',tx=tx)
@bp.route('/admin')
@login_required
def admin():
    if current_user.role!='ADMINISTRADOR': abort(403)
    sessions=InteractionSession.query.order_by(desc(InteractionSession.started_at)).limit(100).all()
    return render_template('admin.html',users=User.query.count(),operations=Transaction.query.count(),sessions=sessions,logs=AuditLog.query.order_by(desc(AuditLog.created_at)).limit(20).all())

@bp.route('/admin/capturas')
@login_required
def admin_captures():
    if current_user.role!='ADMINISTRADOR': abort(403)
    captures=OperationCapture.query.order_by(desc(OperationCapture.created_at)).limit(100).all()
    log(current_user,'CAPTURE_LIST_VIEWED','PRIVACIDAD')
    db.session.commit()
    page=render_template('admin_captures.html',captures=captures,retention_days=current_app.config['CAPTURE_RETENTION_DAYS'])
    return Response(page,mimetype='text/html',headers={'Cache-Control':'no-store'})

@bp.route('/admin/capturas/<int:capture_id>/imagen')
@login_required
def admin_capture_image(capture_id):
    # Las imágenes nunca tienen URL pública: solo se sirven a un administrador con sesión y quedan en la auditoría.
    if current_user.role!='ADMINISTRADOR': abort(403)
    capture=db.session.get(OperationCapture,capture_id)
    if capture is None: abort(404)
    log(current_user,'CAPTURE_IMAGE_VIEWED','PRIVACIDAD')
    db.session.commit()
    return Response(capture.image,mimetype=capture.mime_type,headers={
        'Cache-Control':'no-store, private','X-Content-Type-Options':'nosniff',
        'Content-Disposition':'inline','Cross-Origin-Resource-Policy':'same-origin',
    })

@bp.route('/support')
@login_required
def support():
    if current_user.role!='SOPORTE':
        abort(403)
    events=AuditLog.query.order_by(desc(AuditLog.created_at)).limit(10).all()
    return render_template('support.html',accounts=Account.query.count(),operations=Transaction.query.count(),events=events)

importlib.import_module('.recovery',__package__)  # registra /recuperar sobre este blueprint
