import os
from decimal import Decimal
from flask import current_app
from ..models import db, User, Account, Beneficiary, BankCard, Transaction, AuditLog

def seed_demo():
    if current_app.config.get('APP_ENV')=='production':
        usernames={user.username for user in User.query.filter(User.username.in_(('demo','admin'))).all()}
        if {'demo','admin'}.issubset(usernames):
            return
        raise RuntimeError('La base de producción debe migrarse con las cuentas demo y admin existentes antes de iniciar.')

    client_email=os.getenv('DEMO_EMAIL','').strip().lower() or None
    admin_email=os.getenv('ADMIN_EMAIL','').strip().lower() or None
    existing_users=User.query.filter(User.username.in_(('demo','admin'))).all()
    if existing_users:
        emails={'demo':client_email,'admin':admin_email}
        changed=False
        for user in existing_users:
            if user.username=='admin' and admin_email and user.email!=admin_email:
                user.email=admin_email
                changed=True
            if not user.email and emails[user.username]:
                user.email=emails[user.username]
                changed=True
        if changed:
            db.session.commit()
        if User.query.first():
            return
    client=User(username='demo', full_name='Alex Rivera', role='CLIENTE',email=client_email); client.set_password(os.getenv('DEMO_PASSWORD','Demo123!'))
    admin=User(username='admin', full_name='Administración', role='ADMINISTRADOR',email=admin_email); admin.set_password(os.getenv('ADMIN_PASSWORD','Admin123!'))
    db.session.add_all([client,admin]); db.session.flush()
    account=Account(user_id=client.id, account_type='Cuenta de ahorros', number='001234567890', balance=Decimal('8540.50'))
    db.session.add(account); db.session.flush()
    db.session.add_all([
        Beneficiary(user_id=client.id,name='María Torres',account_number='009876543210'),
        Beneficiary(user_id=client.id,name='Diego Salas',account_number='008812340056'),
        BankCard(user_id=client.id,account_id=account.id,last4='4582',limit_amount=Decimal('3000')),
        Transaction(account_id=account.id,operation_type='INGRESO',description='Abono de demostración',amount=Decimal('2500'),balance_after=Decimal('8540.50')),
        Transaction(account_id=account.id,operation_type='PAGO',description='Pago simulado de servicio',amount=Decimal('-65.90'),balance_after=Decimal('8540.50')),
    ])
    db.session.commit()
