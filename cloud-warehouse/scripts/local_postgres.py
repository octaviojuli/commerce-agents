"""Create/start an isolated loopback-only development database, never a system service.

Run with the repository Python. Runtime credentials are written mode 0600 under the
ignored .warehouse directory. No application credentials are printed.
"""

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".warehouse"
DATA = STATE / "postgres"
PORT = 55432


def main() -> None:
    os.umask(0o077)
    STATE.mkdir(exist_ok=True, mode=0o700)
    for executable in ("initdb", "pg_ctl"):
        if shutil.which(executable) is None:
            raise SystemExit(f"Install PostgreSQL 16+ first; missing {executable}")
    config_path = STATE / "database.json"
    if not config_path.exists():
        if DATA.exists():
            raise SystemExit("Existing cluster without credentials; recover it manually.")
        settings = {
            "admin_password": secrets.token_urlsafe(32),
            "runtime_password": secrets.token_urlsafe(32),
        }
        config_path.write_text(json.dumps(settings))
    settings = json.loads(config_path.read_text())
    settings.setdefault("auth_password", secrets.token_urlsafe(32))
    config_path.write_text(json.dumps(settings))
    if not (DATA / "PG_VERSION").exists():
        password_file = STATE / "init-password"
        password_file.write_text(settings["admin_password"])
        try:
            subprocess.run(
                [
                    "initdb",
                    "-D",
                    str(DATA),
                    "-U",
                    "warehouse_admin",
                    "--auth=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C",
                    f"--pwfile={password_file}",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA / "postgresql.conf").open("a") as handle:
            handle.write(
                f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nunix_socket_directories=''\nshared_buffers='64MB'\nmax_connections=30\n"
            )
    running = (
        subprocess.run(["pg_ctl", "-D", str(DATA), "status"], capture_output=True).returncode == 0
    )
    if not running:
        subprocess.run(
            ["pg_ctl", "-D", str(DATA), "-l", str(STATE / "postgres.log"), "-w", "start"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    with psycopg.connect(
        host="127.0.0.1",
        port=PORT,
        dbname="postgres",
        user="warehouse_admin",
        password=settings["admin_password"],
        autocommit=True,
    ) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='warehouse_runtime'").fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE warehouse_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                ).format(sql.Literal(settings["runtime_password"]))
            )
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='warehouse_auth'").fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE warehouse_auth LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                ).format(sql.Literal(settings["auth_password"]))
            )
        for name in ("warehouse", "warehouse_test"):
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                conn.execute(
                    sql.SQL("CREATE DATABASE {} OWNER warehouse_admin").format(sql.Identifier(name))
                )
    settings.update(
        {
            "admin_url": f"postgresql+psycopg://warehouse_admin:{settings['admin_password']}@127.0.0.1:{PORT}/warehouse",
            "runtime_url": f"postgresql+psycopg://warehouse_runtime:{settings['runtime_password']}@127.0.0.1:{PORT}/warehouse",
            "auth_url": f"postgresql+psycopg://warehouse_auth:{settings['auth_password']}@127.0.0.1:{PORT}/warehouse",
        }
    )
    config_path.write_text(json.dumps(settings))
    print(f"Isolated PostgreSQL ready at 127.0.0.1:{PORT}; config: .warehouse/database.json (0600)")


if __name__ == "__main__":
    main()
