# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The mock ERP's search, its two-layer 团期 reads, and its order book. Every client is built
on a fixed ``today`` and a fake clock, so the fixture's dates and ids are the same whatever
day the suite runs."""

from datetime import UTC, date, datetime, timedelta

import pytest

from tour.api.erp_client import ErpClient, ErpNotFound, ErpRefused, OrderRequest, RouteQuery
from tour.api.mock_erp import MockErpClient

TODAY = date(2026, 9, 6)
LATER = TODAY + timedelta(weeks=3)
WINDOW = (TODAY, date(2026, 11, 30))
CUSTOMER = 4101
CONTACT = ("柯海水", "13800138000")
ROUTE = 1021  # 伊犁北疆环线 8 日纯玩小团, 8 seats a group


class FakeClock:
    def __init__(self, on: date = TODAY) -> None:
        self.current = datetime(on.year, on.month, on.day, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, hours: float) -> None:
        self.current += timedelta(hours=hours)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def erp(clock: FakeClock) -> MockErpClient:
    return MockErpClient(today=TODAY, now=clock)


def query(**overrides) -> RouteQuery:
    return RouteQuery(**{"depart_from": WINDOW[0], "depart_to": WINDOW[1], **overrides})


async def order(erp: MockErpClient, period_id: int, adults: int = 2, **overrides) -> OrderRequest:
    fields = {
        "period_id": period_id,
        "customer_id": CUSTOMER,
        "adults": adults,
        "children": 0,
        "elders": 0,
        "rooms": 1,
        "single_room_diff_count": 0,
        "contact_name": CONTACT[0],
        "contact_mobile": CONTACT[1],
        "store_name": "ACME 旅行社",
    }
    return OrderRequest(**{**fields, **overrides})


async def first_departure(erp: MockErpClient, route_id: int = ROUTE, seats: int = 1):
    """The route's first departure in the window with at least ``seats`` free."""
    rows = await erp.list_departures(route_id, "", *WINDOW)
    return next(row for row in rows if row.available_seats >= seats)


def test_the_mock_is_an_erp_client():
    client: ErpClient = MockErpClient(today=TODAY)
    assert isinstance(client, MockErpClient)


async def test_search_matches_the_route_name(erp: MockErpClient):
    routes = await erp.search_routes(query(route_name="喀纳斯"))
    assert {route.route_id for route in routes} == {1031, 1032}


async def test_search_matches_a_tag_the_erp_carries(erp: MockErpClient):
    routes = await erp.search_routes(query(route_name="亲子"))
    assert {route.route_id for route in routes} == {1021, 1051}


async def test_search_keeps_only_routes_with_a_departure_in_the_window(erp: MockErpClient):
    routes = await erp.search_routes(query(depart_to=date(2026, 9, 13)))
    ids = {route.route_id for route in routes}
    assert ids == {1021, 1022, 1023, 1024, 1032}


async def test_a_destination_the_catalog_does_not_carry_finds_nothing(erp: MockErpClient):
    assert await erp.search_routes(query(route_name="马尔代夫")) == []


async def test_list_departures_returns_one_routes_dates_in_order(erp: MockErpClient):
    rows = await erp.list_departures(ROUTE, "伊犁北疆环线 8 日纯玩小团", *WINDOW)
    assert {row.route_id for row in rows} == {ROUTE}
    assert [row.depart_date for row in rows] == sorted(row.depart_date for row in rows)
    assert all(row.price is None for row in rows)


async def test_a_listed_departure_has_no_price_and_a_fetched_one_does(erp: MockErpClient):
    listed = await first_departure(erp)
    fetched = await erp.get_departure(listed.period_id)
    assert fetched is not None and fetched.price is not None
    assert fetched.price.elder == fetched.price.adult
    assert (fetched.reserve_count, fetched.waitlist_count) == (0, 0)


async def test_an_unknown_departure_is_no_departure(erp: MockErpClient):
    assert await erp.get_departure(1) is None


async def test_the_fixture_carries_departures_that_have_not_formed_yet(erp: MockErpClient):
    rows = await erp.list_departures(ROUTE, "", *WINDOW)
    assert any(row.confirm_count < row.min_group_size for row in rows)
    assert any(row.available_seats == 0 for row in rows)


async def test_search_customers_finds_the_trade_customer_by_name(erp: MockErpClient):
    (customer,) = await erp.search_customers("北京")
    assert (customer.customer_id, customer.customer_type) == (CUSTOMER, 1)


async def test_quote_is_the_list_price_under_the_trade_label(erp: MockErpClient):
    row = await first_departure(erp)
    quote = await erp.quote(row.period_id, CUSTOMER)
    fetched = await erp.get_departure(row.period_id)
    assert quote.price_type == "同行价"
    assert quote.is_external is False
    assert fetched is not None and quote.price == fetched.price


async def test_an_order_takes_the_seats_and_reserves_them_for_the_erps_own_window(
    erp: MockErpClient, clock: FakeClock
):
    row = await first_departure(erp, seats=2)
    result = await erp.create_order(await order(erp, row.period_id))
    assert (result.status, result.is_waitlist, result.needs_approval) == (0, False, False)
    after = await erp.get_departure(row.period_id)
    assert after is not None and after.available_seats == row.available_seats - 2
    stored = await erp.get_order(result.order_id)
    assert stored is not None and stored.status_text == "预留"
    assert stored.reserve_expires_at == clock() + timedelta(hours=row.reserve_hours)


async def test_an_order_is_priced_by_the_party_and_the_single_rooms(erp: MockErpClient):
    row = await first_departure(erp, seats=2)
    price = (await erp.get_departure(row.period_id)).price
    result = await erp.create_order(
        await order(erp, row.period_id, adults=1, children=1, single_room_diff_count=1)
    )
    stored = await erp.get_order(result.order_id)
    assert stored is not None
    assert stored.total_amount == price.adult + price.child + price.single_room_diff
    assert stored.unreceived_amount == stored.total_amount
    assert stored.order_no == f"ORD{result.order_id:06d}"


async def test_a_party_over_the_seats_left_is_a_waitlist_not_a_refusal(erp: MockErpClient):
    row = await first_departure(erp)
    result = await erp.create_order(await order(erp, row.period_id, adults=row.available_seats + 1))
    assert (result.status, result.is_waitlist) == (5, True)
    after = await erp.get_departure(row.period_id)
    assert after is not None and after.available_seats == row.available_seats
    stored = await erp.get_order(result.order_id)
    assert stored is not None and stored.status_text == "候补"
    assert stored.reserve_expires_at is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"adults": 0}, "至少 1 人"),
        ({"contact_name": "K"}, "汉字"),
        ({"contact_mobile": "8613800138000"}, "手机号"),
    ],
    ids=["empty-party", "contact-name", "mobile"],
)
async def test_the_erps_own_checks_refuse_in_chinese(erp: MockErpClient, overrides, message):
    row = await first_departure(erp)
    with pytest.raises(ErpRefused, match=message):
        await erp.create_order(await order(erp, row.period_id, **overrides))


async def test_an_order_for_a_customer_or_a_departure_the_erp_does_not_have(erp: MockErpClient):
    row = await first_departure(erp)
    with pytest.raises(ErpNotFound):
        await erp.create_order(await order(erp, row.period_id, customer_id=1))
    with pytest.raises(ErpNotFound):
        await erp.create_order(await order(erp, 1))


async def test_the_order_book_lists_by_status_newest_first(erp: MockErpClient):
    row = await first_departure(erp, seats=2)
    reserved = await erp.create_order(await order(erp, row.period_id))
    waitlisted = await erp.create_order(await order(erp, row.period_id, adults=99))
    assert [o.order_id for o in await erp.list_orders()] == [
        waitlisted.order_id,
        reserved.order_id,
    ]
    assert [o.order_id for o in await erp.list_orders((0,))] == [reserved.order_id]
    assert [o.order_id for o in await erp.list_orders((5,))] == [waitlisted.order_id]
    assert await erp.get_order(999) is None


async def test_the_dates_shift_by_whole_weeks_and_the_period_code_follows():
    pinned = MockErpClient(today=TODAY)
    shifted = MockErpClient(today=LATER)
    first = (await pinned.list_departures(ROUTE, "", TODAY, date(2026, 12, 31)))[0]
    same = (await shifted.list_departures(ROUTE, "", TODAY, date(2026, 12, 31)))[0]
    assert same.period_id == first.period_id
    assert same.depart_date == first.depart_date + timedelta(weeks=3)
    assert same.return_date == first.return_date + timedelta(weeks=3)
    stamp = same.depart_date.strftime("%Y%m%d")
    assert same.period_code == f"XJ-YLBJ-{stamp}-001"
    assert first.period_code == f"XJ-YLBJ-{first.depart_date.strftime('%Y%m%d')}-001"
