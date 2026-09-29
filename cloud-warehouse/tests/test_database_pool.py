import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout, TooManyRequests
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, database_pool
from cloud_warehouse.api import create_app
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_api import PASSWORD


def until(check):
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline, "Pool did not reach the observed state"
        Event().wait(0.005)


@pytest.fixture
def pools(monkeypatch):
    created = []
    original = database_pool.ConnectionPool

    class ObservedPool(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(database_pool, "ConnectionPool", ObservedPool)
    yield created
    assert all(pool.closed for pool in created), "Every owned driver pool must be closed"


def test_pool_is_lazy_and_scope_resets_after_commit_and_rollback(database, tenant, pools):
    _, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1)
    try:
        assert not pools
        for fail in (False, True):
            try:
                with transaction(engine, tenant.buyer) as conn:
                    assert (
                        conn.scalar(text("SELECT warehouse_org_id()"))
                        == tenant.buyer.organization_id
                    )
                    if fail:
                        raise ValueError("ACME rollback")
            except ValueError:
                assert fail
            with engine.connect() as conn:
                assert conn.scalar(text("SELECT warehouse_org_id()")) is None
                assert conn.scalar(text("SELECT warehouse_user_id()")) is None
                assert conn.scalar(text("SELECT count(*) FROM supplier_product")) == 0
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            assert conn.connection.driver_connection.autocommit
        with engine.connect() as conn:
            assert not conn.connection.driver_connection.autocommit
    finally:
        engine.dispose()


def test_context_entry_is_one_statement_and_does_not_elevate_privileges(
    database, authentication, tenant
):
    admin, runtime = database
    statements = []

    def observed(*args):
        statements.append(args[2])

    event.listen(runtime, "before_cursor_execute", observed)
    try:
        with transaction(runtime, tenant.buyer) as conn:
            assert len(statements) == 1
            assert conn.scalar(text("SELECT warehouse_user_id()")) == tenant.buyer.user_id
            assert conn.scalar(text("SELECT warehouse_org_id()")) == tenant.buyer.organization_id
            assert conn.scalar(text("SELECT current_user")) == runtime.url.username
    finally:
        event.remove(runtime, "before_cursor_execute", observed)
    with admin.connect() as conn:
        assert conn.execute(
            text(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid='public.warehouse_begin_context(uuid,uuid)'::regprocedure"
            )
        ).one() == (False, None)
    with authentication.connect() as conn:
        assert not conn.scalar(
            text(
                "SELECT has_function_privilege(current_user,'public.warehouse_begin_context(uuid,uuid)','EXECUTE')"
            )
        )


def test_denied_context_rolls_back_and_reused_connection_has_no_identity(database, tenant):
    _, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1)
    try:
        for actor in (
            Principal(tenant.buyer.user_id, tenant.supplier.organization_id),
            Principal(uuid4(), tenant.buyer.organization_id),
        ):
            with pytest.raises(Forbidden), transaction(engine, actor):
                pytest.fail("Invalid membership was admitted")
            with engine.connect() as conn:
                assert conn.scalar(text("SELECT warehouse_user_id()")) is None
                assert conn.scalar(text("SELECT warehouse_org_id()")) is None
                assert conn.scalar(text("SELECT count(*) FROM supplier_product")) == 0
        with transaction(engine, tenant.supplier) as conn:
            assert conn.scalar(text("SELECT warehouse_user_id()")) == tenant.supplier.user_id
    finally:
        engine.dispose()


def test_reused_authorization_plans_follow_identity_roles_and_revocation(database, tenant):
    admin, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1)
    pids = set()
    role = text("SELECT warehouse_has_role(ARRAY['advisor'])")
    connections = text("SELECT warehouse_buyer_connections()")
    grant = text("SELECT warehouse_live_grant(:id)")
    try:
        # Warm the same physical connection beyond generic-plan selection, then
        # change both tenant identity and live authorization without reconnecting.
        for _ in range(12):
            for actor in (tenant.buyer, tenant.supplier):
                with transaction(engine, actor) as conn:
                    pids.add(conn.scalar(text("SELECT pg_backend_pid()")))
                    assert conn.scalar(role) == (actor == tenant.buyer)
                    assert conn.scalar(
                        text("SELECT warehouse_has_org(:org)"),
                        {"org": tenant.buyer.organization_id},
                    ) == (actor == tenant.buyer)
                    assert conn.execute(connections).scalars().all() == (
                        [tenant.connection_id] if actor == tenant.buyer else []
                    )
                    assert conn.scalar(grant, {"id": tenant.grant_id})
        with transaction(engine, tenant.buyer) as conn:
            with admin.begin() as control:
                control.execute(
                    text("UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:id"),
                    {"id": tenant.buyer.user_id},
                )
            assert not conn.scalar(role)
            with admin.begin() as control:
                control.execute(
                    text("UPDATE distribution_grant SET active=false WHERE id=:id"),
                    {"id": tenant.grant_id},
                )
            assert conn.execute(connections).all() == []
            assert not conn.scalar(grant, {"id": tenant.grant_id})
        with engine.connect() as conn:
            pids.add(conn.scalar(text("SELECT pg_backend_pid()")))
            assert not conn.scalar(role)
            assert conn.execute(connections).all() == []
            assert not conn.scalar(grant, {"id": tenant.grant_id})
        assert len(pids) == 1
    finally:
        engine.dispose()


def test_returned_connection_goes_to_waiter_before_new_caller(database, pools):
    _, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1, timeout=2)
    order = []

    def first():
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT 1")) == 1
            order.append("waiting")

    try:
        holder = engine.connect()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(first)
            until(lambda: pools[0].get_stats().get("requests_waiting") == 1)
            holder.close()
            with engine.connect() as conn:
                assert conn.scalar(text("SELECT 1")) == 1
                order.append("new")
            pending.result(timeout=3)
        assert order == ["waiting", "new"]
    finally:
        holder.close()
        engine.dispose()


def test_pool_timeout_and_queue_limit_recover_without_leaked_capacity(database, pools):
    _, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1, timeout=0.3, max_waiting=1)

    def queued():
        with pytest.raises(DBAPIError) as error:
            engine.connect()
        assert isinstance(error.value.orig, PoolTimeout)

    try:
        with engine.connect(), ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(queued)
            until(lambda: pools[0].get_stats().get("requests_waiting") == 1)
            with pytest.raises(DBAPIError) as error:
                engine.connect()
            assert isinstance(error.value.orig, TooManyRequests)
            pending.result(timeout=3)
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT 1")) == 1
        assert pools[0].get_stats().get("requests_waiting") == 0
    finally:
        engine.dispose()


def test_dispose_closes_old_pool_and_engine_can_be_reused(database, pools):
    admin, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1)
    borrowed = None
    try:
        borrowed = engine.connect()
        old_pid = borrowed.scalar(text("SELECT pg_backend_pid()"))
        engine.dispose()
        assert pools[0].closed
        assert borrowed.scalar(text("SELECT 1")) == 1
        with engine.connect() as conn:
            new_pid = conn.scalar(text("SELECT pg_backend_pid()"))
        assert new_pid != old_pid and len(pools) == 2
        borrowed.close()
        engine.dispose()

        def closed():
            with admin.connect() as conn:
                return (
                    conn.scalar(
                        text("SELECT count(*) FROM pg_stat_activity WHERE pid IN (:old,:new)"),
                        {"old": old_pid, "new": new_pid},
                    )
                    == 0
                )

        until(closed)
    finally:
        if borrowed is not None:
            borrowed.close()
        engine.dispose()


def test_health_check_replaces_terminated_idle_connection(database, pools):
    admin, runtime = database
    engine = database_pool.pooled_engine(runtime.url, max_size=1, timeout=2)
    try:
        with engine.connect() as conn:
            pid = conn.scalar(text("SELECT pg_backend_pid()"))
        with admin.connect() as conn:
            assert conn.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT pg_backend_pid()")) != pid
        assert pools[0].get_stats().get("connections_lost") == 1
    finally:
        engine.dispose()


def test_driver_options_retain_spaces_and_session_timeouts(database, pools):
    _, runtime = database
    url = runtime.url.update_query_dict(
        {"options": "-c statement_timeout=12345 -c lock_timeout=2345"}
    )
    engine = database_pool.pooled_engine(url, max_size=1)
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SHOW statement_timeout")) == "12345ms"
            assert conn.scalar(text("SHOW lock_timeout")) == "2345ms"
    finally:
        engine.dispose()


def test_http_pool_timeout_is_sanitized_and_recovers(database, authentication, tenant):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    engine = database_pool.pooled_engine(runtime.url, max_size=1, timeout=0.05)
    try:
        with TestClient(create_app(engine, authentication)) as client:
            login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert login.status_code == 200
            headers = {
                "Authorization": "Bearer " + login.json()["access_token"],
                "X-Organization-Id": str(tenant.buyer.organization_id),
            }
            with engine.connect():
                response = client.get("/v1/products", headers=headers)
                assert response.status_code == 503
                assert response.json()["code"] == "DATABASE_OPERATION_UNCONFIRMED"
                assert not any(
                    value in response.text for value in (email, PASSWORD, "psycopg", "warehouse-db")
                )
            assert client.get("/v1/products", headers=headers).status_code == 200
            assert client.post("/v1/auth/logout", headers=headers).status_code == 204
    finally:
        engine.dispose()
