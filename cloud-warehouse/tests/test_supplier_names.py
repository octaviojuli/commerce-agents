"""Supplier labels share catalog authorization without opening the organization table."""

import runpy
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from cloud_warehouse import platform_sales
from cloud_warehouse.admin import onboard, set_short_name
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.persistence import transaction

from .test_advisor import session
from .test_catalog_scope import bind
from .test_platform_sales import independent
from .test_platform_sales import sales_policy as sales_policy
from .test_sync import Connector, batch

NAME = text("SELECT warehouse_supplier_name(:supplier)")


async def test_platform_advisor_reads_supplier_name_on_every_catalog_entry(
    database, tenant, sales_policy
):
    admin, runtime = database
    actor = independent(admin)
    source = batch()
    query = f"ACME supplier {uuid4().hex}"
    source.routes[0]["routeName"] = query
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source))
    backend, context = WarehouseAdvisorBackend(runtime, actor), session(actor)
    product = (await backend.search_products(context, query))[0]
    assert product.attributes["supplier_name"] == "ACME Supplier"
    set_short_name(admin, tenant.supplier.organization_id, "ACME 环线")
    product = (await backend.search_products(context, query))[0]
    detail = await backend.get_product_details(context, product.product_id)
    departure = backend.departures_page(context, product.product_id)["items"][0]
    assert {
        product.attributes["supplier_name"],
        detail.attributes["supplier_name"],
        departure.attributes["supplier_name"],
    } == {"ACME 环线"}
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM distribution_grant WHERE buyer_org_id=:id"),
                {"id": actor.organization_id},
            )
            == 0
        )
        assert not conn.scalar(
            text("SELECT has_any_column_privilege(:role,'organization','SELECT')"),
            {"role": runtime.url.username},
        )


@pytest.mark.parametrize("mode", ["platform", "distribution"])
@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE supplier_connection SET active=false WHERE id=:source",
        "UPDATE organization SET active=false WHERE id=:supplier",
        "UPDATE organization SET active=false WHERE id=:buyer",
        "UPDATE warehouse_user SET active=false WHERE id=:user",
        "UPDATE membership SET active=false WHERE user_id=:user",
    ],
)
def test_supplier_name_rechecks_live_catalog_scope(database, tenant, mode, mutation):
    admin, runtime = database
    actor = independent(admin) if mode == "platform" else tenant.buyer
    platform_sales.configure(admin, enabled=mode == "platform")
    params = {
        "source": tenant.connection_id,
        "supplier": tenant.supplier.organization_id,
        "buyer": actor.organization_id,
        "user": actor.user_id,
    }
    try:
        with transaction(runtime, actor) as conn:
            assert conn.scalar(NAME, params) == "ACME Supplier"
            with admin.begin() as writer:
                writer.execute(text(mutation), params)
            assert conn.scalar(NAME, params) is None
    finally:
        platform_sales.configure(admin, enabled=False)


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:user",
        "UPDATE platform_sales_policy SET enabled=false WHERE singleton",
    ],
)
def test_platform_supplier_name_requires_live_sales_permission(
    database, tenant, sales_policy, mutation
):
    admin, runtime = database
    actor = independent(admin)
    params = {"supplier": tenant.supplier.organization_id, "user": actor.user_id}
    with transaction(runtime, actor) as conn:
        assert conn.scalar(NAME, params) == "ACME Supplier"
        with admin.begin() as writer:
            writer.execute(text(mutation), params)
        assert conn.scalar(NAME, params) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "active=false",
        "valid_from=now()+interval '1 day'",
        "valid_from=now()-interval '2 days',expires_at=now()-interval '1 day'",
    ],
)
def test_distribution_supplier_name_requires_live_grant(database, tenant, mutation):
    admin, runtime = database
    params = {"supplier": tenant.supplier.organization_id, "grant": tenant.grant_id}
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(NAME, params) == "ACME Supplier"
        with admin.begin() as writer:
            writer.execute(
                text(f"UPDATE distribution_grant SET {mutation} WHERE id=:grant"), params
            )
        assert conn.scalar(NAME, params) is None


def test_supplier_keeps_own_name_without_opening_other_organizations(database, tenant):
    admin, runtime = database
    other = onboard(admin, "ACME Other Supplier", "ACME Other Buyer", f"{uuid4()}@acme.example")
    with transaction(runtime, tenant.supplier) as conn:
        assert conn.scalar(NAME, {"supplier": tenant.supplier.organization_id}) == "ACME Supplier"
        assert conn.scalar(NAME, {"supplier": UUID(other["supplier_id"])}) is None
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(NAME, {"supplier": UUID(other["supplier_id"])}) is None


def test_upgrade_repairs_existing_function_and_preserves_name_and_runtime_grant(
    database, tenant, sales_policy
):
    admin, runtime = database
    actor = independent(admin)
    set_short_name(admin, tenant.supplier.organization_id, "ACME 环线")
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/0047_supplier_name_scope.py")
    )
    acl = text(
        "SELECT proacl::text FROM pg_proc WHERE oid='warehouse_supplier_name(uuid)'::regprocedure"
    )
    with admin.connect() as conn:
        tx = conn.begin()
        try:
            bind(conn, actor)
            with Operations.context(MigrationContext.configure(conn)):
                migration["downgrade"]()
                before = conn.scalar(acl)
                assert conn.scalar(NAME, {"supplier": tenant.supplier.organization_id}) is None
                migration["upgrade"]()
            assert conn.scalar(NAME, {"supplier": tenant.supplier.organization_id}) == "ACME 环线"
            assert conn.scalar(acl) == before
            assert conn.scalar(
                text(
                    "SELECT has_function_privilege(:role,'warehouse_supplier_name(uuid)','EXECUTE')"
                ),
                {"role": runtime.url.username},
            )
            assert (
                conn.scalar(
                    text("""SELECT count(*) FROM pg_proc p,LATERAL aclexplode(p.proacl) a
                    WHERE p.oid='warehouse_supplier_name(uuid)'::regprocedure AND a.grantee=0""")
                )
                == 0
            )
        finally:
            tx.rollback()
