from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, changes, management, price_books, quotes
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.merchant import WarehouseMerchantBackend
from cloud_warehouse.persistence import Principal, transaction
from merchant_agent.types import PriceUpdateItem

from .test_api import PASSWORD
from .test_imports import excel_source as excel_source
from .test_merchant import session
from .test_quotes import FUTURE, add_buyer, commit, schedule
from .test_quotes import external_offer as external_offer
from .test_quotes import managed_offer as managed_offer


def window(offer, *, layer="standard", buyer=None, grade=None, amount="100", **updates):
    now = datetime.now(UTC)
    return price_books.PriceProposal(
        **{
            "price_id": uuid4(),
            "offer_id": offer,
            "layer": layer,
            "buyer_org_id": buyer,
            "grade_key": grade,
            "expected_version": 0,
            "schedule": schedule(amount),
            "source_ref": "ACME approved schedule",
            "note": "ACME price policy",
            "valid_from": now - timedelta(hours=1),
            "valid_until": now + timedelta(days=2),
            **updates,
        }
    )


def publish(runtime, actor, command):
    return commit(runtime, actor, price_books.propose(runtime, actor, command))


async def quote(runtime, actor, offer, **party):
    return await quotes.create(
        runtime,
        actor,
        quotes.QuoteRequest(
            offer_id=offer,
            departure_date=FUTURE,
            party=quotes.Party(adults=1, room_type="双人标准间", **party),
        ),
        str(uuid4()),
    )


async def test_whole_schedule_priority_buyer_isolation_and_grade_version(
    database, tenant, managed_offer, excel_source
):
    admin, runtime = database
    other = add_buyer(admin, tenant, excel_source)
    standard = window(managed_offer)
    publish(runtime, tenant.supplier, standard)
    first = await quote(runtime, tenant.buyer, managed_offer)
    assert first["settlement_total"] == "100.00" and first["price_layer"] == "standard"
    grade = window(managed_offer, layer="grade", grade="ACME-A", amount="80")
    publish(runtime, tenant.supplier, grade)
    assert (await quote(runtime, tenant.buyer, managed_offer))["settlement_total"] == "100.00"
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM contract_price WHERE layer='grade'")) == 0
    membership = price_books.GradeProposal(
        connection_id=excel_source,
        buyer_org_id=tenant.buyer.organization_id,
        grade_key="ACME-A",
        expected_version=0,
        note="ACME buyer classification",
    )
    publish(runtime, tenant.supplier, membership)
    graded = await quote(runtime, tenant.buyer, managed_offer)
    assert graded["settlement_total"] == "80.00" and graded["price_layer"] == "grade"
    assert quotes.get(runtime, tenant.buyer, UUID(first["quote_id"]))["snapshot_stale"]
    assert (await quote(runtime, other, managed_offer))["settlement_total"] == "100.00"
    agreed = window(
        managed_offer, layer="contract", buyer=tenant.buyer.organization_id, amount="70"
    )
    publish(runtime, tenant.supplier, agreed)
    agreement = await quote(runtime, tenant.buyer, managed_offer)
    assert agreement["settlement_total"] == "70.00" and agreement["price_layer"] == "contract"
    changed = agreed.schedule.model_copy(deep=True)
    changed.settlement.child = None
    publish(
        runtime,
        tenant.supplier,
        agreed.model_copy(update={"expected_version": 1, "schedule": changed}),
    )
    incomplete = await quote(runtime, tenant.buyer, managed_offer, children=1, child_ages=[6])
    assert (
        incomplete["settlement_total"] is None
        and incomplete["known_settlement_subtotal"] == "70.00"
    )
    assert "settlement.child" in incomplete["missing_items"]
    publish(
        runtime, tenant.supplier, agreed.model_copy(update={"expected_version": 2, "active": False})
    )
    assert (await quote(runtime, tenant.buyer, managed_offer))["settlement_total"] == "80.00"
    publish(
        runtime,
        tenant.supplier,
        membership.model_copy(update={"expected_version": 1, "active": False}),
    )
    assert (await quote(runtime, tenant.buyer, managed_offer))["settlement_total"] == "100.00"
    publish(runtime, tenant.supplier, membership.model_copy(update={"expected_version": 2}))
    assert quotes.get(runtime, tenant.buyer, UUID(graded["quote_id"]))["snapshot_stale"]
    assert (await quote(runtime, other, managed_offer))["settlement_total"] == "100.00"


async def test_adjacent_windows_expiry_boundary_and_database_overlap(
    database, tenant, managed_offer
):
    _, runtime = database
    boundary = datetime.now(UTC) + timedelta(minutes=2)
    first = window(managed_offer, valid_until=boundary)
    second = window(managed_offer, amount="110", valid_from=boundary)
    publish(runtime, tenant.supplier, first)
    publish(runtime, tenant.supplier, second)
    result = await quote(runtime, tenant.buyer, managed_offer)
    assert datetime.fromisoformat(result["fresh_until"]) == boundary
    with transaction(runtime, tenant.buyer) as conn:
        chosen = conn.scalar(
            text("SELECT id FROM warehouse_effective_price(:offer,:buyer,:at)"),
            {"offer": managed_offer, "buyer": tenant.buyer.organization_id, "at": boundary},
        )
        assert chosen == second.price_id
    with pytest.raises(Conflict, match="重叠"):
        price_books.propose(runtime, tenant.supplier, window(managed_offer))
    with transaction(runtime, tenant.supplier) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("""INSERT INTO contract_price(id,supplier_org_id,connection_id,offer_id,layer,schedule,source_ref,valid_from,valid_until)
          SELECT :id,supplier_org_id,connection_id,offer_id,layer,schedule,source_ref,valid_from,valid_until FROM contract_price WHERE id=:existing"""),
            {"id": uuid4(), "existing": first.price_id},
        )


@pytest.mark.parametrize("revocation", ["grant", "connection", "supplier", "membership", "expiry"])
def test_all_price_layers_recheck_live_access_in_one_transaction(
    database, tenant, managed_offer, excel_source, revocation
):
    admin, runtime = database
    publish(runtime, tenant.supplier, window(managed_offer))
    publish(runtime, tenant.supplier, window(managed_offer, layer="grade", grade="ACME-A"))
    publish(
        runtime,
        tenant.supplier,
        window(managed_offer, layer="contract", buyer=tenant.buyer.organization_id),
    )
    publish(
        runtime,
        tenant.supplier,
        price_books.GradeProposal(
            connection_id=excel_source,
            buyer_org_id=tenant.buyer.organization_id,
            grade_key="ACME-A",
            expected_version=0,
            note="ACME classification for access test",
        ),
    )
    with transaction(runtime, tenant.buyer) as conn:
        query = text("SELECT layer FROM contract_price WHERE offer_id=:offer ORDER BY layer")
        assert conn.execute(query, {"offer": managed_offer}).scalars().all() == [
            "contract",
            "grade",
            "standard",
        ]
        with admin.begin() as control:
            statement, identifier = {
                "grant": (
                    "UPDATE distribution_grant SET active=false WHERE connection_id=:id",
                    excel_source,
                ),
                "connection": (
                    "UPDATE supplier_connection SET active=false WHERE id=:id",
                    excel_source,
                ),
                "supplier": (
                    "UPDATE organization SET active=false WHERE id=:id",
                    tenant.supplier.organization_id,
                ),
                "membership": (
                    "UPDATE membership SET active=false WHERE user_id=:id",
                    tenant.buyer.user_id,
                ),
                "expiry": (
                    "UPDATE distribution_grant SET valid_from=now()-interval '2 days',"
                    "expires_at=now()-interval '1 day' WHERE connection_id=:id",
                    excel_source,
                ),
            }[revocation]
            control.execute(text(statement), {"id": identifier})
        assert conn.execute(query, {"offer": managed_offer}).all() == []


def test_live_grant_preserves_supplier_review_of_disabled_source(database, tenant, excel_source):
    admin, runtime = database
    with admin.connect() as conn:
        identifier = conn.scalar(
            text("SELECT id FROM distribution_grant WHERE connection_id=:id"),
            {"id": excel_source},
        )
    check = text("SELECT warehouse_live_grant(:id)")
    with (
        transaction(runtime, tenant.supplier) as supplier,
        transaction(runtime, tenant.buyer) as buyer,
    ):
        assert supplier.scalar(check, {"id": identifier})
        assert buyer.scalar(check, {"id": identifier})
        with admin.begin() as control:
            control.execute(
                text("UPDATE supplier_connection SET active=false WHERE id=:id"),
                {"id": excel_source},
            )
        assert supplier.scalar(check, {"id": identifier})
        assert not buyer.scalar(check, {"id": identifier})
        with admin.begin() as control:
            control.execute(
                text("UPDATE distribution_grant SET active=false WHERE id=:id"),
                {"id": identifier},
            )
        assert not supplier.scalar(check, {"id": identifier})
        assert not buyer.scalar(check, {"id": identifier})


def test_concurrent_publications_conflict_without_partial_application(
    database, tenant, managed_offer
):
    admin, runtime = database
    proposals = [
        price_books.propose(runtime, tenant.supplier, window(managed_offer, amount=str(n)))
        for n in (100, 120)
    ]
    for item in proposals:
        changes.approve(runtime, tenant.supplier, UUID(item["id"]), item["payload_hash"])

    def apply(item):
        try:
            return changes.apply(runtime, tenant.supplier, UUID(item["id"]))
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, proposals))
    assert sum(result is not None for result in results) == 1
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM contract_price WHERE offer_id=:id"),
                {"id": managed_offer},
            )
            == 1
        )
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM change_request WHERE organization_id=:org AND status='applied' AND kind='price_book'"
                ),
                {"org": tenant.supplier.organization_id},
            )
            == 1
        )


@pytest.mark.parametrize("changed", ["grant", "source", "offer"])
def test_price_preview_invalidated_by_scope_change(
    database, tenant, managed_offer, excel_source, changed
):
    admin, runtime = database
    proposed = price_books.propose(
        runtime,
        tenant.supplier,
        window(managed_offer, layer="contract", buyer=tenant.buyer.organization_id),
    )
    with admin.begin() as conn:
        if changed == "grant":
            conn.execute(
                text("UPDATE distribution_grant SET version=version+1 WHERE connection_id=:id"),
                {"id": excel_source},
            )
        elif changed == "source":
            conn.execute(
                text("UPDATE supplier_connection SET version=version+1 WHERE id=:id"),
                {"id": excel_source},
            )
        else:
            conn.execute(
                text("UPDATE offer SET version=version+1 WHERE id=:id"), {"id": managed_offer}
            )
    with pytest.raises(Conflict):
        changes.approve(runtime, tenant.supplier, UUID(proposed["id"]), proposed["payload_hash"])


async def test_merchant_effective_price_and_explicit_contract_target(
    database, authentication, tenant, managed_offer
):
    _, runtime = database
    standard = window(managed_offer)
    publish(runtime, tenant.supplier, standard)
    backend = WarehouseMerchantBackend(
        runtime, tenant.supplier, buyer_org_id=tenant.buyer.organization_id
    )
    context = session(tenant.supplier)
    products = await backend.search_listings(context, "")
    assert products[0].price == 100
    agreed = window(
        managed_offer, layer="contract", buyer=tenant.buyer.organization_id, amount="90"
    )
    publish(runtime, tenant.supplier, agreed)
    saved = management.contract(
        runtime, tenant.supplier, managed_offer, tenant.buyer.organization_id
    )["contract"]
    assert saved["id"] == agreed.price_id
    products = await backend.search_listings(context, "")
    detail = await backend.get_listing(context, products[0].listing_id)
    proposal = await backend.stage_price_update(
        context,
        [PriceUpdateItem(listing_id=detail.variants[0].listing_id, new_price=95)],
        "ACME scoped adjustment",
    )
    changes.approve(runtime, tenant.supplier, UUID(proposal.change_id), proposal.payload_hash)
    changes.apply(runtime, tenant.supplier, UUID(proposal.change_id))
    assert (await quote(runtime, tenant.buyer, managed_offer))["settlement_total"] == "95.00"


def test_http_price_book_permissions_pagination_and_roles(
    database, authentication, tenant, managed_offer, excel_source
):
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
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        first = window(managed_offer)
        proposed = client.post(
            "/v1/merchant/price-books/proposals",
            headers=headers,
            json=first.model_dump(mode="json"),
        )
        assert proposed.status_code == 201
        item = proposed.json()
        assert (
            client.post(
                f"/v1/changes/{item['id']}/approve",
                headers=headers,
                json={"payload_hash": item["payload_hash"]},
            ).status_code
            == 403
        )
        commit(runtime, tenant.supplier, item)
        second = window(managed_offer, layer="grade", grade="ACME-B")
        publish(runtime, tenant.supplier, second)
        page = client.get(
            f"/v1/merchant/price-books?offer_id={managed_offer}&limit=1", headers=headers
        ).json()
        tail = client.get(
            f"/v1/merchant/price-books?offer_id={managed_offer}&limit=1&after={page['next_cursor']}",
            headers=headers,
        ).json()
        assert len(page["items"] + tail["items"]) == 2 and tail["next_cursor"] is None
        assert (
            client.get(
                f"/v1/merchant/price-books?offer_id={managed_offer}&after={uuid4()}",
                headers=headers,
            ).status_code
            == 403
        )
        assert client.get(f"/v1/merchant/price-books?offer_id={managed_offer}").status_code == 401
        buyer = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        assert (
            client.get(
                f"/v1/merchant/price-books?offer_id={managed_offer}", headers=buyer
            ).status_code
            == 403
        )
        grade = price_books.GradeProposal(
            connection_id=excel_source,
            buyer_org_id=tenant.buyer.organization_id,
            grade_key="ACME-B",
            expected_version=0,
            note="ACME grade",
        )
        assert (
            client.post(
                "/v1/merchant/buyer-grades/proposals",
                headers=headers,
                json=grade.model_dump(mode="json"),
            ).status_code
            == 403
        )
    with transaction(runtime, Principal(user, tenant.buyer.organization_id)) as conn:
        assert conn.scalar(text("SELECT count(*) FROM contract_price WHERE layer='grade'")) == 0


def test_approval_before_state_is_server_captured_and_cannot_be_forged(
    database, authentication, tenant, managed_offer, excel_source
):
    _, runtime = database
    original = window(managed_offer)
    publish(runtime, tenant.supplier, original)
    edited = original.model_copy(update={"expected_version": 1, "schedule": schedule("105")})
    proposal = price_books.propose(runtime, tenant.supplier, edited)
    assert Decimal(proposal["preview"]["before"]["schedule"]["settlement"]["adult"]) == 100
    forged = {**proposal["preview"], "before": None}
    with pytest.raises(Conflict, match="修改前"):
        changes.stage(runtime, tenant.supplier, "price_book", forged)
    with transaction(runtime, tenant.supplier) as conn:
        item = {"kind": "price_book", "payload": proposal["preview"]}
        management.label_changes(conn, [item], authentication)
        assert "ACME 环线" in item["target_label"]
    grade = price_books.GradeProposal(
        connection_id=excel_source,
        buyer_org_id=tenant.buyer.organization_id,
        grade_key="ACME-A",
        expected_version=0,
        note="ACME classification",
    )
    publish(runtime, tenant.supplier, grade)
    updated = price_books.propose(
        runtime,
        tenant.supplier,
        grade.model_copy(update={"expected_version": 1, "grade_key": "ACME-B"}),
    )
    assert updated["preview"]["before"] == {"grade_key": "ACME-A", "active": True}


@pytest.mark.parametrize("resource", ["price", "grade"])
def test_price_book_outbox_failure_rolls_back_all_writes(
    database, tenant, managed_offer, excel_source, resource
):
    from sqlalchemy import event

    admin, runtime = database
    command = (
        window(managed_offer)
        if resource == "price"
        else price_books.GradeProposal(
            connection_id=excel_source,
            buyer_org_id=tenant.buyer.organization_id,
            grade_key="ACME-A",
            expected_version=0,
            note="ACME classification",
        )
    )
    proposed = price_books.propose(runtime, tenant.supplier, command)
    identifier = UUID(proposed["id"])
    changes.approve(runtime, tenant.supplier, identifier, proposed["payload_hash"])

    def fail(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO outbox_event" in statement:
            raise RuntimeError("ACME outbox unavailable")

    event.listen(runtime, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="outbox"):
            changes.apply(runtime, tenant.supplier, identifier)
    finally:
        event.remove(runtime, "before_cursor_execute", fail)
    with admin.connect() as conn:
        for table in ("contract_price", "buyer_price_grade"):
            assert (
                conn.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE supplier_org_id=:org"),
                    {"org": tenant.supplier.organization_id},
                )
                == 0
            )
        assert (
            conn.scalar(text("SELECT status FROM change_request WHERE id=:id"), {"id": identifier})
            == "staged"
        )
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM audit_event WHERE resource_id=:id AND action='price_book.applied'"
                ),
                {"id": identifier},
            )
            == 0
        )
    first = changes.apply(runtime, tenant.supplier, identifier)
    assert changes.apply(runtime, tenant.supplier, identifier) == first


def test_existing_price_identity_cannot_be_retargeted(database, tenant, managed_offer):
    from cloud_warehouse.persistence import Forbidden

    _, runtime = database
    original = window(managed_offer)
    publish(runtime, tenant.supplier, original)
    with pytest.raises(Forbidden, match="适用对象"):
        price_books.propose(
            runtime,
            tenant.supplier,
            original.model_copy(
                update={"expected_version": 1, "layer": "grade", "grade_key": "ACME-A"}
            ),
        )


async def test_api_price_authority_cannot_be_overridden(database, tenant, external_offer):
    _, runtime = database
    for command in (
        window(external_offer),
        price_books.GradeProposal(
            connection_id=tenant.connection_id,
            buyer_org_id=tenant.buyer.organization_id,
            grade_key="ACME-A",
            expected_version=0,
            note="ACME classification",
        ),
    ):
        with pytest.raises(Conflict, match="API"):
            price_books.propose(runtime, tenant.supplier, command)
