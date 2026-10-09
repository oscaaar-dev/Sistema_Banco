import os
import secrets
import subprocess
import time
from pathlib import Path

import pymysql
from sqlalchemy import URL, create_engine

from scripts.migrate_sqlite_to_mysql import DEFAULT_SOURCE, copy_database


MYSQL_BIN = Path(r"C:\Program Files\MySQL\MySQL Server 8.4\bin")
MYSQLD = MYSQL_BIN / "mysqld.exe"
NORTE_MYSQL_ROOT = Path(os.environ["LOCALAPPDATA"]) / "NorteBcpMySQL"
DATA_DIR = NORTE_MYSQL_ROOT / "data"
LOG_PATH = NORTE_MYSQL_ROOT / "server.log"
ERROR_LOG_PATH = NORTE_MYSQL_ROOT / "server-error.log"
PID_PATH = NORTE_MYSQL_ROOT / "server.pid"
ROOT_PASSWORD_PATH = NORTE_MYSQL_ROOT / "admin-password.txt"
HOST = "127.0.0.1"
PORT = 3307
DATABASE = "norte_bcp"
APP_USER = "norte_bcp_app"


def _mysql_args(*args: str) -> list[str]:
    return [
        str(MYSQLD),
        "--no-defaults",
        f"--basedir={MYSQL_BIN.parent}",
        f"--datadir={DATA_DIR}",
        f"--port={PORT}",
        f"--bind-address={HOST}",
        "--mysqlx=0",
        "--local-infile=0",
        f"--pid-file={PID_PATH}",
        f"--log-error={ERROR_LOG_PATH}",
        *args,
    ]


def _write_private_file(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    identity = subprocess.run(
        ["whoami"], capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{identity}:(R,W)",
            "SYSTEM:(F)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _connect(password: str | None, database: str | None = None):
    return pymysql.connect(
        host=HOST,
        port=PORT,
        user="root",
        password=password or "",
        database=database,
        connect_timeout=2,
        charset="utf8mb4",
        autocommit=True,
    )


def _verify_server(password: str | None) -> bool:
    try:
        with _connect(password) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT @@datadir, @@port")
                actual_data_dir, actual_port = cursor.fetchone()
        return (
            os.path.normcase(os.path.realpath(actual_data_dir))
            == os.path.normcase(os.path.realpath(DATA_DIR))
            and int(actual_port) == PORT
        )
    except pymysql.MySQLError:
        return False


def _wait_for_server(timeout: int = 45) -> None:
    deadline = time.monotonic() + timeout
    root_password = (
        ROOT_PASSWORD_PATH.read_text(encoding="utf-8").strip()
        if ROOT_PASSWORD_PATH.exists()
        else None
    )
    while time.monotonic() < deadline:
        if _verify_server(root_password):
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"Dedicated MySQL did not become ready on port {PORT}. "
        f"Check the log at {ERROR_LOG_PATH}."
    )


def _start_server() -> None:
    if not MYSQLD.is_file():
        raise FileNotFoundError(f"MySQL server binary not found: {MYSQLD}")
    if not DEFAULT_SOURCE.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {DEFAULT_SOURCE}")
    if ROOT_PASSWORD_PATH.exists():
        raise RuntimeError(
            "This dedicated MySQL instance is already initialized. "
            "Use scripts.start_dedicated_mysql and scripts.migrate_sqlite_to_mysql "
            "instead of running bootstrap again."
        )
    if DATA_DIR.exists():
        _wait_for_server()
        return

    NORTE_MYSQL_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        _mysql_args("--initialize-insecure", "--console"),
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    log_file = LOG_PATH.open("ab")
    try:
        subprocess.Popen(
            _mysql_args("--console"),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    finally:
        log_file.close()
    _wait_for_server()


def _initialize_accounts() -> tuple[str, str]:
    root_password = secrets.token_urlsafe(48)
    app_password = secrets.token_urlsafe(48)
    with _connect(None) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (root_password,))
            cursor.execute(
                "CREATE DATABASE IF NOT EXISTS norte_bcp "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS %s@'127.0.0.1' IDENTIFIED BY %s",
                (APP_USER, app_password),
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS %s@'localhost' IDENTIFIED BY %s",
                (APP_USER, app_password),
            )
            cursor.execute(
                "ALTER USER %s@'127.0.0.1' IDENTIFIED BY %s",
                (APP_USER, app_password),
            )
            cursor.execute(
                "ALTER USER %s@'localhost' IDENTIFIED BY %s",
                (APP_USER, app_password),
            )
            cursor.execute("GRANT ALL PRIVILEGES ON norte_bcp.* TO %s@'127.0.0.1'", (APP_USER,))
            cursor.execute("GRANT ALL PRIVILEGES ON norte_bcp.* TO %s@'localhost'", (APP_USER,))
    _write_private_file(ROOT_PASSWORD_PATH, root_password)
    return root_password, app_password


def main() -> None:
    _start_server()
    root_password, app_password = _initialize_accounts()
    database_url = URL.create(
        "mysql+pymysql",
        username=APP_USER,
        password=app_password,
        host=HOST,
        port=PORT,
        database=DATABASE,
        query={"charset": "utf8mb4"},
    )

    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    env_lines = [
        line for line in env_lines if not line.strip().startswith("DATABASE_URL=")
    ]
    env_lines.append(f"DATABASE_URL={database_url.render_as_string(hide_password=False)}")
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    source_engine = create_engine(f"sqlite:///{DEFAULT_SOURCE.resolve().as_posix()}")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        copied = copy_database(source_engine, engine)
    finally:
        source_engine.dispose()
        engine.dispose()

    print(f"Norte MySQL instance is running on {HOST}:{PORT}.")
    print(f"Database: {DATABASE}; data directory: {DATA_DIR}")
    print("Application credentials saved in the local .env file.")
    print(f"Private MySQL administrator password stored at: {ROOT_PASSWORD_PATH}")
    print("SQLite data copied; the original SQLite database was not changed.")
    for table, count in copied.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
