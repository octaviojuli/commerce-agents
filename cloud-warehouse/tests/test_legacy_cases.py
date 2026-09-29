import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, legacy_cases
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_api import PASSWORD


def bundle(tenant, **updates):
    now = datetime.now(UTC)
    data = dict(
        source_namespace=uuid4(),
        legacy_session_id="legacy-session-1",
        legacy_user_id="ACME advisor",
        organization_id=tenant.buyer.organization_id,
        user_id=tenant.buyer.user_id,
        connection_id=tenant.connection_id,
        source_hash="a" * 64,
        created_at=now,
        updated_at=now,
        messages=[{"role": "user", "text": "ACME 历史行程"}],
        plans=[],
    )
    return legacy_cases.Bundle(**{**data, **updates})


def test_cli_defaults_to_preview_and_redacts_invalid_input(
    database, tenant, tmp_path, monkeypatch, capsys
):
    from cloud_warehouse.cli import main

    admin, runtime = database
    path = tmp_path / "case.json"
    path.write_text(bundle(tenant).model_dump_json())
    path.chmod(0o600)
    monkeypatch.setenv("WAREHOUSE_ADMIN_URL", admin.url.render_as_string(hide_password=False))
    argv = [
        "warehouse",
        "import-legacy-case",
        str(path),
        "--target-database",
        admin.url.database,
        "--note",
        "ACME ownership verified",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    main()
    assert not json.loads(capsys.readouterr().out)["applied"]
    assert legacy_cases.list_page(runtime, tenant.buyer)["items"] == []
    monkeypatch.setattr(sys, "argv", [*argv, "--apply"])
    main()
    assert json.loads(capsys.readouterr().out)["applied"]
    assert len(legacy_cases.list_page(runtime, tenant.buyer)["items"]) == 1
    path.write_text('{"password":"private-marker-do-not-print"}')
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "private-marker" not in output.out + output.err
    assert "历史导入失败" in output.err


def import_case(admin, body, **kwargs):
    return legacy_cases.import_case(
        admin,
        body,
        expected_database=admin.url.database,
        note="ACME fixture ownership verified",
        **kwargs,
    )


def test_dry_run_idempotence_and_immutable_ownership(database, tenant):
    admin, runtime = database
    body = bundle(tenant)
    assert import_case(admin, body)["id"] is None
    assert legacy_cases.list_page(runtime, tenant.buyer)["items"] == []
    result = import_case(admin, body, apply=True)
    assert import_case(admin, body, apply=True)["id"] == result["id"]
    assert import_case(admin, body)["already_imported"]
    stored = legacy_cases.get(runtime, tenant.buyer, UUID(result["id"]))
    assert stored["body"]["messages"] == [{"role": "user", "text": "ACME 历史行程"}]
    assert (
        stored["historical_only"]
        and not stored["resumable"]
        and not stored["public_shares_enabled"]
    )
    with pytest.raises(Conflict):
        import_case(admin, body.model_copy(update={"source_hash": "b" * 64}), apply=True)
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM agent_conversation WHERE user_id=:id"),
                {"id": tenant.buyer.user_id},
            )
            == 0
        )
        row = conn.execute(
            text(
                "SELECT imported_by_database_role,import_note FROM legacy_case_archive WHERE id=:id"
            ),
            {"id": UUID(result["id"])},
        ).one()
        assert row.imported_by_database_role == admin.url.username and row.import_note


def test_offline_admin_target_and_current_authorization_required(database, tenant):
    admin, runtime = database
    body = bundle(tenant)
    with pytest.raises(Forbidden):
        import_case(runtime, body, apply=True)
    with pytest.raises(ValueError, match="目标数据库"):
        legacy_cases.import_case(admin, body, expected_database="wrong", note="ACME", apply=True)
    for actor in (tenant.worker, tenant.supplier, Principal(uuid4(), tenant.buyer.organization_id)):
        with pytest.raises((Forbidden, DBAPIError)):
            import_case(
                admin,
                body.model_copy(
                    update={"user_id": actor.user_id, "organization_id": actor.organization_id}
                ),
                apply=True,
            )
    with pytest.raises(Forbidden):
        import_case(admin, body.model_copy(update={"connection_id": uuid4()}), apply=True)


def test_rls_owner_only_and_runtime_cannot_modify_archive(database, tenant):
    admin, runtime = database
    body = bundle(tenant)
    identifier = UUID(import_case(admin, body, apply=True)["id"])
    other = auth.create_user(
        admin,
        f"{uuid4()}@acme.example",
        PASSWORD,
        {tenant.buyer.organization_id: ["buyer_admin"]},
    )
    stranger = Principal(other, tenant.buyer.organization_id)
    for actor in (
        stranger,
        tenant.supplier,
        Principal(tenant.buyer.user_id, tenant.supplier.organization_id),
    ):
        with pytest.raises(Forbidden):
            legacy_cases.get(runtime, actor, identifier)
    with transaction(runtime, stranger) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM legacy_case_archive WHERE id=:id"), {"id": identifier}
            )
            == 0
        )
    with pytest.raises(Conflict):
        import_case(admin, body.model_copy(update={"user_id": other}), apply=True)
    for sql in (
        "UPDATE legacy_case_archive SET title='bad' WHERE id=:id",
        "DELETE FROM legacy_case_archive WHERE id=:id",
    ):
        with pytest.raises(DBAPIError), transaction(runtime, tenant.buyer) as conn:
            conn.execute(text(sql), {"id": identifier})
    assert (
        legacy_cases.get(runtime, tenant.buyer, identifier)["body"]["legacy_session_id"]
        == body.legacy_session_id
    )


def test_grant_revision_change_hides_archive_even_after_reenable(database, tenant):
    admin, runtime = database
    identifier = UUID(import_case(admin, bundle(tenant), apply=True)["id"])
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false,version=version+1 WHERE id=:id"),
            {"id": tenant.grant_id},
        )
    with pytest.raises(Forbidden):
        legacy_cases.get(runtime, tenant.buyer, identifier)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=true,version=version+1 WHERE id=:id"),
            {"id": tenant.grant_id},
        )
    assert legacy_cases.list_page(runtime, tenant.buyer)["items"] == []


def test_pagination_and_namespaced_duplicate_session(database, tenant):
    admin, runtime = database
    a, b = bundle(tenant), bundle(tenant)
    first = import_case(admin, a, apply=True)
    second = import_case(admin, b, apply=True)
    assert first["id"] != second["id"]
    page = legacy_cases.list_page(runtime, tenant.buyer, limit=1)
    more = legacy_cases.list_page(runtime, tenant.buyer, limit=1, before=UUID(page["next_cursor"]))
    assert {str(r["id"]) for r in page["items"] + more["items"]} == {first["id"], second["id"]}
    assert more["next_cursor"] is None
    with pytest.raises(Forbidden):
        legacy_cases.list_page(runtime, tenant.buyer, before=uuid4())


def test_concurrent_import_has_one_durable_case(database, tenant):
    admin, runtime = database
    body = bundle(tenant)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_case(admin, body, apply=True), range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert sorted(r["already_imported"] for r in results) == [False, True]
    assert len(legacy_cases.list_page(runtime, tenant.buyer)["items"]) == 1


def test_original_sqlite_to_warehouse_retains_plan_versions_without_live_state(
    database, tenant, tmp_path
):
    from tour.api.tests.test_warehouse_legacy import fixture
    from tour.api.warehouse_legacy import export_case

    admin, runtime = database
    path, _, mapping = fixture(tmp_path)
    mapping.update(
        organization_id=tenant.buyer.organization_id,
        user_id=tenant.buyer.user_id,
        connection_id=tenant.connection_id,
    )
    body = export_case(path, **mapping)
    result = import_case(admin, body, apply=True)
    stored = legacy_cases.get(runtime, tenant.buyer, UUID(result["id"]))
    assert stored["body"] == body.model_dump(mode="json")
    assert result["versions"] == 3 and result["plans"] == 1
    assert [v["parent_version"] for v in stored["body"]["plans"][0]["versions"]] == [None, 1, 1]
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE actor_id=:user"),
                {"user": tenant.buyer.user_id},
            )
            == 0
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM agent_conversation WHERE user_id=:user"),
                {"user": tenant.buyer.user_id},
            )
            == 0
        )


def test_http_archive_read_is_authenticated_private_and_has_no_import_route(
    database, tenant, authentication
):
    admin, runtime = database
    user = auth.create_user(
        admin, f"{uuid4()}@acme.example", PASSWORD, {tenant.buyer.organization_id: ["advisor"]}
    )
    body = bundle(tenant, user_id=user)
    identifier = import_case(admin, body, apply=True)["id"]
    with TestClient(create_app(runtime, authentication)) as client:
        path = f"/v1/advisor/legacy-cases/{identifier}"
        assert client.get(path).status_code == 401
        # Use an actual credential lookup rather than constructing an identity token.
        with admin.connect() as conn:
            email = conn.scalar(text("SELECT email FROM warehouse_user WHERE id=:id"), {"id": user})
        token = auth.login(authentication, email, PASSWORD, str(uuid4()))["access_token"]
        client.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Organization-ID": str(tenant.buyer.organization_id),
            }
        )
        response = client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["requires_revalidation"] and not response.json()["resumable"]
        assert (
            client.post("/v1/advisor/legacy-cases", json=body.model_dump(mode="json")).status_code
            == 405
        )
