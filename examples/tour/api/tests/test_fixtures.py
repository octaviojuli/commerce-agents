# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What ``data/routes.json``, ``data/departures.json`` and ``data/customers.json`` have to
hold for the mock ERP to answer like the real one: the ERP's own row keys, ids the client
never rebuilds, codes it does rebuild from the shifted date, and seats that add up. Read as
raw JSON, before the client shifts the dates, so an authoring slip fails here rather than
downstream."""

import re
from collections import Counter
from datetime import date

import pytest

from demo_common.storefront_fixtures import load_json
from tour.api.mock_erp import DATA_DIR

PERIOD_CODE = re.compile(r"XJ-[A-Z]{4}-(\d{8})-\d{3}")
ROUTE_KEYS = {
    "routeId",
    "routeCode",
    "routeName",
    "days",
    "departCityName",
    "companyId",
    "companyName",
    "fromPrice",
    "tags",
    "itineraryTags",
    "periodPriceTags",
    "features",
    "firstImageUrl",
    "routeAttachmentName",
    "routeAttachmentUrl",
}
PRICE_KEYS = {"adultPrice", "childPrice", "elderPrice", "singleRoomDiff", "currency"}
DESTINATIONS = {"伊犁": 4, "喀纳斯": 2, "南疆": 1, "青海": 1}
TAGS = {
    "亲子",
    "老人友好",
    "摄影",
    "轻徒步",
    "深度游",
    "纯玩",
    "小团",
    "高端",
    "零购物",
    "三钻",
    "四钻",
    "五钻",
    "特色民宿",
    "6座商务车",
    "8座商务车",
    "20座中巴",
}
# The ERP extracts some fifty itinerary tags per 线路; the fixtures carry a demo-scale list.
MIN_ITINERARY_TAGS = 12
# The departments the fixtures span, as beta's account does: 新疆部 and 青海部.
DEPARTMENTS = {2, 5}
WINDOW_DAYS = 60
DEPARTURES_PER_ROUTE = range(6, 13)


@pytest.fixture(scope="module")
def routes_raw() -> dict:
    return load_json(DATA_DIR, "routes.json")


@pytest.fixture(scope="module")
def departures_raw() -> dict:
    return load_json(DATA_DIR, "departures.json")


@pytest.fixture(scope="module")
def routes(routes_raw) -> list[dict]:
    return routes_raw["routes"]


@pytest.fixture(scope="module")
def departures(departures_raw) -> list[dict]:
    return departures_raw["departures"]


@pytest.fixture(scope="module")
def customers() -> list[dict]:
    return load_json(DATA_DIR, "customers.json")["customers"]


def test_the_catalog_is_the_eight_routes_the_demo_scripts_expect(routes):
    assert len(routes) == 8
    found = Counter(name for name in DESTINATIONS for route in routes if name in route["routeName"])
    assert found == DESTINATIONS


def test_every_route_row_is_the_erps_own_shape(routes):
    for route in routes:
        assert set(route) == ROUTE_KEYS, route["routeId"]
        assert isinstance(route["routeId"], int)
        assert re.fullmatch(r"[A-Z]{4}", route["routeCode"]), route["routeId"]
        assert set(route["tags"]) <= TAGS, route["routeId"]
        assert route["features"], route["routeId"]


def test_every_route_carries_the_erps_itinerary_tags(routes):
    """The real catalog's tags are the ERP's own extraction of the itinerary attachment —
    some fifty free-text tags per 线路 — and ``api/tags.py`` normalises them into the
    attributes search filters on. The fixtures carry the same shape at demo scale: the
    destination, the departure city, the hotel standard, 购物 and what the price includes."""
    for route in routes:
        tags = route["itineraryTags"]
        assert len(tags) >= MIN_ITINERARY_TAGS, route["routeId"]
        assert len(set(tags)) == len(tags), route["routeId"]
        assert any(word in tag for tag in tags for word in ("钻", "民宿")), route["routeId"]
        assert any("购物" in tag for tag in tags), route["routeId"]
        assert any(tag.endswith("出发") for tag in tags), route["routeId"]
        assert all(isinstance(tag, str) and tag.strip() for tag in tags), route["routeId"]
    priced = [route for route in routes if route["periodPriceTags"]]
    assert priced, "the ERP carries a budget band on a few routes; the fixtures should too"
    assert all(tag.startswith("预算约") for route in priced for tag in route["periodPriceTags"])


def test_route_ids_and_codes_are_unique(routes):
    assert len({route["routeId"] for route in routes}) == len(routes)
    assert len({route["routeCode"] for route in routes}) == len(routes)


def test_every_row_names_the_department_its_writes_are_made_in(routes, departures):
    """A 团期's ``companyId`` is its route's own: reads span the departments, and a quote and
    an order are made in the one the 团期 belongs to."""
    departments = {route["routeId"]: route["companyId"] for route in routes}
    assert set(departments.values()) == DEPARTMENTS
    assert all(isinstance(company_id, int) for company_id in departments.values())
    for row in departures:
        assert row["companyId"] == departments[row["routeId"]], row["periodId"]


def test_period_ids_are_unique_integers_and_name_a_route_the_catalog_has(routes, departures):
    route_ids = {route["routeId"] for route in routes}
    ids = [row["periodId"] for row in departures]
    assert all(isinstance(period_id, int) for period_id in ids)
    assert len(set(ids)) == len(ids)
    for row in departures:
        assert row["routeId"] in route_ids, row["periodId"]


def test_every_period_code_carries_its_own_route_code_and_depart_date(routes, departures):
    codes = {route["routeId"]: route["routeCode"] for route in routes}
    for row in departures:
        match = PERIOD_CODE.fullmatch(row["periodCode"])
        assert match, row["periodCode"]
        assert match.group(1) == date.fromisoformat(row["departDate"]).strftime("%Y%m%d")
        assert row["periodCode"].split("-")[1] == codes[row["routeId"]]


def test_each_route_carries_a_month_or_two_of_departures(routes, departures):
    per_route = Counter(row["routeId"] for row in departures)
    for route in routes:
        assert per_route[route["routeId"]] in DEPARTURES_PER_ROUTE, route["routeId"]


def test_return_dates_and_day_counts_match_the_route(routes, departures):
    days = {route["routeId"]: route["days"] for route in routes}
    for row in departures:
        span = date.fromisoformat(row["returnDate"]) - date.fromisoformat(row["departDate"])
        assert row["days"] == days[row["routeId"]], row["periodId"]
        assert span.days == row["days"] - 1, row["periodId"]


def test_the_seats_add_up_within_the_group_the_departure_plans_for(departures):
    for row in departures:
        assert 0 <= row["availableSeats"] <= row["planGuests"], row["periodId"]
        assert row["confirmCount"] + row["availableSeats"] <= row["planGuests"], row["periodId"]
        assert 0 < row["minGroupSize"] <= row["planGuests"], row["periodId"]
        assert row["reserveHours"] > 0, row["periodId"]


def test_the_fixture_keeps_a_full_departure_and_some_that_have_not_formed(departures):
    assert any(row["availableSeats"] == 0 for row in departures)
    assert any(row["confirmCount"] < row["minGroupSize"] for row in departures)


def test_every_departure_carries_a_complete_price(departures):
    for row in departures:
        price = row["priceInfo"]
        assert set(price) == PRICE_KEYS, row["periodId"]
        assert price["adultPrice"] > 0 and price["currency"] == "CNY", row["periodId"]
        assert price["elderPrice"] == price["adultPrice"], row["periodId"]


def test_a_routes_from_price_is_the_cheapest_departure_it_has(routes, departures):
    cheapest: dict[int, float] = {}
    for row in departures:
        adult = row["priceInfo"]["adultPrice"]
        cheapest[row["routeId"]] = min(cheapest.get(row["routeId"], adult), adult)
    for route in routes:
        assert route["fromPrice"] == cheapest[route["routeId"]], route["routeId"]


def test_the_customers_are_three_trade_accounts(customers):
    assert len(customers) == 3
    assert {row["companyType"] for row in customers} == {1}
    assert len({row["customerId"] for row in customers}) == 3
    assert all(isinstance(row["customerId"], int) for row in customers)


def test_both_fixtures_are_anchored_to_the_same_day(routes_raw, departures_raw):
    assert routes_raw["dates_anchored_to"] == departures_raw["dates_anchored_to"]
    assert routes_raw["store_name"]


def test_every_departure_falls_inside_the_window_after_the_anchor(departures_raw, departures):
    anchor = date.fromisoformat(departures_raw["dates_anchored_to"])
    for row in departures:
        ahead = (date.fromisoformat(row["departDate"]) - anchor).days
        assert 0 <= ahead <= WINDOW_DAYS, row["periodId"]
