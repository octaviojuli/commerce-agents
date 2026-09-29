import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import cli, documents, object_gc
from cloud_warehouse.assets import OBJECT_WRITE_LOCK, LocalObjectStore
from cloud_warehouse.catalog import list_products, synchronize

from .test_documents import docx
from .test_sync import Connector


@pytest.fixture
async def objects(database, tenant, tmp_path):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    product = list_products(runtime, tenant.supplier)[0]
    store = LocalObjectStore(tmp_path / "objects")
    asset = documents.upload(runtime, tenant.supplier, store, product["id"], "ACME.docx", docx())
    # Tests share a database. Retain every other test's metadata too, with private
    # placeholder objects: collection checks references, not content integrity.
    with admin.connect() as conn:
        refs = conn.execute(
            text(
                "SELECT supplier_org_id,id FROM document_asset UNION ALL SELECT supplier_org_id,id FROM document_media UNION ALL SELECT organization_id,id FROM advisor_asset WHERE purged_at IS NULL"
            )
        ).all()
    for org, identifier in refs:
        if str(identifier) != asset["id"]:
            store.put(org, identifier, b"ACME referenced object")
    return store, product, UUID(asset["id"])


def run(admin, store, tmp_path, **kwargs):
    return object_gc.collect(
        admin,
        store.root,
        expected_database=admin.url.database,
        journal=tmp_path / f"{uuid4()}.jsonl",
        **kwargs,
    )


def age_clock(monkeypatch):
    future = time.time() + 8 * 86400
    monkeypatch.setattr(object_gc.time, "time", lambda: future)
    return future


async def test_dry_run_and_cleanup_keep_all_references_and_recent_files(
    database, tenant, objects, tmp_path, monkeypatch
):
    admin, _ = database
    store, _, asset = objects
    org = tenant.supplier.organization_id
    orphan, recent = uuid4(), uuid4()
    store.put(org, orphan, b"orphan")
    store.put(org, recent, b"recent")
    directory = store.root / str(org)
    temporary = directory / f".{uuid4()}.tmp"
    temporary.write_bytes(b"temporary")
    unknown = directory / "keep-this.txt"
    unknown.write_text("ACME")
    link = directory / str(uuid4())
    link.symlink_to(directory / str(asset))
    future = age_clock(monkeypatch)
    os.utime(directory / str(recent), (future, future))
    report = run(admin, store, tmp_path)
    assert report["candidates"] == 2 and report["deleted"] == 0
    assert (directory / str(orphan)).exists() and temporary.exists()
    result = run(admin, store, tmp_path, apply=True)
    assert result["deleted"] == 2 and result["removed_bytes"] == 15
    assert (directory / str(asset)).exists() and (directory / str(recent)).exists()
    assert unknown.exists() and link.is_symlink()
    assert not temporary.exists() and not (directory / str(orphan)).exists()
    assert result["missing"] == 0 and result["unrecognized"] == 2
    journals = [
        json.loads(line) for p in tmp_path.glob("*.jsonl") for line in p.read_text().splitlines()
    ]
    assert len([row for row in journals if row["event"] == "deleted"]) == 2
    assert all(p.stat().st_mode & 0o077 == 0 for p in tmp_path.glob("*.jsonl"))


async def test_mismatched_root_role_database_and_retention_cannot_delete(
    database, tenant, objects, tmp_path, monkeypatch
):
    admin, runtime = database
    store, _, asset = objects
    age_clock(monkeypatch)
    with pytest.raises(object_gc.CollectionError, match="离线管理"):
        run(runtime, store, tmp_path, apply=True)
    with pytest.raises(object_gc.CollectionError, match="数据库名称"):
        object_gc.collect(admin, store.root, expected_database="wrong", journal=tmp_path / "wrong")
    with pytest.raises(object_gc.CollectionError, match="至少一天"):
        run(admin, store, tmp_path, days=0)
    (store.root / str(tenant.supplier.organization_id) / str(asset)).unlink()
    assert run(admin, store, tmp_path)["missing"] == 1
    with pytest.raises(object_gc.CollectionError, match="仅允许检查"):
        run(admin, store, tmp_path, apply=True)


async def test_symlink_root_and_existing_or_internal_journal_refused(database, objects, tmp_path):
    admin, _ = database
    store, _, _ = objects
    path = tmp_path / "linked"
    path.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(OSError):
        object_gc.collect(
            admin, path, expected_database=admin.url.database, journal=tmp_path / "log"
        )
    existing = tmp_path / "existing"
    existing.write_text("keep")
    with pytest.raises(FileExistsError):
        object_gc.collect(admin, store.root, expected_database=admin.url.database, journal=existing)
    assert existing.read_text() == "keep"
    with pytest.raises(object_gc.CollectionError, match="之外"):
        object_gc.collect(
            admin, store.root, expected_database=admin.url.database, journal=store.root / "log"
        )


async def test_uncommitted_document_file_blocks_collection_until_rollback(
    database, tenant, objects, tmp_path, monkeypatch
):
    admin, runtime = database
    store, product, _ = objects
    written, release = Event(), Event()
    original = store.put

    def put(*args):
        original(*args)
        written.set()
        assert release.wait(5)
        raise RuntimeError("ACME injected rollback")

    monkeypatch.setattr(store, "put", put)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            documents.upload,
            runtime,
            tenant.supplier,
            store,
            product["id"],
            "ACME.pdf",
            b"%PDF-1.4\nACME rollback",
        )
        try:
            assert written.wait(5)
            with pytest.raises(object_gc.CollectionError, match="正在运行"):
                run(admin, store, tmp_path, apply=True)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="rollback"):
            future.result(timeout=5)
    age_clock(monkeypatch)
    assert run(admin, store, tmp_path, apply=True)["deleted"] == 1


async def test_collector_lock_prevents_file_creation(
    database, tenant, objects, tmp_path, monkeypatch
):
    admin, runtime = database
    store, product, _ = objects
    entered, written = Event(), Event()
    original = store.put

    def put(*args):
        written.set()
        return original(*args)

    monkeypatch.setattr(store, "put", put)

    def upload():
        entered.set()
        return documents.upload(
            runtime, tenant.supplier, store, product["id"], "ACME.pdf", b"%PDF-1.4\nACME lock"
        )

    with ThreadPoolExecutor(1) as pool:
        with admin.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": OBJECT_WRITE_LOCK})
            future = pool.submit(upload)
            assert entered.wait(5)
            assert not written.wait(0.15)
        future.result(timeout=5)
        assert written.is_set()


async def test_changed_candidate_stops_and_journals_prior_deletions(
    database, tenant, objects, tmp_path, monkeypatch
):
    admin, _ = database
    store, _, _ = objects
    org = tenant.supplier.organization_id
    names = sorted([str(uuid4()), str(uuid4())])
    for name in names:
        store.put(org, UUID(name), b"ACME orphan")
    age_clock(monkeypatch)
    original = object_gc.os.unlink

    def unlink(name, **kwargs):
        original(name, **kwargs)
        (store.root / str(org) / names[1]).write_bytes(b"ACME replaced")

    monkeypatch.setattr(object_gc.os, "unlink", unlink)
    with pytest.raises(object_gc.CollectionError, match="发生变化"):
        run(admin, store, tmp_path, apply=True)
    assert not (store.root / str(org) / names[0]).exists()
    assert (store.root / str(org) / names[1]).read_bytes() == b"ACME replaced"
    rows = [
        json.loads(line) for p in tmp_path.glob("*.jsonl") for line in p.read_text().splitlines()
    ]
    assert sum(row["event"] == "deleted" for row in rows) == 1
    assert not any(row["event"] == "complete" for row in rows)


async def test_cli_is_dry_by_default_and_apply_is_explicit(
    database, tenant, objects, tmp_path, monkeypatch, capsys
):
    admin, _ = database
    store, _, _ = objects
    orphan = uuid4()
    store.put(tenant.supplier.organization_id, orphan, b"ACME orphan")
    age_clock(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_ADMIN_URL", admin.url.render_as_string(hide_password=False))
    base = [
        "warehouse",
        "collect-objects",
        "--target-database",
        admin.url.database,
        "--objects",
        str(store.root),
        "--journal",
    ]
    monkeypatch.setattr(sys, "argv", [*base, str(tmp_path / "inspect.jsonl")])
    cli.main()
    assert json.loads(capsys.readouterr().out)["deleted"] == 0
    monkeypatch.setattr(sys, "argv", [*base, str(tmp_path / "apply.jsonl"), "--apply"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["deleted"] == 1


def test_cli_configuration_errors_do_not_echo_credentials(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WAREHOUSE_ADMIN_URL", "invalid-secret-database-value")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "warehouse",
            "collect-objects",
            "--target-database",
            "warehouse_test",
            "--objects",
            str(tmp_path),
            "--journal",
            str(tmp_path / "journal"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert "invalid-secret" not in capsys.readouterr().err
