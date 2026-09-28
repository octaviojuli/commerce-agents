from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, outbox, search
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.merchant import WarehouseMerchantBackend
from cloud_warehouse.persistence import Forbidden, transaction

from .test_advisor import session
from .test_api import PASSWORD
from .test_merchant import session as merchant_session
from .test_sync import Connector, batch


def count(runtime, actor, table):
    with transaction(runtime, actor) as conn:
        return conn.scalar(text(f"SELECT count(*) FROM {table}"))


async def test_consumer_replay_and_projection_rebuild(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    assert outbox.status(runtime, tenant.supplier)["pending"] == 1
    result = outbox.run_once(runtime, tenant.worker)
    assert result["done"] == result["changed_products"] == 1
    assert outbox.run_once(runtime, tenant.worker)["consumed"] == 0
    assert count(runtime, tenant.worker, "outbox_receipt") == 1
    assert outbox.rebuild(runtime, tenant.worker) == 0
    with admin.begin() as conn:
        conn.execute(
            text("DELETE FROM product_search WHERE supplier_org_id=:id"),
            {"id": tenant.worker.organization_id},
        )
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    assert len(await backend.search_products(session(tenant.buyer), "环线")) == 1
    assert outbox.rebuild(runtime, tenant.worker) == 1
    state = outbox.status(runtime, tenant.supplier)
    assert state["done"] == 1 and state["pending"] == state["failed"] == 0
    assert state["projection"] == {"products": 1, "current": 1, "stale_or_missing": 0}
    assert state["worker"]["heartbeat_at"] and state["worker"]["last_success_at"]


async def test_stale_source_and_display_never_hide_current_search(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    outbox.run_once(runtime, tenant.worker)
    data = batch()
    data.routes[0]["routeName"] = "ACME 北欧新线路"
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    advisor = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = session(tenant.buyer)
    assert await advisor.search_products(context, "环线") == []
    assert len(await advisor.search_products(context, "北欧")) == 1
    # Simulate the already separately tested approved display write.
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_product SET name_override='ACME 100%_!\\线路',display_version=display_version+1 WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    for query in ("100%_!\\", "北欧"):
        assert len(await advisor.search_products(context, query)) == 1
    assert await advisor.search_products(context, "100X") == []
    outbox.run_once(runtime, tenant.worker)
    for query in ("100%_!\\", "北欧"):
        assert len(await advisor.search_products(context, query)) == 1
    merchant = WarehouseMerchantBackend(runtime, tenant.supplier)
    assert len(await merchant.search_listings(merchant_session(tenant.supplier), "北欧")) == 1
    assert (
        len(merchant.catalog_page(merchant_session(tenant.supplier), query="100%_!\\")["items"])
        == 1
    )


@pytest.mark.parametrize("revoke", ["grant", "source", "product"])
async def test_projection_rechecks_live_visibility(database, tenant, revoke):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    outbox.run_once(runtime, tenant.worker)
    assert count(runtime, tenant.buyer, "product_search") == 1
    commands = {
        "grant": "UPDATE distribution_grant SET active=false WHERE connection_id=:id",
        "source": "UPDATE supplier_connection SET active=false WHERE id=:id",
        "product": "UPDATE supplier_product SET status='archived' WHERE connection_id=:id",
    }
    with admin.begin() as conn:
        conn.execute(text(commands[revoke]), {"id": tenant.connection_id})
    assert count(runtime, tenant.buyer, "product_search") == 0
    assert (
        await WarehouseAdvisorBackend(runtime, tenant.buyer).search_products(
            session(tenant.buyer), "ACME"
        )
        == []
    )


async def test_database_failure_rolls_back_effect_and_has_durable_retry_limit(
    database, tenant, monkeypatch
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    real = search.rebuild

    def fail(conn):
        real(conn)
        conn.execute(text("SELECT 1/0"))

    monkeypatch.setattr(search, "rebuild", fail)
    for attempt in range(1, 6):
        result = outbox.run_once(runtime, tenant.worker)
        assert result["failed" if attempt == 5 else "waiting"] == 1
        assert count(runtime, tenant.worker, "product_search") == 0
        assert outbox.run_once(runtime, tenant.worker)["consumed"] == 0
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE outbox_receipt SET next_attempt_at=now() WHERE organization_id=:id"),
                {"id": tenant.worker.organization_id},
            )
    state = outbox.status(runtime, tenant.supplier)
    failure = state["failures"][0]
    assert failure["attempts"] == 5 and failure["last_error"] == "SEARCH_DATABASE_ERROR"
    monkeypatch.setattr(search, "rebuild", real)
    assert outbox.retry(runtime, tenant.worker, failure["event_id"])
    assert outbox.run_once(runtime, tenant.worker)["done"] == 1
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT attempts FROM outbox_receipt")) == 6
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_event WHERE action='outbox.retried'")) == 1
        )


async def test_worker_crash_rolls_back_projection_and_receipt_together(
    database, tenant, monkeypatch
):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    real = outbox._receipt

    def crash(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("ACME simulated worker loss")

    monkeypatch.setattr(outbox, "_receipt", crash)
    with pytest.raises(RuntimeError):
        outbox.run_once(runtime, tenant.worker)
    assert (
        count(runtime, tenant.worker, "product_search")
        == count(runtime, tenant.worker, "outbox_receipt")
        == 0
    )
    monkeypatch.setattr(outbox, "_receipt", real)
    assert outbox.run_once(runtime, tenant.worker)["done"] == 1


async def test_concurrent_consumers_only_apply_once(database, tenant, monkeypatch):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    entered, release = Event(), Event()
    real = search.rebuild

    def held(conn):
        entered.set()
        assert release.wait(10)
        return real(conn)

    monkeypatch.setattr(search, "rebuild", held)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(outbox.run_once, runtime, tenant.worker)
        try:
            assert entered.wait(10)
            assert outbox.run_once(runtime, tenant.worker) == {"busy": True}
        finally:
            release.set()
        assert running.result(timeout=10)["done"] == 1
    assert count(runtime, tenant.worker, "outbox_receipt") == 1


async def test_unrecognized_event_is_visible_not_silently_acknowledged(database, tenant):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with transaction(runtime, tenant.worker) as conn:
        for kind in ("future.unknown", "inventory.applied"):
            conn.execute(
                text(
                    "INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload) VALUES(:id,warehouse_org_id(),:kind,:resource,'{}')"
                ),
                {"id": uuid4(), "kind": kind, "resource": uuid4()},
            )
    result = outbox.run_once(runtime, tenant.worker)
    assert result["done"] == result["ignored"] == result["failed"] == 1
    failure = outbox.status(runtime, tenant.supplier)["failures"][0]
    assert failure["last_error"] == "UNSUPPORTED_EVENT_KIND"


async def test_status_and_writes_are_scoped(database, authentication, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    outbox.run_once(runtime, tenant.worker)
    for actor in (tenant.buyer, tenant.supplier):
        with pytest.raises(Forbidden):
            outbox.run_once(runtime, actor)
        with transaction(runtime, actor) as conn, pytest.raises(DBAPIError):
            conn.execute(
                text("INSERT INTO search_worker_state(organization_id) VALUES(warehouse_org_id())")
            )
    assert count(runtime, tenant.buyer, "outbox_receipt") == 0
    with runtime.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM product_search")) == 0
    assert not outbox.retry(runtime, tenant.worker, uuid4())
    with pytest.raises(Forbidden):
        outbox.status(runtime, tenant.buyer)
    with TestClient(create_app(runtime, authentication)) as client:
        assert client.get("/v1/search-worker").status_code == 401
        for org, role, expected in (
            (tenant.supplier.organization_id, "auditor", 200),
            (tenant.buyer.organization_id, "advisor", 403),
        ):
            email = f"{uuid4()}@acme.example"
            auth.create_user(admin, email, PASSWORD, {org: [role]})
            token = client.post(
                "/v1/auth/login", json={"email": email, "password": PASSWORD}
            ).json()["access_token"]
            response = client.get(
                "/v1/search-worker",
                headers={"Authorization": "Bearer " + token, "X-Organization-Id": str(org)},
            )
            assert response.status_code == expected
