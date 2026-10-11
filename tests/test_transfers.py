from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, Beneficiary, Transaction, Transfer, User, db
from app.services.banking_service import process_operation


def test_transfer_record_is_atomic_scoped_and_idempotent():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        account = Account.query.filter_by(user_id=user.id).one()
        beneficiary = Beneficiary.query.filter_by(user_id=user.id).first()
        before_balance = account.balance
        operation_key = 'transfer-test-key-0000000001'

        transaction = process_operation(
            user, 'TRANSFERENCIA', Decimal('12.34'),
            'Transferencia de prueba', beneficiary_id=beneficiary.id,
            idempotency_key=operation_key,
        )
        first_transfer = Transfer.query.one()
        assert first_transfer.transaction_id == transaction.id
        assert first_transfer.account_id == account.id
        assert first_transfer.beneficiary_id == beneficiary.id
        assert first_transfer.amount == Decimal('12.34')
        assert first_transfer.status == 'REGISTRADA'
        assert first_transfer.is_seed is False
        assert account.balance == before_balance - Decimal('12.34')

        replay = process_operation(
            user, 'TRANSFERENCIA', Decimal('12.34'),
            'Transferencia de prueba', beneficiary_id=beneficiary.id,
            idempotency_key=operation_key,
        )
        assert replay.id == transaction.id
        assert Transfer.query.count() == 1
        assert Transaction.query.filter_by(idempotency_key=operation_key).count() == 1


def test_transfer_rejects_beneficiary_owned_by_another_user():
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        account = Account.query.filter_by(user_id=user.id).one()
        admin = User.query.filter_by(username='admin').one()
        other_beneficiary = Beneficiary(
            user_id=admin.id,
            name='Beneficiario ajeno',
            account_number='009999999999',
        )
        db.session.add(other_beneficiary)
        db.session.flush()
        balance = account.balance

        with pytest.raises(ValueError, match='destinatario válido'):
            process_operation(
                user, 'TRANSFERENCIA', Decimal('12.34'),
                'Transferencia no autorizada', beneficiary_id=other_beneficiary.id,
                idempotency_key='transfer-test-key-0000000002',
            )
        db.session.rollback()
        assert Transfer.query.count() == 0
        assert account.balance == balance
