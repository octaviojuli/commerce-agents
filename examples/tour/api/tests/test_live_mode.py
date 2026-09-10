# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What changes when the ERP behind the backend is the agency's own (``live=True``): the
advisor's identity and department come from the ERP login, the 同行 customer is resolved from
its 客户编码 and there is none until it resolves, an order needs the deployment's own 门店, the
policy tool is not registered, the brand names come from the environment, and every read and
the one write go out on the client the advisor's own login left in the registry. The fixtures are
what stands in for the agency's ERP here — a client exposing ``user_info``, ``companies`` and
``search_customers`` is all the backend reads to tell who is logged in — so nothing in this
suite touches a real one. The data guards are here too, because a beta catalog is where a 0
fare and a 团期 with no 线路 come from."""

import time
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest

from shopping_agent import NotOffered, ShoppingSessionContext
from tour.api.advisors import NEED_LOGIN, AdvisorLogin, AdvisorRegistry
from tour.api.agent_config import (
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_BRAND_NAME,
    build_shopping_config,
)
from tour.api.erp_client import (
    CustomerRecord,
    DepartureRecord,
    ErpAuth,
    ErpClient,
    ErpUnavailable,
    PriceInfo,
    Quote,
)
from tour.api.mock_erp import MockErpClient
from tour.api.tour_backend import DATA_DIR, TourBackend, _group_status

from .test_tour_backend import (
    MOBILE,
    OPEN,
    ROUTE,
    TODAY,
    FakeClock,
    Windowed,
    executor,
    search_yili,
)

ADVISOR = "demo-user"
# The 客户编码 the deployment books for, and the one customer data/customers.json holds it on.
CODE = "TX-BJ-0001"
CUSTOMER = 4101
CUSTOMER_NAME = "ACME 同业 · 北京营业部"
# What the ERP answers a login with, and the 门店 an order is written through.
USER_INFO = {"userId": 88, "userName": "陆珉", "companyId": 4, "companyName": "欧洲部-上海"}
DEPARTMENTS = [{"companyId": 2}, {"companyId": 4}, {"companyId": 5}]
STORE = "ACME 旅行社 上海总部"
# The two advisors of one deployment, as their own logins key them: the ERP employee behind
# each. A live session belongs to one of these and never to a profile in users.json.
SIGNED_IN = f"erp-{USER_INFO['userId']}"
OTHER_ADVISOR = "erp-91"
OTHER_NAME = "沈岚"
OTHER_MOBILE = "13900000091"
# How many 线路 the production-scale fake sells: more than a boot could read one at a time.
CATALOG_ROUTES = 30


class LiveErp(MockErpClient):
    """The fixtures with an ERP login on them: ``user_info`` and ``companies`` are what the
    real client fills from ``POST /login``, and the backend reads nothing else to name the
    advisor. ``logins`` counts the on-demand logins ``identify`` made."""

    def __init__(self, *, logged_in: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.user_info: dict[str, Any] = dict(USER_INFO) if logged_in else {}
        self.companies: list[dict[str, Any]] = list(DEPARTMENTS)
        self.logins = 0

    async def identify(self) -> dict[str, Any]:
        if not self.user_info:
            self.logins += 1
            self.user_info = dict(USER_INFO)
        return self.user_info


class TwoCustomers(LiveErp):
    """A 客户编码 two customers in the book share, which is not a customer to book for."""

    async def search_customers(self, keyword: str) -> list[CustomerRecord]:
        return [
            CustomerRecord(4101, "ACME 同业 · 北京营业部", keyword, 1),
            CustomerRecord(4102, "ACME 同业 · 上海营业部", keyword, 1),
        ]


class NoCustomerBook(LiveErp):
    """The customer book is unreachable; a deployment with no customer is the result."""

    async def search_customers(self, keyword: str) -> list[CustomerRecord]:
        raise ErpUnavailable("旅行社 ERP 暂时无法连接，请稍后再试。")


class NoChildFare(LiveErp):
    """A 团期 whose 儿童价 is 0: the department has not published one, and 0 is not free."""

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> Quote:
        listed = (await super().quote(period_id, customer_id, company_id)).price
        return Quote(
            price=PriceInfo(listed.adult, 0.0, 0.0, 0.0),
            price_type="同行价",
            is_external=False,
        )


class WindowErp(LiveErp):
    """A client that reads a whole window at once, as the real one does, with one broken row
    in it: a 团期 the ERP carries no ``routeId`` on."""

    async def list_window(
        self, depart_from: date | None, depart_to: date | None
    ) -> list[DepartureRecord]:
        rows = [
            row
            for route_id in self._routes
            for row in await self.list_departures(route_id, "", depart_from, depart_to)
        ]
        return [*rows, replace(rows[0], period_id=99001, route_id=0, route_name="")]


class BigCatalog(Windowed, LiveErp):
    """A live login onto a catalog at the agency's own scale: far more 线路 than a boot could
    read one at a time, each with one 团期 inside the default window, their 团期 read a window
    at a time as the real client reads them. ``calls`` counts what the boot asked of it, the
    detail and price reads a 团期 costs included."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        route = dict(next(iter(self._routes.values())))
        period = dict(next(iter(self._departures.values())))
        self._routes, self._departures = {}, {}
        for index in range(CATALOG_ROUTES):
            route_id, period_id = 9000 + index, 7000 + index
            self._routes[route_id] = {
                **route,
                "routeId": route_id,
                "routeCode": f"BIG{index:03d}",
                "routeName": f"目的地{index} 环线 {route['days']} 日",
            }
            depart = self.today + timedelta(days=14 + index % 7)
            self._departures[period_id] = {
                **period,
                "periodId": period_id,
                "routeId": route_id,
                "departDate": depart,
                "returnDate": depart + timedelta(days=route["days"] - 1),
            }

    async def get_departure(self, period_id: int) -> DepartureRecord | None:
        self._called("get_departure")
        return await super().get_departure(period_id)

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> Quote:
        self._called("quote")
        return await super().quote(period_id, customer_id, company_id)


def signed_in(
    user_id: str,
    client: ErpClient,
    *,
    name: str = USER_INFO["userName"],
    mobile: str = MOBILE,
    expires_in: float = 3600.0,
) -> AdvisorLogin:
    """One advisor as their own ERP login left them: the client their calls go out on, and what
    ``POST /login`` said about them (``api/advisors.py`` holds the login itself)."""
    return AdvisorLogin(
        client=client,
        user_id=user_id,
        name=name,
        mobile=mobile,
        department=USER_INFO["companyName"],
        departments=len(DEPARTMENTS),
        expires_at=time.time() + expires_in,
    )


class Registered(AdvisorRegistry):
    """The registry with these advisors already signed in; nobody logs in over the wire here."""

    def __init__(self, *logins: AdvisorLogin) -> None:
        super().__init__()
        for login in logins:
            self._keep(login)


def build(
    erp: MockErpClient,
    *,
    code: str = CODE,
    store: str = STORE,
    registry: AdvisorRegistry | None = None,
) -> TourBackend:
    return TourBackend(
        erp,
        today=TODAY,
        customer_id=0,
        contact_mobile=MOBILE,
        live=True,
        customer_code=code,
        store_name=DEFAULT_BRAND_NAME,
        order_store_name=store,
        registry=registry,
    )


@pytest.fixture
def erp() -> LiveErp:
    return LiveErp(today=TODAY, now=FakeClock())


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="live-1", user_id=ADVISOR)


# -- the advisor, from the ERP and not from users.json --------------------------------------


async def test_the_advisor_is_the_account_the_erp_logged_in(erp, session):
    """users.json is this example's own invention: a live deployment states the salesperson's
    own name and the department the login landed in, and no habits at all."""
    backend = build(erp)
    profile = await backend.get_preferences(session)
    assert (profile.user_id, profile.display_name) == (ADVISOR, "陆珉")
    assert (profile.default_location, profile.loyalty_tier) == ("欧洲部-上海", "欧洲部-上海")
    assert profile.preferences == {}
    # The fixture profile is what the same session reads on the mock.
    fixture = await TourBackend(
        erp, today=TODAY, customer_id=CUSTOMER, contact_mobile=MOBILE
    ).get_preferences(session)
    assert fixture.preferences and fixture.display_name == "林晓（工号 demo-user）"


async def test_the_account_context_names_the_login_the_departments_and_the_customer(erp, session):
    backend = build(erp)
    await backend.load_listings()
    assert await backend.get_account_context(session) == {
        "advisor": "陆珉",
        "store": STORE,
        "department": "欧洲部-上海",
        "departments": 3,
        "customer": f"{CODE} {CUSTOMER_NAME}",
        "active_holds": 0,
    }


async def test_the_advisors_name_is_read_by_logging_in_on_demand(session):
    """``user_info`` is empty until the client has called the ERP once, and the workbench asks
    who the advisor is before anything else."""
    erp = LiveErp(today=TODAY, now=FakeClock(), logged_in=False)
    backend = build(erp)
    assert (await backend.get_preferences(session)).display_name == "陆珉"
    assert erp.logins == 1
    assert (await backend.get_account_context(session))["advisor"] == "陆珉"
    assert erp.logins == 1


# -- the advisor's own login ----------------------------------------------------------------


@pytest.fixture
def signed_in_session() -> ShoppingSessionContext:
    """A session of the advisor the fake ERP logs in, keyed by that ERP employee."""
    return ShoppingSessionContext(session_id="live-3", user_id=SIGNED_IN)


async def test_a_session_reads_and_writes_through_its_own_advisors_login(signed_in_session):
    """Two advisors of one deployment, two ERP accounts: the catalog each reads and the order
    each writes go out on the token their own login bought, and the order is signed with the
    name and the mobile that login carries."""
    mine = LiveErp(today=TODAY, now=FakeClock())
    theirs = LiveErp(today=TODAY, now=FakeClock())
    other_session = ShoppingSessionContext(session_id="live-4", user_id=OTHER_ADVISOR)
    backend = build(
        mine,
        registry=Registered(
            signed_in(SIGNED_IN, mine),
            signed_in(OTHER_ADVISOR, theirs, name=OTHER_NAME, mobile=OTHER_MOBILE),
        ),
    )
    assert backend._erp_for(signed_in_session) is mine
    assert backend._erp_for(other_session) is theirs
    await backend.load_listings()
    await search_yili(backend, other_session)
    await backend.add_to_cart(other_session, OPEN, 4)
    assert await mine.list_orders() == []
    (order,) = await theirs.list_orders()
    assert (order.contact_name, order.contact_mobile) == (OTHER_NAME, OTHER_MOBILE)


async def test_the_advisor_and_the_department_are_the_logins_own(signed_in_session):
    """The deployment's own account is not the advisor: what the workbench states is the name
    and the department the advisor's own login landed in."""
    erp = LiveErp(today=TODAY, now=FakeClock())
    backend = build(erp, registry=Registered(signed_in(SIGNED_IN, erp, name=OTHER_NAME)))
    profile = await backend.get_preferences(signed_in_session)
    assert (profile.user_id, profile.display_name) == (SIGNED_IN, OTHER_NAME)
    assert profile.loyalty_tier == USER_INFO["companyName"]
    context = await backend.get_account_context(signed_in_session)
    assert (context["advisor"], context["departments"]) == (OTHER_NAME, len(DEPARTMENTS))


async def test_a_session_with_no_live_login_asks_the_advisor_to_sign_in(signed_in_session):
    """Nobody signed in on this process, or the eight hours of the token they signed in with
    are up: the deployment's own account is not lent to the session, and every ERP call the
    conversation makes says so in the advisor's own words."""
    erp = LiveErp(today=TODAY, now=FakeClock())
    expired = signed_in(SIGNED_IN, erp, expires_in=-1.0)
    for registry in (Registered(), Registered(expired)):
        backend = build(erp, registry=registry)
        await backend.load_listings()  # the boot snapshot is the deployment's own account's
        assert backend.products
        for call in (
            backend.search_products(signed_in_session, "伊犁"),
            backend.get_product_details(signed_in_session, ROUTE),
            backend.add_to_cart(signed_in_session, OPEN, 4),
            backend.get_orders(signed_in_session),
        ):
            with pytest.raises(ErpAuth, match=NEED_LOGIN):
                await call
        # A conversation that holds no 占位 has nothing to ask the ERP about.
        assert (await backend.get_cart(signed_in_session)).items == []


def test_the_advisor_hears_the_sign_in_notice_and_not_an_outage(signed_in_session):
    """``ErpAuth`` is a rule the advisor can act on, so the executor relays it into the
    conversation as the ERP's own words."""
    backend = build(LiveErp(today=TODAY, now=FakeClock()), registry=Registered())
    relayed = executor(backend, signed_in_session).domain_error(ErpAuth(NEED_LOGIN))
    assert relayed.is_error and NEED_LOGIN in relayed.result_text


# -- the customer, by its 客户编码 ----------------------------------------------------------


async def test_the_customer_is_the_one_the_code_resolves_to(erp, session):
    backend = build(erp)
    await backend.load_listings()
    assert (backend.customer_id, backend.customer_label) == (CUSTOMER, f"{CODE} {CUSTOMER_NAME}")
    await search_yili(backend, session)
    details = await backend.get_product_details(session, OPEN)
    assert details.attributes["quote_source"] == "customer"


@pytest.mark.parametrize(
    ("client", "code"),
    [(LiveErp, ""), (LiveErp, "TX-NOBODY"), (TwoCustomers, CODE), (NoCustomerBook, CODE)],
)
async def test_no_customer_quotes_the_market_price_and_refuses_a_write(
    client, code, session, caplog
):
    """A code that is unset, unknown, shared or unreadable all leave the deployment with no
    customer. That is a deployment error, and it is logged as one: the catalog still reads, the
    records fall back to the 市场价, and the write says which variable is missing."""
    backend = build(client(today=TODAY, now=FakeClock()), code=code)
    with caplog.at_level("ERROR"):
        await backend.load_listings()
    assert backend.customer_id == 0
    assert "TOUR_ERP_CUSTOMER_CODE" in caplog.text
    await search_yili(backend, session)
    details = await backend.get_product_details(session, OPEN)
    assert details.attributes["quote_source"] == "list"
    assert details.attributes["price_type"] == ""
    with pytest.raises(NotOffered, match="TOUR_ERP_CUSTOMER_CODE"):
        await backend.add_to_cart(session, OPEN, 2)


# -- the 门店 an order is written through ---------------------------------------------------


async def test_an_order_is_written_through_the_deployments_own_store(erp, session):
    backend = build(erp)
    await backend.load_listings()
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    (order,) = await erp.list_orders()
    assert order.contact_name == "陆珉"
    assert (await backend.get_account_context(session))["store"] == STORE


async def test_a_write_with_no_salesperson_named_is_not_signed_with_the_fixtures_advisor(
    session,
):
    """林晓 is a name this example invented; it must never land on a real order's 联系人."""

    class Anonymous(LiveErp):
        async def identify(self) -> dict[str, Any]:
            return {}

    erp = Anonymous(today=TODAY, now=FakeClock(), logged_in=False)
    backend = build(erp)
    await backend.load_listings()
    await search_yili(backend, session)
    with pytest.raises(NotOffered, match="ERP 登录人姓名"):
        await backend.add_to_cart(session, OPEN, 4)
    assert await erp.list_orders() == []


async def test_a_write_with_no_store_configured_says_which_variable_is_missing(erp, session):
    """The fixture profile's 门店 is one this example invented; an order must never carry it."""
    backend = build(erp, store="")
    await backend.load_listings()
    await search_yili(backend, session)
    with pytest.raises(NotOffered, match="TOUR_ERP_STORE_NAME"):
        await backend.add_to_cart(session, OPEN, 4)
    assert await erp.list_orders() == []


# -- the boot snapshot a live catalog gets --------------------------------------------------


async def test_a_live_snapshot_holds_every_route_in_the_window_at_no_call_per_route():
    """The workbench's home page names the directions the catalog sells off this snapshot, so a
    live boot holds every 线路 selling seats in the default window and not the first few of them.
    It costs what one search costs — the route list, and the window's 团期 read once for every
    route in it — and asks nothing about any one route."""
    erp = BigCatalog(today=TODAY, now=FakeClock())
    backend = build(erp)
    await backend.load_listings()
    assert len(backend.products) == CATALOG_ROUTES
    assert erp.calls == {"search_routes": 1, "list_window": 1}
    family = backend.product("RT-9000")
    assert family is not None and family.attributes["destination"]
    # The 团期 the window holds are the dates the family offers; the route's 起价 is its price,
    # because no departure was quoted and a listed row carries no 市场价 either.
    assert family.options["depart_date"] == [(TODAY + timedelta(days=14)).isoformat()]
    assert family.price == 5580.0
    # No variant is snapshotted: a 团期 the host asks for is the conversation's own live read.
    assert family.variants == []
    assert backend.product("DP-7000") is None


# -- the tool surface and the brand ---------------------------------------------------------


def test_the_policy_tool_is_not_on_the_live_surface():
    """The agency's 退改 and 儿童价 rules are in a knowledge base this deployment does not
    read, so there is no tool for them and the notes say what to answer instead."""
    live = build_shopping_config(live=True)
    assert live.enable_policies is False
    assert "search_policies" in live.absent_tools()
    assert "knowledge base" in live.domain_search_notes
    assert "never state one from memory" in live.domain_search_notes
    demo = build_shopping_config()
    assert demo.enable_policies is True
    assert "search_policies" not in demo.absent_tools()
    assert "knowledge base" not in demo.domain_search_notes


def test_the_plan_tool_stands_whatever_the_erp_behind_it_is():
    """A 定制方案 is the advisor's own work on a 线路 they were shown, so nothing about it is
    the fixtures': the tool is on the surface either way, and the rules for it are in both
    sets of notes."""
    for config in (build_shopping_config(), build_shopping_config(live=True)):
        assert "present_itinerary" in config.domain_search_notes
        assert "待计调确认" in config.domain_search_notes
        assert "add_to_cart is for a 团期 alone" in config.domain_search_notes


def test_the_plan_tool_is_registered_beside_the_shortlist(main):
    names = {tool["name"] for tool in main.agent._tools}
    assert {"present_shortlist", "present_itinerary"} <= names


def test_the_notes_state_the_two_rules_a_beta_catalog_needs():
    notes = build_shopping_config().domain_search_notes
    # A 团期's own dates disagree with its 线路's day count, so the record's days is the answer.
    assert "never count it off depart_date and return_date" in notes
    # The advisor works in the ERP's backstage; there is no App in this deployment.
    assert "there is no App" in notes


def test_the_brand_and_the_assistant_come_from_the_environment(monkeypatch):
    assert build_shopping_config().brand_name == DEFAULT_BRAND_NAME
    assert build_shopping_config().assistant_name == DEFAULT_ASSISTANT_NAME
    monkeypatch.setenv("TOUR_BRAND_NAME", "环行国际旅行社")
    monkeypatch.setenv("TOUR_ASSISTANT_NAME", "排团助手")
    config = build_shopping_config(live=True)
    assert (config.brand_name, config.assistant_name) == ("环行国际旅行社", "排团助手")


async def test_the_store_name_a_live_backend_carries_is_the_brand(erp, session):
    """The fixture's own store_name is in routes.json, which a live catalog has nothing to do
    with, so the brand is what a record with no department on it is branded with."""
    backend = build(erp)
    assert backend.store_name == DEFAULT_BRAND_NAME


def test_a_live_deployment_seeds_no_memory():
    """The seeded habits are this example's; a real advisor's memory is theirs to write."""
    import json

    assert json.loads((DATA_DIR / "memory-seed-empty.json").read_text(encoding="utf-8")) == {}
    assert json.loads((DATA_DIR / "memory-seed.json").read_text(encoding="utf-8"))


# -- what a beta catalog carries ------------------------------------------------------------


async def test_a_fare_the_party_needs_at_zero_is_unpublished_and_not_free(session):
    """0 in this ERP means 未发布. A party with a child and no 儿童价 has no total at all; the
    same 团期 totals for an adults-only party."""
    erp = NoChildFare(today=TODAY, now=FakeClock())
    backend = build(erp)
    await backend.load_listings()
    await search_yili(backend, session)
    details = await backend.get_product_details(session, OPEN)
    assert details.attributes["quote_source"] == "partial"
    assert details.attributes["party_quote_total"] == "0"
    assert details.attributes["child_price"] == "0"
    assert details.attributes["unpublished_fares"] == "儿童价"
    assert "儿童价未发布，合计待定" in details.short_description
    assert "同业价成人 5780 元" in details.short_description
    # An adults-only party is not short of anything the 团期 prices.
    await search_yili(backend, session, children="0")
    adults_only = await backend.get_product_details(session, OPEN)
    assert adults_only.attributes["quote_source"] == "customer"
    assert adults_only.attributes["party_quote_total"] == "11560"


async def test_a_variant_carries_the_erps_own_price_type(erp, session):
    backend = build(erp)
    await backend.load_listings()
    await search_yili(backend, session)
    details = await backend.get_product_details(session, OPEN)
    assert details.attributes["price_type"] == "同行价"


@pytest.mark.parametrize(
    ("min_group_size", "confirm_count", "expected"),
    [(0, 0, "pending"), (0, 2, "confirmed"), (2, 2, "confirmed"), (2, 1, "pending")],
)
def test_a_departure_with_no_minimum_group_size_is_not_already_confirmed(
    min_group_size, confirm_count, expected
):
    """A 0 in ``minGroupSize`` states no 最低成团人数 rather than needing nobody."""
    row = DepartureRecord(
        period_id=1,
        period_code="X-1",
        route_id=1,
        route_name="线路",
        depart_date=TODAY,
        return_date=TODAY,
        days=1,
        plan_guests=20,
        min_group_size=min_group_size,
        confirm_count=confirm_count,
        available_seats=8,
        reserve_hours=24,
        depart_city="上海",
        company_id=2,
    )
    assert _group_status(row) == expected


async def test_a_period_with_no_route_id_is_not_a_departure(session):
    """A window read carries rows the ERP left ``routeId`` 0 on; they belong to no 线路 this
    side can name, price or book, and asking for id 0 costs no catalog scan."""
    erp = WindowErp(today=TODAY, now=FakeClock())
    backend = build(erp)
    await backend.load_listings()
    products = await search_yili(backend, session)
    assert products and all(product.product_id != "RT-0" for product in products)
    details = await backend.get_product_details(session, ROUTE)
    assert 99001 not in [int(v.product_id.removeprefix("DP-")) for v in details.variants]
    assert await backend.get_product_details(session, "RT-0") is None
