"""Read-only REST resources for the authenticated account workspace."""

from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .models import Account, Transaction, Transfer


api = Blueprint('api', __name__, url_prefix='/api/v1')
TRANSACTION_TYPES = {'INGRESO', 'TRANSFERENCIA', 'PAGO', 'RECARGA'}


def _json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _parse_date_argument(name):
    value = request.args.get(name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f'El parámetro {name} debe usar el formato AAAA-MM-DD.')


@api.get('/me')
@login_required
def me():
    return _json({
        'id': current_user.id,
        'username': current_user.username,
        'full_name': current_user.full_name,
        'role': current_user.role,
    })


@api.get('/accounts')
@login_required
def accounts():
    items = Account.query.filter_by(user_id=current_user.id, status='ACTIVA').order_by(Account.id).all()
    return _json({'items': [
        {
            'id': account.id,
            'type': account.account_type,
            'number_suffix': account.number[-4:],
            'currency': account.currency,
            'balance': format(Decimal(account.balance), '.2f'),
            'status': account.status,
        }
        for account in items
    ]})


@api.get('/accounts/<int:account_id>/transactions')
@login_required
def transactions(account_id):
    account = Account.query.filter_by(
        id=account_id, user_id=current_user.id, status='ACTIVA'
    ).first()
    if account is None:
        return _json({'error': 'No se encontró la cuenta solicitada.'}, 404)

    try:
        page = int(request.args.get('page', '1'))
        per_page = int(request.args.get('per_page', '20'))
        start_date = _parse_date_argument('from')
        end_date = _parse_date_argument('to')
    except ValueError as error:
        return _json({'error': str(error)}, 400)
    if page < 1 or per_page < 1 or per_page > 100:
        return _json({'error': 'La página debe ser positiva y per_page debe estar entre 1 y 100.'}, 400)
    if start_date and end_date and start_date > end_date:
        return _json({'error': 'El rango de fechas no es válido.'}, 400)

    query = Transaction.query.filter_by(account_id=account.id)
    operation_type = request.args.get('type')
    if operation_type:
        operation_type = operation_type.strip().upper()
        if operation_type not in TRANSACTION_TYPES:
            return _json({'error': 'El tipo de movimiento no es válido.'}, 400)
        query = query.filter_by(operation_type=operation_type)
    if start_date:
        query = query.filter(Transaction.created_at >= start_date)
    if end_date:
        query = query.filter(Transaction.created_at < end_date.fromordinal(end_date.toordinal() + 1))

    total = query.count()
    items = query.order_by(Transaction.created_at.desc(), Transaction.id.desc()).offset(
        (page - 1) * per_page
    ).limit(per_page).all()
    return _json({
        'account_id': account.id,
        'page': page,
        'per_page': per_page,
        'total': total,
        'items': [
            {
                'id': item.id,
                'type': item.operation_type,
                'description': item.description,
                'amount': format(Decimal(item.amount), '.2f'),
                'balance_after': format(Decimal(item.balance_after), '.2f'),
                'currency': account.currency,
                'status': item.status,
                'created_at': item.created_at.isoformat(timespec='seconds') + 'Z',
            }
            for item in items
        ],
    })


@api.get('/accounts/<int:account_id>/transfers')
@login_required
def transfers(account_id):
    account = Account.query.filter_by(
        id=account_id, user_id=current_user.id, status='ACTIVA'
    ).first()
    if account is None:
        return _json({'error': 'No se encontró la cuenta solicitada.'}, 404)
    items = Transfer.query.filter_by(account_id=account.id).order_by(
        Transfer.created_at.desc(), Transfer.id.desc()
    ).limit(100).all()
    return _json({
        'account_id': account.id,
        'items': [
            {
                'id': item.id,
                'transaction_id': item.transaction_id,
                'beneficiary': item.beneficiary.name,
                'amount': format(Decimal(item.amount), '.2f'),
                'currency': item.currency,
                'status': item.status,
                'effect': 'SIN EFECTOS FINANCIEROS',
                'created_at': item.created_at.isoformat(timespec='seconds') + 'Z',
            }
            for item in items
        ],
    })
