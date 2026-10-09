from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_migration_up_and_down_only_on_test_sqlite(monkeypatch):
    monkeypatch.setenv('APP_ENV','testing')
    monkeypatch.setenv('MIGRATION_DATABASE_URL','sqlite:///:memory:')
    project_root=Path(__file__).resolve().parents[1]
    migration_config=Config(str(project_root/'alembic.ini'))
    migration_config.set_main_option('script_location',str(project_root/'migrations'))
    engine=create_engine('sqlite:///:memory:')

    with engine.begin() as connection:
        migration_config.attributes['connection']=connection
        command.upgrade(migration_config,'head')
        tables=set(inspect(connection).get_table_names())
        assert {'users','accounts','transactions','cards','beneficiaries','login_challenges','transfers','operation_challenges'}<=tables
        assert 'alembic_version' in tables
        command.downgrade(migration_config,'base')
        remaining=set(inspect(connection).get_table_names())
        assert not {'users','accounts','transactions','cards','beneficiaries','login_challenges','transfers','operation_challenges'}&remaining

    engine.dispose()
