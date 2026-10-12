"""Estado de cuenta en PDF: contenido, filtros, resumen, paginación con cientos de registros y seguridad."""
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, Transaction, User, db
from app.services.statement_pdf import mask_dni, money, safe_text
from config import LEGAL_NOTICE
from pdf_helpers import page_count, pdf_lines, pdf_text


def _client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    return client


@pytest.fixture
def app():
    return create_app()


def _make_client_with_movements(app, rows, *, username='titular', full_name='Lucía Peñaranda Ñáñez', dni='45678912',
                                opening=Decimal('100.00'), number='777788889999'):
    """Crea un cliente con movimientos coherentes: ``rows`` = [(fecha, tipo, detalle, importe)] en orden cronológico."""
    with app.app_context():
        user = User(username=username, full_name=full_name, dni=dni, email=f'{username}@example.test', role='CLIENTE')
        user.set_password('Clave-De-Prueba-1!')
        db.session.add(user)
        db.session.flush()
        account = Account(user_id=user.id, account_type='Cuenta de ahorros', number=number, balance=opening)
        db.session.add(account)
        db.session.flush()
        balance = opening
        for when, kind, detail, amount in rows:
            balance += Decimal(amount)
            db.session.add(Transaction(account_id=account.id, operation_type=kind, description=detail,
                                       amount=Decimal(amount), balance_after=balance, created_at=when))
        account.balance = balance
        ids = (user.id, account.id)
        db.session.commit()
    return _client_for(app, ids[0]), ids[1]


SMALL = [
    (datetime(2026, 3, 1, 9, 0), 'INGRESO', 'Abono de sueldo', '1000.00'),
    (datetime(2026, 3, 5, 18, 30), 'PAGO', 'Pago: Electricidad', '-120.50'),
    (datetime(2026, 4, 2, 7, 15), 'RECARGA', 'Recarga: 987654321', '-30.00'),
    (datetime(2026, 4, 20, 12, 0), 'TRANSFERENCIA', 'Transferencia a Ricardo O*** V***', '-200.00'),
    (datetime(2026, 5, 3, 20, 45), 'INGRESO', 'Transferencia recibida de Alex R***', '50.25'),
]


def _statement(client, account_id, **query):
    response = client.get(f'/statements/{account_id}.pdf', query_string=query)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.data


# ───────── Formato y encabezado ─────────
def test_pdf_is_a_downloadable_a4_document_with_brand_and_title(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    response = client.get(f'/statements/{account_id}.pdf')
    assert response.mimetype == 'application/pdf' and response.headers['Cache-Control'] == 'no-store'
    assert 'attachment' in response.headers['Content-Disposition']
    pdf = response.data
    assert pdf.startswith(b'%PDF-1.4') and pdf.rstrip().endswith(b'%%EOF')
    media = re.search(rb'/MediaBox \[ ?0 0 ([\d.]+) ([\d.]+) ?\]', pdf)
    assert (round(float(media.group(1))), round(float(media.group(2)))) == (595, 842)  # A4
    lines = pdf_lines(pdf)
    assert 'NORTE.' in lines and 'Estado de cuenta' in lines


def test_pdf_shows_masked_dni_client_data_and_account_suffix(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    text = pdf_text(_statement(client, account_id))
    assert 'Lucía Peñaranda Ñáñez' in text
    assert '****8912' in text and '45678912' not in text
    assert 'Cuenta de ahorros terminada en 9999' in text and '777788889999' not in text
    assert 'MONEDA PEN' in text


def test_client_without_dni_is_shown_as_not_registered(app):
    client, account_id = _make_client_with_movements(app, SMALL, dni=None)
    assert 'No registrado' in pdf_text(_statement(client, account_id))


def test_pdf_has_the_issue_date_and_the_period_from_first_to_last_movement(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    text = pdf_text(_statement(client, account_id))
    assert 'DESDE 01/03/2026 HASTA 03/05/2026' in text
    assert re.search(r'FECHA DE EMISIÓN \d{2}/\d{2}/\d{4} \d{2}:\d{2}', text)


def test_period_uses_the_active_date_filters(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    text = pdf_text(_statement(client, account_id, **{'from': '2026-04-01', 'to': '2026-04-30'}))
    assert 'DESDE 01/04/2026 HASTA 30/04/2026' in text
    assert 'Recarga: 987654321' in text and 'Abono de sueldo' not in text and 'Transferencia recibida' not in text


# ───────── Resumen y tabla ─────────
def test_summary_boxes_show_opening_income_expense_and_closing_balance(app):
    client, account_id = _make_client_with_movements(app, SMALL, opening=Decimal('100.00'))
    text = pdf_text(_statement(client, account_id))
    # saldo inicial 100.00 + 1,050.25 de ingresos - 350.50 de egresos = 799.75
    assert 'SALDO INICIAL S/ 100.00' in text and 'TOTAL INGRESOS S/ 1,050.25' in text
    assert 'TOTAL EGRESOS S/ 350.50' in text and 'SALDO FINAL S/ 799.75' in text


def test_summary_for_a_date_window_starts_from_the_balance_before_the_window(app):
    client, account_id = _make_client_with_movements(app, SMALL, opening=Decimal('100.00'))
    text = pdf_text(_statement(client, account_id, **{'from': '2026-04-01', 'to': '2026-04-30'}))
    # antes de abril el saldo era 100 + 1000 - 120.50 = 979.50; en abril salen 30 + 200
    assert 'SALDO INICIAL S/ 979.50' in text and 'TOTAL EGRESOS S/ 230.00' in text and 'SALDO FINAL S/ 749.50' in text


def test_table_has_the_requested_columns_and_signed_amounts(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    pdf = _statement(client, account_id)
    lines = pdf_lines(pdf)
    for column in ('Fecha y hora', 'Tipo', 'Detalle', 'Importe', 'Saldo'):
        assert column in lines
    assert '01/03/2026 09:00' in lines and 'Ingreso' in lines and 'Transferencia' in lines
    assert '+1,000.00' in lines and '-120.50' in lines and '1,100.00' in lines
    assert lines.index('01/03/2026 09:00') < lines.index('03/05/2026 20:45')  # cronológico: del más antiguo al más reciente


def test_incomes_are_green_and_expenses_black(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    pdf = _statement(client, account_id)
    green, black = '.101961 .498039 .294118', '.047059 .031373 .094118'  # #1a7f4b y la tinta #0c0818
    fill, amounts = None, {}
    for raw in pdf.split(b'\n'):
        line = raw.decode('latin-1')
        if line.endswith(' rg'):
            fill = line[:-3]
        match = re.search(r'\(([+-]\d[\d,]*\.\d{2})\) Tj', line)
        if match:
            amounts[match.group(1)] = fill
    assert amounts == {'+1,000.00': green, '-120.50': black, '-30.00': black, '-200.00': black, '+50.25': green}


def test_type_filter_is_respected_and_stated(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    text = pdf_text(_statement(client, account_id, type='PAGO'))
    assert 'Pago: Electricidad' in text and 'Recarga: 987654321' not in text and 'Abono de sueldo' not in text
    assert 'TIPO DE MOVIMIENTO Pago' in text and 'TOTAL EGRESOS S/ 120.50' in text and 'TOTAL INGRESOS S/ 0.00' in text
    assert 'SALDO FINAL S/ 799.75' in text  # el saldo es el de la cuenta, no el de los filtrados


def test_date_and_type_filters_combine(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    text = pdf_text(_statement(client, account_id, type='INGRESO', **{'from': '2026-05-01'}))
    assert 'Transferencia recibida de Alex R***' in text and 'Abono de sueldo' not in text


def test_invalid_filters_are_rejected(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    for query in ({'type': 'ROBO'}, {'from': '2026-02-30'}, {'from': '2026-05-01', 'to': '2026-04-01'}, {'to': 'ayer'}):
        assert client.get(f'/statements/{account_id}.pdf', query_string=query).status_code == 400


def test_account_without_movements_gets_a_clear_message(app):
    client, account_id = _make_client_with_movements(app, [], opening=Decimal('0.00'))
    text = pdf_text(_statement(client, account_id))
    assert 'No hay movimientos en el periodo seleccionado.' in text and 'Sin movimientos' in text
    assert 'SALDO INICIAL S/ 0.00' in text and 'SALDO FINAL S/ 0.00' in text


def test_period_without_movements_inside_an_account_that_has_them(app):
    client, account_id = _make_client_with_movements(app, SMALL, opening=Decimal('100.00'))
    text = pdf_text(_statement(client, account_id, **{'from': '2026-06-01', 'to': '2026-06-30'}))
    assert 'No hay movimientos en el periodo seleccionado.' in text
    assert 'SALDO INICIAL S/ 799.75' in text and 'SALDO FINAL S/ 799.75' in text


# ───────── Pie, tipografía y paginación ─────────
def test_footer_has_the_full_legal_notice_wrapped_and_page_numbers(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    pdf = _statement(client, account_id)
    lines = pdf_lines(pdf)
    assert ' '.join(LEGAL_NOTICE.split()) in ' '.join(lines)  # el aviso completo, sin cortes
    assert len([line for line in lines if line in LEGAL_NOTICE and len(line) > 30]) >= 2  # ajustado en varias líneas
    assert 'Página 1 de 1' in lines


def test_spanish_characters_and_unsupported_characters_do_not_break_the_pdf(app):
    rows = [(datetime(2026, 3, 1, 9, 0), 'INGRESO', 'Año nuevo: ¿qué tal? ¡Sí! Mañana — “prueba” ✓ Łukasz', '10.00')]
    client, account_id = _make_client_with_movements(app, rows, full_name='José Ñandú Peña Łukasz')
    text = pdf_text(_statement(client, account_id))
    assert 'Año nuevo: ¿qué tal? ¡Sí! Mañana' in text and 'José Ñandú Peña' in text
    assert 'Lukasz' in text  # transliterado: Ł → L


def test_hundreds_of_movements_flow_across_pages_with_repeated_header_and_numbering(app):
    start = datetime(2025, 1, 1, 8, 0)
    rows = [(start + timedelta(hours=7 * index), 'PAGO' if index % 5 else 'INGRESO', f'Movimiento número {index}',
             '-12.34' if index % 5 else '250.00') for index in range(450)]
    client, account_id = _make_client_with_movements(app, rows, opening=Decimal('20000.00'))
    pdf = _statement(client, account_id)
    pages = page_count(pdf)
    lines = pdf_lines(pdf)
    assert 8 <= pages <= 20
    assert lines.count('Fecha y hora') == pages  # encabezado de la tabla repetido en cada página
    assert all(f'Página {number} de {pages}' in lines for number in range(1, pages + 1))
    assert 'Movimiento número 0' in lines and 'Movimiento número 449' in lines
    assert 'Movimientos (450)' in lines
    assert sum(1 for line in lines if line.startswith('Movimiento número')) == 450  # no se pierde ninguno entre páginas


def test_two_thousand_movements_are_generated_quickly(app):
    start = datetime(2024, 1, 1)
    rows = [(start + timedelta(hours=index), 'PAGO', f'Pago de prueba {index}', '-1.00') for index in range(2000)]
    client, account_id = _make_client_with_movements(app, rows, opening=Decimal('5000.00'))
    began = time.perf_counter()
    pdf = _statement(client, account_id)
    assert time.perf_counter() - began < 8
    assert page_count(pdf) > 30


# ───────── Seguridad ─────────
def test_statement_is_owner_scoped_and_requires_login(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    other_client, other_account_id = _make_client_with_movements(app, SMALL, username='otro', number='222233334444', dni='87654321')
    assert client.get(f'/statements/{other_account_id}.pdf').status_code == 404
    assert other_client.get(f'/statements/{account_id}.pdf').status_code == 404
    assert app.test_client().get(f'/statements/{account_id}.pdf').status_code == 302


def test_movements_page_passes_the_active_filters_to_the_pdf_link(app):
    client, account_id = _make_client_with_movements(app, SMALL)
    html = client.get('/movements?type=PAGO&from=2026-03-01&to=2026-04-30').get_data(as_text=True)
    link = re.search(r'href="(/statements/\d+\.pdf[^"]*)"', html).group(1).replace('&amp;', '&')
    assert 'type=PAGO' in link and 'from=2026-03-01' in link and 'to=2026-04-30' in link


# ───────── Utilidades ─────────
def test_helpers():
    assert mask_dni('45678912') == '****8912' and mask_dni(None) == 'No registrado' and mask_dni('123') == 'No registrado'
    assert money('PEN', Decimal('1234.5')) == 'S/ 1,234.50' and money('USD', Decimal('2')) == 'USD 2.00'
    assert safe_text('Año ¿qué? ✓ Ł\n') == 'Año ¿qué? ? L '
