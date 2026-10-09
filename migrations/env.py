from logging.config import fileConfig
import os
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

project_root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(project_root))
from app.models import db

config=context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata=db.metadata
migration_url=os.getenv('MIGRATION_DATABASE_URL','').strip()
if not migration_url:
    raise RuntimeError('Define MIGRATION_DATABASE_URL explícitamente antes de ejecutar Alembic.')

database_url=make_url(migration_url)
if database_url.get_backend_name() not in ('postgresql','sqlite'):
    raise RuntimeError('Las migraciones admiten PostgreSQL; SQLite solo se admite para pruebas aisladas.')
if database_url.get_backend_name()=='sqlite' and os.getenv('APP_ENV','').strip().lower()!='testing':
    raise RuntimeError('SQLite solo se admite para migraciones de prueba con APP_ENV=testing.')
config.set_main_option('sqlalchemy.url',migration_url.replace('%','%%'))

def run_migrations_offline():
    context.configure(url=migration_url,target_metadata=target_metadata,literal_binds=True,
                      dialect_opts={'paramstyle':'named'},compare_type=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connection=config.attributes.get('connection')
    if connection is not None:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    connectable=engine_from_config(config.get_section(config.config_ini_section,{}),
                                   prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
