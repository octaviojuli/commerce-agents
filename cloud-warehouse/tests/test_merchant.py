from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, changes, imports, inventory
from cloud_warehouse.admin import onboard
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.merchant import WarehouseMerchantBackend
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation
from merchant_agent.enrichment import resolve_metrics
from merchant_agent.tools.presentation import MetricPick
from merchant_agent.types import (
    ActorKind,
    InventoryActionItem,
    MerchantSessionContext,
    MerchantSessionState,
    PriceUpdateItem,
)

from .test_imports import excel_source as excel_source
from .test_imports import workbook
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer
from .test_sync import Connector


def session(actor):
    return MerchantSessionContext(
        session_id=str(uuid4()), merchant_id=str(actor.organization_id), operator=str(actor.user_id)
    )


async def test_pending_changes_are_complete_and_scoped(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    other = onboard(admin, "ACME Other supplier", "ACME Other buyer", f"{uuid4()}@acme.example")
    other_org = UUID(other["supplier_id"])
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin,
        email,
        "ACME-test-password-only",
        {tenant.supplier.organization_id: ["supplier_admin"], other_org: ["supplier_admin"]},
    )
    actor = Principal(user, tenant.supplier.organization_id)
    backend, context = WarehouseMerchantBackend(runtime, actor), session(actor)
    product = (await backend.search_listings(context, ""))[0]
    proposed = [
        await backend.stage_listing_update(
            context, product.listing_id, {"title": f"ACME Pending route {index}"}
        )
        for index in range(105)
    ]
    await backend.discard_change(context, proposed[0].change_id)
    changes.approve(runtime, actor, UUID(proposed[1].change_id), proposed[1].payload_hash)
    await backend.apply_change(context, proposed[1].change_id)
    # Other change kinds must not enter the original merchant protocol's queue.
    departure = list_departures(runtime, tenant.buyer)[0]
    inventory.stage(
        runtime,
        actor,
        inventory.InventoryCommand(
            action="sale",
            target_id=departure["inventory_pool_id"],
            expected_version=departure["inventory_version"],
            quantity=1,
            business_key="ACME-nonmerchant-pending",
            reason="ACME separate inventory proposal",
        ),
    )
    expected = [change.change_id for change in proposed[2:]]
    assert [change.change_id for change in await backend.get_pending_changes(context)] == expected
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-test-password-only"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(actor.organization_id),
        }
        result = client.get("/v1/merchant/changes", headers=headers)
        assert result.status_code == 200
        assert [change["change_id"] for change in result.json()["items"]] == expected
        assert client.get("/v1/merchant/changes").status_code == 401
        assert client.get(
            "/v1/merchant/changes", headers={**headers, "X-Organization-Id": str(other_org)}
        ).json() == {"items": []}
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET active=false WHERE user_id=:user AND organization_id=:org"
                ),
                {"user": user, "org": actor.organization_id},
            )
        assert client.get("/v1/merchant/changes", headers=headers).status_code == 403
    with pytest.raises(Forbidden):
        await backend.get_pending_changes(context)


async def test_multi_pool_merchant_change_is_atomic_and_replay_safe(database, tenant, excel_source):
    _, runtime = database
    uploaded = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME-batch.xlsx",
        workbook(
            [
                [
                    "ACME-R",
                    "ACME 多团期线路",
                    1,
                    "ACME 城市",
                    code,
                    FUTURE.isoformat(),
                    FUTURE.isoformat(),
                    10,
                    0,
                    0,
                ]
                for code in ("ACME-D1", "ACME-D2")
            ]
        ),
    )
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, uploaded["id"]))
    backend, context = WarehouseMerchantBackend(runtime, tenant.supplier), session(tenant.supplier)
    departures = list_departures(runtime, tenant.buyer)
    items = [
        InventoryActionItem(listing_id=str(row["id"]), action="restock", quantity=2)
        for row in departures
    ]
    stale = await backend.stage_inventory_action(context, items)
    changes.approve(runtime, tenant.supplier, UUID(stale.change_id), stale.payload_hash)
    second = departures[1]
    sale = inventory.stage(
        runtime,
        tenant.supplier,
        inventory.InventoryCommand(
            action="sale",
            target_id=second["inventory_pool_id"],
            expected_version=second["inventory_version"],
            quantity=1,
            business_key="ACME-batch-sale",
            reason="ACME 销售",
        ),
    )
    commit(runtime, tenant.supplier, sale)
    with pytest.raises(Conflict):
        await backend.apply_change(context, stale.change_id)
    assert [row["available_seats"] for row in list_departures(runtime, tenant.buyer)] == [10, 9]
    fresh = await backend.stage_inventory_action(context, list(reversed(items)))
    changes.approve(runtime, tenant.supplier, UUID(fresh.change_id), fresh.payload_hash)
    await backend.apply_change(context, fresh.change_id)
    await backend.apply_change(context, fresh.change_id)
    assert [row["available_seats"] for row in list_departures(runtime, tenant.buyer)] == [12, 11]
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM inventory_movement WHERE change_id=:id"),
                {"id": UUID(fresh.change_id)},
            )
            == 2
        )


async def test_merchant_unknowns_and_live_membership(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    backend = WarehouseMerchantBackend(runtime, tenant.supplier)
    context = session(tenant.supplier)
    rows = await backend.search_listings(context, "")
    assert len(rows) == 1 and rows[0].price is None
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE departure SET availability_expires_at=now()-interval '1 minute' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    assert (await backend.get_listing(context, rows[0].listing_id)).stock is None
    snapshot = await backend.get_business_snapshot(context)
    assert snapshot.orders is None and snapshot.sales is None
    tiles, missing = resolve_metrics(
        MerchantSessionState(latest_snapshot=snapshot),
        [MetricPick(metric="sales"), MetricPick(metric="orders")],
    )
    assert tiles == [] and len(missing) == 2
    with pytest.raises(ChangeNotApplicable):
        await backend.get_order_issues(context)
    with pytest.raises(Forbidden):
        await backend.search_listings(session(tenant.buyer), "")
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE membership SET active=false WHERE user_id=:id"),
            {"id": tenant.supplier.user_id},
        )
    with pytest.raises(Forbidden):
        await backend.get_listing(context, rows[0].listing_id)


async def test_merchant_content_approval_history_and_source_boundary(
    database, tenant, managed_offer
):
    admin, runtime = database
    backend = WarehouseMerchantBackend(runtime, tenant.supplier)
    context = session(tenant.supplier)
    row = (await backend.search_listings(context, ""))[0]
    staged = await backend.stage_listing_update(
        context, row.listing_id, {"title": "ACME 新线路", "long_description": "ACME 已复核行程说明"}
    )
    assert (await backend.get_listing(context, row.listing_id)).title == row.title
    with pytest.raises(Forbidden):
        await backend.apply_change(context, staged.change_id)
    changes.approve(runtime, tenant.supplier, UUID(staged.change_id), staged.payload_hash)
    # Recreate the backend to prove neither proposals nor approval live in memory.
    backend = WarehouseMerchantBackend(runtime, tenant.supplier)
    applied = await backend.apply_change(context, staged.change_id)
    assert applied.status == "applied" and applied.applied_by == context.operator
    assert (await backend.get_listing(context, row.listing_id)).title == "ACME 新线路"
    assert (await backend.apply_change(context, staged.change_id)).status == "applied"
    with transaction(runtime, tenant.supplier) as conn:
        versions = (
            conn.execute(
                text(
                    "SELECT version,body FROM product_revision WHERE product_id=:id ORDER BY version"
                ),
                {"id": UUID(row.listing_id)},
            )
            .mappings()
            .all()
        )
        assert len(versions) == 2 and versions[0]["body"]["name"] == row.title
    with transaction(runtime, tenant.buyer) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM product_revision WHERE product_id=:id"),
                {"id": UUID(row.listing_id)},
            )
            == 0
        )
    with transaction(runtime, tenant.supplier) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("DELETE FROM product_revision WHERE product_id=:id"), {"id": UUID(row.listing_id)}
        )
    # Restoring unchanged source facts after a human edit still changes the live version.
    departure = list_departures(runtime, tenant.buyer)[0]
    original_file = workbook(
        [
            [
                "ACME-R1",
                row.title,
                3,
                "ACME 城市",
                "ACME-D1",
                str(departure["depart_date"]),
                str(departure["return_date"]),
                10,
                None,
                None,
            ]
        ]
    )
    with transaction(runtime, tenant.supplier) as conn:
        source_id = conn.scalar(
            text("SELECT connection_id FROM supplier_product WHERE id=:id"),
            {"id": UUID(row.listing_id)},
        )
    uploaded = imports.upload(
        runtime, tenant.supplier, source_id, "ACME-update.xlsx", original_file
    )
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, uploaded["id"]))
    refreshed = await backend.get_listing(context, row.listing_id)
    assert refreshed.title == row.title and refreshed.attributes["版本"] == "3"
    assert refreshed.long_description == "ACME 已复核行程说明"
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    external = [
        item
        for item in await backend.search_listings(context, "")
        if item.listing_id != row.listing_id
    ][0]
    overlay = await backend.stage_listing_update(
        context, external.listing_id, {"title": "ACME 展示补充"}
    )
    changes.approve(runtime, tenant.supplier, UUID(overlay.change_id), overlay.payload_hash)
    await backend.apply_change(context, overlay.change_id)
    assert (await backend.get_listing(context, external.listing_id)).title == "ACME 展示补充"
    with transaction(runtime, tenant.supplier) as conn:
        original = conn.execute(
            text("SELECT name,name_override FROM supplier_product WHERE id=:id"),
            {"id": UUID(external.listing_id)},
        ).one()
        assert original.name == external.title and original.name_override == "ACME 展示补充"


async def test_merchant_inventory_stale_and_discard(database, tenant, managed_offer):
    _, runtime = database
    backend = WarehouseMerchantBackend(runtime, tenant.supplier)
    context = session(tenant.supplier)
    departure = list_departures(runtime, tenant.buyer)[0]
    staged = await backend.stage_inventory_action(
        context,
        [InventoryActionItem(listing_id=str(departure["id"]), action="restock", quantity=3)],
    )
    changes.approve(runtime, tenant.supplier, UUID(staged.change_id), staged.payload_hash)
    sale = inventory.stage(
        runtime,
        tenant.supplier,
        inventory.InventoryCommand(
            action="sale",
            target_id=departure["inventory_pool_id"],
            expected_version=departure["inventory_version"],
            quantity=1,
            business_key="ACME-sale",
            reason="ACME 线下销售",
        ),
    )
    commit(runtime, tenant.supplier, sale)
    with pytest.raises(Conflict):
        await backend.apply_change(context, staged.change_id)
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 9
    discarded = await backend.discard_change(context, staged.change_id, ActorKind.AGENT)
    assert (
        discarded.discarded_by == context.operator
        and discarded.discarded_by_kind == ActorKind.AGENT
    )
    with pytest.raises(Conflict):
        await backend.apply_change(context, staged.change_id)
    fresh = await backend.stage_inventory_action(
        context,
        [InventoryActionItem(listing_id=str(departure["id"]), action="restock", quantity=3)],
    )
    changes.approve(runtime, tenant.supplier, UUID(fresh.change_id), fresh.payload_hash)
    await backend.apply_change(context, fresh.change_id)
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 12
    with pytest.raises(GuardrailViolation):
        await backend.stage_inventory_action(
            context,
            [InventoryActionItem(listing_id=str(departure["id"]), action="restock", quantity=501)],
        )


async def test_merchant_price_context_caps_and_publication(database, tenant, managed_offer):
    _, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    backend = WarehouseMerchantBackend(
        runtime, tenant.supplier, buyer_org_id=tenant.buyer.organization_id
    )
    context = session(tenant.supplier)
    family = (await backend.search_listings(context, ""))[0]
    assert family.price == 100
    detail = await backend.get_listing(context, family.listing_id)
    departure = detail.variants[0].listing_id
    with pytest.raises(ChangeNotApplicable):
        await WarehouseMerchantBackend(runtime, tenant.supplier).stage_price_update(
            context, [PriceUpdateItem(listing_id=departure, new_price=110)]
        )
    with pytest.raises(GuardrailViolation):
        await backend.stage_price_update(
            context, [PriceUpdateItem(listing_id=departure, new_price=130)]
        )
    staged = await backend.stage_price_update(
        context, [PriceUpdateItem(listing_id=departure, new_price=110)]
    )
    assert staged.currency == "CNY" and staged.items[0].before == "100.00"
    changes.approve(runtime, tenant.supplier, UUID(staged.change_id), staged.payload_hash)
    await backend.apply_change(context, staged.change_id)
    assert (await backend.get_pricing_context(context, departure)).current_price == 110
    pause = await backend.stage_inventory_action(
        context, [InventoryActionItem(listing_id=family.listing_id, action="pause")]
    )
    changes.approve(runtime, tenant.supplier, UUID(pause.change_id), pause.payload_hash)
    await backend.apply_change(context, pause.change_id)
    assert list_departures(runtime, tenant.buyer) == []
    assert (await backend.get_listing(context, family.listing_id)).status == "paused"


async def test_merchant_batch_cannot_escalate_product_editor_to_inventory(
    database, tenant, managed_offer
):
    admin, runtime = database
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE membership SET roles=ARRAY['product_editor'] WHERE user_id=:id"),
            {"id": tenant.supplier.user_id},
        )
    backend = WarehouseMerchantBackend(runtime, tenant.supplier)
    departure = list_departures(runtime, tenant.buyer)[0]
    with pytest.raises(Forbidden):
        await backend.stage_inventory_action(
            session(tenant.supplier),
            [InventoryActionItem(listing_id=str(departure["id"]), action="restock", quantity=1)],
        )
    other = WarehouseMerchantBackend(
        runtime, Principal(tenant.buyer.user_id, tenant.buyer.organization_id)
    )
    with pytest.raises(Forbidden):
        await other.get_pending_changes(session(tenant.buyer))
