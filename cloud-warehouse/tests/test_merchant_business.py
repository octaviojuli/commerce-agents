from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import merchant_business as business
from cloud_warehouse import merchant_source_reads as reads
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.integrations import CatalogBatch, SourceError, canonical
from cloud_warehouse.persistence import Forbidden, Principal

from .test_sync import Connector


async def test_independent_pages_preserve_selection_and_original_inventory(database, tenant):
    admin, runtime = database
    routes = [
        {"routeId": n, "routeCode": f"ACME-R{n}", "routeName": f"ACME route {n}"} for n in (1, 2)
    ]
    departures = [
        {
            "periodId": n,
            "routeId": 1 if n < 4 else 2,
            "periodCode": f"ACME-D{n}",
            "departDate": "2026-10-01",
            "returnDate": "2026-10-02",
            "companyId": 2,
            "availableSeats": 4,
            "planGuests": 20,
            "confirmCount": 9,
            "reserveCount": 3,
            "placeholderCount": 2,
        }
        for n in (2, 3, 4)
    ]
    connector = Connector(CatalogBatch(routes, departures))
    await synchronize(runtime, tenant.worker, tenant.connection_id, connector)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_connection SET capabilities=capabilities||CAST(:selection AS jsonb) WHERE id=:id"
            ),
            {
                "id": tenant.connection_id,
                "selection": canonical(
                    {
                        "catalog_selection": {
                            "route_ids": ["1"],
                            "departure_ids": ["2", "3"],
                            "note": "ACME selection",
                        }
                    }
                ),
            },
        )
    # A later full sync must not undo the selection or delete historical records.
    await synchronize(runtime, tenant.worker, tenant.connection_id, connector)
    page = business.routes(runtime, tenant.supplier)
    assert page["total"] == 1 and page["items"][0]["departure_count"] == 2
    route = business.route(runtime, tenant.supplier, page["items"][0]["id"])
    assert route["document"] is None and route["reference_price"] is None
    page = business.departures(runtime, tenant.supplier, product_id=route["id"], limit=1)
    assert page["total"] == 2 and len(page["items"]) == 1
    second = business.departures(runtime, tenant.supplier, product_id=route["id"], limit=1, page=2)
    assert second["items"][0]["id"] != page["items"][0]["id"]
    detail = business.departure(runtime, tenant.supplier, page["items"][0]["id"])
    assert (detail["observed_available"], detail["reserve_count"], detail["placeholder_count"]) == (
        4,
        3,
        2,
    )
    assert detail["sales_status"] == "confirmation_required"
    assert detail["offers"][0]["schedule"] is None
    with pytest.raises(Forbidden):
        business.routes(runtime, tenant.buyer)
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM departure WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
            == 3
        )
        hidden = conn.scalar(
            text("SELECT id FROM departure WHERE connection_id=:id AND external_id='4'"),
            {"id": tenant.connection_id},
        )
        assert (
            conn.scalar(text("SELECT status FROM departure WHERE id=:id"), {"id": hidden})
            == "paused"
        )
    with pytest.raises(Forbidden):
        business.departure(runtime, tenant.supplier, hidden)
    assert business.departures(runtime, tenant.supplier, query="not-present")["total"] == 0


async def test_order_access_rechecks_scope_after_upstream_and_excludes_editors(database, tenant):
    admin, runtime = database
    calls = []

    class Connector:
        async def read_order_departments(self, allowed_departments):
            calls.append(allowed_departments)
            with admin.begin() as conn:
                conn.execute(
                    text("UPDATE membership SET active=false WHERE user_id=:id"),
                    {"id": tenant.supplier.user_id},
                )
            return {"departments": ["2"]}

    @asynccontextmanager
    async def factory():
        yield Connector()

    registry = {tenant.connection_id: factory}
    with pytest.raises(SourceError, match="ORDER_READ_SCOPE_NOT_CONFIGURED"):
        await reads.read(
            runtime,
            tenant.supplier,
            registry,
            tenant.connection_id,
            "read_order_departments",
            orders=True,
        )
    assert not calls
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_connection SET capabilities=capabilities||:cap::jsonb WHERE id=:id".replace(
                    ":cap::jsonb", "CAST(:cap AS jsonb)"
                )
            ),
            {"id": tenant.connection_id, "cap": canonical({"order_read_departments": ["2"]})},
        )
        editor = uuid4()
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
            {"id": editor, "email": f"{editor}@acme.example"},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,ARRAY['product_editor'])"
            ),
            {"id": editor, "org": tenant.supplier.organization_id},
        )
    with pytest.raises(Forbidden):
        await reads.read(
            runtime,
            Principal(editor, tenant.supplier.organization_id),
            registry,
            tenant.connection_id,
            "read_order_departments",
            orders=True,
        )
    with pytest.raises(Forbidden):
        await reads.read(
            runtime,
            tenant.supplier,
            registry,
            tenant.connection_id,
            "read_order_departments",
            orders=True,
        )
    assert calls == [["2"]]


async def test_order_parent_links_match_source_department_and_keep_history(database, tenant):
    _, runtime = database
    connector = Connector()
    connector.data.departures[0]["companyId"] = 2
    await synchronize(runtime, tenant.worker, tenant.connection_id, connector)
    result = reads.link_orders(
        runtime,
        tenant.supplier,
        tenant.connection_id,
        {
            "department": "2",
            "items": [{"orderId": 1, "periodId": 2}, {"orderId": 2, "periodId": 999}],
        },
    )
    assert result["items"][0]["warehouse_departure_id"]
    assert result["items"][0]["warehouse_route_id"]
    assert result["items"][1]["warehouse_departure_id"] is None
    result = reads.link_orders(
        runtime,
        tenant.supplier,
        tenant.connection_id,
        {"department": "3", "order": {"orderId": 1, "periodId": 2}},
    )
    assert result["order"]["warehouse_departure_id"] is None


async def test_merchant_price_context_is_source_actor_scoped_and_rechecked(database, tenant):
    from cloud_warehouse import pricing
    from cloud_warehouse.pricing import CustomerIdentity

    admin, runtime = database
    source = Connector()
    source.data.departures[0]["companyId"] = 2
    await synchronize(runtime, tenant.worker, tenant.connection_id, source)
    departure = business.departures(runtime, tenant.supplier)["items"][0]["id"]
    verification = await pricing._save_verification(
        runtime,
        tenant.supplier,
        tenant.connection_id,
        CustomerIdentity(customer_id="4101", code="ACME-C1", name="ACME buyer"),
    )
    calls = []

    class Live:
        async def read_departure_observation(self, **kwargs):
            calls.append(kwargs)
            with admin.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE customer_verification SET expires_at=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": verification["verification_id"]},
                )
            return {"inventory": {"availableSeats": 7}}

    @asynccontextmanager
    async def factory():
        yield Live()

    registry = {tenant.connection_id: factory}
    with pytest.raises(Forbidden):
        await reads.observation(runtime, tenant.supplier, registry, departure, uuid4())
    assert calls == []
    with pytest.raises(Forbidden):
        await reads.observation(
            runtime, tenant.buyer, registry, departure, verification["verification_id"]
        )
    assert calls == []
    with pytest.raises(Forbidden):
        await reads.observation(
            runtime, tenant.supplier, registry, departure, verification["verification_id"]
        )
    assert calls[0]["customer_id"] == "4101"
    with pytest.raises(Forbidden):
        await reads.observation(
            runtime, tenant.supplier, registry, departure, verification["verification_id"]
        )
    assert len(calls) == 1
