"""Run warehouse integration tests in a disposable loopback PostgreSQL database.

WAREHOUSE_CI_ADMIN_URL must explicitly point to the maintenance database of a
dedicated test cluster. This script never reads local application credentials.
"""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from cloud_warehouse.persistence import engine_for

ROOT = Path(__file__).resolve().parents[2]


def bootstrap_url(value: str) -> URL:
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "::1", "localhost"}
        or url.database != "postgres"
        or not url.username
        or not url.password
        or url.query
    ):
        raise ValueError("CI requires an explicit loopback PostgreSQL maintenance URL")
    return url


def require_tools() -> None:
    for name in ("pg_dump", "pg_restore", "pdftotext"):
        if shutil.which(name) is None:
            raise ValueError(f"CI requires {name}; integration tests must not be skipped")


def check_report(path: Path) -> None:
    cases = ElementTree.parse(path).getroot().findall(".//testcase")
    if not cases or any(case.find("skipped") is not None for case in cases):
        raise ValueError("CI requires executed tests with zero skipped cases")
    if any(case.find("failure") is not None or case.find("error") is not None for case in cases):
        raise ValueError("Integration test report contains failures")


@contextmanager
def isolated_database(value: str):
    """Own one disposable database and restricted roles for tests or capacity checks."""
    url = bootstrap_url(value)
    cluster = engine_for(url)
    suffix = uuid4().hex
    database = f"warehouse_ci_{suffix}_test"
    roles = {
        "WAREHOUSE_TEST_DATABASE_URL": (
            f"warehouse_ci_runtime_{suffix}",
            secrets.token_urlsafe(32),
        ),
        "WAREHOUSE_TEST_AUTH_URL": (f"warehouse_ci_auth_{suffix}", secrets.token_urlsafe(32)),
    }
    created_roles = []
    created_database = False
    try:
        with cluster.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            quote = conn.dialect.identifier_preparer.quote
            for role, password in roles.values():
                # Use the native driver so SQLAlchemy cannot echo a password-bearing
                # DDL statement in an exception. Role names are generated, not inputs.
                conn.connection.driver_connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOINHERIT NOBYPASSRLS PASSWORD {}"
                    ).format(
                        sql.Identifier(role),
                        sql.Literal(password),
                    )
                )
                created_roles.append(role)
            conn.execute(text(f"CREATE DATABASE {quote(database)}"))
            created_database = True
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("WAREHOUSE_", "TOUR_", "ANTHROPIC_", "SUPPLIER_"))
        }
        environment["WAREHOUSE_TEST_ADMIN_URL"] = url.set(database=database).render_as_string(
            hide_password=False
        )
        for variable, (role, password) in roles.items():
            environment[variable] = url.set(
                database=database, username=role, password=password
            ).render_as_string(hide_password=False)
        yield environment
    finally:
        # Drop only resources created successfully by this invocation. Never drop an
        # existing name after a CREATE failure, and never touch the application database.
        try:
            with cluster.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                quote = conn.dialect.identifier_preparer.quote
                if created_database:
                    conn.execute(text(f"DROP DATABASE {quote(database)} WITH (FORCE)"))
                for role in reversed(created_roles):
                    conn.execute(text(f"DROP ROLE {quote(role)}"))
        finally:
            cluster.dispose()


def run(value: str) -> int:
    require_tools()
    with isolated_database(value) as environment:
        with tempfile.TemporaryDirectory(prefix="warehouse-ci-report-") as directory:
            report = Path(directory) / "results.xml"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "cloud-warehouse/tests",
                    "examples/tour/api/tests/test_warehouse_connector.py",
                    "examples/tour/api/tests/test_warehouse_legacy.py",
                    "-q",
                    "--tb=short",
                    f"--junitxml={report}",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                timeout=600,
            )
            if result.returncode:
                return result.returncode
            check_report(report)
        print("Warehouse integration tests executed with zero skips")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    value = os.environ.get("WAREHOUSE_CI_ADMIN_URL")
    if not value:
        parser.exit(2, "WAREHOUSE_CI_ADMIN_URL is required; no local credentials are loaded\n")
    try:
        code = run(value)
    except ValueError:
        parser.exit(2, "CI configuration, required tools or zero-skip test report is invalid\n")
    except Exception:
        # Connection URLs and generated role passwords must not reach CI logs.
        parser.exit(1, "Warehouse CI failed; check test output and disposable cluster state\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
