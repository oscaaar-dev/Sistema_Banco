import pytest
from flask import Flask
from sqlalchemy import inspect

from app import _validate_existing_schema
from app.models import db


def test_schema_guard_refuses_to_create_or_alter_persistent_schema():
    app=Flask('schema-guard-test')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        with pytest.raises(RuntimeError,match='no crea ni altera esquemas persistentes'):
            _validate_existing_schema()
        assert inspect(db.engine).get_table_names()==[]
