import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import re
from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import desc, or_
from sqlalchemy.exc import IntegrityError
from ..models import db, User, LoginChallenge, Account, BankCard, Beneficiary, Transaction, InteractionSession, AuditLog, now_utc
from ..services.banking_service import account_for, process_operation, log, operation_risk_for
from ..services.email_service import EmailDeliveryError, send_login_code
from ..ai.camera_analysis import analyze_frame
import base64
import time
import cv2
import numpy as np
bp=Blueprint('main',__name__)

def _login_code_hash(user_id, code):
    key=str(current_app.config['SECRET_KEY']).encode('utf-8')
    message=f'{user_id}:{code}'.encode('utf-8')
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
        'eyebrow': 'UNA EXPERIENCIA PENSADA PARA TI',
        'title': 'Tu día, tus planes y una experiencia digital más clara.',
        'intro': 'Norte reúne ejemplos sencillos para que puedas conocer cómo se organiza una experiencia de banca digital, a tu ritmo y desde cualquier pantalla.',
        'icon': 'bi-people',
        'highlights': [
            ('bi-calendar2-check', 'Organiza lo cotidiano', 'Explora un resumen de cuenta de demostración y revisa cómo se presenta la actividad reciente.'),
            ('bi-lightbulb', 'Aprende paso a paso', 'Conoce recorridos guiados para transferencias, pagos y recargas con información clara en cada etapa.'),
            ('bi-phone', 'Continúa desde cualquier pantalla', 'La interfaz se adapta a computadora, tablet y celular para que puedas explorar con comodidad.'),
        ],
        'note': 'Los perfiles, saldos y operaciones son ficticios y se muestran exclusivamente con fines demostrativos.',
        'cta_label': 'Explorar herramientas',
        'cta_endpoint': 'main.herramientas',
    },
    'herramientas': {
        'eyebrow': 'FUNCIONES PARA EXPLORAR',
        'title': 'Todo lo que puedes practicar, explicado con claridad.',
        'intro': 'Recorre las funciones de Norte y descubre cómo se conectan las tareas habituales de una experiencia financiera digital.',
        'icon': 'bi-grid',
        'highlights': [
            ('bi-wallet2', 'Resumen de cuenta', 'Consulta una vista de práctica con saldo ilustrativo y accesos a las principales áreas.'),
            ('bi-clock-history', 'Movimientos y comprobantes', 'Revisa actividad ficticia y conoce cómo se presenta el detalle de una operación.'),
            ('bi-arrow-left-right', 'Transferencias guiadas', 'Sigue un recorrido con beneficiarios de prueba y revisa la información antes de continuar.'),
            ('bi-receipt-cutoff', 'Pagos y recargas', 'Explora formularios de demostración para servicios y recargas sin procesar pagos.'),
            ('bi-credit-card-2-front', 'Tarjetas de práctica', 'Conoce una vista ilustrativa de tarjetas ficticias dentro del resumen de Norte.'),
        ],
        'note': 'Las herramientas son demostrativas: no se conectan a cuentas bancarias ni realizan operaciones reales.',
        'cta_label': 'Conocer la seguridad',
        'cta_endpoint': 'main.seguridad',
    },
    'seguridad': {
        'eyebrow': 'SEGURIDAD Y TRANSPARENCIA',
        'title': 'Explora con tranquilidad: este entorno es una simulación.',
        'intro': 'La confianza también significa saber qué servicio estás usando. Norte es un proyecto académico independiente y no es un banco ni representa al BCP.',
        'icon': 'bi-shield-check',
        'highlights': [
            ('bi-person-vcard', 'Usa solo datos de práctica', 'No ingreses contraseñas bancarias reales, números de tarjeta ni otra información financiera sensible.'),
            ('bi-cash-coin', 'No hay dinero real', 'Los saldos, beneficiarios, movimientos y comprobantes son ficticios; no se procesan pagos ni transferencias.'),
            ('bi-info-circle', 'Identifica los límites de la demo', 'El acceso y sus pantallas ilustran flujos digitales, pero no equivalen a servicios bancarios reales ni a asesoría financiera.'),
        ],
        'note': 'Protege tus credenciales reales: Norte no necesita ni debe recibirlas.',
        'cta_label': 'Acerca del proyecto',
        'cta_endpoint': 'main.acerca',
    },
    'acerca': {
        'eyebrow': 'ACERCA DE NORTE',
        'title': 'Una idea académica para hacer más comprensible la banca digital.',
        'intro': 'Norte nació como un proyecto de demostración con una pregunta sencilla: ¿cómo sería una experiencia financiera si cada pantalla explicara con claridad qué puedes hacer y qué ocurre después?',
        'icon': 'bi-compass',
        'highlights': [
            ('bi-chat-square-text', 'La idea', 'Crear un entorno cercano donde se puedan entender los pasos y la organización de una banca digital.'),
            ('bi-window', 'La experiencia', 'Reunir cuentas, actividad y recorridos guiados en una interfaz adaptable y fácil de explorar.'),
            ('bi-bookmark-check', 'El propósito', 'Promover claridad, aprendizaje y transparencia a través de ejemplos, no de servicios financieros reales.'),
        ],
        'timeline': [
            ('La pregunta', 'La propuesta parte de hacer más claros los flujos digitales cotidianos.'),
            ('El prototipo', 'Se construye una experiencia navegable con pantallas y datos ficticios.'),
            ('La demostración', 'Norte reúne ejemplos para explorar desde diferentes dispositivos.'),
        ],
        'note': 'La historia, la marca y los productos de Norte son ficticios. Este sitio no pertenece a una entidad financiera real.',
        'cta_label': 'Volver al inicio',
        'cta_endpoint': 'main.index',
    },
}


def _public_info_page(page_key):
    return render_template('public_info.html', page=PUBLIC_INFO_PAGES[page_key], page_key=page_key)


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


@bp.route('/login',methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.admin' if current_user.role=='ADMINISTRADOR' else 'main.dashboard'))
    if request.method=='GET' and session.get('pending_login_user_id'):
        return redirect(url_for('main.verify_login'))
    if request.method=='POST':
        identifier=request.form.get('username','').strip()
        # El acceso acepta el usuario heredado, el DNI registrado o el correo.
        # Consultar el DNI de forma explícita evita que el texto de la interfaz
        # prometa una alternativa que no funcione para cuentas migradas.
        user=User.query.filter(or_(
            User.username==identifier,
            User.dni==identifier,
            User.email==identifier.lower(),
        )).first()
        if user and user.check_password(request.form.get('password','')):
            if not user.email:
                flash('Esta cuenta no tiene un correo de seguridad configurado. Contacta a la administración.','error')
                return redirect(url_for('main.login'))
            result=_issue_login_code(user)
            if result=='delivery_error':
                flash('No se pudo enviar el código de seguridad. Revisa la configuración de correo o contacta a la administración.','error')
                return redirect(url_for('main.login'))
            if result=='cooldown':
                flash('Ya se envió un código recientemente. Revisa el correo registrado o espera antes de solicitar otro.','info')
            else:
                flash('Enviamos un código de seguridad al correo registrado.','success')
            return redirect(url_for('main.verify_login'))
        flash('Usuario o clave incorrectos.','error')
    return render_template('login.html')

@bp.route('/register',methods=['GET','POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.admin' if current_user.role=='ADMINISTRADOR' else 'main.dashboard'))
    if request.method=='POST':
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
        allowed_password_characters="!@#$%&*+-=?"
        if (
            not 10<=len(password)<=64
            or not password.isascii()
            or any(character.isspace() or character=='_' for character in password)
            or not any(character.isupper() for character in password)
            or not any(character.islower() for character in password)
            or not any(character.isdigit() for character in password)
            or not any(character in allowed_password_characters for character in password)
            or any(not (character.isalnum() or character in allowed_password_characters) for character in password)
        ):
            errors.append('La contraseña debe tener 10-64 caracteres ASCII, mayúscula, minúscula, número y símbolo (!@#$%&*+-=?); no puede contener espacios ni guion bajo (_).')
        if password!=password_confirmation:
            errors.append('Las contraseñas no coinciden.')
        if errors:
            for error in errors:
                flash(error,'error')
            return render_template('register.html'),400

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
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Ya existe una cuenta con ese DNI o correo. Inicia sesión o usa otros datos.','error')
            return render_template('register.html'),409

        session.pop('pending_login_user_id',None)
        flash('Cuenta demo creada. Inicia sesión con tu DNI o correo y contraseña; te enviaremos un código de seguridad.','success')
        return redirect(url_for('main.login'))
    return render_template('register.html')

@bp.route('/login/verify',methods=['GET','POST'])
def verify_login():
    if current_user.is_authenticated:
        return redirect(url_for('main.admin' if current_user.role=='ADMINISTRADOR' else 'main.dashboard'))
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
            session['login_2fa_verified']=True
            log(user,'LOGIN','SEGURIDAD')
            db.session.commit()
            return redirect(url_for('main.admin' if user.role=='ADMINISTRADOR' else 'main.dashboard'))

    return render_template('login_verify.html',masked_email=_masked_email(user.email))

@bp.route('/login/resend',methods=['POST'])
def resend_login_code():
    if current_user.is_authenticated:
        return redirect(url_for('main.admin' if current_user.role=='ADMINISTRADOR' else 'main.dashboard'))
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
    account=account_for(current_user)
    recent=Transaction.query.filter_by(account_id=account.id).order_by(desc(Transaction.created_at)).limit(5).all() if account else []
    cards=BankCard.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html',account=account,recent=recent,cards=cards)
@bp.route('/movements')
@login_required
def movements():
    account=account_for(current_user); items=Transaction.query.filter_by(account_id=account.id).order_by(desc(Transaction.created_at)).all() if account else []
    return render_template('movements.html',items=items,account=account)
@bp.route('/cards')
@login_required
def cards(): return render_template('cards.html',cards=BankCard.query.filter_by(user_id=current_user.id).all())
@bp.route('/operation-risk')
@login_required
def operation_risk():
    kinds={'transfer':'TRANSFERENCIA','payment':'PAGO','recharge':'RECARGA'}
    kind=kinds.get(request.args.get('kind',''))
    if not kind: abort(400)
    try:
        amount=Decimal(request.args.get('amount','0'))
        if not (Decimal('0.01')<=amount<=Decimal('10000')): raise ValueError
        return jsonify(operation_risk_for(current_user,kind,amount))
    except (ValueError,InvalidOperation):
        return jsonify(error='Ingresa un monto entre S/ 0.01 y S/ 10,000.00.'),400
@bp.route('/camera/analyze', methods=['POST'])
@login_required
def camera_analyze():
    token = request.form.get('csrf_token')
    image_data = request.form.get('frame', '')
    if not image_data:
        return jsonify(error='No se recibió el fotograma de la cámara.'), 400
    try:
        raw = base64.b64decode(image_data.split(',', 1)[-1], validate=True)
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        result = analyze_frame(frame)
        if result.get('ready'):
            session['camera_state'] = {
                'ready': True,
                'checked_at': time.time(),
                'face_confidence': result.get('face_confidence', 0),
            }
        else:
            session['camera_state'] = {'ready': False, 'checked_at': time.time()}
        return jsonify(result)
    except Exception:
        session['camera_state'] = {'ready': False, 'checked_at': time.time()}
        return jsonify(error='No se pudo analizar el fotograma.'), 400

@bp.route('/operation/<kind>',methods=['GET','POST'])
@login_required
def operation(kind):
    if kind not in ('transfer','payment','recharge'): abort(404)
    recipients=Beneficiary.query.filter_by(user_id=current_user.id).all(); account=account_for(current_user)
    if request.method=='POST':
        camera_state = session.get('camera_state', {})
        camera_ok = request.form.get('camera_verified') == 'yes' and camera_state.get('ready') and (time.time() - camera_state.get('checked_at', 0) <= 10)
        if not camera_ok:
            flash('La verificación de cámara no está vigente o el rostro no es suficientemente visible. Corrige la posición y vuelve a verificar.','error')
            return render_template('operation.html',kind=kind,recipients=recipients,account=account), 400
        try:
            amount=Decimal(request.form.get('amount','0'))
            duration_seconds=int(request.form.get('form_duration_seconds','0'))
            if not 0<=duration_seconds<=86400:
                raise ValueError('No se pudo validar la duración del formulario.')
            beneficiary_id=request.form.get('beneficiary_id',type=int)
            if kind=='transfer':
                recipient=Beneficiary.query.filter_by(id=beneficiary_id,user_id=current_user.id).first()
                if not recipient: raise ValueError('Destinatario no válido.')
                description='Transferencia simulada a '+recipient.name; op='TRANSFERENCIA'
            elif kind=='payment': description='Pago simulado: '+request.form.get('service','Servicio')[:70]; op='PAGO'
            else: description='Recarga simulada: '+request.form.get('phone','Número demo')[:40]; op='RECARGA'
            tx=process_operation(current_user,op,amount,description,beneficiary_id,request.form.get('service'),True,face_detected=True,face_confidence=camera_state.get('face_confidence'),duration_seconds=duration_seconds)
            flash('Operación ficticia completada. No se movió dinero real.','success')
            return redirect(url_for('main.receipt',transaction_id=tx.id))
        except (ValueError,InvalidOperation) as e: flash(str(e),'error')
    return render_template('operation.html',kind=kind,recipients=recipients,account=account)
@bp.route('/receipt/<int:transaction_id>')
@login_required
def receipt(transaction_id):
    tx=Transaction.query.join(Account).filter(Transaction.id==transaction_id,Account.user_id==current_user.id).first_or_404()
    return render_template('receipt.html',tx=tx)
@bp.route('/admin')
@login_required
def admin():
    if current_user.role!='ADMINISTRADOR': abort(403)
    sessions=InteractionSession.query.order_by(desc(InteractionSession.started_at)).limit(100).all()
    return render_template('admin.html',users=User.query.count(),operations=Transaction.query.count(),sessions=sessions,logs=AuditLog.query.order_by(desc(AuditLog.created_at)).limit(20).all())
