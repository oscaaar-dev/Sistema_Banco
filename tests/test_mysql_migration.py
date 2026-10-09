import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from app.models import Account, User, db
from scripts.migrate_sqlite_to_mysql import copy_database


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_copy_database_preserves_rows_and_identifiers():
    source = _memory_engine()
    destination = _memory_engine()
    db.metadata.create_all(source)

    with source.begin() as connection:
        connection.execute(
            insert(User.__table__).values(
                id=27,
                username="migration-user",
                full_name="Migration User",
                email="migration@example.test",
                dni="12345678",
                password_hash="hashed-value",
                role="CLIENTE",
            )
        )
        connection.execute(
            insert(Account.__table__).values(
                id=41,
                user_id=27,
                account_type="Ahorros",
                number="001122334455",
                currency="PEN",
                balance=125.50,
                status="ACTIVA",
            )
        )

    copied = copy_database(source, destination)

    with destination.connect() as connection:
        user = connection.execute(
            select(User.__table__).where(User.id == 27)
        ).mappings().one()
        account = connection.execute(
            select(Account.__table__).where(Account.id == 41)
        ).mappings().one()

    assert copied["users"] == 1
    assert copied["accounts"] == 1
    assert user["username"] == "migration-user"
    assert account["user_id"] == user["id"]
    source.dispose()
    destination.dispose()


def test_copy_database_refuses_to_append_to_nonempty_destination():
    source = _memory_engine()
    destination = _memory_engine()
    db.metadata.create_all(source)
    db.metadata.create_all(destination)
    with destination.begin() as connection:
        connection.execute(
            insert(User.__table__).values(
                username="existing-user",
                full_name="Existing User",
                password_hash="hashed-value",
                role="CLIENTE",
            )
        )

    with pytest.raises(RuntimeError, match="not empty"):
        copy_database(source, destination)

    source.dispose()
    destination.dispose()
