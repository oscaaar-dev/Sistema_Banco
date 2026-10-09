"""Copy the existing local Norte MySQL data into an empty Railway MySQL service.

The Railway CLI supplies the destination credentials in memory. The script never
prints connection strings or user data, and deliberately omits pending OTP codes.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, inspect, insert, select
from sqlalchemy.engine import make_url

from config import DATABASE_URL
from app.models import db


RAILWAY_TUNNEL_HOST = "127.0.0.1"
RAILWAY_TUNNEL_PORT = 43306
TRANSIENT_TABLES = {"login_challenges"}


def railway_mysql_url():
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "railway variable list --service MySQL --json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    variables = payload.get("variables", payload)
    raw_url = variables.get("MYSQL_URL")
    if not raw_url:
        raise RuntimeError("Railway did not return the MySQL_URL variable.")

    url = make_url(raw_url)
    if url.get_backend_name() != "mysql":
        raise RuntimeError("Railway MYSQL_URL is not a MySQL connection URL.")
    if url.get_driver_name() != "pymysql":
        url = url.set(drivername="mysql+pymysql")
    return url.set(host=RAILWAY_TUNNEL_HOST, port=RAILWAY_TUNNEL_PORT)


def copy_existing_data():
    if not DATABASE_URL:
        raise RuntimeError("The local MySQL DATABASE_URL is missing from .env.")

    source_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    destination_engine = create_engine(railway_mysql_url(), pool_pre_ping=True)
    tables = [table for table in db.metadata.sorted_tables if table.name not in TRANSIENT_TABLES]

    try:
        source_tables = set(inspect(source_engine).get_table_names())
        expected = {table.name for table in tables}
        missing = expected - source_tables
        if missing:
            raise RuntimeError("Local MySQL is missing application tables: " + ", ".join(sorted(missing)))

        with source_engine.connect() as source:
            users = {
                username: email
                for username, email in source.execute(
                    select(db.metadata.tables["users"].c.username, db.metadata.tables["users"].c.email)
                    .where(db.metadata.tables["users"].c.username.in_(("demo", "admin")))
                )
            }
        if not all(users.get(name) for name in ("demo", "admin")):
            raise RuntimeError("Local MySQL must contain demo and admin users with email addresses.")

        db.metadata.create_all(destination_engine)
        destination_tables = set(inspect(destination_engine).get_table_names())
        for table in tables:
            if table.name in destination_tables:
                with destination_engine.connect() as destination:
                    count = destination.scalar(select(func.count()).select_from(table))
                if count:
                    raise RuntimeError(
                        f"Railway table '{table.name}' is not empty; migration aborted without overwriting data."
                    )

        copied = {}
        with source_engine.connect() as source, destination_engine.begin() as destination:
            for table in tables:
                rows = source.execute(select(table)).mappings().all()
                if rows:
                    destination.execute(insert(table), [dict(row) for row in rows])
                copied[table.name] = len(rows)

            for table in tables:
                actual = destination.scalar(select(func.count()).select_from(table))
                if actual != copied[table.name]:
                    raise RuntimeError(f"Row-count verification failed for '{table.name}'.")

        print("Migration completed. Copied application rows:")
        for name, count in copied.items():
            print(f"  {name}: {count}")
        print("Pending login OTP challenges were not copied; local MySQL was left unchanged.")
    finally:
        source_engine.dispose()
        destination_engine.dispose()


if __name__ == "__main__":
    copy_existing_data()
