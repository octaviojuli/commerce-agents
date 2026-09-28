import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import changes, imports, pricing, quotes
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from cloud_warehouse.pricing import CustomerIdentity, PriceSchedule, SourcePrices
from cloud_warehouse.quotes import Party, QuoteRequest

from .test_imports import excel_source as excel_source
from .test_imports import workbook
from .test_sync import Connector, batch

FUTURE = (datetime.now(UTC) + timedelta(days=30)).date()


def schedule(adult="100.00", *, complete=True):
    return PriceSchedule(
        currency="CNY",
        market={"adult": "120.00", "child": "60.00", "single_room": "30.00"},
        settlement={"adult": adult, "child": "50.00", "single_room": "20.00"},
        fees_complete=complete,
        child_occupies_seat=True,
        child_age_min=2,
        child_age_max=12,
        room_types=["双人标准间"],
    )


def commit(runtime, actor, proposal):
    change_id = UUID(proposal["id"])
    changes.approve(runtime, actor, change_id, proposal["payload_hash"])
    return changes.apply(runtime, actor, change_id)


def add_buyer(admin, tenant, connection_id):
    org, user, grant = uuid4(), uuid4(), uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO organization(id,name,kinds) VALUES(:id,'ACME buyer B',ARRAY['buyer'])"
            ),
            {"id": org},
        )
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
            {"id": user, "email": f"{user}@acme.example"},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:user,:org,ARRAY['advisor','buyer_admin'])"
            ),
            {"user": user, "org": org},
        )
        conn.execute(
            text(
                "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:supplier,:buyer,:connection)"
            ),
            {
                "id": grant,
                "supplier": tenant.supplier.organization_id,
                "buyer": org,
                "connection": connection_id,
            },
        )
    return Principal(user, org)


@pytest.fixture
def managed_offer(database, tenant, excel_source):
    _, runtime = database
    uploaded = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME.xlsx",
        workbook(
            [
                [
                    "ACME-R1",
                    "ACME 环线",
                    3,
                    "ACME 城市",
                    "ACME-D1",
                    FUTURE.isoformat(),
                    (FUTURE + timedelta(days=2)).isoformat(),
                    10,
                    0,
                    0,
                ]
            ]
        ),
    )
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, uploaded["id"]))
    departure = list_departures(runtime, tenant.buyer)[0]
    return pricing.list_offers(runtime, tenant.buyer, departure["id"])[0]["id"]


def contract(runtime, tenant, offer, *, buyer=None, amount="100.00", version=0, complete=True):
    return pricing.propose(
        runtime,
        tenant.supplier,
        pricing.ContractProposal(
            offer_id=offer,
            buyer_org_id=buyer or tenant.buyer.organization_id,
            expected_version=version,
            schedule=schedule(amount, complete=complete),
            source_ref="ACME signed tariff",
            valid_from=datetime.now(UTC) - timedelta(minutes=1),
            valid_until=datetime.now(UTC) + timedelta(days=1),
        ),
    )


def test_decimal_totals_and_missing_components():
    prices = schedule("99.99")
    prices.charges = [
        pricing.Charge(
            code="service",
            label="ACME 服务费",
            basis="per_booking",
            market="1.00",
            settlement="0.50",
        )
    ]
    result = quotes.calculate(prices, Party(adults=2, children=1, child_ages=[8], single_rooms=1))
    assert result["market_total"] == "331.00" and result["settlement_total"] == "270.48"
    assert result["required_seats"] == 3
    unknown = PriceSchedule(currency="CNY", market={}, settlement={}, fees_complete=False)
    result = quotes.calculate(unknown, Party(adults=2))
    assert result["settlement_total"] is None and result["known_settlement_subtotal"] is None
    assert "settlement.adult" in result["missing_items"]
    assert "fees_not_fully_confirmed" in result["missing_items"]
    assert quotes.calculate(prices, Party(children=1, child_ages=[16]))["complete"] is False


async def test_approved_contract_quote_is_scoped_idempotent_and_does_not_sell(
    database, tenant, excel_source, managed_offer
):
    admin, runtime = database
    request = QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party(adults=2))
    missing = await quotes.create(runtime, tenant.buyer, request, "no-price")
    assert missing["complete"] is False and missing["settlement_total"] is None
    proposed = contract(runtime, tenant, managed_offer)
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM contract_price")) == 0
    commit(runtime, tenant.supplier, proposed)
    before = list_departures(runtime, tenant.buyer)[0]["available_seats"]
    quote = await quotes.create(runtime, tenant.buyer, request, "ACME-QUOTE")
    assert quote["market_total"] == "240.00" and quote["settlement_total"] == "200.00"
    assert quote["reservation_created"] is False and not quote["snapshot_stale"]
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == before
    repeated = await quotes.create(runtime, tenant.buyer, request, "ACME-QUOTE")
    assert repeated["quote_id"] == quote["quote_id"]
    with pytest.raises(Conflict):
        await quotes.create(
            runtime,
            tenant.buyer,
            request.model_copy(update={"party": Party(adults=3)}),
            "ACME-QUOTE",
        )
    second = add_buyer(admin, tenant, excel_source)
    with pytest.raises(Forbidden):
        quotes.get(runtime, second, UUID(quote["quote_id"]))
    missing = await quotes.create(runtime, second, request, "B-quote")
    assert missing["settlement_total"] is None
    commit(
        runtime,
        tenant.supplier,
        contract(runtime, tenant, managed_offer, buyer=second.organization_id, amount="80.00"),
    )
    b_quote = await quotes.create(runtime, second, request, "B-priced")
    assert b_quote["settlement_total"] == "160.00"
    with transaction(runtime, second) as conn:
        assert conn.scalar(text("SELECT count(*) FROM contract_price")) == 1
    public = quotes.customer_view(quote)
    assert "settlement_total" not in public and "settlement_lines" not in public
    assert "buyer_org_id" not in public and "source_ref" not in public
    with transaction(runtime, tenant.buyer) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("UPDATE quote_snapshot SET body='{}' WHERE id=:id"),
            {"id": UUID(quote["quote_id"])},
        )


async def test_price_change_and_grant_revocation_invalidate_quote(database, tenant, managed_offer):
    admin, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    quote = await quotes.create(
        runtime,
        tenant.buyer,
        QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party()),
        "Q",
    )
    commit(
        runtime,
        tenant.supplier,
        contract(runtime, tenant, managed_offer, amount="130.00", version=1),
    )
    historical = quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))
    assert historical["settlement_total"] == "100.00" and historical["snapshot_stale"]
    assert historical["availability"] == "unknown" and "available_seats" not in historical
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE buyer_org_id=:id"),
            {"id": tenant.buyer.organization_id},
        )
    with pytest.raises(Forbidden):
        quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))


class FakeSource:
    def __init__(self):
        self.calls = []
        self.on_read = None

    async def resolve_customer(self, code):
        return CustomerIdentity(customer_id="4101", code=code, name="ACME verified buyer")

    async def read_prices(self, departure_id, customer_id, company_id):
        self.calls.append((departure_id, customer_id, company_id))
        if self.on_read:
            self.on_read()
        now = datetime.now(UTC)
        return SourcePrices(
            schedule=schedule(),
            observed_at=now,
            expires_at=now + timedelta(seconds=20),
            source_ref="ACME API",
            available_seats=2,
        )


@pytest.fixture
async def external_offer(database, tenant):
    admin, runtime = database
    data = batch()
    data.departures[0].update(
        departDate=FUTURE.isoformat(),
        returnDate=(FUTURE + timedelta(days=2)).isoformat(),
        companyId=2,
    )
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    departure = list_departures(runtime, tenant.buyer)[0]
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE membership SET roles=ARRAY['advisor','buyer_admin'] WHERE user_id=:id"),
            {"id": tenant.buyer.user_id},
        )
    return pricing.list_offers(runtime, tenant.buyer, departure["id"])[0]["id"]


async def bind(runtime, tenant, source, version=0):
    verified = await pricing.verify_customer(
        runtime, tenant.supplier, tenant.connection_id, "ACME-C1", source
    )
    return commit(
        runtime,
        tenant.supplier,
        pricing.propose(
            runtime,
            tenant.supplier,
            pricing.BindingProposal(
                verification_id=UUID(verified["verification_id"]),
                buyer_org_id=tenant.buyer.organization_id,
                expected_version=version,
            ),
        ),
    )


@pytest.mark.parametrize("stage", ["enter", "read", "exit", "late_return"])
async def test_source_price_budget_covers_full_context_and_discards_late_prices(
    database, tenant, external_offer, monkeypatch, stage
):
    _, runtime = database
    monkeypatch.setattr(quotes, "SOURCE_PRICE_BUDGET_SECONDS", 0.02)
    closed = False
    calls = 0

    class Source(FakeSource):
        async def read_prices(self, *args):
            nonlocal calls
            calls += 1
            if stage in {"read", "late_return"}:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    if stage != "late_return":
                        raise
            return await super().read_prices(*args)

    source = Source()
    bound = await bind(runtime, tenant, source)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)

    @asynccontextmanager
    async def factory():
        nonlocal closed
        try:
            if stage == "enter":
                await asyncio.Event().wait()
            yield source
            if stage == "exit":
                await asyncio.Event().wait()
        finally:
            closed = True

    request = QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party())
    registry = {tenant.connection_id: factory}
    result = await asyncio.wait_for(
        quotes.create(runtime, tenant.buyer, request, "budget", registry), timeout=3
    )
    assert closed and result["missing_items"] == ["SOURCE_PRICE_TIMEOUT"]
    assert not result["complete"] and not result["reservation_created"]
    assert result["market_total"] is None and result["settlement_total"] is None
    assert result["known_market_subtotal"] is None and result["known_settlement_subtotal"] is None
    assert result["market_lines"] == [] and result["settlement_lines"] == []
    assert quotes.get(runtime, tenant.buyer, UUID(result["quote_id"])) == result
    assert await quotes.create(runtime, tenant.buyer, request, "budget", registry) == result
    assert calls == (0 if stage == "enter" else 1)


async def test_caller_cancellation_is_not_converted_to_a_timeout_quote(
    database, tenant, external_offer
):
    admin, runtime = database
    entered = asyncio.Event()
    closed = False

    class Source(FakeSource):
        async def read_prices(self, *args):
            entered.set()
            await asyncio.Event().wait()

    source = Source()
    bound = await bind(runtime, tenant, source)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)

    @asynccontextmanager
    async def factory():
        nonlocal closed
        try:
            yield source
        finally:
            closed = True

    request = QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party())
    task = asyncio.create_task(
        quotes.create(runtime, tenant.buyer, request, "cancelled", {tenant.connection_id: factory})
    )
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE buyer_org_id=:org"),
                {"org": tenant.buyer.organization_id},
            )
            == 0
        )


async def test_timeout_still_rechecks_revoked_buyer_before_saving(
    database, tenant, external_offer, monkeypatch
):
    admin, runtime = database
    monkeypatch.setattr(quotes, "SOURCE_PRICE_BUDGET_SECONDS", 0.02)

    class Source(FakeSource):
        async def read_prices(self, *args):
            with admin.begin() as conn:
                conn.execute(
                    text("UPDATE distribution_grant SET active=false WHERE id=:id"),
                    {"id": tenant.grant_id},
                )
            await asyncio.Event().wait()

    source = Source()
    bound = await bind(runtime, tenant, source)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)

    @asynccontextmanager
    async def factory():
        yield source

    with pytest.raises(Forbidden):
        await asyncio.wait_for(
            quotes.create(
                runtime,
                tenant.buyer,
                QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party()),
                "revoked-timeout",
                {tenant.connection_id: factory},
            ),
            timeout=3,
        )
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE buyer_org_id=:org"),
                {"org": tenant.buyer.organization_id},
            )
            == 0
        )


async def test_verified_binding_requires_buyer_consent_and_cannot_accept_forged_version(
    database, tenant, external_offer
):
    _, runtime = database
    source = FakeSource()

    @asynccontextmanager
    async def factory():
        yield source

    registry = {tenant.connection_id: factory}
    bound = await bind(runtime, tenant, source)
    request = QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party())
    pending = await quotes.create(runtime, tenant.buyer, request, "pending", registry)
    assert (
        pending["missing_items"] == ["CUSTOMER_BINDING_MISSING_OR_NOT_ACCEPTED"]
        and not source.calls
    )
    with pytest.raises(Conflict):
        pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 2)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)
    quote = await quotes.create(runtime, tenant.buyer, request, "accepted", registry)
    assert source.calls == [("2", "4101", "2")]
    assert (
        quote["availability"] == "available"
        and "available_seats" not in quote
        and quote["settlement_total"] == "100.00"
    )
    assert datetime.fromisoformat(quote["fresh_until"]) <= datetime.fromisoformat(
        quote["expires_at"]
    )
    await bind(runtime, tenant, source, version=1)
    assert quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))["snapshot_stale"]
    again = await quotes.create(runtime, tenant.buyer, request, "new-binding", registry)
    assert "CUSTOMER_BINDING_MISSING_OR_NOT_ACCEPTED" in again["missing_items"]


async def test_revoke_grant_during_source_read_never_publishes_quote(
    database, tenant, external_offer
):
    admin, runtime = database
    source = FakeSource()
    bound = await bind(runtime, tenant, source)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)

    def revoke():
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE distribution_grant SET active=false WHERE id=:id"),
                {"id": tenant.grant_id},
            )

    source.on_read = revoke

    @asynccontextmanager
    async def factory():
        yield source

    with pytest.raises(Forbidden):
        await quotes.create(
            runtime,
            tenant.buyer,
            QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party()),
            "revoked",
            {tenant.connection_id: factory},
        )
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE buyer_org_id=:id"),
                {"id": tenant.buyer.organization_id},
            )
            == 0
        )


async def test_source_quarantine_during_price_read_prevents_quote_publication(
    database, tenant, external_offer
):
    _, runtime = database
    invalid = batch()
    invalid.departures[0].update(
        routeId=0, departDate=str(FUTURE), returnDate=str(FUTURE + timedelta(days=2)), companyId=2
    )

    class Source(FakeSource):
        async def read_prices(self, *args):
            result = await super().read_prices(*args)
            await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(invalid))
            return result

    source = Source()
    bound = await bind(runtime, tenant, source)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)

    @asynccontextmanager
    async def factory():
        yield source

    with pytest.raises(Forbidden):
        await quotes.create(
            runtime,
            tenant.buyer,
            QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party()),
            "quarantined-during-price",
            {tenant.connection_id: factory},
        )
    assert len(source.calls) == 1
    # Use the admin here: an invisible saved quote would make an RLS-only count pass.
    with database[0].connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE buyer_org_id=:org"),
                {"org": tenant.buyer.organization_id},
            )
            == 0
        )


@pytest.mark.parametrize(
    ("zone", "utc_hour", "utc_day_offset"),
    [("Asia/Shanghai", 16, 0), ("America/Phoenix", 7, 1)],
)
async def test_quote_uses_source_business_day_and_expires_at_local_midnight(
    database, tenant, excel_source, managed_offer, monkeypatch, zone, utc_hour, utc_day_offset
):
    admin, runtime = database
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_connection SET capabilities=capabilities || jsonb_build_object('business_timezone',CAST(:zone AS text)) WHERE id=:id"
            ),
            {"zone": zone, "id": excel_source},
        )
    # This test freezes the quote clock near a future departure, while SQL still
    # uses actual time to validate the price agreement and human approval.
    command = pricing.ContractProposal(
        offer_id=managed_offer,
        buyer_org_id=tenant.buyer.organization_id,
        expected_version=0,
        schedule=schedule(),
        source_ref="ACME timezone tariff",
        valid_from=datetime.now(UTC) - timedelta(minutes=1),
        valid_until=datetime.combine(FUTURE + timedelta(days=3), time.min, UTC),
    )
    commit(runtime, tenant.supplier, pricing.propose(runtime, tenant.supplier, command))
    midnight = datetime.combine(FUTURE + timedelta(days=utc_day_offset), time(utc_hour), UTC)
    clock = {"now": midnight - timedelta(minutes=1)}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(quotes, "datetime", FrozenDateTime)
    request = QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party(adults=2))
    result = await quotes.create(runtime, tenant.buyer, request, "ACME-before-midnight")
    assert result["business_timezone"] == zone and not result["snapshot_stale"]
    assert result["market_total"] == "240.00" and result["settlement_total"] == "200.00"
    assert datetime.fromisoformat(result["fresh_until"]) == midnight
    assert quotes.customer_view(result)["business_timezone"] == zone
    clock["now"] = midnight
    old = quotes.get(runtime, tenant.buyer, UUID(result["quote_id"]))
    assert (
        old["snapshot_stale"] and old["availability"] == "unknown" and "available_seats" not in old
    )
    assert old["settlement_total"] == "200.00"  # Preserve historical facts.
    with pytest.raises(Conflict, match="出发日期"):
        await quotes.create(runtime, tenant.buyer, request, "ACME-after-midnight")
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE buyer_org_id=:id"),
                {"id": tenant.buyer.organization_id},
            )
            == 1
        )
        assert (
            conn.scalar(
                text(
                    "SELECT total-sold-held-blocked FROM inventory_pool WHERE supplier_org_id=:id"
                ),
                {"id": tenant.supplier.organization_id},
            )
            == 10
        )


@pytest.mark.parametrize(
    ("day", "end"),
    [
        (date(2030, 3, 10), datetime(2030, 3, 11, 4, tzinfo=UTC)),
        (date(2030, 11, 3), datetime(2030, 11, 4, 5, tzinfo=UTC)),
    ],
)
def test_business_day_end_follows_daylight_saving_calendar(day, end):
    assert quotes._departure_day_end(day, ZoneInfo("America/New_York")) == end


async def test_source_timezone_change_invalidates_quote_and_invalid_zone_refuses_new_quote(
    database, tenant, excel_source, managed_offer
):
    admin, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    request = QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party())
    result = await quotes.create(runtime, tenant.buyer, request, "ACME-default-zone")
    assert result["business_timezone"] == "Asia/Shanghai"
    for zone in ("America/Phoenix", "ACME/Invalid", ""):
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE supplier_connection SET capabilities=capabilities || jsonb_build_object('business_timezone',CAST(:zone AS text)) WHERE id=:id"
                ),
                {"id": excel_source, "zone": zone},
            )
        assert quotes.get(runtime, tenant.buyer, UUID(result["quote_id"]))["snapshot_stale"]
        if zone != "America/Phoenix":
            with pytest.raises(Conflict, match="时区"):
                await quotes.create(runtime, tenant.buyer, request, "ACME-invalid-zone")


async def test_timezone_changed_during_upstream_read_does_not_publish_quote(
    database, tenant, external_offer
):
    admin, runtime = database
    source = FakeSource()
    bound = await bind(runtime, tenant, source)
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)

    def change_zone():
        with admin.begin() as conn:
            conn.execute(
                text(
                    'UPDATE supplier_connection SET capabilities=capabilities || \'{"business_timezone":"America/Phoenix"}\'::jsonb WHERE id=:id'
                ),
                {"id": tenant.connection_id},
            )

    source.on_read = change_zone

    @asynccontextmanager
    async def factory():
        yield source

    with pytest.raises(Conflict):
        await quotes.create(
            runtime,
            tenant.buyer,
            QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party()),
            "ACME-zone-race",
            {tenant.connection_id: factory},
        )
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM quote_snapshot WHERE buyer_org_id=:id"),
                {"id": tenant.buyer.organization_id},
            )
            == 0
        )


async def test_point_lookup_plans_keep_same_key_quotes_scoped_on_reused_connection(
    database, tenant, excel_source, managed_offer
):
    from cloud_warehouse.database_pool import pooled_engine

    admin, runtime = database
    other = add_buyer(admin, tenant, excel_source)
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    commit(
        runtime,
        tenant.supplier,
        contract(runtime, tenant, managed_offer, buyer=other.organization_id, amount="80.00"),
    )
    engine = pooled_engine(runtime.url, max_size=1)
    request = QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party())
    try:
        first = await quotes.create(engine, tenant.buyer, request, "ACME-shared-key")
        second = await quotes.create(engine, other, request, "ACME-shared-key")
        assert first["quote_id"] != second["quote_id"]
        assert first["settlement_total"] == "100.00" and second["settlement_total"] == "80.00"
        # Warm both query shapes while alternating identity on one physical connection.
        for _ in range(12):
            for buyer, quote in ((tenant.buyer, first), (other, second)):
                assert (
                    quotes.get(engine, buyer, UUID(quote["quote_id"]))["quote_id"]
                    == quote["quote_id"]
                )
                replay = await quotes.create(engine, buyer, request, "ACME-shared-key")
                assert replay["quote_id"] == quote["quote_id"]
                assert replay["settlement_total"] == quote["settlement_total"]
        with pytest.raises(Forbidden):
            quotes.get(engine, other, UUID(first["quote_id"]))
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE distribution_grant SET active=false WHERE buyer_org_id=:id"),
                {"id": tenant.buyer.organization_id},
            )
        with pytest.raises(Forbidden):
            quotes.get(engine, tenant.buyer, UUID(first["quote_id"]))
        with pytest.raises(Forbidden):
            await quotes.create(engine, tenant.buyer, request, "ACME-shared-key")
        assert (await quotes.create(engine, other, request, "ACME-shared-key"))[
            "quote_id"
        ] == second["quote_id"]
        with transaction(engine, other) as conn:
            assert conn.scalar(text("SELECT count(*) FROM quote_snapshot")) == 1
    finally:
        engine.dispose()


async def test_uniform_source_uses_same_price_for_two_buyers_without_customer_bindings(
    database, tenant, external_offer
):
    admin, runtime = database
    second = add_buyer(admin, tenant, tenant.connection_id)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_connection SET capabilities=capabilities || CAST(:policy AS jsonb) WHERE id=:id"
            ),
            {
                "id": tenant.connection_id,
                "policy": '{"settlement_pricing":{"mode":"uniform","version":1}}',
            },
        )

    class UniformSource(FakeSource):
        async def read_uniform_prices(self, departure_id, company_id, policy_version):
            assert policy_version == 1
            return await super().read_prices(departure_id, "ACME-STANDARD", company_id)

    source = UniformSource()

    @asynccontextmanager
    async def factory():
        yield source

    request = QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=Party())
    first = await quotes.create(
        runtime, tenant.buyer, request, "uniform-first", {tenant.connection_id: factory}
    )
    other = await quotes.create(
        runtime, second, request, "uniform-second", {tenant.connection_id: factory}
    )
    assert first["settlement_total"] == other["settlement_total"] == "100.00"
    assert len(source.calls) == 2 and all(call[1] == "ACME-STANDARD" for call in source.calls)
    assert "CUSTOMER_BINDING_MISSING_OR_NOT_ACCEPTED" not in first["missing_items"]
    assert "available_seats" not in first
    with admin.begin() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM buyer_customer_binding WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
            == 0
        )
        conn.execute(
            text(
                "UPDATE supplier_connection SET capabilities=jsonb_set(capabilities,'{settlement_pricing,version}','2') WHERE id=:id"
            ),
            {"id": tenant.connection_id},
        )
    assert quotes.get(runtime, tenant.buyer, UUID(first["quote_id"]))["snapshot_stale"]
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE buyer_org_id=:id"),
            {"id": second.organization_id},
        )
    with pytest.raises(Forbidden):
        quotes.get(runtime, second, UUID(other["quote_id"]))
