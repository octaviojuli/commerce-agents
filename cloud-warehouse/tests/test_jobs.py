import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import jobs
from cloud_warehouse.catalog import list_products, synchronize
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Forbidden, transaction

from .test_sync import Connector, batch


def registry(tenant, connector=None):
    @asynccontextmanager
    async def factory():
        yield connector or Connector()

    return {tenant.connection_id: factory}


def state(runtime, tenant):
    with transaction(runtime, tenant.worker) as conn:
        return dict(
            conn.execute(
                text("SELECT * FROM sync_job WHERE connection_id=:id"), {"id": tenant.connection_id}
            )
            .mappings()
            .one()
        )


def expire(admin, job_id):
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE sync_job SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": job_id},
        )


def due(admin, job_id):
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE sync_job SET next_run_at=now()-interval '1 second' WHERE id=:id"),
            {"id": job_id},
        )


def test_new_schedule_leaves_freshness_margin_without_overwriting_existing(database, tenant):
    _, runtime = database
    identifier = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    assert state(runtime, tenant)["interval_seconds"] == 180
    assert (
        jobs.schedule(runtime, tenant.worker, tenant.connection_id, interval_seconds=600)
        == identifier
    )
    assert state(runtime, tenant)["interval_seconds"] == 180


async def test_worker_publishes_and_acknowledges_in_same_transaction(database, tenant):
    _, runtime = database
    result = await jobs.work(runtime, tenant.worker, registry(tenant), once=True)
    assert result["status"] == "published"
    row = state(runtime, tenant)
    assert row["completed_count"] == 1 and row["attempts"] == 0
    assert row["status"] == "waiting" and row["lease_id"] is None
    assert str(row["last_run_id"]) == result["sync_run_id"]
    assert await jobs.work(runtime, tenant.worker, registry(tenant), once=True) is None
    assert len(list_products(runtime, tenant.buyer)) == 1
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT count(*) FROM outbox_event")) == 1


async def test_stale_owner_cannot_publish_or_ack_new_owner(database, tenant):
    admin, runtime = database
    jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    old = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    expire(admin, old.id)
    new = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    assert new.lease_id != old.lease_id and new.attempts == 2
    with pytest.raises(jobs.LeaseLost):
        jobs.heartbeat(runtime, tenant.worker, old)
    with pytest.raises(SourceError, match="JOB_LEASE_LOST"):
        await synchronize(
            runtime,
            tenant.worker,
            tenant.connection_id,
            Connector(),
            on_publish=lambda conn, result: jobs.published(conn, old, result),
        )
    assert list_products(runtime, tenant.buyer) == []
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT count(*) FROM outbox_event")) == 0
        assert conn.scalar(text("SELECT count(*) FROM source_snapshot")) == 0
    jobs.failed(runtime, tenant.worker, old, "OLD_FAILURE")
    assert state(runtime, tenant)["lease_id"] == new.lease_id
    await synchronize(
        runtime,
        tenant.worker,
        tenant.connection_id,
        Connector(),
        on_publish=lambda conn, result: jobs.published(conn, new, result),
    )
    assert state(runtime, tenant)["completed_count"] == 1


async def test_retry_backoff_limit_restart_and_explicit_retry(database, tenant):
    admin, runtime = database
    malformed = batch()
    malformed.departures[0]["routeId"] = 999
    config = registry(tenant, Connector(malformed))
    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id, max_attempts=2)
    first = await jobs.run_once(runtime, tenant.worker, config)
    assert first["error_code"] == "SOURCE_REFERENCE_MISSING"
    row = state(runtime, tenant)
    assert row["status"] == "waiting" and row["next_run_at"] > row["updated_at"]
    assert await jobs.run_once(runtime, tenant.worker, config) is None
    due(admin, job_id)
    await jobs.run_once(runtime, tenant.worker, config)
    assert state(runtime, tenant)["status"] == "failed"
    jobs.schedule(runtime, tenant.worker, tenant.connection_id, max_attempts=2)
    assert await jobs.run_once(runtime, tenant.worker, registry(tenant)) is None
    assert jobs.retry(runtime, tenant.worker, tenant.connection_id)
    assert (await jobs.run_once(runtime, tenant.worker, registry(tenant)))["status"] == "published"


def test_final_attempt_crash_is_terminal_and_scope_is_enforced(database, tenant):
    admin, runtime = database
    for actor in (tenant.buyer, tenant.supplier):
        with pytest.raises(Forbidden):
            jobs.schedule(runtime, actor, tenant.connection_id)
    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id, max_attempts=1)
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM sync_job")) == 0
    with transaction(runtime, tenant.supplier) as conn, pytest.raises(DBAPIError) as error:
        conn.execute(text("INSERT INTO sync_job SELECT * FROM sync_job"))
    assert error.value.orig.sqlstate == "42501"
    claimed = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    expire(admin, job_id)
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    assert state(runtime, tenant)["last_error"] == "INTERRUPTED"
    assert state(runtime, tenant)["status"] == "failed"
    with pytest.raises(jobs.LeaseLost):
        jobs.heartbeat(runtime, tenant.worker, claimed)


async def test_worker_heartbeat_timeout_and_cancel(database, tenant, monkeypatch):
    admin, runtime = database
    entered = asyncio.Event()

    class Slow:
        async def read_catalog(self, start=None, end=None):
            entered.set()
            await asyncio.Event().wait()

    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", 0.01)
    actual = jobs.heartbeat
    pulses = []

    def heartbeat(*args):
        actual(*args)
        pulses.append(True)

    monkeypatch.setattr(jobs, "heartbeat", heartbeat)
    monkeypatch.setattr(jobs, "SCAN_TIMEOUT_SECONDS", 0.1)
    result = await jobs.run_once(runtime, tenant.worker, registry(tenant, Slow()))
    assert result["error_code"] == "SCAN_TIMEOUT" and pulses
    assert state(runtime, tenant)["status"] == "waiting"
    due(admin, job_id)
    entered.clear()
    task = asyncio.create_task(jobs.run_once(runtime, tenant.worker, registry(tenant, Slow())))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state(runtime, tenant)["last_error"] == "INTERRUPTED"
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT count(*) FROM sync_run WHERE status='running'")) == 0
    due(admin, job_id)
    assert (await jobs.run_once(runtime, tenant.worker, registry(tenant)))["status"] == "published"


async def test_source_lock_contention_does_not_exhaust_retry_budget(database, tenant):
    _, runtime = database
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow:
        async def read_catalog(self, start=None, end=None):
            entered.set()
            await release.wait()
            return batch()

    task = asyncio.create_task(synchronize(runtime, tenant.worker, tenant.connection_id, Slow()))
    await entered.wait()
    try:
        jobs.schedule(runtime, tenant.worker, tenant.connection_id, max_attempts=1)
        result = await jobs.run_once(runtime, tenant.worker, registry(tenant))
        assert result["error_code"] == "SYNC_BUSY"
        assert state(runtime, tenant)["status"] == "waiting"
        assert state(runtime, tenant)["attempts"] == 0
    finally:
        release.set()
        await task


@pytest.mark.parametrize("retryable", [True, False])
async def test_supplier_retry_policy_survives_catalog_and_restart(database, tenant, retryable):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    before = list_products(runtime, tenant.buyer)
    code = "SOURCE_RATE_LIMITED" if retryable else "SOURCE_AUTH_FAILED"

    class Unavailable:
        async def read_catalog(self, start=None, end=None):
            raise SourceError(
                code, retryable=retryable, retry_after_seconds=3600 if retryable else 0
            )

    result = await jobs.work(runtime, tenant.worker, registry(tenant, Unavailable()), once=True)
    assert result["error_code"] == code
    current = state(runtime, tenant)
    assert current["status"] == ("waiting" if retryable else "failed")
    assert current["attempts"] == 1
    if retryable:
        assert (current["provider_not_before"] - current["updated_at"]).total_seconds() >= 3599
        assert current["next_run_at"] >= current["provider_not_before"]
    assert await jobs.work(runtime, tenant.worker, registry(tenant), once=True) is None
    assert list_products(runtime, tenant.buyer) == before
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT error_code FROM sync_run WHERE status='failed'")) == code


async def test_manual_controls_and_direct_sync_cannot_bypass_supplier_cooldown(database, tenant):
    from cloud_warehouse import sync_management

    _, runtime = database
    job_id = jobs.schedule(runtime, tenant.worker, tenant.connection_id, max_attempts=1)
    lease = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    jobs.failed(runtime, tenant.worker, lease, "SOURCE_RATE_LIMITED", retry_after_seconds=3600)
    cooldown = state(runtime, tenant)["provider_not_before"]
    assert jobs.retry(runtime, tenant.worker, tenant.connection_id)
    for action in ["run_now", "configure"]:
        row = state(runtime, tenant)
        command = sync_management.Control(
            version=row["config_version"],
            action=action,
            note="ACME cooldown acceptance",
            interval_seconds=60,
            max_attempts=5,
            enabled=True,
        )
        sync_management.control(runtime, tenant.supplier, job_id, command)
        updated = state(runtime, tenant)
        assert updated["provider_not_before"] == cooldown and updated["next_run_at"] >= cooldown
    assert jobs.claim(runtime, tenant.worker, [tenant.connection_id]) is None
    with pytest.raises(SourceError, match="SOURCE_COOLDOWN_ACTIVE"):
        await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with (
        transaction(runtime, tenant.worker) as conn,
        pytest.raises(DBAPIError),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE sync_job SET status='running',provider_not_before=NULL WHERE id=:id"),
            {"id": job_id},
        )
    listed = sync_management.jobs(runtime, tenant.supplier)["items"][0]
    assert listed["provider_not_before"] == cooldown
    assert list_products(runtime, tenant.buyer) == []


async def test_expired_cooldown_allows_queued_worker_and_success_clears_it(database, tenant):
    from cloud_warehouse import sync_management

    _, runtime = database
    identifier = jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    lease = jobs.claim(runtime, tenant.worker, [tenant.connection_id])
    jobs.failed(runtime, tenant.worker, lease, "SOURCE_UNAVAILABLE", retry_after_seconds=1)
    sync_management.control(
        runtime,
        tenant.supplier,
        identifier,
        sync_management.Control(version=1, action="run_now", note="ACME queue after cooldown"),
    )
    assert await jobs.run_once(runtime, tenant.worker, registry(tenant)) is None
    await asyncio.sleep(1.1)
    result = await jobs.run_once(runtime, tenant.worker, registry(tenant))
    assert result["status"] == "published"
    row = state(runtime, tenant)
    assert row["provider_not_before"] is None and row["last_error"] is None
    assert row["attempts"] == 0 and row["completed_count"] == 1


def test_source_http_errors_expose_safe_retry_metadata(database, authentication):
    from fastapi.testclient import TestClient

    from cloud_warehouse.api import create_app

    _, runtime = database
    app = create_app(runtime, authentication)

    @app.get("/acme-retry-test")
    def temporary():
        raise SourceError("SOURCE_RATE_LIMITED", retry_after_seconds=120)

    @app.get("/acme-auth-test")
    def permanent():
        raise SourceError("SOURCE_AUTH_FAILED", retryable=False)

    with TestClient(app) as client:
        response = client.get("/acme-retry-test")
        assert response.status_code == 503 and response.headers["retry-after"] == "120"
        assert response.json()["code"] == "SOURCE_RATE_LIMITED" and response.json()["retryable"]
        response = client.get("/acme-auth-test")
        assert response.status_code == 502 and "retry-after" not in response.headers
        assert not response.json()["retryable"]
