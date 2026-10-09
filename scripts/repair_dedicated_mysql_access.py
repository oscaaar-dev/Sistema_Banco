import os
import secrets
import subprocess
from pathlib import Path

import pymysql
from dotenv import dotenv_values
from sqlalchemy import URL, create_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_ROOT = Path(os.environ["LOCALAPPDATA"]) / "NorteBcpMySQL"
ADMIN_PASSWORD_FILE = INSTANCE_ROOT / "admin-password.txt"
WORKBENCH_LOGIN_FILE = INSTANCE_ROOT / "workbench-login.txt"
ENV_FILE = PROJECT_ROOT / ".env"
HOST = "127.0.0.1"
PORT = 3307
DATABASE = "norte_bcp"
APP_USER = "norte_bcp_app"
WORKBENCH_USER = "norte_bcp_viewer"


def _save_private_file(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
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


def _connect(username: str, password: str) -> pymysql.Connection:
    return pymysql.connect(
        host=HOST,
        port=PORT,
        user=username,
        password=password,
        database=DATABASE,
        connect_timeout=5,
        charset="utf8mb4",
        autocommit=True,
    )


def _set_env_database_url(database_url: URL) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    indexes = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("DATABASE_URL=")
    ]
    if len(indexes) > 1:
        raise RuntimeError(".env contains multiple DATABASE_URL settings.")
    setting = f"DATABASE_URL={database_url.render_as_string(hide_password=False)}"
    if indexes:
        lines[indexes[0]] = setting
    else:
        lines.append(setting)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if not ADMIN_PASSWORD_FILE.is_file():
        raise FileNotFoundError(
            f"Dedicated MySQL administrator credential not found: {ADMIN_PASSWORD_FILE}"
        )
    admin_password = ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    app_password = secrets.token_urlsafe(48)
    workbench_password = secrets.token_urlsafe(32)

    with pymysql.connect(
        host=HOST,
        port=PORT,
        user="root",
        password=admin_password,
        connect_timeout=5,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            for host_entry in ("localhost", "127.0.0.1"):
                cursor.execute(
                    f"CREATE USER IF NOT EXISTS '{APP_USER}'@'{host_entry}' IDENTIFIED BY %s",
                    (app_password,),
                )
                cursor.execute(
                    f"ALTER USER '{APP_USER}'@'{host_entry}' IDENTIFIED BY %s",
                    (app_password,),
                )
                cursor.execute(
                    f"GRANT ALL PRIVILEGES ON `{DATABASE}`.* TO '{APP_USER}'@'{host_entry}'"
                )
                cursor.execute(
                    f"CREATE USER IF NOT EXISTS '{WORKBENCH_USER}'@'{host_entry}' IDENTIFIED BY %s",
                    (workbench_password,),
                )
                cursor.execute(
                    f"ALTER USER '{WORKBENCH_USER}'@'{host_entry}' IDENTIFIED BY %s",
                    (workbench_password,),
                )
                cursor.execute(
                    f"GRANT SELECT, SHOW VIEW ON `{DATABASE}`.* "
                    f"TO '{WORKBENCH_USER}'@'{host_entry}'"
                )

    database_url = URL.create(
        "mysql+pymysql",
        username=APP_USER,
        password=app_password,
        host=HOST,
        port=PORT,
        database=DATABASE,
        query={"charset": "utf8mb4"},
    )
    _set_env_database_url(database_url)
    _save_private_file(
        WORKBENCH_LOGIN_FILE,
        f"Username: {WORKBENCH_USER}\nPassword: {workbench_password}\n"
        f"Host: {HOST}\nPort: {PORT}\nSchema: {DATABASE}\n",
    )

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT DATABASE(), @@port, COUNT(*) FROM users"
            ).one()
            if row[0] != DATABASE or row[1] != PORT:
                raise RuntimeError("Verified application account reached the wrong MySQL instance.")
    finally:
        engine.dispose()

    with _connect(WORKBENCH_USER, workbench_password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM users")
            viewer_count = cursor.fetchone()[0]
    if viewer_count != row[2]:
        raise RuntimeError("Workbench account verification returned an unexpected user count.")

    print(f"Norte application credential rotated and verified on port {PORT}.")
    print(f"Read-only Workbench login saved locally at: {WORKBENCH_LOGIN_FILE}")
    print(f"Workbench user: {WORKBENCH_USER}; host: {HOST}; port: {PORT}; schema: {DATABASE}.")
    print("Passwords were not printed.")


if __name__ == "__main__":
    main()
