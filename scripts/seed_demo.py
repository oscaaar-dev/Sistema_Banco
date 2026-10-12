"""Carga clientes, tarjetas y movimientos de demostración en la base de STAGING de Norte.

    SEED_CONFIRM=staging python scripts/seed_demo.py --dry-run     # ensayo: lo hace todo y revierte
    SEED_CONFIRM=staging python scripts/seed_demo.py               # carga real (una sola transacción)

Lee 4 CSV de ``data/seed/`` (no se suben a git): clientes_demo, tarjetas_demo, movimientos_demo y saldos_finales.
Todo ocurre en UNA transacción: o se guarda todo o no se guarda nada. Antes de confirmar, el saldo de cada cuenta
creada se compara con saldos_finales.csv; si algo no cuadra se hace rollback y se muestran las diferencias.

Reglas
- Se niega a correr sin SEED_CONFIRM=staging (y si Railway declara otro entorno).
- Clientes nuevos: usuario (username = DNI) + cuenta de ahorros en PEN con saldo 0. La contraseña de demo se guarda
  solo como hash, con el mismo método que el registro (User.set_password). Si el DNI o el correo ya existen se omite
  la fila y sus movimientos; no se duplica ni se borra nada.
- Los saldos salen de aplicar los movimientos en orden de fecha; nunca se escriben a mano.
- Las filas con cliente_dni = OSCAR usan la cuenta existente de Oscar (terminada en 4779): no se crea usuario. Sus
  movimientos solo se agregan. Si su saldo previo no era 0, se registra un movimiento de ajuste para dejar 100000.00.
- Todo el contenido de los CSV se valida (formato, longitudes, tipos) y se inserta con el ORM (consultas
  parametrizadas). Un CSV mal formado detiene la carga antes de escribir nada.
- Los registros creados llevan is_seed=True para poder identificarlos.

No guarda en tarjetas el número completo, la marca ni el vencimiento como columnas: la tabla ``cards`` solo tiene
últimos 4, tipo, estado y límite. La marca se guarda dentro de ``card_type`` ("Débito Visa"). La fecha de nacimiento
de los clientes no tiene columna en ``users`` y no se guarda (agregarla requiere una migración aprobada).
"""
import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEMO_PASSWORD = 'Demo2026.'
OSCAR_MARK = 'OSCAR'
OSCAR_ACCOUNT_SUFFIX = '4779'
OSCAR_TARGET_BALANCE = Decimal('100000.00')
KEY_PREFIX = 'demo-'

OPERATION_TYPES = {
    'deposito': 'INGRESO',
    'transferencia_recibida': 'INGRESO',
    'transferencia_enviada': 'TRANSFERENCIA',
    'pago_servicio': 'PAGO',
    'compra': 'PAGO',
    'recarga': 'RECARGA',
}
LEG_ORDER = {'transferencia_enviada': 0, 'transferencia_recibida': 1}

_DNI = re.compile(r'\d{8}')
_EMAIL = re.compile(r'[^@\s<>"]+@[^@\s<>"]+\.[^@\s<>"]+')
_PHONE = re.compile(r'\d{9}')
_ACCOUNT = re.compile(r'[0-9][0-9-]{4,22}[0-9]')
_AMOUNT = re.compile(r'-?\d{1,10}\.\d{2}')
_OPERATION_ID = re.compile(r'[A-Za-z0-9_-]{3,30}')
_LAST4 = re.compile(r'\d{4}')
_BRANDS = {'Visa', 'Mastercard'}
_NAME_FORBIDDEN = re.compile(r'[<>\x00-\x1f]')


class SeedError(Exception):
    """Error de datos o de seguridad: se detiene la carga sin escribir nada."""


# ───────────────────────── Lectura y validación de los CSV ─────────────────────────
def _read(path, required):
    if not path.is_file():
        raise SeedError(f'No existe el archivo {path}')
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in required if name not in (reader.fieldnames or [])]
        if missing:
            raise SeedError(f'{path.name}: faltan columnas {missing}')
        return [{key: (value or '').strip() for key, value in row.items() if key} for row in reader]


def _check(condition, path_name, line, message):
    if not condition:
        raise SeedError(f'{path_name} fila {line}: {message}')


def load_files(data_dir):
    data_dir = Path(data_dir)
    clients = _read(data_dir / 'clientes_demo.csv', ['nombre_completo', 'dni', 'correo', 'telefono', 'fecha_nacimiento', 'numero_cuenta'])
    cards = _read(data_dir / 'tarjetas_demo.csv', ['cliente_dni', 'tipo', 'marca', 'ultimos4', 'vence'])
    movements = _read(data_dir / 'movimientos_demo.csv', ['operacion_id', 'fecha', 'cliente_dni', 'tipo', 'descripcion', 'monto', 'contraparte_dni'])
    balances = _read(data_dir / 'saldos_finales.csv', ['cliente_dni', 'saldo_final'])

    for number, row in enumerate(clients, start=2):
        name = 'clientes_demo.csv'
        _check(_DNI.fullmatch(row['dni']), name, number, f'DNI inválido {row["dni"]!r}')
        _check(2 <= len(row['nombre_completo']) <= 100 and not _NAME_FORBIDDEN.search(row['nombre_completo']), name, number, 'nombre inválido')
        _check(_EMAIL.fullmatch(row['correo']) and len(row['correo']) <= 254, name, number, 'correo inválido')
        _check(_PHONE.fullmatch(row['telefono']), name, number, 'teléfono inválido (9 dígitos)')
        _check(_ACCOUNT.fullmatch(row['numero_cuenta']), name, number, 'numero_cuenta inválido')
        try:
            datetime.strptime(row['fecha_nacimiento'], '%Y-%m-%d')
        except ValueError:
            raise SeedError(f'{name} fila {number}: fecha_nacimiento inválida') from None
    for number, row in enumerate(cards, start=2):
        name = 'tarjetas_demo.csv'
        _check(row['cliente_dni'] == OSCAR_MARK or _DNI.fullmatch(row['cliente_dni']), name, number, 'cliente_dni inválido')
        _check(row['tipo'] == 'debito', name, number, f'tipo no soportado {row["tipo"]!r}')
        _check(row['marca'] in _BRANDS, name, number, f'marca no soportada {row["marca"]!r}')
        _check(_LAST4.fullmatch(row['ultimos4']), name, number, 'ultimos4 debe tener 4 dígitos (no se aceptan números completos)')
        _check(re.fullmatch(r'(0[1-9]|1[0-2])/\d{2}', row['vence']), name, number, 'vence debe ser MM/AA')
    parsed = []
    for number, row in enumerate(movements, start=2):
        name = 'movimientos_demo.csv'
        _check(_OPERATION_ID.fullmatch(row['operacion_id']), name, number, 'operacion_id inválido')
        _check(row['cliente_dni'] == OSCAR_MARK or _DNI.fullmatch(row['cliente_dni']), name, number, 'cliente_dni inválido')
        _check(row['tipo'] in OPERATION_TYPES, name, number, f'tipo desconocido {row["tipo"]!r}')
        _check(1 <= len(row['descripcion']) <= 120 and not _NAME_FORBIDDEN.search(row['descripcion']), name, number, 'descripción inválida')
        _check(_AMOUNT.fullmatch(row['monto']), name, number, f'monto inválido {row["monto"]!r}')
        _check(not row['contraparte_dni'] or _DNI.fullmatch(row['contraparte_dni']), name, number, 'contraparte_dni inválido')
        try:
            when = datetime.strptime(row['fecha'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            raise SeedError(f'{name} fila {number}: fecha inválida {row["fecha"]!r}') from None
        amount = Decimal(row['monto'])
        _check(amount != 0, name, number, 'monto 0')
        credit = row['tipo'] in ('deposito', 'transferencia_recibida')
        _check((amount > 0) == credit, name, number, f'el signo del monto no corresponde al tipo {row["tipo"]}')
        parsed.append({**row, 'when': when, 'amount': amount})
    for number, row in enumerate(balances, start=2):
        _check(row['cliente_dni'] == OSCAR_MARK or _DNI.fullmatch(row['cliente_dni']), 'saldos_finales.csv', number, 'cliente_dni inválido')
        _check(_AMOUNT.fullmatch(row['saldo_final']) and not row['saldo_final'].startswith('-'), 'saldos_finales.csv', number, 'saldo_final inválido')
    final = {row['cliente_dni']: Decimal(row['saldo_final']) for row in balances}
    if len(final) != len(balances):
        raise SeedError('saldos_finales.csv: hay clientes repetidos')
    return clients, cards, parsed, final


# ───────────────────────── Carga ─────────────────────────
def run_seed(data_dir, *, dry_run=False, app=None):
    """Ejecuta la carga completa en una transacción. Devuelve el informe; lanza SeedError si algo no cuadra."""
    from sqlalchemy import func, or_

    from app import create_app
    from app.models import Account, AuditLog, BankCard, Transaction, User, db, now_utc

    clients, cards, movements, expected = load_files(data_dir)
    app = app or create_app()
    report = defaultdict(int)
    report['omitted_clients'] = []
    report['omitted_cards'] = []
    report['adjustment'] = None

    with app.app_context():
        try:
            # ── Oscar: cuenta existente, nunca se crea ──
            oscar_accounts = (Account.query.join(User, User.id == Account.user_id)
                              .filter(User.full_name.ilike('oscar%'), Account.number.like('%' + OSCAR_ACCOUNT_SUFFIX))
                              .with_for_update().all())
            if len(oscar_accounts) != 1:
                raise SeedError(f'Se esperaba exactamente 1 cuenta de Oscar terminada en {OSCAR_ACCOUNT_SUFFIX} y hay {len(oscar_accounts)}. No se crea ningún usuario nuevo.')
            oscar_account = oscar_accounts[0]
            oscar_user = db.session.get(User, oscar_account.user_id)
            oscar_previous_balance = Decimal(oscar_account.balance)

            # ── a) clientes y cuentas ──
            dnis = [row['dni'] for row in clients]
            emails = [row['correo'].lower() for row in clients]
            numbers = [row['numero_cuenta'] for row in clients]
            taken_users = User.query.filter(or_(User.dni.in_(dnis), func.lower(User.email).in_(emails), User.username.in_(dnis))).all()
            taken_dni = {u.dni for u in taken_users if u.dni} | {u.username for u in taken_users}
            taken_email = {u.email.lower() for u in taken_users if u.email}
            taken_numbers = {a.number for a in Account.query.filter(Account.number.in_(numbers)).all()}
            seen_email, created = set(), {}
            for row in clients:
                reason = None
                if row['dni'] in taken_dni:
                    reason = 'el DNI ya existe'
                elif row['correo'].lower() in taken_email:
                    reason = 'el correo ya existe en la base'
                elif row['correo'].lower() in seen_email:
                    reason = 'el correo está repetido dentro del CSV'
                elif row['numero_cuenta'] in taken_numbers:
                    reason = 'el número de cuenta ya existe'
                if reason:
                    report['omitted_clients'].append((row['dni'], row['nombre_completo'], reason))
                    continue
                seen_email.add(row['correo'].lower())
                user = User(username=row['dni'], full_name=row['nombre_completo'], email=row['correo'], dni=row['dni'],
                            phone=row['telefono'], role='CLIENTE', is_seed=True)
                user.set_password(DEMO_PASSWORD)
                db.session.add(user)
                db.session.flush()
                account = Account(user_id=user.id, account_type='Cuenta de ahorros', number=row['numero_cuenta'], currency='PEN',
                                  balance=Decimal('0.00'), is_seed=True)
                db.session.add(account)
                db.session.flush()
                created[row['dni']] = {'user': user, 'account': account}
                report['clients'] += 1

            # ── b) tarjetas de débito (solo últimos 4; la marca va en card_type) ──
            existing_cards = {c.account_id for c in BankCard.query.filter(BankCard.user_id.in_([oscar_user.id] + [v['user'].id for v in created.values()])).all()}
            for row in cards:
                owner = row['cliente_dni']
                if owner == OSCAR_MARK:
                    account, user = oscar_account, oscar_user
                elif owner in created:
                    account, user = created[owner]['account'], created[owner]['user']
                else:
                    report['omitted_cards'].append((owner, 'el cliente se omitió o ya existía'))
                    continue
                if account.id in existing_cards:
                    report['omitted_cards'].append((owner, 'ya tiene una tarjeta'))
                    continue
                db.session.add(BankCard(user_id=user.id, account_id=account.id, last4=row['ultimos4'],
                                        card_type=f'Débito {row["marca"]}', status='Activa', is_seed=True))
                existing_cards.add(account.id)
                report['cards'] += 1

            # ── c) movimientos: en orden de fecha; el saldo sale de aplicarlos ──
            accounts = {OSCAR_MARK: oscar_account, **{dni: v['account'] for dni, v in created.items()}}
            balances = {key: Decimal(account.balance) for key, account in accounts.items()}
            known_keys = {t.idempotency_key for t in Transaction.query.filter(
                Transaction.account_id == oscar_account.id, Transaction.idempotency_key.like(KEY_PREFIX + '%')).all()}
            for movement in sorted(movements, key=lambda m: (m['when'], m['operacion_id'], LEG_ORDER.get(m['tipo'], 2), m['cliente_dni'])):
                owner = movement['cliente_dni']
                if owner not in accounts:
                    report['omitted_movements'] += 1      # cliente omitido (ya existía o correo repetido)
                    continue
                key = f'{KEY_PREFIX}{movement["operacion_id"]}-{LEG_ORDER.get(movement["tipo"], 0)}'[:64]
                if owner == OSCAR_MARK and key in known_keys:
                    report['already_loaded_movements'] += 1   # ejecución repetida: no se duplica
                    continue
                balances[owner] += movement['amount']
                db.session.add(Transaction(
                    account_id=accounts[owner].id, operation_type=OPERATION_TYPES[movement['tipo']],
                    description=movement['descripcion'][:180], amount=movement['amount'], balance_after=balances[owner],
                    status='COMPLETADA', created_at=movement['when'], is_seed=True, idempotency_key=key))
                report['movements'] += 1
                if movement['tipo'].startswith('transferencia') and movement['contraparte_dni'] not in accounts:
                    report['transfer_legs_with_omitted_counterpart'] += 1

            # ── Oscar: si ya tenía saldo, un movimiento de diferencia deja exactamente 100000.00 ──
            difference = OSCAR_TARGET_BALANCE - balances[OSCAR_MARK]
            if difference != 0:
                balances[OSCAR_MARK] += difference
                db.session.add(Transaction(
                    account_id=oscar_account.id, operation_type='AJUSTE', description='Ajuste de saldo para la demostración',
                    amount=difference, balance_after=balances[OSCAR_MARK], status='COMPLETADA', created_at=now_utc(),
                    is_seed=True, idempotency_key=f'{KEY_PREFIX}ajuste-oscar-{now_utc():%Y%m%d%H%M%S}'))
                report['adjustment'] = {'previous_balance': oscar_previous_balance, 'amount': difference}
            for key, account in accounts.items():
                account.balance = balances[key].quantize(Decimal('0.01'))

            # ── 3. verificación contra saldos_finales.csv ──
            db.session.flush()
            differences = []
            for key, account in accounts.items():
                target = OSCAR_TARGET_BALANCE if key == OSCAR_MARK else expected.get(key)
                if target is None:
                    differences.append((key, account.balance, 'sin fila en saldos_finales.csv'))
                elif Decimal(account.balance) != target:
                    differences.append((key, account.balance, target))
                if Decimal(account.balance) < 0:
                    differences.append((key, account.balance, 'saldo negativo'))
            if differences:
                raise SeedError('Los saldos no coinciden con saldos_finales.csv. Se hizo rollback.\n' + '\n'.join(
                    f'  {dni}: calculado {got} / esperado {want}' for dni, got, want in differences[:25]))
            report['verified_accounts'] = len(accounts)
            report['oscar_final_balance'] = Decimal(oscar_account.balance)

            db.session.add(AuditLog(user_id=None, action='SEED_DEMO_LOADED', module='SISTEMA', result='SIMULACION' if dry_run else 'COMPLETADA'))
            if dry_run:
                db.session.rollback()
            else:
                db.session.commit()
        except BaseException:
            db.session.rollback()
            raise
    report['dry_run'] = dry_run
    return dict(report)


def _summarize(items, label, limit=8):
    """Agrupa por motivo y muestra solo los primeros casos (la lista completa puede tener cientos de filas)."""
    lines = []
    reasons = defaultdict(list)
    for item in items:
        reasons[item[-1]].append(item[:-1])
    for reason, entries in reasons.items():
        lines.append(f'    - {len(entries)} x {reason}')
        for entry in entries[:limit]:
            lines.append('        ' + ' '.join(str(part) for part in entry))
        if len(entries) > limit:
            lines.append(f'        ... y {len(entries) - limit} más')
    return lines


def format_report(report):
    lines = ['', '=== ENSAYO (no se guardó nada) ===' if report['dry_run'] else '=== CARGA COMPLETADA ===',
             f"Clientes insertados:        {report.get('clients', 0)}",
             f"Clientes omitidos:          {len(report['omitted_clients'])}"]
    lines += _summarize(report['omitted_clients'], 'clientes')
    lines += [f"Tarjetas creadas:           {report.get('cards', 0)}",
              f"Tarjetas omitidas:          {len(report['omitted_cards'])}"]
    lines += _summarize(report['omitted_cards'], 'tarjetas')
    lines += [f"Movimientos insertados:     {report.get('movements', 0)}",
              f"Movimientos omitidos:       {report.get('omitted_movements', 0)} (de clientes omitidos)",
              f"Movimientos ya cargados:    {report.get('already_loaded_movements', 0)} (ejecución repetida)",
              f"Transferencias con la otra pata omitida: {report.get('transfer_legs_with_omitted_counterpart', 0)}",
              f"Cuentas verificadas contra saldos_finales.csv: {report.get('verified_accounts', 0)}",
              f"Saldo final de OSCAR:       S/ {report.get('oscar_final_balance', Decimal('0')):,.2f}"]
    adjustment = report.get('adjustment')
    if adjustment:
        lines.append(f"AJUSTE en la cuenta de Oscar: saldo previo S/ {adjustment['previous_balance']:,.2f}; movimiento de diferencia S/ {adjustment['amount']:,.2f}")
    else:
        lines.append('Cuenta de Oscar sin ajuste: el saldo cuadró con los movimientos.')
    return chr(10).join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Carga datos de demostración en STAGING (una sola transacción).')
    parser.add_argument('--data-dir', default=str(ROOT / 'data' / 'seed'), help='Carpeta con los 4 CSV (por defecto data/seed).')
    parser.add_argument('--dry-run', action='store_true', help='Hace todo y las verificaciones, pero revierte al final.')
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')   # tildes legibles en la consola de Windows

    if os.getenv('SEED_CONFIRM', '') != 'staging':
        print('Me niego a correr: define SEED_CONFIRM=staging para confirmar que el destino es STAGING.', file=sys.stderr)
        return 2
    railway_env = os.getenv('RAILWAY_ENVIRONMENT_NAME', '').strip().lower()
    if railway_env and railway_env != 'staging':
        print(f'Me niego a correr: Railway indica el entorno {railway_env!r}, no staging.', file=sys.stderr)
        return 2
    try:
        from app import create_app
        app = create_app()
        target = app.config['SQLALCHEMY_DATABASE_URI']
        host = re.sub(r'^[^@]*@', '', target.split('://', 1)[-1])
        print(f'Base de datos destino: {host.split("?")[0]}  (modo {"ENSAYO" if args.dry_run else "REAL"})')
        report = run_seed(args.data_dir, dry_run=args.dry_run, app=app)
    except SeedError as error:
        print(f'\nSE DETUVO SIN GUARDAR NADA: {error}', file=sys.stderr)
        return 1
    print(format_report(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
