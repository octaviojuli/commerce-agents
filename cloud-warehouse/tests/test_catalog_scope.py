from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import outbox
from cloud_warehouse.admin import onboard
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.persistence import Principal, transaction

from .test_sync import Connector


def bind(conn, actor):
    for setting, value in (
        ("warehouse.user_id", actor.user_id),
        ("warehouse.organization_id", actor.organization_id),
    ):
        conn.execute(
            text("SELECT set_config(:setting,:value,true)"),
            {"setting": setting, "value": str(value)},
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE distribution_grant SET active=false WHERE id=:grant",
        "UPDATE distribution_grant SET valid_from=now()+interval '1 day' WHERE id=:grant",
        "UPDATE distribution_grant SET valid_from=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE id=:grant",
        "UPDATE supplier_connection SET active=false WHERE id=:source",
        "UPDATE organization SET active=false WHERE id=:supplier",
        "UPDATE organization SET active=false WHERE id=:buyer",
        "UPDATE membership SET active=false WHERE user_id=:user",
        "UPDATE warehouse_user SET active=false WHERE id=:user",
    ],
)
async def test_live_catalog_scope_revocations_apply_on_same_connection(database, tenant, mutation):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    outbox.run_once(runtime, tenant.worker)
    params = {
        "grant": tenant.grant_id,
        "source": tenant.connection_id,
        "supplier": tenant.supplier.organization_id,
        "buyer": tenant.buyer.organization_id,
        "user": tenant.buyer.user_id,
    }
    with runtime.begin() as conn:
        bind(conn, tenant.buyer)
        assert list(conn.execute(text("SELECT warehouse_buyer_connections()")).scalars()) == [
            tenant.connection_id
        ]
        for table in ("supplier_product", "departure", "product_search"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 1
        with admin.begin() as writer:
            writer.execute(text(mutation), params)
        # Same pool connection and transaction: no cross-statement permission cache.
        assert list(conn.execute(text("SELECT warehouse_buyer_connections()")).scalars()) == []
        for table in ("supplier_product", "departure", "product_search"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0


async def test_scope_preserves_owner_status_and_cannot_impersonate_another_buyer(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    ids = onboard(admin, "ACME Other supplier", "ACME Other buyer", f"{uuid4()}@acme.example")
    other = Principal(UUID(ids["worker_id"]), UUID(ids["supplier_id"]))
    await synchronize(runtime, other, UUID(ids["connection_id"]), Connector())
    for status in ("published", "draft", "paused", "archived"):
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE supplier_product SET status=:status WHERE connection_id=:id"),
                {"status": status, "id": tenant.connection_id},
            )
            conn.execute(
                text("UPDATE departure SET status=:status WHERE connection_id=:id"),
                {"status": "paused" if status == "draft" else status, "id": tenant.connection_id},
            )
        for actor in (tenant.worker, tenant.buyer):
            with transaction(runtime, actor) as conn:
                for table in ("supplier_product", "departure"):
                    rows = list(conn.execute(text(f"SELECT connection_id FROM {table}")).scalars())
                    expected = (
                        [tenant.connection_id]
                        if actor == tenant.worker or status == "published"
                        else []
                    )
                    assert rows == expected
    with runtime.begin() as conn:
        bind(conn, Principal(tenant.buyer.user_id, UUID(ids["buyer_id"])))
        assert list(conn.execute(text("SELECT warehouse_buyer_connections()")).scalars()) == []
        assert conn.scalar(text("SELECT count(*) FROM supplier_product")) == 0
    with runtime.begin() as conn:
        # Context from the preceding checked-out connection cannot survive commit.
        assert list(conn.execute(text("SELECT warehouse_buyer_connections()")).scalars()) == []


def test_scope_helper_is_not_publicly_executable(database):
    admin, runtime = database
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM pg_proc p, LATERAL aclexplode(p.proacl) a WHERE p.proname='warehouse_buyer_connections' AND a.grantee=0 AND a.privilege_type='EXECUTE'"
                )
            )
            == 0
        )
        assert conn.scalar(
            text("SELECT has_function_privilege(:role,'warehouse_buyer_connections()','EXECUTE')"),
            {"role": runtime.url.username},
        )
