from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, changes, imports, jobs, management
from cloud_warehouse.admin import onboard
from cloud_warehouse.api import create_app
from cloud_warehouse.persistence import Principal

from .test_imports import excel_source as excel_source
from .test_imports import workbook


def test_workbench_scope_pagination_and_import_review(
    database, authentication, tenant, excel_source
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin,
        email,
        "ACME-test-password-only",
        {
            tenant.supplier.organization_id: ["supplier_admin"],
            tenant.buyer.organization_id: ["advisor"],
        },
    )
    actor = Principal(user, tenant.supplier.organization_id)
    jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-test-password-only"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(actor.organization_id),
        }
        buyer = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        assert client.get("/v1/sync-jobs").status_code == 401
        assert client.get("/v1/sync-jobs", headers=buyer).status_code == 403
        scheduled = client.get("/v1/sync-jobs", headers=headers).json()["items"]
        assert len(scheduled) == 1 and scheduled[0]["status"] == "waiting"
        assert "lease_id" not in scheduled[0] and "worker_id" not in scheduled[0]
        paths = ["overview", "connections", "partners", "imports", "inventory", "catalog"]
        for path in paths:
            assert client.get("/v1/merchant/" + path, headers=buyer).status_code == 403
        assert client.get("/v1/import-template", headers=buyer).status_code == 403
        template = client.get("/v1/import-template", headers=headers)
        assert template.status_code == 200 and template.content[:2] == b"PK"
        connections = client.get("/v1/merchant/connections", headers=headers).json()["items"]
        assert {row["id"] for row in connections} == {str(tenant.connection_id), str(excel_source)}
        assert all(
            set(row) == {"id", "name", "connector_type", "capabilities", "active"}
            for row in connections
        )
        partners = client.get("/v1/merchant/partners", headers=headers).json()["items"]
        assert len(partners) == 2
        assert all(
            row["buyer_name"] == "ACME Buyer"
            and row["buyer_org_id"] == str(tenant.buyer.organization_id)
            for row in partners
        )
        name = "供应商团期.xlsx"
        content = workbook(
            [
                [
                    f"ACME-R-{i}",
                    f"ACME 线路 {i}",
                    1,
                    "ACME 城市",
                    f"ACME-D-{i}",
                    "2026-10-01",
                    "2026-10-01",
                    10,
                    0,
                    0,
                ]
                for i in range(3)
            ]
        )
        uploaded = client.post(
            "/v1/imports",
            params={"connection_id": str(excel_source)},
            content=content,
            headers={**headers, "X-File-Name": quote(name)},
        )
        assert uploaded.status_code == 201, uploaded.text
        batch = uploaded.json()
        proposal = client.post(f"/v1/imports/{batch['id']}/preview", headers=headers).json()
        review = client.get("/v1/changes", headers=headers).json()["items"]
        assert len(review) == 1 and review[0]["target_label"] == name
        assert review[0]["payload_hash"] == proposal["payload_hash"]
        assert client.get("/v1/merchant/overview", headers=headers).json()["products"] == 0
        changes.approve(runtime, actor, UUID(proposal["id"]), proposal["payload_hash"])
        changes.apply(runtime, actor, UUID(proposal["id"]))
        summary = client.get("/v1/merchant/overview", headers=headers).json()
        assert summary == {
            "products": 3,
            "departures": 3,
            "pending_changes": 0,
            "managed_pools": 3,
            "connections": 2,
        }
        history = client.get("/v1/merchant/imports", headers=headers).json()["items"]
        assert history[0]["file_name"] == name and history[0]["status"] == "published"
        for path in ["catalog", "inventory"]:
            ids, after = [], None
            for _ in range(3):
                result = client.get(
                    "/v1/merchant/" + path,
                    params={"limit": 1, **({"after": after} if after else {})},
                    headers=headers,
                )
                assert result.status_code == 200, result.text
                page = result.json()
                assert len(page["items"]) == 1
                row = page["items"][0]
                ids.append(row["listing_id"] if path == "catalog" else row["id"])
                after = page["next_cursor"]
            assert after is None and len(set(ids)) == 3
        catalog = client.get(
            "/v1/merchant/catalog", params={"query": "线路 2"}, headers=headers
        ).json()
        assert len(catalog["items"]) == 1
        detail = client.get(
            "/v1/merchant/listings/" + catalog["items"][0]["listing_id"], headers=headers
        ).json()
        assert len(detail["variants"]) == 1
        # Losing organization membership immediately revokes the newly added read surface.
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET active=false WHERE user_id=:user AND organization_id=:org"
                ),
                {"user": user, "org": actor.organization_id},
            )
        assert client.get("/v1/merchant/overview", headers=headers).status_code == 403
        assert client.get("/v1/sync-jobs", headers=headers).status_code == 403


def test_history_lists_all_imports_and_old_pending_changes(
    database, authentication, tenant, excel_source
):
    admin, runtime = database
    batches, pending, all_changes = set(), set(), set()
    for index in range(131):
        content = workbook(
            [
                [
                    f"ACME-H{index}",
                    f"ACME 历史 {index}",
                    1,
                    "ACME 城市",
                    f"ACME-HD{index}",
                    "2026-10-01",
                    "2026-10-01",
                    10,
                    0,
                    0,
                ]
            ]
        )
        batch = imports.upload(
            runtime, tenant.supplier, excel_source, f"ACME-{index}.xlsx", content
        )
        proposal = imports.preview(runtime, tenant.supplier, batch["id"])
        batches.add(batch["id"])
        identity = UUID(proposal["id"])
        all_changes.add(identity)
        if index < 31:
            pending.add(identity)
        else:
            changes.discard(runtime, tenant.supplier, identity)
    first = management.changes(runtime, authentication, tenant.supplier, status="staged")
    assert len(first["items"]) == 25
    assert all(
        row["status"] == "staged" and row["target_label"].startswith("ACME-")
        for row in first["items"]
    )
    anchor = UUID(first["next_cursor"])
    # The current page's anchor becomes processed before the next request.
    changes.discard(runtime, tenant.supplier, anchor)
    next_page = management.changes(
        runtime, authentication, tenant.supplier, status="staged", before=anchor
    )
    assert len(next_page["items"]) == 6 and next_page["next_cursor"] is None
    assert {row["id"] for row in first["items"] + next_page["items"]} == pending
    for kind, expected in (("imports", batches), ("changes", all_changes)):
        seen, cursor = [], None
        while True:
            page = (
                management.imports(runtime, tenant.supplier, before=cursor, limit=17)
                if kind == "imports"
                else management.changes(
                    runtime, authentication, tenant.supplier, before=cursor, limit=17
                )
            )
            seen.extend(page["items"])
            if not page["next_cursor"]:
                break
            cursor = UUID(page["next_cursor"])
        assert {row["id"] for row in seen} == expected
        assert len(seen) == 131
        assert [(r["created_at"], r["id"]) for r in seen] == sorted(
            [(r["created_at"], r["id"]) for r in seen], reverse=True
        )
        assert all("file_body" not in row for row in seen)

    second = onboard(admin, "ACME Other", "ACME Other Buyer", f"{uuid4()}@acme.example")
    email = f"{uuid4()}@acme.example"
    auth.create_user(
        admin,
        email,
        "ACME-test-password-only",
        {
            tenant.supplier.organization_id: ["auditor"],
            UUID(second["supplier_id"]): ["auditor"],
            tenant.buyer.organization_id: ["advisor"],
        },
    )
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-test-password-only"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        other = {**headers, "X-Organization-Id": second["supplier_id"]}
        buyer = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        for path, before in (
            ("/v1/changes", anchor),
            ("/v1/merchant/imports", next(iter(batches))),
        ):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=buyer).status_code == 403
            assert client.get(path, headers=other).json() == {"items": [], "next_cursor": None}
            assert (
                client.get(path, params={"before": str(before)}, headers=other).status_code == 403
            )
            assert client.get(path, params={"limit": 101}, headers=headers).status_code == 422
            assert (
                client.get(path, params={"before": str(uuid4())}, headers=headers).status_code
                == 403
            )
        pending_rows = client.get("/v1/changes?status=staged&limit=100", headers=headers).json()[
            "items"
        ]
        assert len(pending_rows) == 30
        assert client.get("/v1/changes?status=unknown", headers=headers).status_code == 422
