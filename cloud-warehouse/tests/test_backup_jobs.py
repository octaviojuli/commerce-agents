import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import backup_jobs, cli, recovery
from cloud_warehouse.admin import migrate
from cloud_warehouse.assets import OBJECT_WRITE_LOCK
from cloud_warehouse.persistence import engine_for

from .test_recovery import empty_database


@pytest.fixture
def job(tmp_path):
    root, objects = tmp_path / "backups", tmp_path / "objects"
    root.mkdir(mode=0o700)
    objects.mkdir(mode=0o700)
    return root, objects


def run(url, job, **kwargs):
    root, objects = job
    return backup_jobs.run(
        url,
        objects,
        root,
        expected_database=backup_jobs.make_url(url).database,
        max_bytes=kwargs.get("max_bytes", 2**40),
        reserve_bytes=kwargs.get("reserve_bytes", 1),
    )


def test_real_cycle_preserves_prior_success_on_budget_failure_and_reentry(
    database, job, monkeypatch
):
    root, _ = job
    with empty_database(database[0], f"warehouse_recovery_{uuid4().hex}_test") as url:
        migrate(url)
        original = recovery.backup

        def nested(*args):
            assert run(url, job) == {"status": "already_running"}
            assert backup_jobs.status(root, max_age_seconds=900)["status"] == "running"
            return original(*args)

        monkeypatch.setattr(recovery, "backup", nested)
        result = run(url, job)
        assert result["status"] == "succeeded"
        manifest = recovery.inspect_bundle(root / result["last_bundle"])
        assert result["last_success_at"] == manifest.created_at.isoformat().replace("+00:00", "Z")
        observed = backup_jobs.status(root, max_age_seconds=900)
        assert observed["fresh"] and observed["status"] == "succeeded"
        assert not observed["restore_verified"] and not observed["bundle_reverified"]
        with pytest.raises(backup_jobs.BackupJobError, match="SPACE_GUARD"):
            run(url, job, max_bytes=1)
        failed = backup_jobs.status(root, max_age_seconds=900)
        assert failed["status"] == "failed" and failed["fresh"]
        assert failed["last_bundle"] == result["last_bundle"]
        assert failed["last_success_at"] == result["last_success_at"]
        assert len(list(root.glob("run-*"))) == 1
        assert recovery.inspect_bundle(root / result["last_bundle"]) == manifest
        original_inspect = recovery.inspect_bundle

        def invalid_bundle(*args):
            raise ValueError("ACME-private-inspection-error")

        monkeypatch.setattr(recovery, "inspect_bundle", invalid_bundle)
        with pytest.raises(backup_jobs.BackupJobError, match="^BACKUP_FAILED$"):
            run(url, job)
        assert len(list(root.glob("run-*"))) == 2
        assert backup_jobs.status(root, max_age_seconds=900)["last_bundle"] == result["last_bundle"]
        assert original_inspect(root / result["last_bundle"]) == manifest
        (root / result["last_bundle"]).rename(root / "operator-moved-bundle")
        assert not backup_jobs.status(root, max_age_seconds=900)["fresh"]


def test_interrupted_marker_is_not_a_live_process_and_retry_owns_new_bundle(job, monkeypatch):
    root, _ = job
    monkeypatch.setattr(backup_jobs, "_estimate", lambda *_: 1)

    def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(recovery, "backup", interrupted)
    url = "postgresql+psycopg://acme@localhost/acme_test"
    with pytest.raises(KeyboardInterrupt):
        run(url, job)
    observed = backup_jobs.status(root, max_age_seconds=900)
    assert observed["status"] == "interrupted" and not observed["fresh"]
    first = observed["attempt_id"]
    with pytest.raises(KeyboardInterrupt):
        run(url, job)
    assert backup_jobs.status(root, max_age_seconds=900)["attempt_id"] != first


def test_space_reserve_refuses_before_export_and_errors_are_fixed(job, monkeypatch):
    root, _ = job
    monkeypatch.setattr(backup_jobs, "_estimate", lambda *_: 100)
    exported = []
    monkeypatch.setattr(recovery, "backup", lambda *args: exported.append(args))
    with pytest.raises(backup_jobs.BackupJobError, match="SPACE_GUARD"):
        run("postgresql+psycopg://acme@localhost/acme_test", job, reserve_bytes=2**63)
    assert not exported
    state = backup_jobs.status(root, max_age_seconds=900)
    assert state["error"] == "SPACE_GUARD" and not state["fresh"]


@pytest.mark.parametrize("mutation", ["database", "objects", "preexisting", "symlink"])
def test_wrong_target_or_unowned_root_rejected_without_network(
    job, tmp_path, monkeypatch, mutation
):
    root, objects = job
    monkeypatch.setattr(backup_jobs, "_estimate", lambda *_: pytest.fail("network must not run"))
    url = "postgresql+psycopg://acme@localhost/acme_test"
    expected = "acme_test"
    if mutation == "database":
        expected = "other_test"
    elif mutation == "preexisting":
        (root / "old-backup").mkdir(mode=0o700)
    elif mutation == "symlink":
        (root / "job.lock").symlink_to(tmp_path / "outside")
    else:
        identity = backup_jobs.Identity(job_id=uuid4(), source="0" * 64, objects="0" * 64)
        backup_jobs._write(root / "job.json", identity)
    with pytest.raises((backup_jobs.BackupJobError, OSError)):
        backup_jobs.run(
            url, objects, root, expected_database=expected, max_bytes=2**40, reserve_bytes=1
        )
    assert not (root / "state.json").exists()


def test_stale_and_future_snapshot_are_not_fresh(job):
    root, _ = job
    identity = backup_jobs.Identity(job_id=uuid4(), source="0" * 64, objects="0" * 64)
    backup_jobs._write(root / "job.json", identity)
    with backup_jobs._lock(root, create=True):
        pass
    for delta in (timedelta(days=-1), timedelta(days=1)):
        backup_jobs._write(
            root / "state.json",
            backup_jobs.State(
                job_id=identity.job_id,
                attempt_id=uuid4(),
                started_at=datetime.now(UTC),
                status="succeeded",
                last_success_at=datetime.now(UTC) + delta,
            ),
        )
        assert not backup_jobs.status(root, max_age_seconds=900)["fresh"]


def test_cli_status_requires_no_database_secret_and_rejects_stale(job, monkeypatch, capsys):
    monkeypatch.delenv("WAREHOUSE_ADMIN_URL", raising=False)
    monkeypatch.setattr(
        backup_jobs, "status", lambda *args, **kwargs: {"status": "interrupted", "fresh": False}
    )
    monkeypatch.setattr(
        "sys.argv",
        ["warehouse", "backup-status", "--root", str(job[0]), "--max-age-seconds", "900"],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "interrupted"

    def secret_error(*args, **kwargs):
        raise ValueError("ACME-private-token")

    monkeypatch.setattr(backup_jobs, "status", secret_error)
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr()
    assert "ACME-private-token" not in output.err + output.out
    assert output.err.strip() == "BACKUP_JOB_UNCONFIRMED"


def test_backup_and_object_collection_lock_exclude_each_other(database, job, monkeypatch):
    root, objects = job
    with empty_database(database[0], f"warehouse_recovery_{uuid4().hex}_test") as url:
        migrate(url)
        admin = engine_for(url)
        try:
            with admin.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": OBJECT_WRITE_LOCK})
                with pytest.raises(recovery.RecoveryError, match="对象清理"):
                    recovery.backup(url, objects, root / "blocked")
            original = recovery._copy_objects

            def check_lock(*args):
                with admin.begin() as conn:
                    assert not conn.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": OBJECT_WRITE_LOCK}
                    )
                return original(*args)

            monkeypatch.setattr(recovery, "_copy_objects", check_lock)
            recovery.backup(url, objects, root / "verified")
            with admin.begin() as conn:
                assert conn.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": OBJECT_WRITE_LOCK}
                )
            assert not list(root.glob(".partial-*"))
        finally:
            admin.dispose()
