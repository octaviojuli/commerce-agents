# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What ``data/routes.json`` and ``data/departures.json`` have to hold for the mock ERP to
read them: ids the client rebuilds the same way, dates inside the demo's two-month window,
and enumerated fields the backend and the web apps render. Read as raw JSON, before the
client shifts the dates, so an authoring slip fails here rather than downstream."""

import re
from collections import Counter
from datetime import date

import pytest

from demo_common.storefront_fixtures import load_json
from tour.api.mock_erp import DATA_DIR

ROUTE_ID = re.compile(r"RT-\d{4}")
DEPARTURE_ID = re.compile(r"DP-\d{4}-\d{8}")
DESTINATIONS = {"伊犁": 4, "喀纳斯": 2, "南疆": 1, "青海": 1}
GROUP_STATUSES = {"confirmed", "pending", "closed"}
FIT_TAGS = {"亲子", "老人友好", "摄影", "轻徒步", "深度游", "纯玩", "小团", "高端"}
HOTEL_LEVELS = {"三钻", "四钻", "五钻", "特色民宿"}
VEHICLES = {"6座商务车", "8座商务车", "20座中巴"}
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


def test_the_catalog_is_the_eight_routes_the_demo_scripts_expect(routes):
    assert len(routes) == 8
    assert Counter(route["destination"] for route in routes) == DESTINATIONS


def test_route_ids_are_well_formed_and_unique(routes):
    ids = [route["route_id"] for route in routes]
    assert all(ROUTE_ID.fullmatch(route_id) for route_id in ids)
    assert len(set(ids)) == len(ids)


def test_every_route_states_an_enumerated_hotel_level_vehicle_and_fit_tags(routes):
    for route in routes:
        assert route["hotel_level"] in HOTEL_LEVELS, route["route_id"]
        assert route["vehicle"] in VEHICLES, route["route_id"]
        assert set(route["fit_tags"]) <= FIT_TAGS, route["route_id"]


def test_departure_ids_are_well_formed_unique_and_rebuilt_from_their_date(routes, departures):
    route_ids = {route["route_id"] for route in routes}
    ids = [row["departure_id"] for row in departures]
    assert len(set(ids)) == len(ids)
    for row in departures:
        departure_id = row["departure_id"]
        assert DEPARTURE_ID.fullmatch(departure_id), departure_id
        assert row["route_id"] in route_ids, departure_id
        digits = row["route_id"].split("-")[-1]
        compact = date.fromisoformat(row["depart_date"]).strftime("%Y%m%d")
        assert departure_id == f"DP-{digits}-{compact}"


def test_each_route_carries_a_month_or_two_of_departures(routes, departures):
    per_route = Counter(row["route_id"] for row in departures)
    for route in routes:
        assert per_route[route["route_id"]] in DEPARTURES_PER_ROUTE, route["route_id"]


def test_return_dates_match_the_routes_length(routes, departures):
    days = {route["route_id"]: route["days"] for route in routes}
    for row in departures:
        span = (
            date.fromisoformat(row["return_date"]) - date.fromisoformat(row["depart_date"])
        ).days
        assert span == days[row["route_id"]] - 1, row["departure_id"]


def test_seats_left_never_exceeds_the_seats_the_group_has(departures):
    for row in departures:
        assert 0 <= row["seats_left"] <= row["seats_total"], row["departure_id"]


def test_group_statuses_are_the_three_the_policies_name(departures):
    assert {row["group_status"] for row in departures} <= GROUP_STATUSES


def test_both_fixtures_are_anchored_to_the_same_day(routes_raw, departures_raw):
    assert routes_raw["dates_anchored_to"] == departures_raw["dates_anchored_to"]


def test_every_departure_falls_inside_the_window_after_the_anchor(departures_raw, departures):
    anchor = date.fromisoformat(departures_raw["dates_anchored_to"])
    for row in departures:
        ahead = (date.fromisoformat(row["depart_date"]) - anchor).days
        assert 0 <= ahead <= WINDOW_DAYS, row["departure_id"]
