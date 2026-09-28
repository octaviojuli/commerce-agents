import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth
from cloud_warehouse.admin import set_short_name
from cloud_warehouse.advisor import WarehouseAdvisorBackend, departure_id
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.integrations import CatalogBatch
from cloud_warehouse.persistence import Forbidden
from cloud_warehouse.quotes import Party
from commerce_common.presentation import EnrichmentContext
from commerce_common.testing import FakeClient, text_message, tool_use_message
from shopping_agent import NotOffered, SearchFilters, ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState
from tour.api.warehouse_agent import DeparturePage, DepartureQuote, build_agent, departures, quote

from .test_api import PASSWORD
from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer
from .test_sync import Connector, batch


def session(actor):
    return ShoppingSessionContext(session_id=str(uuid4()), user_id=str(actor.user_id))


async def test_advisor_flags_source_duration_conflict_without_rewriting_dates(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(batch()))
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET days=3 WHERE connection_id=:id"),
            {"id": tenant.connection_id},
        )
        conn.execute(
            text("UPDATE departure SET return_date=depart_date+3 WHERE connection_id=:id"),
            {"id": tenant.connection_id},
        )
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    product = (await backend.search_products(context, "ACME"))[0]
    departure = backend.departures_page(context, product.product_id)["items"][0]
    assert departure.attributes["route_days"] == "3"
    assert departure.attributes["calendar_days"] == "4"
    assert departure.attributes["duration_check"] == "mismatch_requires_confirmation"


async def test_advisor_pagination_unknown_stock_and_revocation(database, tenant):
    admin, runtime = database
    source = batch()
    multiple = CatalogBatch(
        source.routes,
        [{**source.departures[0], "periodId": n, "periodCode": f"ACME-D{n}"} for n in range(1, 62)],
    )
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(multiple))
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    products = await backend.search_products(context, "ACME")
    assert len(products) == 1 and products[0].price is None and products[0].in_stock is None
    assert products[0].attributes["source_name"] == "B2B catalog"
    details = await backend.get_product_details(context, products[0].product_id)
    assert details.variants == [] and details.options == {"团期": []}
    assert "departure_has_more" not in details.attributes
    seen, cursor = set(), None
    while True:
        page = backend.departures_page(context, products[0].product_id, after=cursor, limit=3)
        ids = {row.product_id for row in page["items"]}
        assert not seen.intersection(ids)
        seen.update(ids)
        if not page["next_cursor"]:
            break
        cursor = page["next_cursor"]
    assert len(seen) == 61  # 21 pages are traversable; no hidden 20-page cap.
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE departure SET availability_expires_at=now()-interval '1 minute' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    stale = await backend.get_product_details(context, next(iter(seen)))
    assert (
        stale.in_stock is None
        and stale.attributes["availability"] == "unknown"
        and "available_seats" not in stale.attributes
    )
    with pytest.raises(NotOffered):
        await backend.get_product_details(context, "DP-2")
    with pytest.raises(NotOffered):
        await backend.search_products(context, "", SearchFilters(max_price=1))
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE id=:id"), {"id": tenant.grant_id}
        )
    assert await backend.search_products(context, "") == []
    assert await backend.get_product_details(context, next(iter(seen))) is None


async def test_advisor_quote_and_disabled_transaction_paths(database, tenant, managed_offer):
    _, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    dep = list_departures(runtime, tenant.buyer)[0]
    before = dep["available_seats"]
    result = await backend.quote_departure(
        context, departure_id(dep["id"]), Party(adults=2), idempotency_key="ACME-advisor-quote"
    )
    assert result["settlement_total"] == "200.00" and result["market_total"] == "240.00"
    assert result["product_name"] and result["departure_code"] and result["source_name"]
    assert (
        result["reservation_created"] is False
        and result["availability"] == "available"
        and "available_seats" not in result
    )
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == before
    for call in (
        backend.add_to_cart(context, departure_id(dep["id"]), 2),
        backend.update_cart_item(context, departure_id(dep["id"]), 2),
        backend.remove_from_cart(context, departure_id(dep["id"])),
        backend.checkout_handoff(context, await backend.get_cart(context)),
    ):
        with pytest.raises(NotOffered, match="未开放"):
            await call
    with pytest.raises(Forbidden):
        await backend.search_products(session(tenant.supplier), "")


async def test_advisor_tools_are_grounded_and_transactions_absent(database, tenant, managed_offer):
    _, runtime = database
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    from datetime import date

    from cloud_warehouse import conversations, trip_brief

    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    context = ShoppingSessionContext(session_id=str(identifier), user_id=str(tenant.buyer.user_id))
    trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(
            expected_version=0,
            fields={
                "destinations": ["ACME"],
                "window": {"start": date.today().isoformat(), "end": "2030-01-01"},
                "adults": 2,
                "children": 0,
                "rooms": {"doubles": 1},
            },
        ),
    )
    agent = build_agent(
        backend, client=object(), config={"enable_cart": True, "enable_orders": True}
    )
    names = {tool["name"] for tool in agent._tools}
    assert not names.intersection(
        {
            "add_to_cart",
            "update_cart_item",
            "remove_from_cart",
            "checkout",
            "get_orders",
            "get_order",
        }
    )
    assert {"present_warehouse_quote", "present_warehouse_departures"} <= names
    state = ShoppingSessionState()
    enrichment = EnrichmentContext(
        backend=backend, session=context, state=state, config=agent.config
    )
    route = (await backend.search_products(context, ""))[0]
    work = conversations.begin(runtime, tenant.buyer, identifier, "search", "查 ACME 线路")
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [
            {
                "type": "ui",
                "data": {
                    "component": "warehouse_routes",
                    "payload": {"items": [route.model_dump(mode="json")]},
                },
            }
        ],
    )
    work = conversations.begin(runtime, tenant.buyer, identifier, "choose", "第一条，看看团期")
    with pytest.raises(ValueError):
        await departures(DeparturePage(product_id=route.product_id), enrichment)
    state.remember_products([route])
    result = await departures(DeparturePage(product_id=route.product_id), enrichment)
    dep_id = result["items"][0]["product_id"]
    assert dep_id in state.seen_products
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [{"type": "ui", "data": {"component": "warehouse_departures", "payload": result}}],
    )
    work = conversations.begin(runtime, tenant.buyer, identifier, "quote", "第一期，请询价")
    with pytest.raises(ValueError):
        await quote(DepartureQuote(departure_id="WD-" + str(uuid4())), enrichment)
    quoted = await quote(DepartureQuote(departure_id=dep_id), enrichment)
    assert quoted["complete"] is False and quoted["settlement_total"] is None
    conversations.finish(
        runtime, tenant.buyer, work, state.model_dump(mode="json"), work["messages"], []
    )


def test_advisor_http_uses_platform_identity(database, authentication, tenant, managed_offer):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        rows = client.get("/v1/advisor/products", headers=headers).json()["items"]
        assert len(rows) == 1 and rows[0]["price"] is None
        result = client.get(
            "/v1/advisor/departures", params={"product_id": rows[0]["product_id"]}, headers=headers
        ).json()
        dep_id = result["items"][0]["product_id"]
        quote_response = client.post(
            "/v1/advisor/quotes",
            headers={**headers, "Idempotency-Key": "ACME-api-quote"},
            json={"departure_id": dep_id, "party": {"adults": 2}},
        )
        assert quote_response.status_code == 201, quote_response.text
        assert quote_response.json()["reservation_created"] is False
        assert (
            client.get(
                "/v1/advisor/products",
                headers={**headers, "X-Organization-Id": str(tenant.supplier.organization_id)},
            ).status_code
            == 403
        )
        assert client.get("/v1/advisor/products").status_code == 401


async def test_advisor_http_pages_preserve_unknown_values_variants_and_cursors(
    database, authentication, tenant
):
    admin, runtime = database
    data = batch()
    data.routes.append({**data.routes[0], "routeId": 9, "routeName": "ACME 第二线路"})
    data.departures[0]["availableSeats"] = None
    data.departures.append({**data.departures[0], "periodId": 3, "periodCode": "ACME-D3"})
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        client.headers.update(
            {
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(tenant.buyer.organization_id),
            }
        )
        response = client.get("/v1/advisor/products", params={"limit": 1})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"] == "application/json"
        first = response.json()
        second = client.get(
            "/v1/advisor/products", params={"limit": 1, "after": first["next_cursor"]}
        ).json()
        assert second["next_cursor"] is None
        products = first["items"] + second["items"]
        assert len({p["product_id"] for p in products}) == 2
        for product in products:
            assert product["price"] is None and product["in_stock"] is None
            assert product["brand"] is None and product["short_description"] is None
            assert product["labels"] == [] and product["options"] == {"团期": []}
            assert product["attributes"]["supplier_id"] == str(tenant.supplier.organization_id)
        route = next(p for p in products if p["title"] == "ACME 环线")
        parameters = {"product_id": route["product_id"], "limit": 1}
        first = client.get("/v1/advisor/departures", params=parameters).json()
        second = client.get(
            "/v1/advisor/departures", params={**parameters, "after": first["next_cursor"]}
        ).json()
        assert first["next_cursor"] and second["next_cursor"] is None
        assert first["items"][0]["product_id"] != second["items"][0]["product_id"]
        for page in (first, second):
            assert page["ordering"] == "window_date_id" and page["reservation_created"] is False
            item = page["items"][0]
            assert item["variant_of"] == route["product_id"]
            assert item["price"] is None and item["in_stock"] is None
            assert (
                item["attributes"]["availability"] == "unknown"
                and "available_seats" not in item["attributes"]
            )
            assert item["option_values"]["团期"] in {"ACME-D2", "ACME-D3"}


def test_original_advisor_runtime_streams_cloud_data_and_persists_provenance(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    from cloud_warehouse.persistence import Principal

    actor = Principal(user, tenant.buyer.organization_id)
    backend = WarehouseAdvisorBackend(runtime, actor)
    route = backend.catalog_page(session(actor))["items"][0]
    responses = [
        tool_use_message(
            "update_trip_brief",
            {
                "expected_version": 0,
                "fields": {
                    "destinations": {"value": ["ACME"], "source": "said", "evidence": "ACME"},
                    "window": {
                        "value": {"start": str(FUTURE), "end": str(FUTURE)},
                        "source": "said",
                        "evidence": str(FUTURE),
                    },
                },
            },
        ),
        tool_use_message("search_routes", {}),
        tool_use_message("get_product_details", {"product_id": route.product_id}),
        tool_use_message(
            "present_warehouse_departures", {"product_id": route.product_id, "limit": 3}
        ),
        text_message("请先选择一条线路。"),
        tool_use_message(
            "present_warehouse_departures", {"product_id": route.product_id, "limit": 3}
        ),
        text_message("已读取所选线路的团期。"),
    ]
    fake = FakeClient(responses)
    app = create_app(
        runtime,
        authentication,
        agent_factory=lambda role, principal, buyer: build_agent(
            WarehouseAdvisorBackend(runtime, principal), client=fake
        ),
    )
    with TestClient(app) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        identifier = client.post(
            "/v1/conversations", headers=headers, json={"role": "advisor"}
        ).json()["id"]
        result = client.post(
            f"/v1/conversations/{identifier}/chat",
            headers={**headers, "Idempotency-Key": "ACME-real-runtime"},
            json={"message": f"查询 ACME {FUTURE} 的线路"},
        )

        def cards(response):
            return [
                json.loads(chunk.split("data: ", 1)[1])["component"]
                for chunk in response.text.split("\n\n")
                if chunk.startswith("event: ui\n")
            ]

        assert "warehouse_routes" in cards(result) and "event: turn_complete" in result.text
        assert "warehouse_departures" not in cards(result)
        assert "event: error" not in result.text
        saved = client.get(f"/v1/conversations/{identifier}", headers=headers).json()
        assert saved["turns"][0]["status"] == "complete"
        with admin.connect() as conn:
            state = conn.scalar(
                text("SELECT state FROM agent_conversation WHERE id=:id"), {"id": UUID(identifier)}
            )
        assert route.product_id in state["seen_products"]
        assert not any(key.startswith("WD-") for key in state["seen_products"])
        result = client.post(
            f"/v1/conversations/{identifier}/chat",
            headers={**headers, "Idempotency-Key": "ACME-human-choice"},
            json={"message": "第一条，看看团期"},
        )
        assert cards(result).count("warehouse_departures") == 1
        assert "event: error" not in result.text
        with admin.connect() as conn:
            state = conn.scalar(
                text("SELECT state FROM agent_conversation WHERE id=:id"), {"id": UUID(identifier)}
            )
        assert any(key.startswith("WD-") for key in state["seen_products"])


@pytest.mark.parametrize("filtered", [False, True])
async def test_catalog_merges_complete_source_pages_before_and_after_revocation(
    database, tenant, filtered
):
    from uuid import uuid5

    from cloud_warehouse.admin import onboard
    from cloud_warehouse.persistence import Principal

    admin, runtime = database
    sources = [(tenant.worker, tenant.connection_id)]
    for _ in range(2):
        ids = onboard(admin, "ACME Other Supplier", "ACME Other Buyer", f"{uuid4()}@acme.example")
        sources.append(
            (
                Principal(UUID(ids["worker_id"]), UUID(ids["supplier_id"])),
                UUID(ids["connection_id"]),
            )
        )
    second_worker, second_connection = sources[1]
    with admin.begin() as conn:
        conn.execute(
            text("""INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id)
            VALUES(:id,:supplier,:buyer,:connection)"""),
            {
                "id": uuid4(),
                "supplier": second_worker.organization_id,
                "buyer": tenant.buyer.organization_id,
                "connection": second_connection,
            },
        )
    expected = {}
    for worker, connection in sources:
        routes = [
            {
                "routeId": n,
                "routeCode": f"ACME-R{n}",
                "routeName": "ACME match" if n % 7 == 0 else "ACME other",
                "days": 3 if n % 2 else 2,
                "departCityName": "ACME Gateway",
            }
            for n in range(1, 48)
        ]
        await synchronize(runtime, worker, connection, Connector(CatalogBatch(routes, [])))
        if connection != sources[2][1]:
            expected[connection] = {
                uuid5(connection, f"route:{row['routeId']}")
                for row in routes
                if not filtered or (row["routeId"] % 7 == 0 and row["days"] == 3)
            }
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    kwargs = {
        "query": "ACME match" if filtered else "",
        "filters": SearchFilters(attributes={"days": "3", "depart_city": "ACME Gateway"})
        if filtered
        else None,
        "limit": 1 if filtered else 3,
    }

    def collect(after=None):
        seen = []
        while True:
            page = backend.catalog_page(context, after=after, **kwargs)
            seen.extend(UUID(p.product_id.removeprefix("WP-")) for p in page["items"])
            if not page["next_cursor"]:
                return seen
            next_cursor = UUID(page["next_cursor"])
            assert after is None or next_cursor > after
            after = next_cursor

    all_ids = sorted(set.union(*expected.values()))
    assert collect() == all_ids  # Includes more than twenty global pages, interleaved sources.
    first = backend.catalog_page(context, **kwargs)
    cursor = UUID(first["next_cursor"])
    with admin.begin() as conn:
        conn.execute(
            text("""UPDATE distribution_grant SET active=false
              WHERE connection_id=:connection AND buyer_org_id=:buyer"""),
            {"connection": second_connection, "buyer": tenant.buyer.organization_id},
        )
    assert collect(cursor) == sorted(i for i in expected[tenant.connection_id] if i > cursor)


async def test_catalog_source_window_preserves_owner_reads(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE membership SET roles=roles||ARRAY['advisor'] WHERE user_id=:id"),
            {"id": tenant.supplier.user_id},
        )
        # Supplier review of its own published data does not require an active buyer grant.
        conn.execute(
            text("UPDATE supplier_connection SET active=false WHERE id=:id"),
            {"id": tenant.connection_id},
        )
    owner = WarehouseAdvisorBackend(runtime, tenant.supplier)
    assert len(owner.catalog_page(session(tenant.supplier))["items"]) == 1
    buyer = WarehouseAdvisorBackend(runtime, tenant.buyer)
    assert buyer.catalog_page(session(tenant.buyer))["items"] == []


@pytest.mark.parametrize("bounds", [(None, None), (2, None), (None, 3), (2, 3), (2, 2)])
async def test_catalog_and_departure_filter_shapes_preserve_boundaries(database, tenant, bounds):
    from datetime import date
    from uuid import uuid5

    _, runtime = database
    routes = [
        {
            "routeId": n,
            "routeCode": f"ACME-{n}",
            "routeName": "ACME key%_!" if n == 1 else f"ACME route {n}",
            "days": n + 1,
            "departCityName": "ACME Gateway",
        }
        for n in (1, 2, 3)
    ]
    departures = [
        {
            "periodId": n,
            "routeId": 1 if n % 2 else 2,
            "periodCode": f"ACME-D{n}",
            "departDate": f"2026-10-0{n}",
            "returnDate": f"2026-10-0{n}",
            "availableSeats": n,
        }
        for n in (1, 2, 3, 4)
    ]
    await synchronize(
        runtime, tenant.worker, tenant.connection_id, Connector(CatalogBatch(routes, departures))
    )
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    start, end = (date(2026, 10, n) if n else None for n in bounds)
    expected = [
        row
        for row in departures
        if (bounds[0] is None or row["periodId"] >= bounds[0])
        and (bounds[1] is None or row["periodId"] <= bounds[1])
    ]

    def product(n):
        return uuid5(tenant.connection_id, f"route:{n}")

    def departure(n):
        return uuid5(tenant.connection_id, f"departure:{n}")

    attrs = {}
    if start:
        attrs["depart_from"] = start.isoformat()
    if end:
        attrs["depart_to"] = end.isoformat()
    matched_routes = {row["routeId"] for row in expected} if attrs else {1, 2, 3}
    page = backend.catalog_page(context, query="  ", filters=SearchFilters(attributes=attrs))
    assert [item.product_id for item in page["items"]] == [
        f"WP-{value}" for value in sorted(product(n) for n in matched_routes)
    ]
    filtered = backend.catalog_page(
        context,
        query="key%_!",
        filters=SearchFilters(attributes={**attrs, "days": "2", "depart_city": "ACME Gateway"}),
    )
    assert [item.product_id for item in filtered["items"]] == (
        [f"WP-{product(1)}"] if 1 in matched_routes else []
    )
    assert backend.catalog_page(context, query="' OR true --")["items"] == []
    raw = list_departures(runtime, tenant.buyer, start=start, end=end, offset=1, limit=2)
    assert [row["id"] for row in raw] == [departure(row["periodId"]) for row in expected[1:3]]
    for route in (1, 2, 3):
        wanted = [departure(row["periodId"]) for row in expected if row["routeId"] == route]
        seen, cursor = [], None
        while True:
            page = backend.departures_page(
                context, f"WP-{product(route)}", start=start, end=end, limit=1, after=cursor
            )
            seen.extend(UUID(item.product_id.removeprefix("WD-")) for item in page["items"])
            if not page["next_cursor"]:
                break
            cursor = page["next_cursor"]
        assert seen == wanted
        with backend._read(context) as conn:
            rows = backend._departures(
                conn, parent=product(route), departure=departure(1), start=start, end=end
            )
            assert [row["id"] for row in rows] == ([departure(1)] if departure(1) in wanted else [])


async def test_advisors_see_the_suppliers_short_name(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(batch()))
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    product = (await backend.search_products(context, "ACME"))[0]
    assert product.attributes["supplier_name"] == "ACME Supplier"
    supplier = tenant.supplier.organization_id
    assert set_short_name(admin, supplier, " ACME 环线 ")["short_name"] == "ACME 环线"
    product = (await backend.search_products(context, "ACME"))[0]
    departure = backend.departures_page(context, product.product_id)["items"][0]
    details = await backend.get_product_details(context, product.product_id)
    assert {
        product.attributes["supplier_name"],
        departure.attributes["supplier_name"],
        details.attributes["supplier_name"],
    } == {"ACME 环线"}
    with pytest.raises(ValueError):
        set_short_name(admin, supplier, "一个超过十二个字的供应商简称写法")
    with pytest.raises(ValueError):
        set_short_name(admin, tenant.buyer.organization_id, "ACME")
