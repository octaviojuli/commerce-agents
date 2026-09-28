"""Reparsing preserves evidence and publications without reusing an old approval."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, changes, documents
from cloud_warehouse.admin import onboard
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_api import PASSWORD
from .test_documents import parsed, proposal
from .test_documents import source as source


def request(identifier):
    return documents.ReparseRequest(expected_parse_id=identifier, note="ACME 使用新版规则核对原文")


def finish(runtime, tenant, store, *, version="acme-next-parser"):
    def parse(work, body):
        result = parsed(work, body)
        result.source.parser = version
        return result

    return documents.run_once(runtime, tenant.worker, store, parse)


def test_reparse_preserves_old_draft_and_requires_new_review(database, tenant, source):
    _, runtime = database
    store, product, asset, body = source
    first_id = finish(runtime, tenant, store, version="acme-first-parser")
    original = documents.parse_detail(runtime, tenant.supplier, asset, UUID(first_id))
    staged = proposal(runtime, tenant, asset)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    queued = documents.reparse(runtime, tenant.supplier, asset, request(first_id))
    assert queued["parse_generation"] == 2
    assert documents.get(runtime, tenant.supplier, asset)["parse"] is None
    assert documents.parse_detail(runtime, tenant.supplier, asset, UUID(first_id)) == original
    with pytest.raises(Conflict, match="尚未成功解析"):
        changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    second_id = finish(runtime, tenant, store)
    second = documents.get(runtime, tenant.supplier, asset)["parse"]
    assert second["generation"] == 2 and str(second["id"]) == second_id != first_id
    assert second["parser_version"] == "acme-next-parser"
    with pytest.raises(Conflict, match="新解析版本"):
        changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    current = proposal(runtime, tenant, asset)
    changes.approve(runtime, tenant.supplier, UUID(current["id"]), current["payload_hash"])
    result = changes.apply(runtime, tenant.supplier, UUID(current["id"]))
    public = documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    assert "parse_id" not in public
    assert (
        str(documents.published(runtime, tenant.supplier, UUID(result["document_id"]))["parse_id"])
        == second_id
    )
    assert "source" not in public["body"]
    assert (
        documents.published(runtime, tenant.supplier, UUID(result["document_id"]))["body"][
            "source"
        ]["parser"]
        == "acme-next-parser"
    )
    assert documents.download(runtime, tenant.buyer, store, asset)["body"] == body
    assert documents.parse_detail(runtime, tenant.supplier, asset, UUID(first_id)) == original
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM document_asset WHERE product_id=:id"),
                {"id": product["id"]},
            )
            == 1
        )
    for statement in (
        "UPDATE document_parse SET body='{}' WHERE id=:id",
        "DELETE FROM document_parse WHERE id=:id",
    ):
        with pytest.raises(DBAPIError), transaction(runtime, tenant.supplier) as conn:
            conn.execute(text(statement), {"id": UUID(first_id)})


def test_failed_reparse_and_retry_preserve_live_publication(database, tenant, source):
    _, runtime = database
    store, product, asset, _ = source
    first_id = finish(runtime, tenant, store)
    staged = proposal(runtime, tenant, asset)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    result = changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    public_id = UUID(result["document_id"])
    previous = documents.published(runtime, tenant.buyer, public_id)
    current = documents.current(runtime, tenant.buyer, product["id"])
    documents.reparse(runtime, tenant.supplier, asset, request(first_id))

    def fail(*_):
        raise documents.DocumentParseError("DOCUMENT_PARSE_TIMEOUT")

    assert documents.run_once(runtime, tenant.worker, store, fail)["status"] == "failed"
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["parse_generation"] == 2 and detail["parse"] is None
    assert len(documents.parse_history(runtime, tenant.supplier, asset)["items"]) == 1
    assert documents.published(runtime, tenant.buyer, public_id) == previous
    assert documents.current(runtime, tenant.buyer, product["id"]) == current
    documents.retry(runtime, tenant.supplier, asset)
    finish(runtime, tenant, store)
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["parse_generation"] == detail["parse"]["generation"] == 2
    assert detail["attempts"] == 1
    assert documents.published(runtime, tenant.buyer, public_id) == previous
    # Parsing keeps the original product binding; it cannot silently rebase old evidence.
    with pytest.raises(Conflict, match="线路已更新"):
        proposal(runtime, tenant, asset)


def test_concurrent_reparse_queues_once_and_stale_worker_cannot_finish(database, tenant, source):
    _, runtime = database
    store, _, asset, body = source
    first_id = finish(runtime, tenant, store)

    def enqueue(_):
        try:
            return documents.reparse(runtime, tenant.supplier, asset, request(first_id))
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enqueue, range(2)))
    assert sum(result is not None for result in results) == 1
    work = documents.claim(runtime, tenant.worker)
    assert work["parse_generation"] == 2
    with pytest.raises(Conflict, match="租约已失效"):
        documents.finish_parse(
            runtime, tenant.worker, {**work, "parse_generation": 1}, parsed(work, body)
        )
    second_id = documents.finish_parse(runtime, tenant.worker, work, parsed(work, body))
    documents.reparse(runtime, tenant.supplier, asset, request(second_id))
    with pytest.raises(Conflict, match="租约已失效"):
        documents.finish_parse(runtime, tenant.worker, work, parsed(work, body))
    finish(runtime, tenant, store)
    assert [
        row["generation"]
        for row in documents.parse_history(runtime, tenant.supplier, asset)["items"]
    ] == [3, 2, 1]
    with transaction(runtime, tenant.supplier) as conn:
        events = conn.execute(
            text(
                "SELECT details FROM audit_event WHERE action='document.reparse_requested' AND resource_id=:id"
            ),
            {"id": str(asset)},
        ).all()
        assert len(events) == 2


def test_history_is_scoped_paginated_and_read_only(database, authentication, tenant, source):
    admin, runtime = database
    store, _, asset, _ = source
    first_id = finish(runtime, tenant, store)
    documents.reparse(runtime, tenant.supplier, asset, request(first_id))
    second_id = finish(runtime, tenant, store)
    page = documents.parse_history(runtime, tenant.supplier, asset, limit=1)
    assert page["next_cursor"] == second_id
    older = documents.parse_history(
        runtime, tenant.supplier, asset, limit=1, before=UUID(second_id)
    )
    assert str(older["items"][0]["id"]) == first_id and older["next_cursor"] is None
    with pytest.raises(Forbidden):
        documents.parse_history(runtime, tenant.supplier, asset, before=uuid4())
    with pytest.raises(Forbidden):
        documents.parse_detail(runtime, tenant.supplier, asset, uuid4())
    other = onboard(admin, "ACME Other", "ACME Other Buyer", f"{uuid4()}@acme.example")
    other_org = UUID(other["supplier_id"])
    with TestClient(create_app(runtime, authentication, object_store=store)) as client:
        assert client.get(f"/v1/documents/{asset}/parses").status_code == 401
        assert (
            client.post(
                f"/v1/documents/{asset}/reparse", json=request(second_id).model_dump(mode="json")
            ).status_code
            == 401
        )
        for role, read_status, write_status in [
            ("auditor", 200, 403),
            ("sync_worker", 403, 403),
            ("advisor", 403, 403),
            ("product_editor", 200, 202),
        ]:
            email = f"{uuid4()}@acme.example"
            user = auth.create_user(
                admin,
                email,
                PASSWORD,
                {tenant.supplier.organization_id: [role], other_org: ["supplier_admin"]},
            )
            token = client.post(
                "/v1/auth/login", json={"email": email, "password": PASSWORD}
            ).json()["access_token"]
            headers = {
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(tenant.supplier.organization_id),
            }
            assert (
                client.get(f"/v1/documents/{asset}/parses", headers=headers).status_code
                == read_status
            )
            assert (
                client.get(f"/v1/documents/{asset}/parses/{first_id}", headers=headers).status_code
                == read_status
            )
            assert (
                client.post(
                    f"/v1/documents/{asset}/reparse",
                    headers=headers,
                    json=request(second_id).model_dump(mode="json"),
                ).status_code
                == write_status
            )
            for path in (
                f"/v1/documents/{asset}/parses",
                f"/v1/documents/{asset}/parses/{first_id}",
            ):
                assert (
                    client.get(
                        path, headers={**headers, "X-Organization-Id": str(other_org)}
                    ).status_code
                    == 403
                )
            with pytest.raises(Forbidden):
                documents.reparse(runtime, Principal(user, other_org), asset, request(second_id))


def test_upgrade_retains_original_parse_and_publication(database, tenant, source):
    import runpy
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    admin, runtime = database
    store, _, asset, _ = source
    first_id = finish(runtime, tenant, store)
    staged = proposal(runtime, tenant, asset)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    published = changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    migration = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "migrations/versions/0028_document_reparses.py")
    )
    with admin.connect() as conn:
        tx = conn.begin()
        try:
            # Other tests retain newer generations in this session's database.
            # Shadow only these tables with this fixture's rows to recreate the old
            # one-parse schema without deleting anyone else's test evidence.
            for table, row_id in (
                ("document_asset", asset),
                ("document_parse", UUID(first_id)),
                ("document_publication", UUID(published["document_id"])),
            ):
                conn.execute(
                    text(
                        f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP"
                    )
                )
                conn.execute(
                    text(f"INSERT INTO {table} SELECT * FROM public.{table} WHERE id=:id"),
                    {"id": row_id},
                )
            conn.execute(text("ALTER TABLE document_asset DROP COLUMN parse_generation"))
            conn.execute(text("ALTER TABLE document_parse DROP COLUMN generation"))
            conn.execute(
                text(
                    "ALTER TABLE document_parse ADD CONSTRAINT document_parse_asset_id_key UNIQUE(asset_id)"
                )
            )
            asset_before = dict(
                conn.execute(text("SELECT * FROM document_asset WHERE id=:id"), {"id": asset})
                .mappings()
                .one()
            )
            parse_before = dict(
                conn.execute(
                    text("SELECT * FROM document_parse WHERE id=:id"), {"id": UUID(first_id)}
                )
                .mappings()
                .one()
            )
            public_before = dict(
                conn.execute(
                    text("SELECT * FROM document_publication WHERE id=:id"),
                    {"id": UUID(published["document_id"])},
                )
                .mappings()
                .one()
            )
            with Operations.context(MigrationContext.configure(conn)):
                migration["upgrade"]()
            asset_after = dict(
                conn.execute(text("SELECT * FROM document_asset WHERE id=:id"), {"id": asset})
                .mappings()
                .one()
            )
            parse_after = dict(
                conn.execute(
                    text("SELECT * FROM document_parse WHERE id=:id"), {"id": UUID(first_id)}
                )
                .mappings()
                .one()
            )
            assert asset_after.pop("parse_generation") == parse_after.pop("generation") == 1
            assert asset_after == asset_before and parse_after == parse_before
            assert (
                dict(
                    conn.execute(
                        text("SELECT * FROM document_publication WHERE id=:id"),
                        {"id": UUID(published["document_id"])},
                    )
                    .mappings()
                    .one()
                )
                == public_before
            )
        finally:
            tx.rollback()
