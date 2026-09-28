from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, changes, quotes, sales
from cloud_warehouse.advisor import WarehouseAdvisorBackend, departure_id
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.merchant import WarehouseMerchantBackend
from cloud_warehouse.persistence import Forbidden
from merchant_agent.types import MerchantSessionContext
from shopping_agent import ShoppingSessionContext

from .test_api import PASSWORD
from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer
from .test_sync import Connector, batch


def command(runtime, tenant, *, paused=True, deadline=None):
    row = list_departures(runtime, tenant.buyer)[0]
    current = sales.get(runtime, tenant.supplier, row["id"])
    return sales.Control(
        target_id=row["id"],
        expected_version=current["version"],
        sales_paused=paused,
        local_booking_deadline=deadline,
        reason="ACME sales control",
    )


async def test_pause_requires_approval_preserves_stock_and_invalidates_old_quotes(
    database, tenant, managed_offer
):
    _, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    request = quotes.QuoteRequest(
        offer_id=managed_offer, departure_date=FUTURE, party=quotes.Party()
    )
    original = await quotes.create(runtime, tenant.buyer, request, "before-pause")
    control = command(runtime, tenant)
    proposed = sales.propose(runtime, tenant.supplier, control)
    with pytest.raises(Forbidden):
        changes.apply(runtime, tenant.supplier, UUID(proposed["id"]))
    assert list_departures(runtime, tenant.buyer)[0]["can_quote"]
    applied = commit(runtime, tenant.supplier, proposed)
    assert changes.apply(runtime, tenant.supplier, UUID(proposed["id"])) == applied
    dep = list_departures(runtime, tenant.buyer)[0]
    assert dep["available_seats"] == 10 and not dep["can_quote"] and dep["sales_status"] == "paused"
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    details = await backend.get_product_details(
        ShoppingSessionContext(session_id="ACME", user_id=str(tenant.buyer.user_id)),
        departure_id(dep["id"]),
    )
    assert (
        details.in_stock is False
        and details.attributes["availability"] == "available"
        and "available_seats" not in details.attributes
    )
    assert details.attributes["can_quote"] == "false"
    merchant = WarehouseMerchantBackend(runtime, tenant.supplier)
    listing = await merchant.get_listing(
        MerchantSessionContext(
            session_id="ACME",
            merchant_id=str(tenant.supplier.organization_id),
            operator=str(tenant.supplier.user_id),
        ),
        str(dep["id"]),
    )
    assert listing.status == "paused" and listing.stock == 10
    with pytest.raises(Conflict, match="停售"):
        await quotes.create(runtime, tenant.buyer, request, "paused")
    assert quotes.get(runtime, tenant.buyer, UUID(original["quote_id"]))["snapshot_stale"]
    reopened = sales.propose(runtime, tenant.supplier, command(runtime, tenant, paused=False))
    commit(runtime, tenant.supplier, reopened)
    assert list_departures(runtime, tenant.buyer)[0]["can_quote"]
    assert quotes.get(runtime, tenant.buyer, UUID(original["quote_id"]))["snapshot_stale"]


async def test_deadline_limits_quote_then_closes_without_modifying_inventory(
    database, tenant, managed_offer, monkeypatch
):
    _, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    cutoff = datetime.now(UTC) + timedelta(minutes=1)
    commit(
        runtime,
        tenant.supplier,
        sales.propose(
            runtime, tenant.supplier, command(runtime, tenant, paused=False, deadline=cutoff)
        ),
    )
    request = quotes.QuoteRequest(
        offer_id=managed_offer, departure_date=FUTURE, party=quotes.Party()
    )
    quote = await quotes.create(runtime, tenant.buyer, request, "before-cutoff")
    assert datetime.fromisoformat(quote["fresh_until"]) == cutoff
    assert datetime.fromisoformat(quote["local_booking_deadline"]) == cutoff

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz) if tz else cutoff.replace(tzinfo=None)

    monkeypatch.setattr(sales, "datetime", Clock)
    monkeypatch.setattr(quotes, "datetime", Clock)
    dep = list_departures(runtime, tenant.buyer)[0]
    assert (
        not dep["can_quote"]
        and dep["sales_status"] == "deadline_passed"
        and dep["available_seats"] == 10
    )
    with pytest.raises(Conflict, match="截止"):
        await quotes.create(runtime, tenant.buyer, request, "after-cutoff")
    assert quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))["snapshot_stale"]


async def test_b2b_resync_preserves_local_controls_and_does_not_write_upstream(database, tenant):
    _, runtime = database
    data = batch()
    data.departures[0].update(departDate=FUTURE.isoformat(), returnDate=FUTURE.isoformat())
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    cutoff = datetime.now(UTC) + timedelta(days=1)
    commit(
        runtime,
        tenant.supplier,
        sales.propose(runtime, tenant.supplier, command(runtime, tenant, deadline=cutoff)),
    )
    data.departures[0]["availableSeats"] = 13
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    dep = list_departures(runtime, tenant.buyer)[0]
    assert dep["sales_status"] == "paused" and dep["available_seats"] == 13
    assert datetime.fromisoformat(dep["local_booking_deadline"]) == cutoff


def test_control_versions_source_config_and_atomic_apply(
    database, tenant, excel_source, managed_offer, monkeypatch
):
    admin, runtime = database
    proposal = sales.propose(runtime, tenant.supplier, command(runtime, tenant))
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_connection SET name=name||' updated' WHERE id=:id"),
            {"id": excel_source},
        )
    with pytest.raises(Conflict):
        changes.approve(runtime, tenant.supplier, UUID(proposal["id"]), proposal["payload_hash"])
    a = sales.propose(runtime, tenant.supplier, command(runtime, tenant))
    b = sales.propose(runtime, tenant.supplier, command(runtime, tenant))
    for item in (a, b):
        changes.approve(runtime, tenant.supplier, UUID(item["id"]), item["payload_hash"])

    original = changes.audit

    def fail(conn, actor, action, resource, details):
        if action == "departure_sales.applied":
            raise RuntimeError("ACME audit failure")
        return original(conn, actor, action, resource, details)

    monkeypatch.setattr(changes, "audit", fail)
    with pytest.raises(RuntimeError):
        changes.apply(runtime, tenant.supplier, UUID(a["id"]))
    assert list_departures(runtime, tenant.buyer)[0]["can_quote"]
    monkeypatch.setattr(changes, "audit", original)

    def apply(item):
        try:
            return changes.apply(runtime, tenant.supplier, UUID(item["id"]))
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [a, b]))
    assert sum(row is not None for row in results) == 1
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM outbox_event WHERE organization_id=:id AND kind='departure_sales.applied'"
                ),
                {"id": tenant.supplier.organization_id},
            )
            == 1
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM inventory_movement WHERE supplier_org_id=:id"),
                {"id": tenant.supplier.organization_id},
            )
            == 1
        )


def test_sales_http_permissions_and_aware_deadline(database, authentication, tenant, managed_offer):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin,
        email,
        PASSWORD,
        {
            tenant.supplier.organization_id: ["product_editor"],
            tenant.buyer.organization_id: ["advisor"],
        },
    )
    body = command(runtime, tenant).model_dump(mode="json")
    path = f"/v1/merchant/departures/{body['target_id']}/sales"
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        assert client.get(path, headers=headers).status_code == 200
        result = client.post("/v1/merchant/departure-sales/preview", headers=headers, json=body)
        assert result.status_code == 201
        assert (
            client.post(
                "/v1/merchant/departure-sales/preview",
                headers=headers,
                json={**body, "local_booking_deadline": "2026-10-01T12:00:00"},
            ).status_code
            == 422
        )
        assert (
            client.get(
                path, headers={**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/merchant/departure-sales/preview",
                headers=headers,
                json={**body, "target_id": str(uuid4())},
            ).status_code
            == 403
        )
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:id AND organization_id=:org"
                ),
                {"id": user, "org": tenant.supplier.organization_id},
            )
        assert client.get(path, headers=headers).status_code == 200
        assert (
            client.post(
                "/v1/merchant/departure-sales/preview", headers=headers, json=body
            ).status_code
            == 403
        )


def test_sales_state_never_treats_unknown_upstream_conditions_as_confirmed_open():
    now = datetime(2030, 1, 2, 0, tzinfo=UTC)
    assert (
        sales.evaluate(now.date(), "Asia/Shanghai", now=now)["sales_status"]
        == "confirmation_required"
    )
    assert (
        sales.evaluate(now.date() - timedelta(days=1), "Asia/Shanghai", now=now)["can_quote"]
        is False
    )
    assert sales.evaluate(now.date(), "ACME/Unknown", now=now)["sales_status"] == "timezone_invalid"


def test_deadline_bounds_noop_and_explicit_clear(database, tenant, managed_offer):
    _, runtime = database
    with pytest.raises(Conflict, match="未变化"):
        sales.propose(runtime, tenant.supplier, command(runtime, tenant, paused=False))
    end = sales.departure_day_end(FUTURE, sales.business_zone("Asia/Shanghai"))
    with pytest.raises(Conflict, match="不得晚于"):
        sales.propose(
            runtime,
            tenant.supplier,
            command(runtime, tenant, paused=False, deadline=end + timedelta(seconds=1)),
        )
    commit(
        runtime,
        tenant.supplier,
        sales.propose(
            runtime, tenant.supplier, command(runtime, tenant, paused=False, deadline=end)
        ),
    )
    cleared = sales.propose(runtime, tenant.supplier, command(runtime, tenant, paused=False))
    commit(runtime, tenant.supplier, cleared)
    row = list_departures(runtime, tenant.buyer)[0]
    assert row["local_booking_deadline"] is None and row["can_quote"]
    assert row["available_seats"] == 10
