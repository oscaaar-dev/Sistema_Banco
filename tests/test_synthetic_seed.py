from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, BankCard, Beneficiary, Transaction, User
from app.services.seed_service import seed_synthetic_clients


def test_synthetic_seed_is_reproducible_reconciled_and_safe():
    app=create_app()
    with app.app_context():
        demo=User.query.filter_by(username='demo').one()
        demo_account=Account.query.filter_by(user_id=demo.id).one()
        demo_transactions=Transaction.query.filter_by(account_id=demo_account.id).order_by(Transaction.id.asc()).all()
        assert demo.is_seed and demo_account.is_seed
        assert len(demo_transactions)==3 and all(row.is_seed for row in demo_transactions)
        assert sum((row.amount for row in demo_transactions),Decimal('0.00'))==demo_account.balance

        clients=seed_synthetic_clients()
        assert len(clients)==5
        assert all(client.is_seed for client in clients)

        for index,client in enumerate(clients,1):
            account=Account.query.filter_by(user_id=client.id,is_seed=True).one()
            transactions=(Transaction.query.filter_by(account_id=account.id,is_seed=True)
                          .order_by(Transaction.created_at.asc(),Transaction.id.asc()).all())
            beneficiary=Beneficiary.query.filter_by(user_id=client.id,is_seed=True).one()
            card=BankCard.query.filter_by(user_id=client.id,is_seed=True).one()

            assert client.email==f'seed-{index:03d}@example.test'
            assert client.phone.startswith('9') and len(client.phone)==9
            assert len(account.number)==20 and account.number.isdigit()
            assert len(account.cci)==20 and account.cci.isdigit()
            assert len(beneficiary.account_number)==20 and beneficiary.account_number.isdigit()
            assert card.last4.isdigit() and len(card.last4)==4
            assert not hasattr(card,'number')
            assert len(transactions)==64
            assert transactions[0].created_at.strftime('%d/%m/%Y')=='30/09/2025'
            assert transactions[-1].created_at.strftime('%d/%m/%Y')=='28/09/2026'
            assert len({transaction.created_at for transaction in transactions})==len(transactions)
            assert sum(transaction.operation_type=='TRANSFERENCIA' for transaction in transactions)==3

            running=Decimal('0.00')
            for transaction in transactions:
                running+=transaction.amount
                assert transaction.balance_after==running
                assert transaction.is_seed
            assert running==account.balance
            assert all(transaction.amount>0 for transaction in transactions if transaction.operation_type=='INGRESO')
            assert all(transaction.amount<0 for transaction in transactions if transaction.operation_type=='PAGO')
            assert all(transaction.amount<0 for transaction in transactions if transaction.operation_type=='TRANSFERENCIA')

        assert seed_synthetic_clients()==[]
        assert User.query.filter(User.username.like('seed-client-%')).count()==5


def test_synthetic_seed_refuses_non_test_database():
    app=create_app()
    with app.app_context():
        app.config['APP_ENV']='development'
        with pytest.raises(RuntimeError,match='SQLite de pruebas en memoria'):
            seed_synthetic_clients()
