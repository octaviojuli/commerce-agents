# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What changes when the ERP behind the backend is the agency's own (``live=True``): the
advisor's identity and department come from the ERP login, the 同行 customer is resolved from
its 客户编码 and there is none until it resolves, an order needs the deployment's own 门店, the
policy tool is not registered, and the brand names come from the environment. The fixtures are
what stands in for the agency's ERP here — a client exposing ``user_info``, ``companies`` and
``search_customers`` is all the backend reads to tell who is logged in — so nothing in this
suite touches a real one. The data guards are here too, because a beta catalog is where a 0
fare and a 团期 with no 线路 come from."""

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from shopping_agent import NotOffered, ShoppingSessionContext
from tour.api.agent_config import (
    DEFAULT_ASSISTANT_NAME,
    DEFAULT_BRAND_NAME,
    build_shopping_config,
)
from tour.api.erp_client import CustomerRecord, DepartureRecord, ErpUnavailable, PriceInfo, Quote
from tour.api.mock_erp import MockErpClient
from tour.api.tour_backend import DATA_DIR, TourBackend, _group_status

from .test_tour_backend import (
    MOBILE,
    OPEN,
    ROUTE,
    TODAY,
    FakeClock,
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


def build(erp: MockErpClient, *, code: str = CODE, store: str = STORE) -> TourBackend:
    return TourBackend(
        erp,
        today=TODAY,
        customer_id=0,
        contact_mobile=MOBILE,
        live=True,
        customer_code=code,
        store_name=DEFAULT_BRAND_NAME,
        order_store_name=store,
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
