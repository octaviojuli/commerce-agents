import asyncio
from contextlib import asynccontextmanager, suppress
from threading import Event, get_ident
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import event, text

from cloud_warehouse import auth, conversations, pricing, quotes, source_limits
from cloud_warehouse.admin import onboard
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.concurrency import in_worker_thread
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Principal
from commerce_common.presentation import EnrichmentContext
from shopping_agent import ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState
from tour.api.warehouse_agent import DeparturePage, OfferPage, configuration, departures, offers

from .conftest import Tenant
from .test_api import PASSWORD
from .test_conversations import FakeAgent
from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, FakeSource, bind, schedule
from .test_quotes import managed_offer as managed_offer
from .test_sync import Connector, batch


@pytest.mark.parametrize("failure", ["timeout", "rate_limit"])
async def test_one_supplier_failure_does_not_block_same_buyer_other_supplier(
    database, authentication, tenant, failure
):
    admin, runtime = database
    ids = onboard(admin, "ACME Supplier B", "ACME Buyer B", f"{uuid4()}@acme.example")
    supplier_id = UUID(ids["supplier_id"])
    supplier_user = auth.create_user(
        admin, f"{uuid4()}@acme.example", PASSWORD, {supplier_id: ["supplier_admin"]}
    )
    other = Tenant(
        Principal(supplier_user, supplier_id),
        tenant.buyer,
        Principal(UUID(ids["worker_id"]), supplier_id),
        UUID(ids["connection_id"]),
        UUID(ids["grant_id"]),
    )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET buyer_org_id=:buyer WHERE id=:id"),
            {"buyer": tenant.buyer.organization_id, "id": other.grant_id},
        )
        conn.execute(
            text("UPDATE membership SET roles=ARRAY['advisor','buyer_admin'] WHERE user_id=:id"),
            {"id": tenant.buyer.user_id},
        )
    entered, release = asyncio.Event(), asyncio.Event()
    closed, attempts, registry, requests = [], [], {}, []

    class Source(FakeSource):
        def __init__(self, label):
            super().__init__()
            self.label = label

        async def read_prices(self, *args):
            await source_limits.check_or_record()
            attempts.append(self.label)
            if self.label == "A":
                entered.set()
                await release.wait()
                if failure == "timeout":
                    raise TimeoutError
                await source_limits.check_or_record(60)
                raise SourceError("SOURCE_RATE_LIMITED", retry_after_seconds=60)
            value = await super().read_prices(*args)
            return value.model_copy(update={"schedule": schedule("200.00"), "available_seats": 7})

    def factory(source):
        @asynccontextmanager
        async def connect():
            try:
                yield source
            finally:
                closed.append(source.label)

        return connect

    previous = set()
    for scope, label in ((tenant, "A"), (other, "B")):
        data = batch(19)
        data.routes[0].update(routeId=36, routeName=f"ACME {label}")
        data.departures[0].update(
            routeId=36,
            periodId=36,
            companyId=2,
            departDate=FUTURE.isoformat(),
            returnDate=FUTURE.isoformat(),
        )
        await synchronize(runtime, scope.worker, scope.connection_id, Connector(data))
        departures = list_departures(runtime, tenant.buyer)
        departure = next(row for row in departures if row["id"] not in previous)
        previous.add(departure["id"])
        offer = pricing.list_offers(runtime, tenant.buyer, departure["id"])[0]
        source = Source(label)
        bound = await bind(runtime, scope, source)
        pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)
        registry[scope.connection_id] = factory(source)
        requests.append(
            {
                "departure_id": "WD-" + str(departure["id"]),
                "offer_id": str(offer["id"]),
                "party": {"adults": 1},
            }
        )

    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    app = create_app(runtime, authentication, registry)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://acme.invalid"
        ) as client,
    ):
        login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200
        headers = {
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }

        async def quote(index, key):
            response = await client.post(
                "/v1/advisor/quotes",
                headers={**headers, "Idempotency-Key": key},
                json=requests[index],
            )
            assert response.status_code == 201, response.text
            return response.json()

        pending = asyncio.create_task(quote(0, "ACME-failing"))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            healthy = await asyncio.wait_for(quote(1, "ACME-healthy"), 3)
            catalog = await asyncio.wait_for(client.get("/v1/advisor/products", headers=headers), 3)
            assert catalog.status_code == 200 and len(catalog.json()["items"]) == 2
            assert not pending.done(), "Healthy supplier waited for the failing supplier"
            assert healthy["complete"] and healthy["settlement_total"] == "200.00"
            assert (
                healthy["availability"] == "available"
                and "available_seats" not in healthy
                and not healthy["reservation_created"]
            )
            release.set()
            failed = await asyncio.wait_for(pending, 3)
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending

        code = "SOURCE_PRICE_TIMEOUT" if failure == "timeout" else "SOURCE_RATE_LIMITED"
        assert failed["missing_items"] == [code]
        assert not failed["complete"] and not failed["reservation_created"]
        assert (
            failed["availability"] == "unknown" and "available_seats" not in failed
        )  # Never reuse the cached 19 seats.
        assert failed["market_total"] is None and failed["settlement_total"] is None
        assert failed["market_lines"] == [] and failed["settlement_lines"] == []
        assert await quote(0, "ACME-failing") == failed
        assert await quote(1, "ACME-healthy") == healthy
        assert attempts == ["A", "B"] and sorted(closed) == ["A", "B"]
        # A's persisted cooldown must not affect B's next independent upstream read.
        fresh = await quote(1, "ACME-healthy-next")
        assert fresh["complete"] and fresh["settlement_total"] == "200.00"
        assert fresh["availability"] == "available" and "available_seats" not in fresh
        if failure == "rate_limit":
            retry = await quote(0, "ACME-failing-next")
            assert retry["missing_items"] == ["SOURCE_RATE_LIMITED"]
            assert retry["availability"] == "unknown" and "available_seats" not in retry
        assert attempts == ["A", "B", "B"]


async def test_slow_quote_database_does_not_block_health(
    database, authentication, tenant, managed_offer, monkeypatch
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    entered, release, expired = Event(), Event(), Event()
    loop_thread, scope_threads = get_ident(), []
    original = quotes._scope

    def delayed(*args, **kwargs):
        scope_threads.append(get_ident())
        if not entered.is_set():
            entered.set()
            if not release.wait(2):
                expired.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(quotes, "_scope", delayed)
    app = create_app(runtime, authentication)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://acme.invalid"
        ) as client,
    ):
        login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200
        headers = {
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-Organization-Id": str(tenant.buyer.organization_id),
            "Idempotency-Key": uuid4().hex,
        }
        pending = asyncio.create_task(
            client.post(
                "/v1/quotes",
                headers=headers,
                json={
                    "offer_id": str(managed_offer),
                    "departure_date": FUTURE.isoformat(),
                    "party": {"adults": 1},
                },
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert (await client.get("/health")).status_code == 200
            assert not expired.is_set(), "Health was blocked until the database delay ended"
            assert not pending.done()
        finally:
            release.set()
            result = await pending
        assert result.status_code == 201
        assert scope_threads and all(thread != loop_thread for thread in scope_threads)


async def test_http_business_and_chat_sql_stays_off_event_loop(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(
        admin,
        email,
        PASSWORD,
        {
            tenant.buyer.organization_id: ["advisor"],
            tenant.supplier.organization_id: ["supplier_admin"],
        },
    )
    loop_thread, sql_threads, upstream_threads, agent_threads = get_ident(), [], [], []
    calls = []

    def observed_sql(*args):
        sql_threads.append(get_ident())

    class Source(FakeSource):
        async def resolve_customer(self, code):
            upstream_threads.append(get_ident())
            return await super().resolve_customer(code)

    @asynccontextmanager
    async def connector():
        yield Source()

    app = create_app(
        runtime,
        authentication,
        {tenant.connection_id: connector},
        agent_factory=lambda *args: FakeAgent(
            calls, on_stream=lambda: agent_threads.append(get_ident())
        ),
    )
    for engine in (runtime, authentication):
        event.listen(engine, "before_cursor_execute", observed_sql)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://acme.invalid"
            ) as client,
        ):
            login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert login.status_code == 200
            buyer = {
                "Authorization": "Bearer " + login.json()["access_token"],
                "X-Organization-Id": str(tenant.buyer.organization_id),
            }
            supplier = {**buyer, "X-Organization-Id": str(tenant.supplier.organization_id)}

            async def request(method, path, headers=buyer, expected=200, **kwargs):
                result = await client.request(method, path, headers=headers, **kwargs)
                assert result.status_code == expected, result.text
                return result

            catalog = (await request("GET", "/v1/advisor/products")).json()["items"]
            product = catalog[0]["product_id"]
            await request("GET", f"/v1/advisor/products/{product}")
            departures = (
                await request("GET", "/v1/advisor/departures", params={"product_id": product})
            ).json()["items"]
            body = {
                "departure_id": departures[0]["product_id"],
                "offer_id": str(managed_offer),
                "party": {"adults": 1},
            }
            quote_headers = {**buyer, "Idempotency-Key": uuid4().hex}
            first = await request("POST", "/v1/advisor/quotes", quote_headers, 201, json=body)
            replay = await request("POST", "/v1/advisor/quotes", quote_headers, 201, json=body)
            assert first.json()["quote_id"] == replay.json()["quote_id"]
            listings = (await request("GET", "/v1/merchant/listings", supplier)).json()["items"]
            listing = listings[0]["listing_id"]
            await request("GET", f"/v1/merchant/listings/{listing}", supplier)
            await request("GET", "/v1/merchant/snapshot", supplier)
            await request(
                "POST",
                "/v1/merchant/content/proposals",
                supplier,
                201,
                json={"listing_id": listing, "title": "ACME 修订线路"},
            )
            await request("GET", "/v1/merchant/changes", supplier)
            await request(
                "POST",
                "/v1/customer-bindings/verify",
                supplier,
                json={"connection_id": str(tenant.connection_id), "customer_code": "ACME-CODE"},
            )
            for role, headers in (("advisor", buyer), ("merchant", supplier)):
                created = await request(
                    "POST", "/v1/conversations", headers, 201, json={"role": role}
                )
                url = f"/v1/conversations/{created.json()['id']}/chat"
                chat_headers = {**headers, "Idempotency-Key": uuid4().hex}
                result = await request("POST", url, chat_headers, json={"message": "ACME 查询"})
                assert "event: turn_complete" in result.text
                replay = await request("POST", url, chat_headers, json={"message": "ACME 查询"})
                assert replay.text == result.text
        assert sql_threads and loop_thread not in sql_threads
        assert upstream_threads == [loop_thread]
        assert agent_threads == [loop_thread, loop_thread] and len(calls) == 2
    finally:
        for engine in (runtime, authentication):
            event.remove(engine, "before_cursor_execute", observed_sql)


async def test_shutdown_releases_disconnected_chat_lease_and_closes_agent(
    database, authentication, tenant
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    started, closed = asyncio.Event(), asyncio.Event()

    class WaitingAgent(FakeAgent):
        async def stream_turn(self, *args):
            started.set()
            await asyncio.Event().wait()
            async for item in super().stream_turn(*args):
                yield item

        async def aclose(self):
            closed.set()

    app = create_app(runtime, authentication, agent_factory=lambda *args: WaitingAgent([]))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://acme.invalid"
        ) as client,
    ):
        login = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        headers = {
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        created = await client.post("/v1/conversations", headers=headers, json={"role": "advisor"})
        identifier = UUID(created.json()["id"])
        pending = asyncio.create_task(
            client.post(
                f"/v1/conversations/{identifier}/chat",
                headers={**headers, "Idempotency-Key": "ACME-cancelled"},
                json={"message": "ACME 等待"},
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 3)
        finally:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        assert not closed.is_set()
    # A disconnected advisor turn remains owned by the app until shutdown.
    assert closed.is_set()
    actor = Principal(user, tenant.buyer.organization_id)
    saved = await asyncio.to_thread(conversations.get, runtime, actor, identifier)
    assert saved["turns"][0]["status"] == "interrupted"
    work = await asyncio.to_thread(
        conversations.begin, runtime, actor, identifier, "ACME-retry", "ACME 重试"
    )
    await asyncio.to_thread(conversations.interrupt, runtime, actor, work)


async def test_cancelled_quote_commit_is_recovered_by_same_idempotency_key(
    database, tenant, managed_offer, monkeypatch
):
    admin, runtime = database
    entered, release, finished = Event(), Event(), Event()
    original = quotes._save_quote.__wrapped__

    def delayed_save(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(3)
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(quotes, "_save_quote", in_worker_thread(delayed_save))
    request = quotes.QuoteRequest(
        offer_id=managed_offer, departure_date=FUTURE, party=quotes.Party(adults=1)
    )
    key = "ACME-interrupted-response"
    pending = asyncio.create_task(quotes.create(runtime, tenant.buyer, request, key, {}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
    # Cancellation is not a rollback guarantee: the worker may have committed.
    replay = await quotes.create(runtime, tenant.buyer, request, key, {})
    with admin.connect() as conn:
        saved = (
            conn.execute(
                text(
                    "SELECT id FROM quote_snapshot WHERE buyer_org_id=:buyer AND idempotency_key=:key"
                ),
                {"buyer": tenant.buyer.organization_id, "key": key},
            )
            .scalars()
            .all()
        )
        assert [str(value) for value in saved] == [replay["quote_id"]]
        assert conn.execute(
            text(
                "SELECT total,sold,held,blocked,version FROM inventory_pool WHERE supplier_org_id=:org"
            ),
            {"org": tenant.supplier.organization_id},
        ).all() == [(10, 0, 0, 0, 1)]


async def test_advisor_presentation_tools_keep_sql_off_event_loop(database, tenant, managed_offer):
    _, runtime = database
    loop_thread, sql_threads = get_ident(), []
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    session = ShoppingSessionContext(session_id=str(uuid4()), user_id=str(tenant.buyer.user_id))
    from datetime import date

    from cloud_warehouse import conversations, trip_brief

    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    session = ShoppingSessionContext(session_id=str(identifier), user_id=str(tenant.buyer.user_id))
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
    state = ShoppingSessionState()
    context = EnrichmentContext(backend, configuration(), session, state)
    products = await backend.search_products(session, "ACME")
    state.remember_products(products)
    work = conversations.begin(runtime, tenant.buyer, identifier, "search", "找 ACME 线路")
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
                    "payload": {"items": [p.model_dump(mode="json") for p in products]},
                },
            }
        ],
    )
    work = conversations.begin(runtime, tenant.buyer, identifier, "choose", "第一条，看看团期")

    def observed_sql(*args):
        sql_threads.append(get_ident())

    event.listen(runtime, "before_cursor_execute", observed_sql)
    try:
        page = await departures(DeparturePage(product_id=products[0].product_id), context)
        departure = page["items"][0]["product_id"]
        await asyncio.to_thread(
            conversations.finish,
            runtime,
            tenant.buyer,
            work,
            state.model_dump(mode="json"),
            work["messages"],
            [{"type": "ui", "data": {"component": "warehouse_departures", "payload": page}}],
        )
        work = await asyncio.to_thread(
            conversations.begin, runtime, tenant.buyer, identifier, "offer", "第一期，看看报价方案"
        )
        options = await offers(OfferPage(departure_id=departure), context)
        assert options["items"][0]["offer_id"] == str(managed_offer)
        assert "WO-" + str(managed_offer) in state.seen_products
        assert sql_threads and loop_thread not in sql_threads
        await asyncio.to_thread(
            conversations.finish,
            runtime,
            tenant.buyer,
            work,
            state.model_dump(mode="json"),
            work["messages"],
            [],
        )
    finally:
        event.remove(runtime, "before_cursor_execute", observed_sql)
