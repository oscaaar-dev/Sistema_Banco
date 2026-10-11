import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, func, inspect, insert, select
from sqlalchemy.engine import Engine, make_url

from app.models import db


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "instance" / "norte_local.db"


def copy_database(source_engine: Engine, destination_engine: Engine) -> dict[str, int]:
    tables = db.metadata.sorted_tables
    expected_tables = {table.name for table in tables}
    source_tables = set(inspect(source_engine).get_table_names())
    missing_tables = expected_tables - source_tables
    if missing_tables:
        raise RuntimeError(
            "SQLite is missing application tables: " + ", ".join(sorted(missing_tables))
        )

    db.metadata.create_all(destination_engine)
    destination_inspector = inspect(destination_engine)
    for table in tables:
        if table.name in destination_inspector.get_table_names():
            with destination_engine.connect() as connection:
                row_count = connection.scalar(select(func.count()).select_from(table))
            if row_count:
                raise RuntimeError(
                    f"Destination table '{table.name}' is not empty; migration aborted."
                )

    copied: dict[str, int] = {}
    with source_engine.connect() as source, destination_engine.begin() as destination:
        for table in tables:
            rows = source.execute(select(table)).mappings().all()
            if rows:
                destination.execute(insert(table), [dict(row) for row in rows])
            copied[table.name] = len(rows)

        for table in tables:
            count = destination.scalar(select(func.count()).select_from(table))
            if count != copied[table.name]:
                raise RuntimeError(
                    f"Row count verification failed for table '{table.name}'."
                )

    return copied


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Copy the local SQLite application data into an empty MySQL database."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="SQLite database file (default: instance/norte_local.db).",
    )
    args = parser.parse_args()

    target_url = os.getenv("DATABASE_URL", "").strip()
    if not target_url:
        parser.error("Set DATABASE_URL to the dedicated MySQL connection in .env first.")
    if not args.source.is_file():
        parser.error(f"SQLite source database does not exist: {args.source}")

    parsed_target_url = make_url(target_url)
    if parsed_target_url.get_backend_name() != "mysql":
        parser.error("DATABASE_URL must point to MySQL for this migration.")
    if parsed_target_url.get_driver_name() != "pymysql":
        parser.error("Use the mysql+pymysql:// driver in DATABASE_URL.")

    source_engine = create_engine(f"sqlite:///{args.source.resolve().as_posix()}")
    destination_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        copied = copy_database(source_engine, destination_engine)
    finally:
        source_engine.dispose()
        destination_engine.dispose()

    print("Migration completed. Copied rows:")
    for table_name, row_count in copied.items():
        print(f"  {table_name}: {row_count}")
    print("The SQLite source was left unchanged.")


if __name__ == "__main__":
    main()
