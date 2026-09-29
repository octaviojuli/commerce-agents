import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import (
    auth,
    conversations,
    documents,
    imports,
    legacy_cases,
    outbox,
    pricing,
    quote_shares,
    quotes,
    recovery,
)
from cloud_warehouse.admin import grant_auth, grant_runtime, migrate, onboard
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.catalog import list_departures, list_products
from cloud_warehouse.persistence import Forbidden, Principal, engine_for
from tour.api.tests.test_warehouse_legacy import fixture as legacy_fixture
from tour.api.warehouse_legacy import export_case

from .test_api import PASSWORD
from .test_documents import docx
from .test_imports import workbook
from .test_inventory import commit, propose
from .test_quotes import FUTURE, schedule


@contextmanager
def empty_database(admin, name):
    quote = admin.dialect.identifier_preparer.quote
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"CREATE DATABASE {quote(name)}"))
    url = admin.url.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        # This helper creates and owns only random, isolated test databases.
        assert name.startswith("warehouse_recovery_") and name.endswith("_test")
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"DROP DATABASE {quote(name)} WITH (FORCE)"))


async def test_full_bundle_restore_snapshot_inventory_documents_quotes_and_rls(
    database, authentication, tmp_path, monkeypatch
):
    cluster, original_runtime = database
    names = [f"warehouse_recovery_{uuid4().hex}_test" for _ in range(2)]
    with (
        empty_database(cluster, names[0]) as source_url,
        empty_database(cluster, names[1]) as target_url,
    ):
        migrate(source_url)
        admin, target = engine_for(source_url), engine_for(target_url)
        runtime_role, auth_role = original_runtime.url.username, authentication.url.username
        grant_runtime(admin, runtime_role)
        grant_auth(admin, auth_role)
        runtime = engine_for(
            original_runtime.url.set(database=names[0]).render_as_string(hide_password=False)
        )
        source_auth = engine_for(
            authentication.url.set(database=names[0]).render_as_string(hide_password=False)
        )
        restored = engine_for(
            original_runtime.url.set(database=names[1]).render_as_string(hide_password=False)
        )
        try:
            ids = onboard(admin, "ACME 恢复供应商", "ACME 恢复采购方", f"{uuid4()}@acme.example")
            other = onboard(admin, "ACME 隔离供应商", "ACME 隔离采购方", f"{uuid4()}@acme.example")
            email = f"{uuid4()}@acme.example"
            user = auth.create_user(
                admin,
                email,
                PASSWORD,
                {
                    UUID(ids["supplier_id"]): ["supplier_admin"],
                    UUID(ids["buyer_id"]): ["advisor"],
                    UUID(other["supplier_id"]): ["supplier_admin"],
                },
            )
            supplier, buyer = (
                Principal(user, UUID(ids["supplier_id"])),
                Principal(user, UUID(ids["buyer_id"])),
            )
            isolated = Principal(user, UUID(other["supplier_id"]))
            token = auth.login(source_auth, email, PASSWORD, "ACME-recovery")["access_token"]
            with admin.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE supplier_connection SET connector_type='excel',capabilities=jsonb_build_object('inventory_owner','warehouse') WHERE id=:id"
                    ),
                    {"id": UUID(ids["connection_id"])},
                )
            batch = imports.upload(
                runtime,
                supplier,
                UUID(ids["connection_id"]),
                "ACME.xlsx",
                workbook(
                    [
                        [
                            "ACME-R",
                            "ACME 恢复环线",
                            3,
                            "ACME 城市",
                            "ACME-D",
                            FUTURE.isoformat(),
                            (FUTURE + timedelta(days=2)).isoformat(),
                            20,
                            2,
                            1,
                        ]
                    ]
                ),
            )
            commit(runtime, supplier, imports.preview(runtime, supplier, batch["id"]))
            with admin.connect() as conn:
                pool = conn.scalar(text("SELECT id FROM inventory_pool"))
                offer = conn.scalar(text("SELECT id FROM offer"))
            sale = commit(runtime, supplier, propose(runtime, supplier, "sale", pool, 1, 3))
            commit(
                runtime,
                supplier,
                propose(
                    runtime, supplier, "reverse", pool, 2, reverses_id=UUID(sale["movement_id"])
                ),
            )
            contract = pricing.propose(
                runtime,
                supplier,
                pricing.ContractProposal(
                    offer_id=offer,
                    buyer_org_id=buyer.organization_id,
                    expected_version=0,
                    schedule=schedule(),
                    source_ref="ACME 恢复价表",
                    valid_from=datetime.now(UTC) - timedelta(minutes=1),
                    valid_until=datetime.now(UTC) + timedelta(days=1),
                ),
            )
            commit(runtime, supplier, contract)
            quote = await quotes.create(
                runtime,
                buyer,
                quotes.QuoteRequest(
                    offer_id=offer, departure_date=FUTURE, party=quotes.Party(adults=2)
                ),
                "ACME-restore-quote",
            )
            active_share, revoked_share = [
                quote_shares.create(
                    runtime, buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
                )
                for _ in range(2)
            ]
            quote_shares.revoke(runtime, buyer, revoked_share["id"])
            public_before = quote_shares.read(runtime, source_auth, active_share["token"])
            assert public_before is not None
            assert quote_shares.read(runtime, source_auth, revoked_share["token"]) is None
            # The restored stock belongs to Excel; historical ERP cases must retain
            # a separate, explicitly authorized API source instead of changing authority.
            legacy_source = uuid4()
            with admin.begin() as conn:
                conn.execute(
                    text("""INSERT INTO supplier_connection
                        (id,supplier_org_id,name,connector_type,credential_ref,capabilities)
                        VALUES(:id,:supplier,'ACME historical B2B','tour_b2b','none',
                        jsonb_build_object('catalog_read',true,'inventory_owner','source',
                        'order_write',false))"""),
                    {"id": legacy_source, "supplier": supplier.organization_id},
                )
                conn.execute(
                    text("""INSERT INTO distribution_grant
                        (id,supplier_org_id,buyer_org_id,connection_id)
                        VALUES(:id,:supplier,:buyer,:source)"""),
                    {
                        "id": uuid4(),
                        "supplier": supplier.organization_id,
                        "buyer": buyer.organization_id,
                        "source": legacy_source,
                    },
                )
            legacy_path, _, mapping = legacy_fixture(tmp_path)
            mapping.update(
                organization_id=buyer.organization_id,
                user_id=buyer.user_id,
                connection_id=legacy_source,
            )
            legacy_bundle = export_case(legacy_path, **mapping)
            legacy_import = legacy_cases.import_case(
                admin,
                legacy_bundle,
                expected_database=names[0],
                note="ACME recovery fixture ownership verified",
                apply=True,
            )
            archive_id = UUID(legacy_import["id"])
            archive_before = legacy_cases.get(runtime, buyer, archive_id)
            assert legacy_import["plans"] == 1 and legacy_import["versions"] == 3
            conversation = conversations.create(runtime, buyer, "advisor")
            work = conversations.begin(
                runtime, buyer, UUID(conversation["id"]), "ACME-turn", "ACME 查询"
            )
            conversations.finish(runtime, buyer, work, {}, work["messages"], [])
            product = list_products(runtime, supplier)[0]
            store = LocalObjectStore(tmp_path / "source-objects")
            original_document = docx()
            uploaded = documents.upload(
                runtime, supplier, store, product["id"], "ACME.docx", original_document
            )
            worker = Principal(UUID(ids["worker_id"]), supplier.organization_id)
            consumed = outbox.run_once(runtime, worker)
            assert consumed["done"] == 1 and consumed["changed_products"] == 1
            original_pg = recovery._pg

            def write_after_snapshot(tool, url, *args):
                if tool == "pg_dump":
                    with admin.begin() as conn:
                        conn.execute(
                            text(
                                "INSERT INTO organization(id,name,kinds) VALUES(:id,'ACME AFTER SNAPSHOT',ARRAY['supplier'])"
                            ),
                            {"id": uuid4()},
                        )
                original_pg(tool, url, *args)

            monkeypatch.setattr(recovery, "_pg", write_after_snapshot)
            bundle = tmp_path / "bundle"
            manifest = recovery.backup(source_url, store.root, bundle)
            assert manifest.tables["inventory_movement"].size == 3
            assert manifest.tables["quote_snapshot"].size == 1
            assert manifest.tables["quote_share"].size == 2
            assert manifest.tables["legacy_case_archive"].size == 1
            assert manifest.tables["agent_turn"].size == 1
            assert manifest.tables["auth_session"].size == 1
            assert manifest.tables["product_search"].size == 1
            assert manifest.tables["outbox_receipt"].size == consumed["consumed"]
            assert len(manifest.objects) == 1
            assert PASSWORD not in (bundle / "manifest.json").read_text()
            assert recovery.inspect_bundle(bundle) == manifest
            object_path = bundle / "objects" / str(supplier.organization_id) / str(uploaded["id"])
            saved_path = object_path.with_suffix(".saved")
            object_path.rename(saved_path)
            with pytest.raises(FileNotFoundError):
                recovery.restore(
                    target_url,
                    bundle,
                    tmp_path / "missing-objects",
                    expected_database=names[1],
                    runtime_role=runtime_role,
                    auth_role=auth_role,
                )
            assert not (tmp_path / "missing-objects").exists()
            with target.connect() as conn:
                assert (
                    conn.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'"))
                    == 0
                )
            saved_path.rename(object_path)
            result = recovery.restore(
                target_url,
                bundle,
                tmp_path / "restored-objects",
                expected_database=names[1],
                runtime_role=runtime_role,
                auth_role=auth_role,
            )
            assert result["inventory_pools_reconstructed"] == 1 and result["activation_required"]
            with target.connect() as conn:
                assert not conn.scalar(
                    text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                    {"role": runtime_role},
                )
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM organization WHERE name='ACME AFTER SNAPSHOT'")
                    )
                    == 0
                )
            # Explicitly enable only this isolated test target for runtime acceptance.
            with target.begin() as conn:
                for role in (runtime_role, auth_role):
                    conn.execute(
                        text(
                            f"GRANT CONNECT ON DATABASE {names[1]} TO {conn.dialect.identifier_preparer.quote(role)}"
                        )
                    )
            assert len(list_products(restored, supplier)) == 1
            assert list_products(restored, isolated) == []
            assert list_departures(restored, buyer)[0]["available_seats"] == 17
            assert outbox.run_once(restored, worker)["consumed"] == 0
            assert outbox.rebuild(restored, worker) == 0
            assert (
                quotes.get(restored, buyer, UUID(quote["quote_id"]))["quote_id"]
                == quote["quote_id"]
            )
            assert (
                conversations.get(restored, buyer, UUID(conversation["id"]))["turns"][0]["status"]
                == "complete"
            )
            with pytest.raises(Forbidden):
                quotes.get(restored, isolated, UUID(quote["quote_id"]))
            restored_auth = engine_for(
                authentication.url.set(database=names[1]).render_as_string(hide_password=False)
            )
            try:
                assert auth.authenticate(restored_auth, token) == user
                assert (
                    quote_shares.read(restored, restored_auth, active_share["token"])
                    == public_before
                )
                assert quote_shares.read(restored, restored_auth, revoked_share["token"]) is None
                with pytest.raises(Forbidden):
                    quote_shares.list_for_quote(restored, isolated, UUID(quote["quote_id"]))
                assert legacy_cases.get(restored, buyer, archive_id) == archive_before
                recovered_case = legacy_cases.get(restored, buyer, archive_id)
                versions = recovered_case["body"]["plans"][0]["versions"]
                assert [row["parent_version"] for row in versions] == [None, 1, 1]
                assert recovered_case["body"] == legacy_bundle.model_dump(mode="json")
                assert recovered_case["historical_only"]
                assert not recovered_case["resumable"]
                assert not recovered_case["public_shares_enabled"]
                with pytest.raises(Forbidden):
                    legacy_cases.get(restored, isolated, archive_id)
            finally:
                restored_auth.dispose()
            recovered_store = LocalObjectStore(tmp_path / "restored-objects")
            assert (
                recovered_store.read(
                    supplier.organization_id, UUID(uploaded["id"]), manifest.objects[0].sha256
                )
                == original_document
            )
            with pytest.raises(recovery.RecoveryError, match="非空"):
                recovery.restore(
                    target_url,
                    bundle,
                    tmp_path / "another-objects",
                    expected_database=names[1],
                    runtime_role=runtime_role,
                    auth_role=auth_role,
                )
            # A changed database cannot pass checksum verification even when all rows remain.
            with target.begin() as conn:
                conn.execute(text("UPDATE inventory_pool SET sold=sold+1"))
            with pytest.raises(recovery.RecoveryError, match="内容哈希"):
                recovery.verify(target_url, recovered_store.root, manifest)
            with target.connect() as conn, pytest.raises(recovery.RecoveryError, match="流水"):
                recovery._ledger(conn)
            archive = bundle / "database.dump"
            intact = archive.read_bytes()
            archive.write_bytes(intact + b"ACME corruption")
            with pytest.raises(recovery.RecoveryError, match="数据库备份长度或哈希"):
                recovery.inspect_bundle(bundle)
            archive.write_bytes(intact)
            file = (
                bundle
                / "objects"
                / str(manifest.objects[0].organization)
                / str(manifest.objects[0].id)
            )
            file.write_bytes(b"corrupt")
            with pytest.raises(ValueError, match="完整性"):
                recovery.inspect_bundle(bundle)
            with admin.begin() as conn:
                conn.execute(text("UPDATE inventory_pool SET sold=sold+1"))
            with pytest.raises(recovery.RecoveryError, match="流水"):
                recovery.backup(source_url, store.root, tmp_path / "inconsistent-bundle")
            assert not (tmp_path / "inconsistent-bundle").exists()
            assert not list(tmp_path.glob(".partial-*"))
        finally:
            restored.dispose()
            source_auth.dispose()
            runtime.dispose()
            target.dispose()
            admin.dispose()


def test_backup_permissions_and_pg_failures_do_not_print_secrets(tmp_path, monkeypatch):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(recovery.RecoveryError, match="私有"):
        recovery._private_directory(link)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)  # The local integration runner intentionally uses umask 077.
    with pytest.raises(recovery.RecoveryError, match="私有"):
        recovery._private_directory(public)
    secret = "not-a-real-ACME-password"
    seen = {}

    def run(args, **kwargs):
        from types import SimpleNamespace

        seen.update(args=args, **kwargs)
        return SimpleNamespace(returncode=1, stderr=b"ACME sensitive source record")

    monkeypatch.setattr(recovery.shutil, "which", lambda _: "/ACME/pg_dump")
    monkeypatch.setattr(recovery.subprocess, "run", run)
    with pytest.raises(recovery.RecoveryError) as error:
        recovery._pg(
            "pg_dump", f"postgresql+psycopg://acme:{secret}@localhost/acme", "--format=custom"
        )
    assert secret not in str(seen["args"]) and seen["env"]["PGPASSWORD"] == secret
    assert secret not in str(error.value) and "sensitive" not in str(error.value)


def test_existing_backup_is_never_overwritten(tmp_path):
    objects = tmp_path / "objects"
    objects.mkdir(mode=0o700)
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    with pytest.raises(recovery.RecoveryError, match="禁止覆盖"):
        recovery.backup("postgresql+psycopg://unused/unused", objects, bundle)


def test_invalid_manifest_rejected(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    recovery._write_json(bundle / "manifest.json", {"format": 2})
    with pytest.raises(recovery.RecoveryError, match="清单格式"):
        recovery.inspect_bundle(bundle)
    data = json.loads((bundle / "manifest.json").read_text())
    assert data == {"format": 2}
