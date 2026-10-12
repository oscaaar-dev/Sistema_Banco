"""Pantalla «Cuentas»: datos reales de la base, resumen mensual, CCI determinista, estados vacíos y autorización."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, BankCard, Transaction, User, db, now_utc
from app.services.account_identifiers import account_number_from_cci, cci_for, derive_cci
from app.services.account_service import account_overview, month_summary, parse_month


def _client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    return client


def _new_client_user(username, full_name='Cliente de Prueba', dni='30000001', **account):
    user = User(username=username, full_name=full_name, dni=dni, email=f'{username}@example.test', role='CLIENTE')
    user.set_password('Clave-De-Prueba-1!')
    db.session.add(user)
    db.session.flush()
    created = None
    if account is not None:
        created = Account(user_id=user.id, account_type='Cuenta de ahorros', number=account.pop('number', '321321321321'),
                          balance=account.pop('balance', Decimal('0.00')), **account)
        db.session.add(created)
        db.session.flush()
    return user, created


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def demo(app):
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        account = Account.query.filter_by(user_id=user.id).one()
        return {'user': user.id, 'account': account.id, 'client': _client_for(app, user.id)}


def _html(response):
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _totals(html):
    """(ingresos, gastos) del bloque «Resumen del mes», sin confundirlos con la tabla de movimientos."""
    match = re.search(r'Total ingresos</span><b class="amount positive">PEN ([\d,.]+)</b>.*?Total gastos</span><b class="amount">PEN ([\d,.]+)</b>', html, re.S)
    assert match, 'no se encontró el resumen del mes'
    return match.group(1), match.group(2)


# ───────── Contenido de la página ─────────
def test_accounts_page_shows_the_card_with_real_account_data(demo):
    html = _html(demo['client'].get('/accounts'))
    assert 'CUENTA DE AHORROS' in html and '···· 7890' in html
    assert '8,540.50' in html and 'PEN' in html
    assert 'ACTIVA' in html and 'Fecha de apertura' in html and '30/09/2025' in html
    assert 'id="acBalanceToggle"' in html and 'Ocultar saldo' in html


def test_receive_block_shows_full_number_and_a_fictitious_20_digit_cci_with_copy_buttons(demo):
    html = _html(demo['client'].get('/accounts'))
    cci = derive_cci('001234567890')
    assert '001234567890' in html and cci in html and len(cci) == 20
    assert 'ficticio, solo demostración' in html
    assert html.count('data-copy="') == 2 and f'data-copy="{cci}"' in html and 'data-copy="001234567890"' in html


def test_a_stored_cci_is_respected_over_the_derived_one(app):
    with app.app_context():
        user, account = _new_client_user('con-cci', cci='99900011112222333344')
        account_id, user_id = account.id, user.id
        db.session.commit()
    html = _html(_client_for(app, user_id).get('/accounts'))
    assert '99900011112222333344' in html and derive_cci('321321321321') not in html


def test_cci_is_deterministic_and_reversible_for_twelve_digit_accounts():
    cci = derive_cci('555566667777')
    assert cci == derive_cci('555566667777') and len(cci) == 20 and cci.isdigit()
    assert account_number_from_cci(cci) == '555566667777'
    assert account_number_from_cci(cci[:-1] + str((int(cci[-1]) + 1) % 10)) is None
    assert len(derive_cci('900000000000000098')) == 20 and len(derive_cci('')) == 20


def test_cci_for_uses_the_column_when_present():
    assert cci_for(Account(number='555566667777', cci='11112222333344445555')) == '11112222333344445555'
    assert cci_for(Account(number='555566667777', cci=None)) == derive_cci('555566667777')


def test_recent_movements_are_the_last_five_with_a_link_to_all(app):
    with app.app_context():
        user, account = _new_client_user('movimientos', balance=Decimal('100.00'))
        for index in range(8):
            db.session.add(Transaction(account_id=account.id, operation_type='PAGO', description=f'Movimiento {index}',
                                       amount=Decimal('-1.00'), balance_after=Decimal('100.00'),
                                       created_at=datetime(2026, 1, 1) + timedelta(days=index)))
        account_id, user_id = account.id, user.id
        db.session.commit()
    html = _html(_client_for(app, user_id).get('/accounts'))
    assert all(f'Movimiento {index}' in html for index in range(3, 8))
    assert not any(f'Movimiento {index}<' in html for index in range(0, 3))
    assert f'/movements?account_id={account_id}' in html and 'Ver todos' in html


def test_statement_button_links_to_the_pdf_of_the_account(demo):
    html = _html(demo['client'].get('/accounts'))
    assert 'Descargar estado de cuenta' in html and f'/statements/{demo["account"]}.pdf' in html


def test_associated_debit_card_is_shown_only_when_it_exists(app, demo):
    html = _html(demo['client'].get('/accounts'))
    assert '4582' in html and 'Tarjeta de débito' in html
    with app.app_context():
        user, _ = _new_client_user('sin-tarjeta')
        user_id = user.id
        db.session.commit()
    assert 'no tiene una tarjeta de débito asociada' in _html(_client_for(app, user_id).get('/accounts'))


def test_the_card_of_another_account_is_not_shown(app):
    with app.app_context():
        user, first = _new_client_user('dos-cuentas', balance=Decimal('5.00'))
        second = Account(user_id=user.id, account_type='Cuenta de ahorros', number='999999999999', balance=Decimal('1.00'))
        db.session.add(second)
        db.session.flush()
        db.session.add(BankCard(user_id=user.id, account_id=second.id, last4='6543'))
        user_id, first_id, second_id = user.id, first.id, second.id
        db.session.commit()
    client = _client_for(app, user_id)
    assert '6543' not in _html(client.get(f'/accounts?account_id={first_id}'))
    html = _html(client.get(f'/accounts?account_id={second_id}'))
    assert '6543' in html and 'Elegir cuenta' in html


# ───────── Resumen del mes ─────────
@pytest.fixture
def month_data(app):
    today = now_utc().date()
    this_month = datetime(today.year, today.month, 1, 10, 0)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    with app.app_context():
        user, account = _new_client_user('resumen', balance=Decimal('500.00'))
        rows = [(this_month, 'INGRESO', '1000.00'), (this_month + timedelta(hours=2), 'PAGO', '-120.50'),
                (this_month + timedelta(hours=3), 'RECARGA', '-30.00'), (last_month, 'INGRESO', '250.25'),
                (last_month + timedelta(hours=1), 'PAGO', '-10.00')]
        for when, kind, amount in rows:
            db.session.add(Transaction(account_id=account.id, operation_type=kind, description=f'{kind} de prueba',
                                       amount=Decimal(amount), balance_after=Decimal('0.00'), created_at=when))
        ids = {'user': user.id, 'account': account.id}
        db.session.commit()
    return {**ids, 'client': _client_for(app, ids['user']), 'this': this_month.date(), 'last': last_month.date(), 'app': app}


def test_month_summary_adds_incomes_and_expenses_of_the_current_month_only(month_data):
    html = _html(month_data['client'].get('/accounts'))
    assert _totals(html) == ('1,000.00', '150.50')
    assert 'class="ac-chart"' in html and 'role="img"' in html and html.count('class="ac-bar-in"') == 1 and html.count('class="ac-bar-out"') == 1


def test_month_can_be_changed_and_navigation_is_bounded_by_the_data(month_data):
    previous = month_data['last'].strftime('%Y-%m')
    html = _html(month_data['client'].get(f'/accounts?month={previous}'))
    assert _totals(html) == ('250.25', '10.00')
    assert 'aria-label="Mes anterior"' not in html and 'aria-label="Mes siguiente"' in html
    current = _html(month_data['client'].get('/accounts'))
    assert 'aria-label="Mes anterior"' in current and 'aria-label="Mes siguiente"' not in current


def test_month_without_movements_shows_an_empty_state_and_a_way_to_the_latest_month(month_data):
    old = _html(month_data['client'].get('/accounts?month=2001-03'))
    assert 'No hay movimientos en marzo de 2001' in old and 'Ver el último mes con movimientos' in old
    assert _totals(old) == ('0.00', '0.00') and 'class="ac-chart"' not in old


@pytest.mark.parametrize('month', ['2026-13', '2026-00', 'octubre', '2026-1', '0001-01', "2026-10' OR 1=1"])
def test_invalid_months_are_rejected(month_data, month):
    assert month_data['client'].get('/accounts', query_string={'month': month}).status_code == 400


def test_parse_month_and_summary_service(month_data):
    assert parse_month('2026-10') == date(2026, 10, 1) and parse_month('') is None and parse_month(None) is None
    with month_data['app'].app_context():
        account = db.session.get(Account, month_data['account'])
        summary = month_summary(account, month_data['this'].replace(day=1))
        assert (summary['income'], summary['expense'], summary['net'], summary['count']) == (
            Decimal('1000.00'), Decimal('150.50'), Decimal('849.50'), 3)
        assert len(summary['daily']) in range(28, 32) and len(summary['bars']) == len(summary['daily'])
        assert all(0 <= bar['up'] <= 52 and 0 <= bar['down'] <= 52 for bar in summary['bars'])
        overview = account_overview(account)
        assert overview['has_movements'] and overview['previous_month'] == month_data['last'] and overview['next_month'] is None


# ───────── Estados vacíos ─────────
def test_account_without_movements_shows_clear_empty_states_and_no_invented_figures(app):
    with app.app_context():
        user, account = _new_client_user('vacia')
        user_id = user.id
        db.session.commit()
    html = _html(_client_for(app, user_id).get('/accounts'))
    assert 'Aún no hay movimientos en esta cuenta' in html and 'PEN 0.00' in html
    assert 'no tiene una tarjeta de débito asociada' in html
    assert 'Fecha de apertura' in html and now_utc().date().strftime('%d/%m/%Y') in html  # alta del cliente, no un dato inventado


def test_client_without_accounts_sees_an_empty_state(app):
    with app.app_context():
        user = User(username='sin-cuenta', full_name='Sin Cuenta', role='CLIENTE', email='s@example.test')
        user.set_password('Clave-De-Prueba-1!')
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    html = _html(_client_for(app, user_id).get('/accounts'))
    assert 'Todavía no tienes una cuenta' in html and 'Descargar estado de cuenta' not in html


# ───────── Autorización ─────────
def test_accounts_requires_login_and_client_role(app):
    assert app.test_client().get('/accounts').status_code == 302
    with app.app_context():
        admin_id = User.query.filter_by(username='admin').one().id
    assert _client_for(app, admin_id).get('/accounts').status_code == 403


def test_user_cannot_see_the_account_of_another_user(app, demo):
    with app.app_context():
        _, other = _new_client_user('ajeno', number='888877776666', balance=Decimal('77777.77'))
        other_id = other.id
        db.session.commit()
    response = demo['client'].get(f'/accounts?account_id={other_id}')
    assert response.status_code == 404 and '77,777.77' not in response.get_data(as_text=True)
    assert demo['client'].get('/accounts?account_id=abc').status_code == 400


# ───────── Navegación y responsive ─────────
def test_navigation_points_to_the_accounts_page_and_page_is_responsive(demo):
    html = _html(demo['client'].get('/accounts'))
    assert 'href="/accounts"' in html and 'name="viewport"' in html
    assert 'css/accounts.css' in html and 'js/accounts.js' in html
    css = demo['client'].get('/static/css/accounts.css').get_data(as_text=True)
    assert '@media(max-width:980px)' in css and '@media(max-width:560px)' in css
