import os
from datetime import date, datetime, time
from decimal import Decimal
from flask import current_app
from ..models import db, User, Account, Beneficiary, BankCard, Transaction, AuditLog

_SYNTHETIC_CLIENTS=(
    {'name':'Lucía Torres Rojas','opening':'6234.70','income':'4725.35','expenses':('212.68','286.43','89.90','640.25')},
    {'name':'Mateo Quispe Salazar','opening':'4878.90','income':'3884.60','expenses':('54.70','318.45','79.90','426.35')},
    {'name':'Valeria Paredes Huamán','opening':'8021.35','income':'5210.25','expenses':('1650.75','181.30','387.60','720.45')},
    {'name':'Diego Salazar Ríos','opening':'3592.65','income':'3428.90','expenses':('156.20','244.80','69.90','94.50')},
    {'name':'Camila Huamán Quispe','opening':'9314.20','income':'4682.15','expenses':('84.90','535.70','129.35','1160.80')},
)

def _luhn_digit(first_fifteen):
    checksum=0
    for index,digit in enumerate(reversed(first_fifteen)):
        value=int(digit)*(2 if index%2==0 else 1)
        checksum+=value-9 if value>9 else value
    return str((-checksum)%10)

def _luhn_valid(number):
    checksum=0
    for index,digit in enumerate(reversed(number)):
        value=int(digit)*(1 if index%2==0 else 2)
        checksum+=value-9 if value>9 else value
    return checksum%10==0

def _synthetic_movements(opening,income,expenses,client_index):
    movements=[(datetime.combine(date(2025,9,30),time()),'INGRESO','Saldo de apertura de muestra',Decimal(opening))]
    categories=(('Servicio del hogar', 'Compras del hogar'),('Servicio de agua','Supermercado'),('Pago de vivienda','Servicio de electricidad'),('Servicios del hogar','Mercado local'),('Telefonía','Compras del hogar'))
    expense_names=categories[client_index]
    income_amount=Decimal(income)
    expense_values=tuple(Decimal(value) for value in expenses)
    for month_offset in range(12):
        year=2025+(9+month_offset)//12
        month=(9+month_offset)%12+1
        month_date=lambda day: datetime.combine(date(year,month,day),time())
        movements.append((month_date(5),'INGRESO','Abono de ingresos de muestra',income_amount))
        expense_one=expense_values[month_offset%len(expense_values)]
        movements.append((month_date(8+(month_offset%3)),'PAGO',expense_names[0],-expense_one))
        expense_two=expense_values[(month_offset+1)%len(expense_values)]
        movements.append((month_date(13+(month_offset%3)),'PAGO',expense_names[1],-expense_two))
        market=Decimal(f'{(23+client_index*7+month_offset*3):02d}.{(13+client_index*11+month_offset*7)%100:02d}')
        transport=Decimal(f'{(8+client_index*2+month_offset%5):02d}.{(47+client_index*9+month_offset*13)%100:02d}')
        movements.append((month_date(16+(month_offset%4)),'PAGO','Compra cotidiana de muestra',-market))
        if month_offset in (3,7,11):
            transfer=Decimal(f'{(95+client_index*13+month_offset*4):03d}.{(27+client_index*17+month_offset*9)%100:02d}')
            movements.append((month_date(22),'TRANSFERENCIA','Transferencia a beneficiario de muestra',-transfer))
        movements.append((month_date(26+(month_offset%3)),'PAGO','Transporte local de muestra',-transport))
    total=Decimal('0.00')
    return [(when,kind,label,amount,(total:=total+amount)) for when,kind,label,amount in movements]

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
    client=User(username='demo', full_name='Alex Rivera', role='CLIENTE',email=client_email,is_seed=True); client.set_password(os.getenv('DEMO_PASSWORD','Demo123!'))
    admin=User(username='admin', full_name='Administración', role='ADMINISTRADOR',email=admin_email,is_seed=True); admin.set_password(os.getenv('ADMIN_PASSWORD','Admin123!'))
    db.session.add_all([client,admin]); db.session.flush()
    account=Account(user_id=client.id, account_type='Cuenta de ahorros', number='001234567890', balance=Decimal('8540.50'),is_seed=True)
    db.session.add(account); db.session.flush()
    db.session.add_all([
        Beneficiary(user_id=client.id,name='Beneficiario de muestra 1',account_number='009876543210',is_seed=True),
        Beneficiary(user_id=client.id,name='Beneficiario de muestra 2',account_number='008812340056',is_seed=True),
        BankCard(user_id=client.id,account_id=account.id,last4='4582',limit_amount=Decimal('3000'),is_seed=True),
        Transaction(account_id=account.id,operation_type='INGRESO',description='Saldo de apertura de muestra',amount=Decimal('6106.40'),balance_after=Decimal('6106.40'),created_at=datetime(2025,9,30),is_seed=True),
        Transaction(account_id=account.id,operation_type='INGRESO',description='Abono de ingresos de muestra',amount=Decimal('2500.00'),balance_after=Decimal('8606.40'),is_seed=True),
        Transaction(account_id=account.id,operation_type='PAGO',description='Servicio del hogar',amount=Decimal('-65.90'),balance_after=Decimal('8540.50'),is_seed=True),
    ])
    db.session.commit()

def seed_synthetic_clients():
    """Create reproducible sample records only in isolated in-memory test SQLite."""
    database_url=db.engine.url
    if (current_app.config.get('APP_ENV')!='testing'
            or database_url.get_backend_name()!='sqlite'
            or database_url.database!=':memory:'):
        raise RuntimeError('Los datos sintéticos solo se pueden generar en SQLite de pruebas en memoria.')

    seeded=[]
    for index,profile in enumerate(_SYNTHETIC_CLIENTS,1):
        username=f'seed-client-{index:03d}'
        if User.query.filter_by(username=username).first():
            continue
        user=User(username=username,full_name=profile['name'],email=f'seed-{index:03d}@example.test',
                  phone=f'900000{index:03d}',role='CLIENTE',is_seed=True)
        user.set_password(f'SyntheticOnly-{index:03d}!')
        db.session.add(user)
        db.session.flush()

        account_number=f'{90000000000000000000+index:020d}'
        cci=f'999{index:017d}'
        account=Account(user_id=user.id,account_type='Cuenta de ahorros de muestra',number=account_number,
                        cci=cci,balance=Decimal(profile['opening']),is_seed=True)
        db.session.add(account)
        db.session.flush()

        beneficiary_number=f'{91000000000000000000+index:020d}'
        db.session.add(Beneficiary(user_id=user.id,name=f'Beneficiario de muestra {index}',
                                   account_number=beneficiary_number,bank_name='Norte · prueba',is_seed=True))

        pan_body=f'424242{index:09d}'
        test_pan=pan_body+_luhn_digit(pan_body)
        if not _luhn_valid(test_pan):
            raise AssertionError('El número de tarjeta transitorio de prueba no pasó Luhn.')
        db.session.add(BankCard(user_id=user.id,account_id=account.id,last4=test_pan[-4:],
                                card_type='Visa de muestra',status='Activa · prueba',
                                limit_amount=Decimal('3000.00'),is_seed=True))

        history=_synthetic_movements(profile['opening'],profile['income'],profile['expenses'],index-1)
        for created_at,operation_type,description,amount,balance_after in history:
            db.session.add(Transaction(account_id=account.id,operation_type=operation_type,
                                       description=description,amount=amount,balance_after=balance_after,
                                       created_at=created_at,is_seed=True))
        account.balance=history[-1][4]
        seeded.append(user)

    db.session.commit()
    return seeded
