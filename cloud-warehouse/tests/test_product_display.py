from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, changes, documents, product_display, quotes
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_products, synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_advisor import session
from .test_documents import parsed
from .test_documents import proposal as document_proposal
from .test_documents import source as source
from .test_quotes import commit
from .test_sync import Connector, batch


@pytest.fixture
async def product(database, tenant):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    return list_products(runtime, tenant.supplier)[0]["id"]


def command(runtime, tenant, product, **values):
    current = product_display.get(runtime, tenant.supplier, product)
    return product_display.Command(
        **{
            "target_id": product,
            "expected_version": current["version"],
            "expected_display_version": current["display_version"],
            "name_override": "ACME 云仓精选",
            "description_override": "ACME 人工展示说明",
            "note": "ACME 展示验收",
            **values,
        }
    )


async def test_display_survives_sync_and_reset_uses_latest_source(database, tenant, product):
    admin, runtime = database
    cmd = command(runtime, tenant, product)
    commit(runtime, tenant.supplier, product_display.propose(runtime, tenant.supplier, cmd))
    advisor = WarehouseAdvisorBackend(runtime, tenant.buyer)
    read = session(tenant.buyer)
    for query in ("云仓精选", "ACME 环线"):
        item = advisor.catalog_page(read, query=query)["items"][0]
        assert (
            item.title == cmd.name_override and item.short_description == cmd.description_override
        )
        assert item.attributes["name_origin"] == "warehouse_display"
    data = batch(seats=9)
    data.routes[0]["routeName"] = "ACME 最新源名称"
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    current = product_display.get(runtime, tenant.supplier, product)
    assert (
        current["source_name"] == "ACME 最新源名称"
        and current["name_override"] == cmd.name_override
    )
    with admin.connect() as conn:
        row = (
            conn.execute(
                text("SELECT name,source,display_version FROM supplier_product WHERE id=:id"),
                {"id": product},
            )
            .mappings()
            .one()
        )
        assert (
            row["name"] == row["source"]["routeName"] == "ACME 最新源名称"
            and row["display_version"] == 1
        )
        assert (
            conn.scalar(
                text("SELECT available_seats FROM departure WHERE product_id=:id"), {"id": product}
            )
            == 9
        )
    reset = command(runtime, tenant, product, name_override=None, description_override=None)
    proposal = product_display.propose(runtime, tenant.supplier, reset)
    first = commit(runtime, tenant.supplier, proposal)
    assert changes.apply(runtime, tenant.supplier, UUID(proposal["id"])) == first
    item = advisor.catalog_page(read)["items"][0]
    assert item.title == "ACME 最新源名称" and item.attributes["name_origin"] == "source"


async def test_display_quote_provenance_and_empty_description(database, tenant, product):
    admin, runtime = database
    with admin.connect() as conn:
        offer, day = conn.execute(
            text(
                "SELECT o.id,d.depart_date FROM offer o JOIN departure d ON d.id=o.departure_id WHERE d.product_id=:id"
            ),
            {"id": product},
        ).one()
    request = quotes.QuoteRequest(offer_id=offer, departure_date=day, party=quotes.Party(adults=1))
    original = await quotes.create(runtime, tenant.buyer, request, str(uuid4()))
    cmd = command(runtime, tenant, product, name_override=None, description_override="")
    commit(runtime, tenant.supplier, product_display.propose(runtime, tenant.supplier, cmd))
    assert quotes.get(runtime, tenant.buyer, UUID(original["quote_id"]))["snapshot_stale"]
    item = WarehouseAdvisorBackend(runtime, tenant.buyer).catalog_page(session(tenant.buyer))[
        "items"
    ][0]
    assert (
        item.attributes["name_origin"] == "source"
        and item.attributes["description_origin"] == "warehouse_display"
    )
    assert item.short_description is None
    cmd = command(runtime, tenant, product)
    commit(runtime, tenant.supplier, product_display.propose(runtime, tenant.supplier, cmd))
    result = await quotes.create(runtime, tenant.buyer, request, str(uuid4()))
    assert (
        result["product_name"] == cmd.name_override
        and result["product_name_origin"] == "warehouse_display"
    )
    assert quotes.customer_view(result)["product_name_origin"] == "warehouse_display"


async def test_display_changes_preserve_published_document_and_reject_stale_editor(
    database, tenant, source
):
    _, runtime = database
    store, product, asset, _ = source
    assert documents.run_once(runtime, tenant.worker, store, parsed)
    commit(runtime, tenant.supplier, document_proposal(runtime, tenant, asset))
    document = documents.current(runtime, tenant.buyer, product["id"])
    assert document is not None
    cmd = command(runtime, tenant, product["id"])
    commit(runtime, tenant.supplier, product_display.propose(runtime, tenant.supplier, cmd))
    assert documents.current(runtime, tenant.buyer, product["id"]) == document
    assert (
        product_display.get(runtime, tenant.supplier, product["id"])["version"]
        == cmd.expected_version
    )
    with pytest.raises(Conflict, match="展示版本"):
        product_display.propose(
            runtime, tenant.supplier, cmd.model_copy(update={"name_override": "ACME stale browser"})
        )


@pytest.mark.parametrize("change", ["source", "connection", "display"])
async def test_display_approval_detects_changes_and_tampered_before(
    database, tenant, product, change
):
    admin, runtime = database
    cmd = command(runtime, tenant, product)
    proposal = product_display.propose(runtime, tenant.supplier, cmd)
    with transaction(runtime, tenant.supplier) as conn:
        payload = product_display.capture(conn, cmd).model_dump(mode="json")
    payload["source"]["name"] = "ACME 伪造源名称"
    with pytest.raises(Conflict):
        changes.stage(runtime, tenant.supplier, "product_display", payload)
    changes.approve(runtime, tenant.supplier, UUID(proposal["id"]), proposal["payload_hash"])
    if change == "source":
        data = batch()
        data.routes[0]["routeName"] = "ACME newer"
        await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    elif change == "connection":
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE supplier_connection SET version=version+1 WHERE id=:id"),
                {"id": tenant.connection_id},
            )
    else:
        other = command(runtime, tenant, product, name_override="ACME Other")
        commit(runtime, tenant.supplier, product_display.propose(runtime, tenant.supplier, other))
    with pytest.raises(Conflict):
        changes.apply(runtime, tenant.supplier, UUID(proposal["id"]))


async def test_display_view_preserves_rls_and_http_roles(database, authentication, tenant, product):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin, email, "ACME-test-only-42", {tenant.supplier.organization_id: ["product_editor"]}
    )
    editor = Principal(user, tenant.supplier.organization_id)
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-test-only-42"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(editor.organization_id),
        }
        assert client.get(f"/v1/merchant/products/{product}/display").status_code == 401
        assert (
            client.get(f"/v1/merchant/products/{product}/display", headers=headers).status_code
            == 200
        )
        request = command(runtime, tenant, product).model_dump(mode="json")
        proposal = client.post(
            "/v1/merchant/product-display/proposals", headers=headers, json=request
        )
        assert proposal.status_code == 201
        with pytest.raises(Forbidden):
            changes.approve(
                runtime, editor, UUID(proposal.json()["id"]), proposal.json()["payload_hash"]
            )
        invalid = client.post(
            "/v1/merchant/product-display/proposals",
            headers=headers,
            json={**request, "available_seats": 99},
        )
        assert invalid.status_code == 422
        commit(runtime, tenant.supplier, proposal.json())
    with pytest.raises(Forbidden):
        product_display.get(runtime, tenant.buyer, product)
    with pytest.raises(Forbidden):
        product_display.propose(
            runtime,
            tenant.worker,
            command(runtime, tenant, product, name_override="ACME forbidden"),
        )
    with runtime.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM product_listing")) == 0
    with transaction(runtime, tenant.buyer) as conn:
        assert (
            conn.scalar(
                text("SELECT effective_name FROM product_listing WHERE id=:id"), {"id": product}
            )
            == "ACME 云仓精选"
        )
        with pytest.raises(DBAPIError):
            conn.execute(
                text("UPDATE product_listing SET name_override='ACME invalid' WHERE id=:id"),
                {"id": product},
            )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE id=:id"), {"id": tenant.grant_id}
        )
    with transaction(runtime, tenant.buyer) as conn:
        assert (
            conn.scalar(text("SELECT count(*) FROM product_listing WHERE id=:id"), {"id": product})
            == 0
        )


async def test_display_outbox_failure_rolls_back_and_cannot_edit_another_supplier(
    database, tenant, product
):
    admin, runtime = database
    proposal = product_display.propose(runtime, tenant.supplier, command(runtime, tenant, product))
    changes.approve(runtime, tenant.supplier, UUID(proposal["id"]), proposal["payload_hash"])
    before = product_display.get(runtime, tenant.supplier, product)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO outbox_event" in statement:
            raise RuntimeError("ACME outbox failure")

    event.listen(runtime, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="outbox"):
            changes.apply(runtime, tenant.supplier, UUID(proposal["id"]))
    finally:
        event.remove(runtime, "before_cursor_execute", fail)
    assert product_display.get(runtime, tenant.supplier, product) == before
    with pytest.raises(Forbidden):
        product_display.get(
            runtime, Principal(tenant.supplier.user_id, tenant.buyer.organization_id), product
        )
    with pytest.raises(Forbidden):
        product_display.propose(
            runtime,
            tenant.supplier,
            command(runtime, tenant, product).model_copy(update={"target_id": uuid4()}),
        )
