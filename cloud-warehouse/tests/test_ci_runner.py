import runpy
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

CI = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/ci_database.py"))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://acme:secret@db.acme.example/postgres",
        "postgresql+psycopg://acme:secret@127.0.0.1/warehouse",
        "postgresql+psycopg://acme:secret@127.0.0.1/postgres?host=db.acme.example",
        "postgresql+psycopg://acme@127.0.0.1/postgres",
    ],
)
def test_ci_rejects_remote_application_and_override_targets(url):
    with pytest.raises(ValueError):
        CI["bootstrap_url"](url)


@pytest.mark.parametrize(
    "case",
    [
        "",
        '<testcase><skipped message="missing database"/></testcase>',
        "<testcase><failure/></testcase>",
    ],
)
def test_ci_report_rejects_empty_skipped_or_failed_results(tmp_path, case):
    report = tmp_path / "report.xml"
    report.write_text(f"<testsuites><testsuite>{case}</testsuite></testsuites>")
    with pytest.raises(ValueError):
        CI["check_report"](report)


def test_ci_cleans_its_database_and_roles_after_test_failure(database, monkeypatch):
    admin, _ = database
    bootstrap = admin.url.set(database="postgres").render_as_string(hide_password=False)

    def resources():
        with admin.connect() as conn:
            return (
                set(conn.execute(text("SELECT datname FROM pg_database")).scalars()),
                set(conn.execute(text("SELECT rolname FROM pg_roles")).scalars()),
            )

    before = resources()

    def failed_tests(command, **kwargs):
        assert command[1:3] == ["-m", "pytest"]
        environment = kwargs["env"]
        assert "TOUR_ERP_PASSWORD" not in environment
        assert "ANTHROPIC_API_KEY" not in environment
        for key in (
            "WAREHOUSE_TEST_ADMIN_URL",
            "WAREHOUSE_TEST_DATABASE_URL",
            "WAREHOUSE_TEST_AUTH_URL",
        ):
            assert "warehouse_ci_" in environment[key]
            assert environment[key].endswith("_test")
        during = resources()
        assert len(during[0] - before[0]) == 1
        assert len(during[1] - before[1]) == 2
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setenv("TOUR_ERP_PASSWORD", "ACME-never-inherit")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ACME-never-inherit")
    monkeypatch.setattr(CI["subprocess"], "run", failed_tests)
    assert CI["run"](bootstrap) == 7
    assert resources() == before
