"""Ayudantes de prueba del sistema antifraude. Solo datos sintéticos, en SQLite en memoria; nada lee ni escribe ``data/``."""
from datetime import timedelta
from decimal import Decimal

from app.models import Account, AdminPermission, AuditLog, Beneficiary, Transaction, User, db, now_utc
from app.services.admin_service import PERMISSIONS

PASSWORD = 'Clave-De-Prueba-1!'


def make_client(username, dni, number, balance='1000.00', role='CLIENTE', email=None, permissions=None, status='ACTIVA'):
    """Crea un usuario con una cuenta activa. Devuelve (user_id, account_id).

    Un ADMINISTRADOR recibe **todos** los permisos por defecto (así las pruebas antifraude existentes siguen cubriendo el flujo completo);
    ``permissions=()`` crea uno sin permisos y ``permissions=(...)`` uno con permisos concretos. ADMIN_GENERAL no necesita filas."""
    user = User(username=username, full_name=f'Cliente {username}', dni=dni, email=email or f'{username}@example.test', role=role, account_status=status)
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.flush()
    if role == 'ADMINISTRADOR':
        for code in (PERMISSIONS if permissions is None else permissions):
            db.session.add(AdminPermission(user_id=user.id, permission=code))
    account = Account(user_id=user.id, account_type='Cuenta de ahorros', number=number, balance=Decimal(balance))
    db.session.add(account)
    db.session.commit()
    return user.id, account.id


def make_beneficiary(user_id, account_number, name='Destino P***'):
    beneficiary = Beneficiary(user_id=user_id, name=name, account_number=account_number, bank_name='Norte')
    db.session.add(beneficiary)
    db.session.commit()
    return beneficiary.id


def add_debits(account_id, seconds_ago, amount='10.00', kind='TRANSFERENCIA', balance_after='1000.00'):
    """Salidas de dinero ya registradas (historial) hace ``seconds_ago`` segundos cada una."""
    now = now_utc()
    for seconds in seconds_ago:
        db.session.add(Transaction(account_id=account_id, operation_type=kind, description=f'{kind} previa', amount=-Decimal(amount),
                                   balance_after=Decimal(balance_after), created_at=now - timedelta(seconds=seconds)))
    db.session.commit()


def add_audit(user_id, action, minutes_ago=1, ip_local=None, count=1):
    now = now_utc()
    for _ in range(count):
        db.session.add(AuditLog(user_id=user_id, action=action, module='SEGURIDAD', result='RECHAZADA', ip_local=ip_local,
                                created_at=now - timedelta(minutes=minutes_ago)))
    db.session.commit()


def balance(account_id):
    return db.session.get(Account, account_id).balance


def counts(*models):
    return {model.__tablename__: model.query.count() for model in models}


def session_client(app, user_id):
    """Cliente HTTP con una sesión ya autenticada (el flujo real de OTP se prueba por separado)."""
    client = app.test_client()
    with app.app_context():
        stamp = db.session.get(User, user_id).session_stamp()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
        browser_session['pwd_stamp'] = stamp
    return client
