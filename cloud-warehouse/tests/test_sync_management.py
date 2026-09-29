from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, jobs
from cloud_warehouse import sync_management as management
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_api import PASSWORD
from .test_jobs import due, expire, registry, state
from .test_sync import Connector, batch


def configure(version=1, **kwargs):
    return management.Control(
        version=version,
        action="configure",
        interval_seconds=600,
        max_attempts=3,
        enabled=kwargs.pop("enabled", True),
        note="ACME 调度调整",
        **kwargs,
    )


def act(action, version):
    return management.Control(version=version, action=action, note="ACME 操作说明")


def test_paused_crashed_lease_recovered_without_execution(database, tenant):
    admin, runtime = database
    job = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    claimed = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    command = configure(enabled=False).model_copy(
        update={"interval_seconds": jobs.DEFAULT_INTERVAL_SECONDS, "max_attempts": 5}
    )
    management.control(runtime, tenant.supplier, job, command)
    expire(admin, job)
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    row = state(runtime, tenant)
    assert row["status"] == "waiting" and row["lease_id"] is None
    assert row["attempts"] == 1 and row["last_error"] == "INTERRUPTED"
    with pytest.raises(jobs.LeaseLost):
        jobs.heartbeat(runtime, tenant.worker, claimed)


def test_controls_audited_scoped_versioned_and_restart_preserves_config(database, tenant):
    admin, runtime = database
    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    for actor in (tenant.buyer, tenant.worker):
        with pytest.raises(Forbidden):
            management.control(runtime, actor, job_id, configure())
    with pytest.raises(Forbidden):
        management.control(runtime, tenant.supplier, uuid4(), configure())
    with transaction(runtime, tenant.supplier) as conn:
        affected = conn.execute(
            text("UPDATE sync_job SET worker_id=:user WHERE id=:id"),
            {"user": tenant.supplier.user_id, "id": job_id},
        )
        assert affected.rowcount == 0
    assert state(runtime, tenant)["worker_id"] == tenant.worker.user_id
    before = state(runtime, tenant)
    result = management.control(runtime, tenant.supplier, job_id, configure(enabled=False))
    assert set(result) == {"id", "config_version", "status", "enabled"}
    assert result["config_version"] == 2 and not result["enabled"]
    with pytest.raises(Conflict, match="配置已变化"):
        management.control(runtime, tenant.supplier, job_id, configure())
    jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    row = state(runtime, tenant)
    assert row["interval_seconds"] == 600 and row["max_attempts"] == 3
    assert not row["enabled"] and row["worker_id"] == before["worker_id"]
    assert row["worker_last_seen_at"] is None
    due(admin, job_id)
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    assert state(runtime, tenant)["worker_last_seen_at"] is not None
    with transaction(runtime, tenant.supplier) as conn:
        audits = (
            conn.execute(text("SELECT * FROM audit_event WHERE resource_id=:id"), {"id": job_id})
            .mappings()
            .all()
        )
    assert len(audits) == 1 and audits[0]["actor_id"] == tenant.supplier.user_id
    assert audits[0]["details"]["before_version"] == 1
    assert audits[0]["details"]["note"] == "ACME 调度调整"


async def test_pause_keeps_running_lease_and_publication_then_stops_claims(database, tenant):
    admin, runtime = database
    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    claimed = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    with pytest.raises(Conflict, match="正在执行"):
        management.control(runtime, tenant.supplier, job_id, configure(enabled=False))
    command = configure(enabled=False).model_copy(
        update={"interval_seconds": jobs.DEFAULT_INTERVAL_SECONDS, "max_attempts": 5}
    )
    management.control(runtime, tenant.supplier, job_id, command)
    assert state(runtime, tenant)["lease_id"] == claimed.lease_id
    jobs.heartbeat(runtime, tenant.worker, claimed)
    await synchronize(
        runtime,
        tenant.worker,
        tenant.connection_id,
        Connector(),
        on_publish=lambda conn, result: jobs.published(conn, claimed, result),
    )
    assert state(runtime, tenant)["completed_count"] == 1
    due(admin, job_id)
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    with pytest.raises(Conflict, match="先启用"):
        management.control(runtime, tenant.supplier, job_id, act("run_now", 2))


async def test_operator_retry_state_limits_and_inactive_source(database, tenant):
    admin, runtime = database
    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    with pytest.raises(Conflict, match="失败任务"):
        management.control(runtime, tenant.supplier, job_id, act("retry", 1))
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE sync_job SET attempts=3,last_error='ACME_FAILURE' WHERE id=:id"),
            {"id": job_id},
        )
    management.control(runtime, tenant.supplier, job_id, configure())
    assert state(runtime, tenant)["status"] == "failed"
    management.control(runtime, tenant.supplier, job_id, act("retry", 2))
    assert state(runtime, tenant)["attempts"] == 0
    with pytest.raises(Conflict, match="配置已变化"):
        management.control(runtime, tenant.supplier, job_id, act("retry", 2))
    assert (await jobs.run_once(runtime, tenant.worker, registry(tenant)))["status"] == "published"
    management.control(runtime, tenant.supplier, job_id, act("run_now", 3))
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_connection SET active=false WHERE id=:id"),
            {"id": tenant.connection_id},
        )
    with pytest.raises(Conflict, match="来源已停用"):
        management.control(runtime, tenant.supplier, job_id, act("run_now", 4))
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    management.control(runtime, tenant.supplier, job_id, configure(4, enabled=False))


def test_history_paging_complete_tied_timestamps_and_scope(database, tenant):
    admin, runtime = database
    stamp = datetime.now(UTC)
    run_ids = [uuid4() for _ in range(125)]
    with admin.begin() as conn:
        conn.execute(
            text("""INSERT INTO sync_run(id,supplier_org_id,connection_id,status,started_at,actor_id)
          VALUES(:id,:org,:source,'published',:started,:actor)"""),
            [
                {
                    "id": id,
                    "org": tenant.supplier.organization_id,
                    "source": tenant.connection_id,
                    "started": stamp,
                    "actor": tenant.worker.user_id,
                }
                for id in run_ids
            ],
        )
        conn.execute(
            text("""INSERT INTO source_issue(id,supplier_org_id,connection_id,sync_run_id,external_id,code)
          VALUES(:id,:org,:source,:run,:external,'UNLINKED_ROUTE')"""),
            [
                {
                    "id": uuid4(),
                    "org": tenant.supplier.organization_id,
                    "source": tenant.connection_id,
                    "run": run_ids[0],
                    "external": str(i),
                }
                for i in range(511)
            ],
        )
    seen, cursor = [], None
    while True:
        page = management.runs(runtime, tenant.supplier, before=cursor)
        seen.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == sorted(run_ids, reverse=True)
    seen, cursor = [], None
    while True:
        page = management.issues(runtime, tenant.supplier, run_ids[0], after=cursor)
        seen.extend(row["external_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 511
    for operation in (
        lambda: management.runs(runtime, tenant.supplier, before=uuid4()),
        lambda: management.runs(runtime, tenant.supplier, connection_id=uuid4()),
        lambda: management.issues(
            runtime, tenant.supplier, run_ids[1], after=page["items"][0]["id"]
        ),
        lambda: management.issues(runtime, tenant.supplier, uuid4()),
    ):
        with pytest.raises(Forbidden):
            operation()
    # A valid admin in another organization cannot use these history anchors.
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE membership SET roles=ARRAY['supplier_admin'] WHERE user_id=:id"),
            {"id": tenant.buyer.user_id},
        )
    with pytest.raises(Forbidden):
        management.runs(runtime, tenant.buyer, before=run_ids[0])
    assert management.runs(runtime, tenant.buyer)["items"] == []
    job = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    with pytest.raises(Forbidden):
        management.jobs(runtime, tenant.buyer, after=job)
    with pytest.raises(Forbidden):
        management.control(runtime, tenant.buyer, job, configure())


def test_sync_controls_http_permissions_validation_and_response(database, authentication, tenant):
    admin, runtime = database
    job = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin, email, PASSWORD, {tenant.supplier.organization_id: ["supplier_admin"]}
    )
    with TestClient(create_app(runtime, authentication)) as client:
        path = f"/v1/sync-jobs/{job}/control"
        assert client.post(path, json=configure().model_dump()).status_code == 401
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        assert client.get("/v1/sync-runs", headers=headers).status_code == 200
        assert client.get("/v1/sync-runs?limit=101", headers=headers).status_code == 422
        response = client.get("/v1/sync-jobs", headers=headers)
        assert response.status_code == 200
        assert "lease_id" not in response.json()["items"][0]
        assert "worker_id" not in response.json()["items"][0]
        assert client.post(path, headers=headers, json=configure().model_dump()).status_code == 200
        assert client.post(path, headers=headers, json=configure().model_dump()).status_code == 409
        assert (
            client.post(
                path, headers=headers, json={**configure(2).model_dump(), "note": " "}
            ).status_code
            == 409
        )
        assert (
            client.post(
                path, headers=headers, json={**configure(2).model_dump(), "interval_seconds": True}
            ).status_code
            == 422
        )
        assert (
            client.post(
                path, headers=headers, json={**configure(2).model_dump(), "worker_id": str(uuid4())}
            ).status_code
            == 422
        )
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:id"), {"id": user}
            )
        assert client.get("/v1/sync-jobs", headers=headers).status_code == 200
        assert client.post(path, headers=headers, json=configure(2).model_dump()).status_code == 403
    with (
        transaction(runtime, Principal(user, tenant.supplier.organization_id)) as conn,
        pytest.raises(DBAPIError),
    ):
        conn.execute(
            text("SELECT warehouse_sync_control(:id,2,'run_now',NULL,NULL,NULL,'ACME')"),
            {"id": job},
        )


@pytest.mark.parametrize("latest_state", ["unlinked", "linked", "not_observed"])
async def test_issue_details_compare_successful_snapshots_without_exposing_raw_data(
    database, tenant, latest_state
):
    from cloud_warehouse.integrations import CatalogBatch, SourceError

    _, runtime = database
    original = batch()
    original.departures[0].update(
        routeId=0,
        token="ACME-PRIVATE-TOKEN",
        settlementPrice="ACME-PRIVATE-PRICE",
        fileUrl="https://acme.example/private?token=ACME-PRIVATE-URL",
    )
    first = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(original))
    issue = management.issues(runtime, tenant.supplier, first["sync_run_id"])["items"][0]
    data = batch()
    if latest_state == "unlinked":
        data.departures[0]["routeId"] = 0
    if latest_state == "not_observed":
        data = CatalogBatch(data.routes, [], date(2027, 1, 1), date(2027, 1, 31))
    second = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))

    class Failed:
        async def read_catalog(self, start=None, end=None):
            raise SourceError("SOURCE_UNAVAILABLE")

    with pytest.raises(SourceError):
        await synchronize(runtime, tenant.worker, tenant.connection_id, Failed())
    detail = management.issue_detail(runtime, tenant.supplier, issue["id"])
    assert detail["original"] == {
        "period_code": "ACME-D2",
        "depart_date": "2026-10-01",
        "return_date": "2026-10-03",
        "route_id": 0,
    }
    latest = detail["latest_successful_scan"]
    assert str(latest["run_id"]) == second["sync_run_id"] and latest["state"] == latest_state
    assert (latest["observation"] is None) is (latest_state == "not_observed")
    assert detail["latest_attempt"]["status"] == "failed"
    assert detail["latest_attempt"]["error_code"] == "SOURCE_UNAVAILABLE"
    assert "ACME-PRIVATE" not in str(detail)
    assert "available_seats" not in str(detail)
    assert management.issues(runtime, tenant.supplier, first["sync_run_id"])["items"] == [issue]


async def test_issue_detail_http_live_roles_and_organization_scope(
    database, authentication, tenant
):
    admin, runtime = database
    data = batch()
    data.departures[0]["routeId"] = 0
    run = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    issue = management.issues(runtime, tenant.supplier, run["sync_run_id"])["items"][0]
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin,
        email,
        PASSWORD,
        {
            tenant.supplier.organization_id: ["supplier_admin"],
            tenant.buyer.organization_id: ["supplier_admin"],
        },
    )
    with TestClient(create_app(runtime, authentication)) as client:
        path = f"/v1/source-issues/{issue['id']}"
        assert client.get(path).status_code == 401
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        for role in ("supplier_admin", "product_editor", "auditor", "sync_worker"):
            with admin.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE membership SET roles=:roles WHERE user_id=:id AND organization_id=:org"
                    ),
                    {"roles": [role], "id": user, "org": tenant.supplier.organization_id},
                )
            response = client.get(path, headers=headers)
            assert (
                response.status_code == 200
                and response.json()["latest_successful_scan"]["state"] == "unlinked"
            )
            assert response.headers["cache-control"] == "no-store"
        assert (
            client.get(
                path, headers={**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
            ).status_code
            == 403
        )
        assert client.get(f"/v1/source-issues/{uuid4()}", headers=headers).status_code == 403
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE membership SET roles=ARRAY['advisor'] WHERE user_id=:id"), {"id": user}
            )
        assert client.get(path, headers=headers).status_code == 403
    with pytest.raises(Forbidden):
        management.issue_detail(runtime, tenant.buyer, issue["id"])
