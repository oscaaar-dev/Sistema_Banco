"""scripts/seed_demo.py: carga de demostración en una sola transacción, idempotente y verificada contra saldos_finales."""
import csv
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from app.models import Account, AuditLog, BankCard, Transaction, User, db

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('seed_demo', ROOT / 'scripts' / 'seed_demo.py')
seed_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed_demo)

CLIENT_COLS = ['nombre_completo', 'dni', 'correo', 'telefono', 'fecha_nacimiento', 'departamento', 'numero_cuenta', 'saldo_inicial']
MOVE_COLS = ['operacion_id', 'fecha', 'cliente_dni', 'tipo', 'descripcion', 'monto', 'contraparte_dni']


def write_csv(path, columns, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, '') for column in columns})


def client(dni, name, email, number):
    return {'nombre_completo': name, 'dni': dni, 'correo': email, 'telefono': '987654321', 'fecha_nacimiento': '1990-05-17',
            'departamento': 'Lima', 'numero_cuenta': number, 'saldo_inicial': '0.00'}


def move(op, when, dni, kind, desc, amount, other=''):
    return {'operacion_id': op, 'fecha': when, 'cliente_dni': dni, 'tipo': kind, 'descripcion': desc, 'monto': amount, 'contraparte_dni': other}


@pytest.fixture
def world(tmp_path):
    """Dos clientes nuevos (Ana y Beto), una transferencia entre ellos, una compra y Oscar con cuenta ...4779."""
    clients = [client('11111111', 'Ana Prueba', 'ana@example.test', '1111-000001'),
               client('22222222', 'Beto Prueba', 'beto@example.test', '2222-000002')]
    moves = [
        move('OP-1', '2026-08-01 10:00:00', '11111111', 'deposito', 'Depósito inicial', '500.00'),
        move('OP-2', '2026-08-01 11:00:00', '22222222', 'deposito', 'Depósito inicial', '50.00'),
        move('OP-3', '2026-08-02 09:00:00', '11111111', 'transferencia_enviada', 'Transferencia enviada', '-100.00', '22222222'),
        move('OP-3', '2026-08-02 09:00:00', '22222222', 'transferencia_recibida', 'Transferencia recibida', '100.00', '11111111'),
        move('OP-4', '2026-08-03 18:30:00', '11111111', 'compra', 'Compra en supermercado', '-45.30'),
        move('OP-5', '2026-08-04 12:00:00', 'OSCAR', 'deposito', 'Abono inicial de demostración', '99980.40'),
        move('OP-6', '2026-08-05 12:00:00', 'OSCAR', 'recarga', 'Recarga celular', '-20.00'),
        move('OP-7', '2026-08-06 12:00:00', 'OSCAR', 'deposito', 'Abono', '39.60'),
    ]
    cards = [{'cliente_dni': '11111111', 'tipo': 'debito', 'marca': 'Visa', 'ultimos4': '5899', 'vence': '02/29'},
             {'cliente_dni': 'OSCAR', 'tipo': 'debito', 'marca': 'Mastercard', 'ultimos4': '4779', 'vence': '12/29'}]
    balances = [{'cliente_dni': '11111111', 'saldo_final': '354.70'}, {'cliente_dni': '22222222', 'saldo_final': '150.00'},
                {'cliente_dni': 'OSCAR', 'saldo_final': '100000.00'}]
    data = tmp_path / 'seed'
    data.mkdir()

    def save():
        write_csv(data / 'clientes_demo.csv', CLIENT_COLS, clients)
        write_csv(data / 'tarjetas_demo.csv', ['cliente_dni', 'tipo', 'marca', 'ultimos4', 'vence'], cards)
        write_csv(data / 'movimientos_demo.csv', MOVE_COLS, moves)
        write_csv(data / 'saldos_finales.csv', ['cliente_dni', 'saldo_final'], balances)

    save()
    app = create_app()
    with app.app_context():
        oscar = User(username='oscar', full_name='Oscar Prueba', email='oscar@example.test', dni='55555555', role='CLIENTE')
        oscar.set_password('Clave.Real1')
        db.session.add(oscar)
        db.session.flush()
        account = Account(user_id=oscar.id, account_type='Cuenta de ahorros', number='001234564779', balance=Decimal('0.00'))
        db.session.add(account)
        db.session.commit()
        ids = {'oscar_user': oscar.id, 'oscar_account': account.id}
    return {'app': app, 'data': data, 'save': save, 'clients': clients, 'moves': moves, 'cards': cards, 'balances': balances, 'ids': ids}


def balance_of(app, number):
    with app.app_context():
        return Account.query.filter_by(number=number).one().balance


# ───────── Seguridad del script ─────────
def test_refuses_to_run_without_the_staging_confirmation(monkeypatch, capsys):
    monkeypatch.delenv('SEED_CONFIRM', raising=False)
    assert seed_demo.main(['--dry-run']) == 2
    assert 'SEED_CONFIRM=staging' in capsys.readouterr().err


def test_refuses_when_the_confirmation_is_not_exactly_staging(monkeypatch):
    for value in ('production', 'Staging', 'yes', ''):
        monkeypatch.setenv('SEED_CONFIRM', value)
        assert seed_demo.main(['--dry-run']) == 2


def test_refuses_when_railway_says_this_is_another_environment(monkeypatch, capsys):
    monkeypatch.setenv('SEED_CONFIRM', 'staging')
    monkeypatch.setenv('RAILWAY_ENVIRONMENT_NAME', 'production')
    assert seed_demo.main(['--dry-run']) == 2
    assert 'production' in capsys.readouterr().err


# ───────── Carga correcta ─────────
def test_loads_clients_cards_and_movements_and_the_balances_come_from_applying_them(world):
    report = seed_demo.run_seed(world['data'], app=world['app'])
    assert report['clients'] == 2 and report['cards'] == 2 and report['movements'] == 8
    assert report['verified_accounts'] == 3 and report['adjustment'] is None
    with world['app'].app_context():
        ana = Account.query.filter_by(number='1111-000001').one()
        beto = Account.query.filter_by(number='2222-000002').one()
        assert (ana.balance, beto.balance) == (Decimal('354.70'), Decimal('150.00'))
        assert ana.currency == 'PEN' and ana.account_type == 'Cuenta de ahorros'
        history = Transaction.query.filter_by(account_id=ana.id).order_by(Transaction.created_at).all()
        assert [t.balance_after for t in history] == [Decimal('500.00'), Decimal('400.00'), Decimal('354.70')]
        assert [t.operation_type for t in history] == ['INGRESO', 'TRANSFERENCIA', 'PAGO']
        assert history[1].created_at.isoformat() == '2026-08-02T09:00:00' and history[1].amount == Decimal('-100.00')
        assert all(t.is_seed and t.status == 'COMPLETADA' for t in history)
        received = Transaction.query.filter_by(account_id=beto.id, operation_type='INGRESO').order_by(Transaction.created_at).all()
        assert received[-1].description == 'Transferencia recibida' and received[-1].amount == Decimal('100.00')
        assert AuditLog.query.filter_by(action='SEED_DEMO_LOADED', result='COMPLETADA').count() == 1


def test_passwords_are_stored_only_as_hashes_and_cards_keep_only_the_last_four_digits(world):
    seed_demo.run_seed(world['data'], app=world['app'])
    with world['app'].app_context():
        ana = User.query.filter_by(dni='11111111').one()
        assert ana.password_hash != 'Demo2026.' and 'Demo2026.' not in ana.password_hash
        assert ana.check_password('Demo2026.') and ana.username == '11111111' and ana.role == 'CLIENTE' and ana.is_seed
        card = BankCard.query.filter_by(user_id=ana.id).one()
        assert card.last4 == '5899' and card.card_type == 'Débito Visa' and card.status == 'Activa'


def test_oscar_uses_his_existing_account_and_card_without_creating_a_user(world):
    seed_demo.run_seed(world['data'], app=world['app'])
    with world['app'].app_context():
        assert User.query.filter_by(full_name='Oscar Prueba').count() == 1
        assert User.query.filter(User.dni.is_(None), User.username != 'demo', User.username != 'admin').count() == 0
        account = db.session.get(Account, world['ids']['oscar_account'])
        assert account.balance == Decimal('100000.00')
        assert Transaction.query.filter_by(account_id=account.id).count() == 3
        card = BankCard.query.filter_by(user_id=world['ids']['oscar_user']).one()
        assert card.last4 == '4779' and card.card_type == 'Débito Mastercard'
        assert db.session.get(User, world['ids']['oscar_user']).check_password('Clave.Real1'), 'no se toca su contraseña'


def test_oscar_with_a_previous_balance_gets_an_adjustment_and_keeps_his_existing_movements(world):
    with world['app'].app_context():
        account = db.session.get(Account, world['ids']['oscar_account'])
        account.balance = Decimal('1234.56')
        db.session.add(Transaction(account_id=account.id, operation_type='INGRESO', description='Movimiento de prueba previo',
                                   amount=Decimal('1234.56'), balance_after=Decimal('1234.56')))
        db.session.commit()
    report = seed_demo.run_seed(world['data'], app=world['app'])
    assert report['adjustment']['previous_balance'] == Decimal('1234.56')
    assert report['adjustment']['amount'] == Decimal('-1234.56')
    assert report['oscar_final_balance'] == Decimal('100000.00')
    with world['app'].app_context():
        account = db.session.get(Account, world['ids']['oscar_account'])
        assert account.balance == Decimal('100000.00')
        assert Transaction.query.filter_by(account_id=account.id, description='Movimiento de prueba previo').count() == 1
        assert Transaction.query.filter_by(account_id=account.id, operation_type='AJUSTE').count() == 1


# ───────── Idempotencia y omisiones ─────────
def test_running_twice_does_not_duplicate_anything(world):
    seed_demo.run_seed(world['data'], app=world['app'])
    with world['app'].app_context():
        counts = (User.query.count(), Account.query.count(), BankCard.query.count(), Transaction.query.count())
    second = seed_demo.run_seed(world['data'], app=world['app'])
    assert second.get('clients', 0) == 0 and second.get('cards', 0) == 0 and second.get('movements', 0) == 0
    assert second['already_loaded_movements'] == 3 and second['adjustment'] is None
    with world['app'].app_context():
        assert (User.query.count(), Account.query.count(), BankCard.query.count(), Transaction.query.count()) == counts


def test_existing_dni_or_email_skips_the_row_and_its_movements_without_touching_data(world):
    with world['app'].app_context():
        existing = User(username='ana-real', full_name='Ana Real', email='otra@example.test', dni='11111111', role='CLIENTE')
        existing.set_password('Clave.Real1')
        db.session.add(existing)
        taken_email = User(username='beto-real', full_name='Beto Real', email='BETO@example.test', dni='99999999', role='CLIENTE')
        taken_email.set_password('Clave.Real1')
        db.session.add_all([taken_email])
        db.session.commit()
        hash_before = existing.password_hash
    world['balances'][:] = [{'cliente_dni': 'OSCAR', 'saldo_final': '100000.00'}]
    world['save']()
    report = seed_demo.run_seed(world['data'], app=world['app'])
    reasons = {dni: reason for dni, _, reason in report['omitted_clients']}
    assert reasons == {'11111111': 'el DNI ya existe', '22222222': 'el correo ya existe en la base'}
    assert report.get('clients', 0) == 0 and report['omitted_movements'] == 5
    with world['app'].app_context():
        assert Account.query.filter_by(number='1111-000001').count() == 0
        assert User.query.filter_by(dni='11111111').one().password_hash == hash_before
        assert db.session.get(Account, world['ids']['oscar_account']).balance == Decimal('100000.00')


def test_a_repeated_email_inside_the_csv_skips_the_second_client_and_their_movements(world):
    world['clients'].append(client('33333333', 'Ana Copia', 'ANA@example.test', '3333-000003'))
    world['moves'].append(move('OP-8', '2026-08-07 09:00:00', '33333333', 'deposito', 'Depósito inicial', '10.00'))
    world['balances'].append({'cliente_dni': '33333333', 'saldo_final': '10.00'})
    world['save']()
    report = seed_demo.run_seed(world['data'], app=world['app'])
    assert report['omitted_clients'] == [('33333333', 'Ana Copia', 'el correo está repetido dentro del CSV')]
    assert report['omitted_movements'] == 1 and report['clients'] == 2
    with world['app'].app_context():
        assert User.query.filter_by(dni='33333333').count() == 0


# ───────── Verificación y rollback ─────────
def test_a_balance_mismatch_rolls_everything_back_and_shows_the_differences(world):
    world['balances'][0]['saldo_final'] = '999.99'
    world['save']()
    with pytest.raises(seed_demo.SeedError) as error:
        seed_demo.run_seed(world['data'], app=world['app'])
    assert '11111111: calculado 354.70 / esperado 999.99' in str(error.value)
    with world['app'].app_context():
        assert User.query.filter_by(dni='11111111').count() == 0 and Account.query.filter_by(number='1111-000001').count() == 0
        assert BankCard.query.filter(BankCard.last4.in_(['5899', '4779'])).count() == 0 and Transaction.query.filter(Transaction.idempotency_key.like('demo-%')).count() == 0
        assert db.session.get(Account, world['ids']['oscar_account']).balance == Decimal('0.00')


def test_dry_run_does_everything_and_saves_nothing(world):
    report = seed_demo.run_seed(world['data'], app=world['app'], dry_run=True)
    assert report['clients'] == 2 and report['dry_run'] is True
    with world['app'].app_context():
        assert User.query.filter_by(dni='11111111').count() == 0
        assert db.session.get(Account, world['ids']['oscar_account']).balance == Decimal('0.00')
        assert AuditLog.query.filter_by(action='SEED_DEMO_LOADED').count() == 0


def test_missing_oscar_account_aborts_without_creating_anyone(world):
    with world['app'].app_context():
        Account.query.filter_by(id=world['ids']['oscar_account']).update({'number': '001234560000'})
        db.session.commit()
    with pytest.raises(seed_demo.SeedError, match='exactamente 1 cuenta de Oscar'):
        seed_demo.run_seed(world['data'], app=world['app'])
    with world['app'].app_context():
        assert User.query.filter_by(dni='11111111').count() == 0


# ───────── CSV hostiles o mal formados ─────────
@pytest.mark.parametrize('mutate, message', [
    (lambda w: w['clients'][0].update(dni="1' OR '1'='1"), 'DNI inválido'),
    (lambda w: w['clients'][0].update(correo='no-es-correo'), 'correo inválido'),
    (lambda w: w['clients'][0].update(nombre_completo='<script>alert(1)</script>'), 'nombre inválido'),
    (lambda w: w['clients'][0].update(numero_cuenta="1'; DROP TABLE users;--"), 'numero_cuenta inválido'),
    (lambda w: w['moves'][0].update(monto='1e3'), 'monto inválido'),
    (lambda w: w['moves'][0].update(monto='-500.00'), 'el signo del monto no corresponde'),
    (lambda w: w['moves'][0].update(tipo='retiro'), 'tipo desconocido'),
    (lambda w: w['moves'][0].update(fecha='13/07/2026'), 'fecha inválida'),
    (lambda w: w['cards'][0].update(ultimos4='4111111111115899'), 'ultimos4 debe tener 4 dígitos'),
    (lambda w: w['cards'][0].update(marca='Amex'), 'marca no soportada'),
])
def test_malformed_or_hostile_csv_content_stops_the_run_before_writing(world, mutate, message):
    mutate(world)
    world['save']()
    with pytest.raises(seed_demo.SeedError, match=message):
        seed_demo.run_seed(world['data'], app=world['app'])
    with world['app'].app_context():
        assert User.query.filter_by(dni='11111111').count() == 0
        assert Transaction.query.filter(Transaction.idempotency_key.like('demo-%')).count() == 0


def test_missing_files_or_columns_are_reported(world, tmp_path):
    (world['data'] / 'saldos_finales.csv').unlink()
    with pytest.raises(seed_demo.SeedError, match='No existe el archivo'):
        seed_demo.run_seed(world['data'], app=world['app'])
    world['save']()
    write_csv(world['data'] / 'saldos_finales.csv', ['cliente_dni'], [])
    with pytest.raises(seed_demo.SeedError, match='faltan columnas'):
        seed_demo.run_seed(world['data'], app=world['app'])


# ───────── Los CSV reales (si están en data/seed) ─────────
REAL = ROOT / 'data' / 'seed'


@pytest.mark.skipif(not (REAL / 'movimientos_demo.csv').exists(), reason='data/seed no está en este equipo (no se sube a git)')
def test_the_real_csv_files_reconcile_with_saldos_finales(world):
    report = seed_demo.run_seed(REAL, app=world['app'], dry_run=True)
    assert report['clients'] == 99 and len(report['omitted_clients']) == 1
    assert report['omitted_clients'][0][2] == 'el correo está repetido dentro del CSV'
    assert report['oscar_final_balance'] == Decimal('100000.00') and report['adjustment'] is None
    assert report['verified_accounts'] == 100
    assert report['cards'] == 55 and len(report['omitted_cards']) == 1     # 56 filas: la de la clienta omitida no se crea
    assert report['movements'] == 846 and report['omitted_movements'] == 10   # 856 - los 10 de la clienta omitida
