from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from cloud_warehouse import auth, changes, inventory, jobs, operations, outbox
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.persistence import Forbidden, transaction

from .test_api import PASSWORD
from .test_inventory import commit, propose
from .test_inventory import stock as stock
from .test_sync import Connector


def codes(report):
    return {row["code"] for row in report["issues"]}


@pytest.mark.parametrize(
    "interval,duration,risk", [(180, 30, False), (300, 30, True), (180, 70, True), (900, 0, True)]
)
async def test_freshness_risk_counts_both_scans_and_respects_pause(
    database, tenant, interval, duration, risk
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    identifier = jobs.schedule(
        runtime, tenant.worker, tenant.connection_id, interval_seconds=interval
    )
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE sync_run SET started_at=completed_at-make_interval(secs=>:duration) WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id, "duration": duration},
        )
    report = operations.snapshot(runtime, tenant.supplier)
    source = report["sources"][0]
    assert ("SYNC_FRESHNESS_GAP_RISK" in codes(report)) is risk
    assert source["last_scan_seconds"] == duration
    assert source["estimated_refresh_headroom_seconds"] == 300 - interval - 2 * duration - 5
    assert source["inventory_freshness_seconds"] == 300
    # A warning does not silently reschedule a supplier or renew a snapshot.
    with admin.begin() as conn:
        assert (
            conn.scalar(
                text("SELECT interval_seconds FROM sync_job WHERE id=:id"), {"id": identifier}
            )
            == interval
        )
        conn.execute(text("UPDATE sync_job SET enabled=false WHERE id=:id"), {"id": identifier})
    assert "SYNC_FRESHNESS_GAP_RISK" not in codes(operations.snapshot(runtime, tenant.supplier))


async def test_fresh_scheduled_source_then_stale_and_auth_failure(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    report = operations.snapshot(runtime, tenant.supplier)
    assert "SYNC_NOT_SCHEDULED" in codes(report)
    assert "SEARCH_WORKER_STALE" in codes(report)
    assert report["not_monitored"]
    identifier = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE sync_job SET worker_last_seen_at=now() WHERE id=:id"), {"id": identifier}
        )
    outbox.run_once(runtime, tenant.worker)
    report = operations.snapshot(runtime, tenant.worker)
    assert report["status"] == "ok" and report["sources"][0]["successes_24h"] == 1
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE sync_job SET status='failed',last_error='SOURCE_AUTH_FAILED',worker_last_seen_at=now()-interval '2 minutes' WHERE id=:id"
            ),
            {"id": identifier},
        )
        conn.execute(
            text(
                "UPDATE sync_run SET completed_at=now()-interval '1 hour' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
        conn.execute(
            text(
                "UPDATE search_worker_state SET heartbeat_at=now()-interval '1 minute' WHERE organization_id=:id"
            ),
            {"id": tenant.supplier.organization_id},
        )
        conn.execute(
            text(
                "UPDATE departure SET availability_expires_at=now()-interval '1 minute' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    report = operations.snapshot(runtime, tenant.supplier)
    assert report["status"] == "critical"
    assert {
        "SOURCE_AUTH_FAILURE",
        "SYNC_WORKER_STALE",
        "SYNC_JOB_FAILED",
        "SOURCE_SYNC_STALE",
        "SEARCH_WORKER_STALE",
        "INVENTORY_SNAPSHOTS_EXPIRED",
    } <= codes(report)
    assert report["inventory"]["expired"] == 1
    with admin.begin() as conn:
        conn.execute(text("UPDATE sync_job SET enabled=false WHERE id=:id"), {"id": identifier})
    paused = codes(operations.snapshot(runtime, tenant.supplier))
    assert "SYNC_PAUSED" in paused
    assert not {"SYNC_WORKER_STALE", "SYNC_JOB_FAILED", "SOURCE_SYNC_STALE"} & paused


async def test_disabled_source_and_past_departures_are_not_stale_inventory(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_connection SET active=false WHERE id=:id"),
            {"id": tenant.connection_id},
        )
    report = operations.snapshot(runtime, tenant.supplier)
    assert report["sources"][0]["monitored"] is False
    assert not {"SYNC_NOT_SCHEDULED", "SOURCE_SYNC_STALE"} & codes(report)
    assert report["inventory"]["departures"] == 0
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_connection SET active=true WHERE id=:id"),
            {"id": tenant.connection_id},
        )
        conn.execute(
            text(
                "UPDATE departure SET depart_date=current_date-3,return_date=current_date-1,availability_expires_at=now()-interval '1 minute' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    assert operations.snapshot(runtime, tenant.supplier)["inventory"]["departures"] == 0


def test_stock_rejections_are_observed_without_business_writes(database, tenant, stock):
    _, runtime = database
    opening = commit(
        runtime, tenant.supplier, propose(runtime, tenant.supplier, "open", stock, 0, 2)
    )
    pool = UUID(opening["pool_id"])
    insufficient = propose(runtime, tenant.supplier, "sale", pool, 1, 3)
    with pytest.raises(changes.Conflict, match="库存不足"):
        commit(runtime, tenant.supplier, insufficient)
    assert "INVENTORY_LIMIT_REJECTED" in codes(operations.snapshot(runtime, tenant.supplier))
    first = propose(runtime, tenant.supplier, "sale", pool, 1, 1, business_key="ACME-sold-once")
    commit(runtime, tenant.supplier, first)
    assert changes.apply(runtime, tenant.supplier, UUID(first["id"]))["available"] == 1
    duplicate = propose(runtime, tenant.supplier, "sale", pool, 2, 1, business_key="ACME-sold-once")
    with pytest.raises(changes.Conflict, match="已经登记"):
        commit(runtime, tenant.supplier, duplicate)
    report = operations.snapshot(runtime, tenant.supplier)
    assert "DUPLICATE_STOCK_REFERENCE" in codes(report)
    assert sum(row["count"] for row in report["apply_failures_24h"]) == 2
    assert report["ledger"] == {"pools": 1, "mismatches": 0}
    with transaction(runtime, tenant.supplier) as conn:
        assert conn.scalar(text("SELECT sold FROM inventory_pool WHERE id=:id"), {"id": pool}) == 1
        assert (
            conn.scalar(
                text("SELECT count(*) FROM inventory_movement WHERE pool_id=:id"), {"id": pool}
            )
            == 2
        )
        failed = (
            conn.execute(
                text(
                    "SELECT details FROM audit_event WHERE action='change.apply_failed' ORDER BY occurred_at"
                )
            )
            .scalars()
            .all()
        )
        assert all(set(row) == {"code", "change_kind"} for row in failed)


def test_database_apply_error_is_sanitized_and_original_error_preserved(
    database, tenant, stock, monkeypatch
):
    _, runtime = database
    proposal = propose(runtime, tenant.supplier, "open", stock, 0, 2)

    def fail(conn, *args):
        conn.execute(text("SELECT CAST('ACME-private-operation-data' AS integer)"))

    monkeypatch.setattr(inventory, "apply_command", fail)
    with pytest.raises(DBAPIError):
        commit(runtime, tenant.supplier, proposal)
    report = operations.snapshot(runtime, tenant.supplier)
    assert "APPLY_DATABASE_ERROR" in codes(report)
    assert "ACME-private-operation-data" not in str(report)
    assert report["ledger"]["pools"] == 0


def test_ledger_drift_detected_without_automatic_repair(database, tenant, stock):
    admin, runtime = database
    opening = commit(
        runtime, tenant.supplier, propose(runtime, tenant.supplier, "open", stock, 0, 5)
    )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE inventory_pool SET sold=sold+1 WHERE id=:id"),
            {"id": UUID(opening["pool_id"])},
        )
    report = operations.snapshot(runtime, tenant.supplier)
    assert report["ledger"]["mismatches"] == 1 and "INVENTORY_LEDGER_MISMATCH" in codes(report)
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT sold FROM inventory_pool WHERE id=:id"),
                {"id": UUID(opening["pool_id"])},
            )
            == 1
        )


async def test_operations_api_role_and_organization_isolation(database, authentication, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with pytest.raises(Forbidden):
        operations.snapshot(runtime, tenant.buyer)
    # Foreign resources cannot create operator error observations in this organization.
    operations.record_change_failure(runtime, tenant.supplier, uuid4(), "APPLY_DATABASE_ERROR")
    assert operations.snapshot(runtime, tenant.supplier)["apply_failures_24h"] == []
    with TestClient(create_app(runtime, authentication)) as client:
        assert client.get("/v1/operations").status_code == 401
        for role, status in (("auditor", 200), ("product_editor", 403), ("advisor", 403)):
            email = f"{uuid4()}@acme.example"
            auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: [role]})
            token = client.post(
                "/v1/auth/login", json={"email": email, "password": PASSWORD}
            ).json()["access_token"]
            headers = {
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(tenant.supplier.organization_id),
            }
            response = client.get("/v1/operations", headers=headers)
            assert response.status_code == status
            headers["X-Organization-Id"] = str(tenant.buyer.organization_id)
            assert client.get("/v1/operations", headers=headers).status_code == 403


def test_database_api_error_never_exposes_exception_or_promises_rollback(
    database, authentication, tenant, monkeypatch
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: ["supplier_admin"]})

    def fail(*args):
        raise SQLAlchemyError("ACME-secret-driver-payload")

    monkeypatch.setattr(changes, "apply", fail)
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        response = client.post(
            f"/v1/changes/{uuid4()}/apply",
            headers={
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(tenant.supplier.organization_id),
            },
        )
        assert response.status_code == 503
        assert response.json()["code"] == "DATABASE_OPERATION_UNCONFIRMED"
        assert "ACME-secret-driver-payload" not in response.text
        assert "先查询原变更状态" in response.json()["message"]
