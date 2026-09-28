import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, conversations, documents, platform_sales, quotes, source_limits
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from cloud_warehouse.pricing import SourcePrices
from shopping_agent import SearchFilters

from .test_advisor import session
from .test_api import PASSWORD
from .test_documents import parsed, proposal
from .test_documents import source as source
from .test_imports import excel_source as excel_source
from .test_price_books import publish, window
from .test_quotes import FUTURE, commit, contract, schedule
from .test_quotes import external_offer as external_offer
from .test_quotes import managed_offer as managed_offer
from .test_sync import Connector, batch


@pytest.fixture
def sales_policy(database):
    admin, _ = database
    platform_sales.configure(admin, enabled=True)
    try:
        yield
    finally:
        platform_sales.configure(admin, enabled=False)


def independent(admin):
    return platform_sales.create_advisor(admin, email=f"{uuid4()}@acme.example", password=PASSWORD)


async def test_independent_sales_account_reads_catalog_without_agency_or_grants(
    database, tenant, authentication, sales_policy
):
    admin, runtime = database
    actor = independent(admin)
    source = batch()
    query = f"ACME platform {uuid4().hex}"
    source.routes[0]["routeName"] = query
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source))
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM distribution_grant WHERE buyer_org_id=:id"),
                {"id": actor.organization_id},
            )
            == 0
        )
    backend = WarehouseAdvisorBackend(runtime, actor)
    assert any(
        p.attributes["supplier_id"] == str(tenant.supplier.organization_id)
        for p in await backend.search_products(session(actor), query)
    )
    with transaction(runtime, actor) as conn:
        assert conn.scalar(text("SELECT count(*) FROM source_snapshot")) == 0
        assert conn.scalar(text("SELECT count(*) FROM document_asset")) == 0
        assert conn.scalar(text("SELECT count(*) FROM document_parse")) == 0
        assert conn.scalar(text("SELECT count(*) FROM route_content_candidate")) == 0
    with transaction(runtime, actor) as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT credential_ref FROM supplier_connection"))
    with admin.connect() as conn:
        email = conn.scalar(
            text("SELECT email FROM warehouse_user WHERE id=:id"), {"id": actor.user_id}
        )
    with TestClient(create_app(runtime, authentication)) as client:
        login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        headers = {
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-Organization-Id": str(actor.organization_id),
        }
        assert client.get("/v1/advisor/context", headers=headers).json()["mode"] == "platform"
        assert client.get("/v1/advisor/products", headers=headers).status_code == 200
        assert client.get("/v1/merchant/routes", headers=headers).status_code == 403
        assert client.get("/v1/advisor/products").status_code == 401


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE supplier_connection SET active=false WHERE id=:source",
        "UPDATE organization SET active=false WHERE id=:supplier",
        "UPDATE warehouse_user SET active=false WHERE id=:user",
        "UPDATE membership SET active=false WHERE user_id=:user",
        "UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:user",
    ],
)
async def test_platform_access_is_live_and_revocable(database, tenant, sales_policy, mutation):
    admin, runtime = database
    actor = independent(admin)
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with transaction(runtime, actor) as conn:
        query = text("SELECT count(*) FROM supplier_product WHERE connection_id=:source")
        params = {
            "source": tenant.connection_id,
            "supplier": tenant.supplier.organization_id,
            "user": actor.user_id,
        }
        assert conn.scalar(query, params) == 1
        with admin.begin() as writer:
            writer.execute(text(mutation), params)
        assert conn.scalar(query, params) == 0


async def test_platform_grant_removal_does_not_hide_products_but_unlisting_does(
    database, tenant, sales_policy
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE id=:id"), {"id": tenant.grant_id}
        )
    with transaction(runtime, tenant.buyer) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM supplier_product WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
            == 1
        )
    for table, mutation in (
        ("departure", "source_quarantined=true"),
        ("supplier_product", "status='draft'"),
    ):
        with admin.begin() as conn:
            conn.execute(
                text(f"UPDATE {table} SET {mutation} WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
        with transaction(runtime, tenant.buyer) as conn:
            assert (
                conn.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE connection_id=:id"),
                    {"id": tenant.connection_id},
                )
                == 0
            )
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM offer WHERE connection_id=:id"),
                    {"id": tenant.connection_id},
                )
                == 0
            )


async def test_uniform_excel_price_ignores_customer_contract_and_quotes_remain_personal(
    database, tenant, managed_offer, sales_policy
):
    admin, runtime = database
    # Same space, different advisor; same idempotency key is allowed without sharing a quote.
    other_id = auth.create_user(
        admin, f"{uuid4()}@acme.example", PASSWORD, {tenant.buyer.organization_id: ["advisor"]}
    )
    other = Principal(other_id, tenant.buyer.organization_id)
    solo = independent(admin)
    publish(runtime, tenant.supplier, window(managed_offer, amount="100"))
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer, amount="70"))
    request = quotes.QuoteRequest(
        offer_id=managed_offer,
        departure_date=FUTURE,
        party=quotes.Party(adults=4, room_type="双人标准间"),
    )
    results = [
        await quotes.create(runtime, actor, request, "same-key")
        for actor in (tenant.buyer, other, solo)
    ]
    assert {r["settlement_total"] for r in results} == {"400.00"}
    assert {r["price_layer"] for r in results} == {"standard"}
    assert len({r["quote_id"] for r in results}) == 3
    for actor in (other, solo):
        with pytest.raises(Forbidden):
            quotes.get(runtime, actor, UUID(results[0]["quote_id"]))
    with transaction(runtime, solo) as conn:
        assert conn.scalar(text("SELECT count(*) FROM contract_price WHERE layer='contract'")) == 0
        saved = conn.execute(
            text("SELECT grant_id,sales_policy_version FROM quote_snapshot WHERE id=:id"),
            {"id": UUID(results[2]["quote_id"])},
        ).one()
        assert saved.grant_id is None and saved.sales_policy_version > 0


async def test_api_uniform_quote_works_without_buyer_binding_or_grant(
    database, tenant, external_offer, sales_policy
):
    admin, runtime = database
    actor = independent(admin)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_connection SET capabilities=capabilities||CAST(:policy AS jsonb) WHERE id=:id"
            ),
            {
                "id": tenant.connection_id,
                "policy": json.dumps({"settlement_pricing": {"mode": "uniform", "version": 2}}),
            },
        )
    calls = []

    class Source:
        async def read_uniform_prices(self, departure, company, version):
            await source_limits.check_or_record()
            calls.append(version)
            now = datetime.now(UTC)
            return SourcePrices(
                schedule=schedule("12700"),
                observed_at=now,
                expires_at=now + timedelta(minutes=3),
                source_ref="ACME API",
                available_seats=5,
            )

        async def read_prices(self, *args):
            raise AssertionError("Must not query buyer-specific prices")

    @asynccontextmanager
    async def connector():
        yield Source()

    request = quotes.QuoteRequest(
        offer_id=external_offer,
        departure_date=FUTURE,
        party=quotes.Party(adults=4, room_type="双人标准间"),
    )
    result = await quotes.create(
        runtime, actor, request, "uniform", {tenant.connection_id: connector}
    )
    assert calls == [2] and result["settlement_total"] == "50800.00"
    assert result["capacity_sufficient"] is True and "available_seats" not in result


async def test_platform_unconfigured_price_never_falls_back_to_buyer_price(
    database, tenant, external_offer, sales_policy
):
    _, runtime = database
    request = quotes.QuoteRequest(
        offer_id=external_offer, departure_date=FUTURE, party=quotes.Party()
    )
    result = await quotes.create(runtime, tenant.buyer, request, "missing-standard")
    assert result["known_settlement_subtotal"] is None
    assert result["missing_items"] == ["UNIFORM_PRICE_CONFIGURATION_MISSING"]


def test_policy_change_fences_old_conversation_and_is_not_runtime_writable(database, tenant):
    admin, runtime = database
    old = conversations.create(runtime, tenant.buyer, "advisor")
    platform_sales.configure(admin, enabled=True)
    try:
        with pytest.raises(Conflict):
            conversations.get(runtime, tenant.buyer, UUID(old["id"]))
        actor = independent(admin)
        own = conversations.create(runtime, actor, "advisor")
        with pytest.raises(Forbidden):
            conversations.get(runtime, tenant.buyer, UUID(own["id"]))
        with transaction(runtime, actor) as conn, pytest.raises(DBAPIError):
            conn.execute(text("UPDATE platform_sales_policy SET enabled=false"))
        with admin.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_proc p,LATERAL aclexplode(p.proacl) a WHERE p.proname='warehouse_sales_version' AND a.grantee=0"
                    )
                )
                == 0
            )
    finally:
        platform_sales.configure(admin, enabled=False)


def test_platform_exposes_published_content_but_never_supplier_original(
    database, tenant, source, sales_policy
):
    admin, runtime = database
    actor = independent(admin)
    store, product, asset, body = source
    documents.run_once(runtime, tenant.worker, store, parsed)
    result = commit(runtime, tenant.supplier, proposal(runtime, tenant, asset))
    public = documents.published(runtime, actor, UUID(result["document_id"]))
    assert public["body"]["days"]
    assert not {"source", "raw", "attachments", "pricing"}.intersection(public["body"])
    assert not {"asset_id", "parse_id", "field_sources", "review_note"}.intersection(public)
    assert documents.current(runtime, actor, product["id"])["body"] == public["body"]
    with pytest.raises(Forbidden):
        documents.download(runtime, actor, store, asset)
    assert documents.download(runtime, tenant.supplier, store, asset)["body"] == body


@pytest.mark.parametrize(
    "query",
    [
        "西班牙 葡萄牙",
        "西班牙+葡萄牙",
        "西班牙葡萄牙",
        "西葡",
        "西葡 线路",
        "西葡线路",
        "欧洲 出境游",
    ],
)
async def test_destination_search_returns_both_countries_not_just_one(database, tenant, query):
    _, runtime = database
    source = batch()
    source.routes[:] = [
        {**source.routes[0], "routeId": 1, "routeName": "ACME 西班牙+葡萄牙 12天"},
        {
            **source.routes[0],
            "routeId": 2,
            "routeCode": "ACME-R2",
            "routeName": "ACME 阳光西葡 13天",
        },
        {
            **source.routes[0],
            "routeId": 3,
            "routeCode": "ACME-R3",
            "routeName": "ACME 西班牙+意大利 15天",
        },
    ]
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source))
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    products = await backend.search_products(session(tenant.buyer), query)
    assert len(products) == (3 if query == "欧洲 出境游" else 2)
    assert backend.catalog_page(session(tenant.buyer), query=query, filters=None)["items"]


async def test_search_date_evidence_requires_matching_departure(database, tenant):
    _, runtime = database
    data = batch()
    data.routes[0]["routeName"] = "ACME 西班牙+葡萄牙"
    data.departures[0].update(departDate="2026-11-18", returnDate="2026-11-29")
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = session(tenant.buyer)
    unfiltered = await backend.search_products(context, "西葡")
    assert unfiltered[0].attributes["departure_date_check"] == "not_checked"
    dates = SearchFilters(attributes={"depart_from": "2026-11-11", "depart_to": "2026-11-20"})
    matched = await backend.search_products(context, "西葡", dates)
    assert len(matched) == 1
    assert matched[0].attributes["departure_date_check"] == "matched"
    assert matched[0].attributes["matched_depart_from"] == "2026-11-11"
    assert matched[0].attributes["matched_depart_to"] == "2026-11-20"
    dates.attributes["depart_from"] = "2026-11-19"
    assert await backend.search_products(context, "西葡", dates) == []
