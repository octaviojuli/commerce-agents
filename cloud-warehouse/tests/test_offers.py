from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import changes, inventory, offers, price_books, quotes
from cloud_warehouse.advisor import WarehouseAdvisorBackend, departure_id
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, transaction
from shopping_agent import NotOffered

from .test_advisor import session
from .test_imports import excel_source as excel_source
from .test_price_books import window
from .test_quotes import commit
from .test_quotes import external_offer as external_offer
from .test_quotes import managed_offer as managed_offer


def command(runtime, actor, offer, **values):
    with transaction(runtime, actor) as conn:
        departure = conn.scalar(text("SELECT departure_id FROM offer WHERE id=:id"), {"id": offer})
    return offers.Command(
        **{
            "offer_id": uuid4(),
            "departure_id": departure,
            "code": "comfort",
            "name": "ACME 舒适方案",
            "service_description": "ACME 升级住宿，名额共用",
            "active": True,
            "expected_version": 0,
            "note": "ACME 方案验收",
            **values,
        }
    )


def publish(runtime, actor, body):
    return commit(runtime, actor, offers.propose(runtime, actor, body))


async def test_offer_choices_share_inventory_and_require_explicit_selection(
    database, tenant, managed_offer
):
    admin, runtime = database
    added = command(runtime, tenant.supplier, managed_offer)
    publish(runtime, tenant.supplier, added)
    for identifier, amount in [(managed_offer, "100"), (added.offer_id, "150")]:
        commit(
            runtime,
            tenant.supplier,
            price_books.propose(runtime, tenant.supplier, window(identifier, amount=amount)),
        )
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = session(tenant.buyer)
    identifier = departure_id(added.departure_id)
    page = backend.offers_page(context, identifier, limit=1)
    tail = backend.offers_page(context, identifier, limit=1, after=UUID(page["next_cursor"]))
    all_offers = page["items"] + tail["items"]
    assert len(all_offers) == 2 and tail["next_cursor"] is None
    assert len({row["inventory_pool_id"] for row in all_offers}) == 1
    assert all_offers[0]["inventory_pool_id"] is not None
    with pytest.raises(Conflict, match="多个报价方案"):
        await backend.quote_departure(context, identifier, quotes.Party(adults=1))
    results = [
        await backend.quote_departure(
            context, identifier, quotes.Party(adults=1, room_type="双人标准间"), offer_id=identity
        )
        for identity in (managed_offer, added.offer_id)
    ]
    assert [r["settlement_total"] for r in results] == ["100.00", "150.00"]
    assert (
        results[1]["offer_name"] == added.name
        and results[1]["service_description"] == added.service_description
    )
    assert len({r["inventory_pool_id"] for r in results}) == 1
    assert [r["availability"] for r in results] == ["available", "available"]
    pool = UUID(results[0]["inventory_pool_id"])
    with admin.connect() as conn:
        version = conn.scalar(text("SELECT version FROM inventory_pool WHERE id=:id"), {"id": pool})
    sale = inventory.InventoryCommand(
        target_id=pool,
        expected_version=version,
        action="sale",
        quantity=2,
        business_key="ACME-shared-sale",
        reason="ACME one sale",
    )
    commit(
        runtime,
        tenant.supplier,
        changes.stage(runtime, tenant.supplier, "inventory", sale.model_dump(mode="json")),
    )
    refreshed = [
        await backend.quote_departure(
            context, identifier, quotes.Party(adults=1, room_type="双人标准间"), offer_id=identity
        )
        for identity in (managed_offer, added.offer_id)
    ]
    assert [r["availability"] for r in refreshed] == ["available", "available"]
    publish(
        runtime,
        tenant.supplier,
        added.model_copy(update={"expected_version": 1, "name": "ACME 修订方案"}),
    )
    assert quotes.get(runtime, tenant.buyer, UUID(results[1]["quote_id"]))["snapshot_stale"]
    with pytest.raises(NotOffered):
        await backend.quote_departure(context, identifier, quotes.Party(adults=1), offer_id=uuid4())


def test_concurrent_same_code_and_before_state_integrity(database, tenant, managed_offer):
    admin, runtime = database
    first = command(runtime, tenant.supplier, managed_offer)
    second = first.model_copy(update={"offer_id": uuid4()})
    staged = [offers.propose(runtime, tenant.supplier, c) for c in (first, second)]
    for row in staged:
        changes.approve(runtime, tenant.supplier, UUID(row["id"]), row["payload_hash"])

    def apply(row):
        try:
            return changes.apply(runtime, tenant.supplier, UUID(row["id"]))
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply, staged))
    assert sum(row is not None for row in results) == 1
    saved = first if results[0] else second
    edit = saved.model_copy(update={"expected_version": 1, "active": False})
    proposal = offers.propose(runtime, tenant.supplier, edit)
    assert proposal["preview"]["before"]["active"] is True
    with pytest.raises(Conflict, match="预览"):
        changes.stage(runtime, tenant.supplier, "offer", {**proposal["preview"], "before": None})
    publish(runtime, tenant.supplier, edit)
    assert len(offers.list_for_departure(runtime, tenant.buyer, saved.departure_id)["items"]) == 1
    assert (
        len(
            offers.list_for_departure(runtime, tenant.supplier, saved.departure_id, merchant=True)[
                "items"
            ]
        )
        == 2
    )
    publish(
        runtime, tenant.supplier, edit.model_copy(update={"expected_version": 2, "active": True})
    )


@pytest.mark.parametrize("resource", ["source", "product", "departure"])
def test_preview_scope_conflicts(database, tenant, managed_offer, resource):
    admin, runtime = database
    body = command(runtime, tenant.supplier, managed_offer)
    proposed = offers.propose(runtime, tenant.supplier, body)
    with admin.begin() as conn:
        if resource == "departure":
            conn.execute(
                text("UPDATE departure SET version=version+1 WHERE id=:id"),
                {"id": body.departure_id},
            )
        elif resource == "product":
            conn.execute(
                text(
                    "UPDATE supplier_product SET version=version+1 WHERE id=(SELECT product_id FROM departure WHERE id=:id)"
                ),
                {"id": body.departure_id},
            )
        else:
            conn.execute(
                text(
                    "UPDATE supplier_connection SET version=version+1 WHERE id=(SELECT connection_id FROM departure WHERE id=:id)"
                ),
                {"id": body.departure_id},
            )
    with pytest.raises(Conflict):
        changes.approve(runtime, tenant.supplier, UUID(proposed["id"]), proposed["payload_hash"])


async def test_api_authority_roles_and_outbox_rollback(
    database, tenant, managed_offer, external_offer
):
    admin, runtime = database
    with transaction(runtime, tenant.supplier) as conn:
        external_offer = conn.scalar(
            text("SELECT id FROM offer WHERE connection_id=:id LIMIT 1"),
            {"id": tenant.connection_id},
        )
    with pytest.raises(Conflict, match="API"):
        offers.propose(runtime, tenant.supplier, command(runtime, tenant.supplier, external_offer))
    body = command(runtime, tenant.supplier, managed_offer)
    with pytest.raises(Forbidden):
        offers.propose(runtime, tenant.buyer, body)
    with pytest.raises(Forbidden):
        offers.list_for_departure(runtime, tenant.buyer, body.departure_id, merchant=True)
    with pytest.raises(Forbidden):
        offers.list_for_departure(
            runtime, tenant.supplier, body.departure_id, merchant=True, after=uuid4()
        )
    staged = offers.propose(runtime, tenant.supplier, body)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])

    def fail(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO outbox_event" in statement:
            raise RuntimeError("ACME outbox failure")

    event.listen(runtime, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    finally:
        event.remove(runtime, "before_cursor_execute", fail)
    with admin.connect() as conn:
        assert (
            conn.scalar(text("SELECT count(*) FROM offer WHERE id=:id"), {"id": body.offer_id}) == 0
        )
        assert (
            conn.scalar(
                text("SELECT status FROM change_request WHERE id=:id"), {"id": UUID(staged["id"])}
            )
            == "staged"
        )
    changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
    with transaction(runtime, tenant.supplier) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("UPDATE offer SET inventory_pool_id=:pool WHERE id=:id"),
            {"pool": uuid4(), "id": body.offer_id},
        )
