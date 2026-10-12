"""Opciones del motor SQL y límites configurables por entorno (config.py)."""
import pytest
from sqlalchemy import create_engine, text

import config
from app import create_app


@pytest.mark.parametrize('uri', ['sqlite://', 'sqlite:///:memory:', 'sqlite:///instance/norte_local.db'])
def test_sqlite_connections_are_never_recycled(uri):
    options = config._engine_options(uri)
    assert 'pool_recycle' not in options and options['pool_pre_ping'] is True and options['hide_parameters'] is True


@pytest.mark.parametrize('uri', ['mysql+pymysql://u:p@host/db', 'postgresql+psycopg://u:p@host/db'])
def test_server_databases_still_recycle_connections(uri):
    options = config._engine_options(uri)
    assert options == {'pool_pre_ping': True, 'hide_parameters': True, 'pool_recycle': 300}


def test_an_in_memory_sqlite_database_is_not_dropped_when_the_old_recycle_time_passes():
    """Con pool_recycle una base en memoria se descartaba (con todas sus tablas) pasado ese tiempo; sin él, la conexión se conserva."""
    engine = create_engine('sqlite://', **config._engine_options('sqlite://'))
    assert engine.pool._recycle == -1
    with engine.begin() as connection:
        connection.execute(text('create table t(x int)'))
    recycled = create_engine('sqlite://', pool_recycle=1)
    assert recycled.pool._recycle == 1                                 # lo que ocurría antes: la conexión caduca y la base se pierde


def test_the_application_uses_these_options():
    app = create_app()
    assert app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite')
    assert 'pool_recycle' not in app.config['SQLALCHEMY_ENGINE_OPTIONS']
    assert config.Config.SQLALCHEMY_ENGINE_OPTIONS == config._engine_options(config.Config.SQLALCHEMY_DATABASE_URI)


@pytest.mark.parametrize('value,expected', [(None, 20), ('', 20), ('5000', 5000), (' 7 ', 7), ('abc', 20), ('0', 1), ('-3', 1), ('2.5', 20)])
def test_integer_environment_overrides_are_validated(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('NORI_TEST_LIMIT', raising=False)
    else:
        monkeypatch.setenv('NORI_TEST_LIMIT', value)
    assert config._int_env('NORI_TEST_LIMIT', 20) == expected


def test_nori_limits_keep_their_documented_defaults_when_no_environment_is_set(monkeypatch):
    for name in ('NORI_RATE_LIMIT_MESSAGES', 'NORI_DAILY_LIMIT'):
        monkeypatch.delenv(name, raising=False)
    assert config._int_env('NORI_RATE_LIMIT_MESSAGES', 20) == 20 and config._int_env('NORI_DAILY_LIMIT', 300) == 300
