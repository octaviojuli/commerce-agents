# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour backend's mapping onto ``StorefrontBackend``: 线路 as families, 团期 as variants
quoted for the searched party, the 预留 orders this conversation wrote as cart lines, and the
relaxation that answers a request the catalog cannot meet exactly. Every backend runs on a
fixed ``today``, so the fixture's dates and ids are the same whatever day the suite runs."""

from datetime import UTC, date, datetime, timedelta

import pytest

from commerce_common.skills import SkillRegistry
from shopping_agent import (
    NotOffered,
    SearchFilters,
    ShoppingAgentConfig,
    ShoppingSessionContext,
    ShoppingSessionState,
    Unavailable,
)
from shopping_agent.executor import ShoppingToolExecutor
from tour.api import tour_backend
from tour.api.erp_client import (
    DepartureRecord,
    ErpAuth,
    ErpNotFound,
    ErpRefused,
    ErpThrottled,
    ErpUnavailable,
    PriceInfo,
    Quote,
)
from tour.api.mock_erp import MockErpClient
from tour.api.tour_backend import TourBackend, TourToolExecutor

TODAY = date(2026, 9, 6)
ADVISOR = "demo-user"
CUSTOMER_ID = 4101
MOBILE = "13900000001"
ROUTE = "RT-1021"
FULL = "DP-3007"  # 伊犁北疆环线, 10/13, no seats left
OPEN = "DP-3008"  # 伊犁北疆环线, 10/17, four seats left

# The advisor's stated request: 伊犁, 10/11-10/20, 8-10 天, 2 大 2 小, 纯玩.
YILI = {
    "destination": "伊犁",
    "depart_from": "2026-10-11",
    "depart_to": "2026-10-20",
    "days_min": "8",
    "days_max": "10",
    "adults": "2",
    "children": "2",
    "child_ages": "5|9",
    "no_shopping": "yes",
}


class FakeClock:
    def __init__(self, on: date = TODAY) -> None:
        self.current = datetime(on.year, on.month, on.day, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current


def build(erp: MockErpClient) -> TourBackend:
    return TourBackend(erp, today=TODAY, customer_id=CUSTOMER_ID, contact_mobile=MOBILE)


@pytest.fixture
def erp() -> MockErpClient:
    return MockErpClient(today=TODAY, now=FakeClock())


@pytest.fixture
def backend(erp: MockErpClient) -> TourBackend:
    return build(erp)


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="ts-1", user_id=ADVISOR)


@pytest.fixture
def other_session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="ts-2", user_id=ADVISOR)


async def search_yili(backend, session, **overrides):
    filters = SearchFilters(attributes={**YILI, **overrides})
    return await backend.search_products(session, "伊犁亲子游", filters)


async def search(backend, session, query, **attributes):
    return await backend.search_products(session, query, SearchFilters(attributes=dict(attributes)))


def executor(backend, session) -> TourToolExecutor:
    return TourToolExecutor(
        backend=backend,
        config=ShoppingAgentConfig(brand_name="ACME"),
        skills=SkillRegistry([]),
        session=session,
        state=ShoppingSessionState(),
    )


def ids(products) -> list[str]:
    return [product.product_id for product in products]


# -- search ------------------------------------------------------------------------------


async def test_search_returns_route_families_for_the_stated_window(backend, session):
    products = await search_yili(backend, session)
    assert set(ids(products)) == {"RT-1021", "RT-1022", "RT-1024"}
    # RT-1023 carries neither 纯玩 nor 零购物 in its name or its tags, and nothing relaxes
    # far enough to add it.
    for product in products:
        assert product.variant_of is None
        assert product.options["depart_date"]
        assert product.attributes["match"] == "exact"
        assert product.category == "tour"
        assert product.currency == "CNY"
        assert product.attributes["depart_city"] == "乌鲁木齐"
    route = next(product for product in products if product.product_id == ROUTE)
    # The window holds 10/13 (no seats) and 10/17 (four), so the family is the cheaper of
    # the two list prices and is in stock for the 2 大 2 小 party.
    assert route.options["depart_date"] == ["2026-10-13", "2026-10-17"]
    assert route.price == 5780.0
    assert route.in_stock is True
    assert route.labels == ["纯玩", "小团", "亲子", "轻徒步"]
    assert route.attributes["tags"].startswith("纯玩|小团")
    # The two dates are the span the advisor means, whichever order they state them in.
    backwards = await search_yili(
        backend, session, depart_from="2026-10-20", depart_to="2026-10-11"
    )
    assert [(p.product_id, p.attributes["match"]) for p in backwards] == [
        (product.product_id, "exact") for product in products
    ]


async def test_a_hotel_standard_is_matched_however_the_advisor_spells_it(backend, session):
    """The ERP states the standard only as a word in the name or the tags, and 五钻, 5钻,
    五星 and 5星 are the same standard."""
    for level in ("五钻", "5钻", "五星", "5星"):
        products = await search_yili(backend, session, hotel_level=level)
        exact = [p.product_id for p in products if p.attributes["match"] == "exact"]
        assert exact == ["RT-1024"], level


# -- details -----------------------------------------------------------------------------


async def test_route_details_quote_the_searched_party_over_the_padded_window(backend, session):
    await search_yili(backend, session)
    details = await backend.get_product_details(session, ROUTE)
    assert 0 < len(details.variants) <= 24
    assert details.specs["行程天数"] == "8 天"
    assert details.specs["出发城市"] == "乌鲁木齐"
    assert "四钻" in details.specs["标签"]
    for variant in details.variants:
        assert variant.variant_of == ROUTE
        departs = date.fromisoformat(variant.attributes["depart_date"])
        assert date(2026, 10, 4) <= departs <= date(2026, 10, 27)
        assert variant.attributes["quote_party"] == "2大2小"
        assert variant.attributes["quote_source"] == "list"
        quote = 2 * float(variant.attributes["adult_price"]) + 2 * float(
            variant.attributes["child_price"]
        )
        assert float(variant.attributes["party_quote_total"]) == quote
    full = next(v for v in details.variants if v.product_id == FULL)
    assert full.in_stock is False
    assert full.attributes["seats_left"] == "0"
    assert full.attributes["seats_total"] == "8"


async def test_a_group_is_confirmed_pending_or_waiting_on_the_erps_own_counts(backend, session):
    """成团 is confirmCount against minGroupSize; a departure whose seats are gone and whose
    waitlist is no longer empty is neither, and says so."""
    details = await backend.get_product_details(session, "RT-1023")
    status = {v.product_id: v.attributes["group_status"] for v in details.variants}
    assert status["DP-3020"] == "confirmed"  # 20 confirmed against a minimum of 6
    assert status["DP-3028"] == "pending"  # 4 against 6
    await backend.add_to_cart(session, FULL, 4)  # over the seats left: a 候补 order
    reread = await backend.get_product_details(session, FULL)
    assert reread.attributes["group_status"] == "waitlist"


async def test_a_departure_id_is_quoted_at_the_customers_own_price(backend, session):
    await search_yili(backend, session)
    details = await backend.get_product_details(session, OPEN)
    assert details.variant_of == ROUTE
    assert details.variants == []
    assert details.option_values == {"depart_date": "2026-10-17"}
    assert details.attributes["quote_source"] == "customer"
    assert details.attributes["period_code"] == "XJ-YLBJ-20261017-001"
    assert details.price == 5780.0
    assert float(details.attributes["party_quote_total"]) == 2 * 5780.0 + 2 * 3880.0
    assert await backend.get_product_details(session, "DP-999999") is None
    assert await backend.get_product_details(session, "not-an-id") is None


class Unpriced(MockErpClient):
    """An ERP with no price row for this customer on this departure, as beta answers for a
    departure the catalog never priced."""

    async def quote(self, period_id: int, customer_id: int) -> Quote:
        raise ErpNotFound("找不到该团期的报价")


async def test_a_departure_without_a_customer_price_falls_back_to_the_list_price(session):
    backend = build(Unpriced(today=TODAY, now=FakeClock()))
    details = await backend.get_product_details(session, OPEN)
    assert details.attributes["quote_source"] == "list"
    assert details.price == 5780.0


async def test_a_pasted_route_id_is_quoted_for_a_default_party_it_states(backend, other_session):
    """No search ran in this conversation, so the quote is for two adults over the next
    sixty days, and ``quote_party`` says so rather than implying the advisor's party."""
    details = await backend.get_product_details(other_session, ROUTE)
    departures = [date.fromisoformat(v.attributes["depart_date"]) for v in details.variants]
    assert details.variants and all(
        v.attributes["quote_party"] == "2大0小" for v in details.variants
    )
    assert min(departures) >= TODAY
    assert max(departures) <= TODAY + timedelta(days=67)


async def test_a_route_the_session_never_searched_still_resolves(backend, session):
    """The advisor searched one day of 伊犁; 青海 was never returned, so the id is looked up
    over the default window rather than that day, and comes back quotable."""
    await search(backend, session, "伊犁", depart_from="2026-10-11", depart_to="2026-10-11")
    details = await backend.get_product_details(session, "RT-1051")
    assert [v.attributes["depart_date"] for v in details.variants] == ["2026-10-09", "2026-10-16"]


async def test_a_window_the_route_does_not_run_in_falls_back_to_the_default_one(backend, session):
    """Nothing departs near the window this session searched. The default window's 团期 are
    the answer the advisor can act on: their dates say when the route actually runs, which a
    family with no variants does not."""
    await search(backend, session, "新疆", depart_from="2027-06-01", depart_to="2027-06-30")
    details = await backend.get_product_details(session, ROUTE)
    departures = [date.fromisoformat(v.attributes["depart_date"]) for v in details.variants]
    assert departures
    assert min(departures) >= TODAY
    assert max(departures) <= TODAY + timedelta(days=60)


class DailyDepartures(MockErpClient):
    """Route 1021 runs every day of whatever window it is asked about; every other route
    answers from the fixtures."""

    async def list_departures(self, route_id, route_name, depart_from, depart_to):
        if route_id != 1021:
            return await super().list_departures(route_id, route_name, depart_from, depart_to)
        span = (depart_to - depart_from).days + 1
        return [_daily(depart_from + timedelta(days=day)) for day in range(span)]


def _daily(depart: date) -> DepartureRecord:
    return DepartureRecord(
        period_id=int(depart.strftime("%m%d")),
        period_code=f"XJ-YLBJ-{depart:%Y%m%d}-001",
        route_id=1021,
        route_name="伊犁北疆环线 8 日纯玩小团",
        depart_date=depart,
        return_date=depart + timedelta(days=7),
        days=8,
        plan_guests=16,
        min_group_size=2,
        confirm_count=4,
        available_seats=16,
        reserve_hours=24,
        depart_city="乌鲁木齐",
        price=PriceInfo(6980.0, 4980.0, 6980.0, 1200.0),
    )


async def test_a_route_that_runs_every_day_is_trimmed_around_the_searched_window(session):
    """One fenced result cannot hold a 团期 a day, so the details keep the 24 nearest the
    middle of the window the advisor is working in and drop the far ends of the run."""
    backend = build(DailyDepartures(today=TODAY, now=FakeClock()))
    await search(backend, session, "伊犁", depart_from="2026-09-20", depart_to="2026-10-05")
    details = await backend.get_product_details(session, ROUTE)
    departures = [date.fromisoformat(v.attributes["depart_date"]) for v in details.variants]
    # The padded window runs 9/13–10/12, so its middle is 9/27 and both ends are trimmed.
    window = [date(2026, 9, 13) + timedelta(days=day) for day in range(30)]
    middle = date(2026, 9, 27)
    assert len(departures) == 24
    assert departures == sorted(departures)
    assert window[0] < departures[0] and departures[-1] < window[-1]
    dropped = set(window) - set(departures)
    assert max(abs((day - middle).days) for day in departures) <= min(
        abs((day - middle).days) for day in dropped
    )


# -- relaxation --------------------------------------------------------------------------


async def test_a_window_with_no_departures_offers_the_nearest_date(backend, session):
    products = await search(
        backend,
        session,
        "青海湖",
        destination="青海",
        depart_from="2026-10-03",
        depart_to="2026-10-08",
    )
    assert ids(products) == ["RT-1051"]
    assert products[0].attributes["match"] == "adjacent_date"
    assert products[0].attributes["mismatch"] == "无 10/3–10/8 团期，最近为 10/2"


async def test_a_day_count_nothing_matches_offers_the_nearest_length(backend, session):
    products = await search(
        backend, session, "南疆", destination="南疆", days_min="12", days_max="12"
    )
    assert ids(products) == ["RT-1041"]
    assert products[0].attributes["match"] == "similar_route"
    assert products[0].attributes["mismatch"] == "天数 11 天，超出要求的 12–12 天"


async def test_dropping_a_preference_names_the_one_it_dropped(backend, session):
    products = await search(
        backend, session, "喀纳斯", destination="喀纳斯", hotel_level="五钻", no_shopping="yes"
    )
    notes = {product.product_id: product.attributes["mismatch"] for product in products}
    assert set(notes) == {"RT-1031", "RT-1032"}
    assert all(product.attributes["match"] == "similar_route" for product in products)
    assert notes["RT-1031"] == "未标注五钻"
    assert notes["RT-1032"] == "未标注五钻；未标注纯玩或零购物"


async def test_a_relaxed_route_names_every_condition_it_misses(backend, session):
    """RT-1031 is admitted by the step that drops the preferences, and it misses the window
    too; the advisor reads the whole list back, so the note carries both."""
    products = await search(
        backend,
        session,
        "喀纳斯",
        destination="喀纳斯",
        depart_from="2026-10-03",
        depart_to="2026-10-05",
        hotel_level="五钻",
        no_shopping="yes",
    )
    notes = {product.product_id: product.attributes["mismatch"] for product in products}
    assert notes["RT-1031"] == "无 10/3–10/5 团期，最近为 10/1；未标注五钻"
    # RT-1032 does depart on 10/3, so the window is not one of the conditions it misses.
    assert notes["RT-1032"] == "未标注五钻；未标注纯玩或零购物"


# -- cart: the 预留 orders the conversation wrote -------------------------------------------


async def test_an_add_writes_the_order_and_becomes_one_cart_line(backend, erp, session):
    await search_yili(backend, session)
    cart = await backend.add_to_cart(session, OPEN, 4)
    assert len(cart.items) == 1
    line = cart.items[0]
    assert (line.product_id, line.quantity, line.variant_of) == (OPEN, 4, ROUTE)
    assert line.option_values == {"depart_date": "2026-10-17"}
    assert line.title == "伊犁北疆环线 8 日纯玩小团 10/17 出发"
    # The searched party splits the heads the model asked for, and the ERP's own total is
    # what the line adds back up to.
    (order,) = await erp.list_orders()
    assert (order.adults, order.children, order.elders) == (2, 2, 0)
    assert (order.customer_id, order.contact_mobile) == (CUSTOMER_ID, MOBILE)
    assert order.contact_name == "林晓"
    assert order.status_text == "预留"
    assert cart.subtotal == order.total_amount
    # The seats the order took are gone from the departure.
    assert (await erp.get_departure(3008)).available_seats == 0
    # The 30-minute countdown the advisor sees is ours; the ERP's own 预留 runs on its
    # reserveHours whatever we say.
    (order_no, product_id, expires_at) = backend.holds_snapshot(session.session_id)[0]
    assert (order_no, product_id) == (order.order_no, OPEN)
    assert timedelta(minutes=29) < expires_at - datetime.now(UTC) <= timedelta(minutes=30)


async def test_a_party_over_the_seats_left_is_a_waitlist_line_that_says_so(backend, session):
    await search_yili(backend, session)
    cart = await backend.add_to_cart(session, FULL, 4)
    assert cart.items[0].title.endswith("（候补）")
    assert cart.items[0].product_id == FULL
    # A 候补 order holds no seats, so it is not one of the conversation's 占位.
    assert backend.holds_snapshot(session.session_id) == []
    assert (await backend.get_account_context(session))["active_holds"] == 1


async def test_only_a_departure_id_can_be_booked(backend, session):
    await search_yili(backend, session)
    with pytest.raises(Unavailable):
        await backend.add_to_cart(session, ROUTE, 4)
    with pytest.raises(Unavailable):
        await backend.add_to_cart(session, "not-an-id", 4)


async def test_a_line_keeps_one_adult_whatever_the_model_asked_for(backend, erp, session):
    """The searched party is 2 大 2 小, but one head is one 成人: the ERP seats no child on
    their own, and an order for nobody is not an order."""
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 1)
    (order,) = await erp.list_orders()
    assert (order.adults, order.children) == (1, 0)


async def test_a_hold_older_than_half_an_hour_drops_out(backend, erp, session, monkeypatch):
    """The 30-minute window is ours, not the ERP's, so the line goes without asking it
    anything; the order itself stands in the ERP until the advisor deals with it."""
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    reads: list[int] = []
    original = erp.get_order

    async def counted(order_id: int):
        reads.append(order_id)
        return await original(order_id)

    erp.get_order = counted
    later = datetime.now(UTC) + timedelta(minutes=31)
    monkeypatch.setattr(tour_backend, "_utcnow", lambda: later)
    cart = await backend.get_cart(session)
    assert cart.items == []
    assert reads == []
    assert backend.holds_snapshot(session.session_id) == []
    assert len(await erp.list_orders()) == 1


async def test_the_cart_cannot_cancel_or_resize_what_the_erp_wrote(backend, session):
    """The ERP has no cancel, release or amend call, so both are refused with the reason
    the advisor acts on."""
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    with pytest.raises(NotOffered, match="ERP 后台"):
        await backend.remove_from_cart(session, OPEN)
    with pytest.raises(NotOffered, match="ERP 后台"):
        await backend.update_cart_item(session, OPEN, 2)
    assert [item.product_id for item in (await backend.get_cart(session)).items] == [OPEN]


@pytest.mark.parametrize(
    "error",
    [
        ErpRefused("联系人姓名需为 2-50 个汉字。"),
        ErpNotFound("找不到该团期：3999"),
        ErpThrottled("旅行社 ERP 登录过于频繁，请稍后再试。"),
        ErpAuth("旅行社 ERP 登录失败，请检查账号、密码与所属部门。"),
    ],
    ids=["refused", "not-found", "throttled", "auth"],
)
def test_the_advisor_hears_the_erps_own_rule_instead_of_an_outage(backend, session, error):
    relayed = executor(backend, session).domain_error(error)
    assert relayed.is_error
    assert relayed.result_text.startswith("Nothing changed:")
    assert str(error)[:6] in relayed.result_text


def test_an_unreachable_erp_stays_an_outage(backend, session):
    """``ErpUnavailable`` is the ERP being down, which the base executor already words."""
    tour = executor(backend, session)
    error = ErpUnavailable("旅行社 ERP 暂时无法连接，请稍后再试。")
    assert tour.domain_error(error) == ShoppingToolExecutor.domain_error(tour, error)


async def test_nothing_ships_for_a_tour_booking(backend, session):
    with pytest.raises(NotOffered):
        await backend.get_fulfillment_options(session, [OPEN])


# -- orders, account, help -----------------------------------------------------------------


async def test_the_orders_are_the_salespersons_own_ones_from_the_erp(backend, erp, session):
    assert await backend.get_orders(session) == []
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    (order,) = await backend.get_orders(session)
    (record,) = await erp.list_orders()
    assert order.order_id == str(record.order_id)
    assert order.status == "processing"
    assert order.total == record.total_amount
    assert order.currency == "CNY"
    assert order.estimated_delivery == "预留，出发 2026-10-17"
    assert [item.product_id for item in order.items] == [OPEN]
    assert order.items[0].quantity == 4
    # One order by the ERP's id, and by the 订单号 the advisor reads off a screen.
    assert (await backend.get_order(session, str(record.order_id))).order_id == order.order_id
    assert (await backend.get_order(session, record.order_no)).order_id == order.order_id
    assert await backend.get_order(session, "70999") is None
    assert await backend.get_order(session, "ORD000000") is None


async def test_the_account_context_names_the_advisor_the_store_and_the_customer(backend, session):
    assert await backend.get_account_context(session) == {
        "advisor": ADVISOR,
        "store": "上海徐汇门店",
        "customer_id": CUSTOMER_ID,
        "active_holds": 0,
    }
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    assert (await backend.get_account_context(session))["active_holds"] == 1


# -- the demo host's view ----------------------------------------------------------------


async def test_the_listing_snapshot_holds_every_route_with_its_departures(backend):
    await backend.load_listings()
    assert backend.store_name == "ACME 旅行社"
    assert len(backend.products) == 8
    for family in backend.products.values():
        assert family.variants
        assert all(variant.variant_of == family.product_id for variant in family.variants)


class BrokenErp(MockErpClient):
    async def search_routes(self, q):
        raise ErpUnavailable("旅行社 ERP 暂时无法连接，请稍后再试。")


async def test_an_erp_that_is_down_leaves_the_snapshot_empty_rather_than_stopping_boot():
    backend = build(BrokenErp(today=TODAY, now=FakeClock()))
    await backend.load_listings()
    assert backend.products == {}
    assert backend.product(ROUTE) is None


async def test_the_snapshot_resolves_a_route_id_and_a_departure_id(backend):
    await backend.load_listings()
    assert backend.product(ROUTE).product_id == ROUTE
    assert backend.product(OPEN).variant_of == ROUTE
    assert backend.product("RT-9999") is None


async def test_resetting_a_session_forgets_the_window_the_party_and_the_cart(backend, session):
    await search_yili(backend, session)
    held = await backend.get_product_details(session, OPEN)
    assert held.attributes["quote_party"] == "2大2小"
    await backend.add_to_cart(session, OPEN, 4)
    backend.reset_session(session.session_id)
    fresh = await backend.get_product_details(session, OPEN)
    assert fresh.attributes["quote_party"] == "2大0小"
    assert (await backend.get_cart(session)).items == []
    assert backend.holds_snapshot(session.session_id) == []


def test_the_demo_starts_with_no_cross_user_order_feed(backend):
    """Every 报名单 in this demo belongs to the salesperson the ERP logged in; the portal
    feed a merchant example fills has nothing to show here."""
    assert backend.recent_orders() == []
