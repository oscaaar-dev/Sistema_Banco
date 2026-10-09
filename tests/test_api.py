from app import create_app
from app.models import Account, User


def _authenticated_client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    return client


def test_rest_resources_require_authentication_and_disable_caching():
    app = create_app()
    response = app.test_client().get('/api/v1/accounts')

    assert response.status_code == 401
    assert response.is_json
    assert response.headers['Cache-Control'] == 'no-store'


def test_authenticated_api_returns_owned_accounts_and_decimal_strings():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        user_id = user.id
        account = Account.query.filter_by(user_id=user_id).one()
        account_id = account.id
        expected_balance = f'{account.balance:.2f}'

    response = _authenticated_client(app, user_id).get('/api/v1/accounts')

    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json['items'] == [{
        'id': account_id,
        'type': 'Cuenta de ahorros',
        'number_suffix': '7890',
        'currency': 'PEN',
        'balance': expected_balance,
        'status': 'ACTIVA',
    }]


def test_transaction_api_is_scoped_paginated_and_validates_filters():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        user_id = user.id
        account = Account.query.filter_by(user_id=user_id).one()
        account_id = account.id

    client = _authenticated_client(app, user_id)
    response = client.get(f'/api/v1/accounts/{account_id}/transactions?per_page=2')

    assert response.status_code == 200
    assert response.json['total'] == 3
    assert len(response.json['items']) == 2
    assert all(isinstance(item['amount'], str) for item in response.json['items'])
    assert client.get(f'/api/v1/accounts/{account_id}/transactions?type=INVALID').status_code == 400
    assert client.get(f'/api/v1/accounts/{account_id}/transactions?from=2026-02-30').status_code == 400
    assert client.get(f'/api/v1/accounts/{account_id}/transactions?per_page=101').status_code == 400


def test_transaction_api_does_not_disclose_another_users_account():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        other_user = User.query.filter_by(username='admin').one()
        account = Account.query.filter_by(user_id=user.id).one()
        user_id = other_user.id
        account_id = account.id

    response = _authenticated_client(app, user_id).get(
        f'/api/v1/accounts/{account_id}/transactions'
    )

    assert response.status_code == 404
    assert 'items' not in response.json
