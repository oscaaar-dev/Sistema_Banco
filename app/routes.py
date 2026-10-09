from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_user, logout_user, login_required, current_user
from decimal import Decimal, InvalidOperation
from sqlalchemy import desc
from . import bp
from ..models import db, User, Account, BankCard, Beneficiary, Transaction, InteractionSession, MLResult, AuditLog
from ..services.banking_service import account_for, process_operation, log

@bp.route('/')
def index(): return redirect(url_for('main.dashboard') if current_user.is_authenticated else url_for('main.login'))
@bp.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        user=User.query.filter_by(username=request.form.get('username','').strip()).first()
        if user and user.check_password(request.form.get('password','')):
            login_user(user); log(user,'LOGIN','SEGURIDAD'); db.session.commit()
            return redirect(url_for('main.admin' if user.role=='ADMINISTRADOR' else 'main.dashboard'))
        flash('Usuario o clave incorrectos.','error')
    return render_template('login.html')
@bp.route('/logout',methods=['POST'])
@login_required
def logout(): log(current_user,'LOGOUT','SEGURIDAD'); db.session.commit(); logout_user(); return redirect(url_for('main.login'))
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
@bp.route('/operation/<kind>',methods=['GET','POST'])
@login_required
def operation(kind):
    kinds={'transfer':'TRANSFERENCIA','payment':'PAGO','recharge':'RECARGA'}
    if kind not in kinds: abort(404)
    recipients=Beneficiary.query.filter_by(user_id=current_user.id).all()
    account=account_for(current_user)
    if request.method=='POST':
        try:
            amount=Decimal(request.form.get('amount','0'))
            if kind=='transfer':
                recipient=Beneficiary.query.filter_by(id=request.form.get('beneficiary_id',type=int),user_id=current_user.id).first()
                if not recipient: raise ValueError('Destinatario no válido.')
                description='Transferencia simulada a '+recipient.name
            elif kind=='payment': description='Pago simulado: '+request.form.get('service','Servicio')[:70]
            else: description='Recarga simulada: '+request.form.get('phone','Número demo')[:40]
            tx=process_operation(current_user,kinds[kind],amount,description,request.form.get('beneficiary_id',type=int),request.form.get('service'),request.form.get('consent')=='yes')
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
