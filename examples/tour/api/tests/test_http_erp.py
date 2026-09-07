# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``HttpErpClient`` against an ``httpx.MockTransport``: what each call puts on the wire
and what each answer, refusal, and outage maps back to. The handler stands in for the
旅行社 ERP, so these tests hold the client to the HTTP contract without a server."""

import hashlib
import json
import logging
from datetime import UTC, date, datetime

import httpx
import pytest

from tour.api.erp_client import (
    ErpClient,
    ErpDeadlinePassed,
    ErpError,
    ErpHoldLimit,
    ErpHoldNotFound,
    ErpSoldOut,
    ErpUnavailable,
    RouteQuery,
)
from tour.api.http_erp import HttpErpClient
from tour.api.mock_erp import MockErpClient

BASE_URL = "https://erp.acme-tour.example/v1"
TOKEN = "erp-token-abc"
ADVISOR = "advisor-1"
SESSION = "session-1"

ROUTE = {
    "route_id": "RT-1021",
    "route_name": "伊犁草原深度 8 日小团",
    "destination": "伊犁",
    "region": "新疆",
    "departure_city": "乌鲁木齐",
    "days": 8,
    "nights": 7,
    "hotel_level": "四钻",
    "vehicle": "商务车",
    "group_size_max": 12,
    "includes_transport": True,
    "shopping_stops": 0,
    "optional_paid_items": 1,
    "child_min_age": 3,
    "child_policy": "2-12 岁不占床按儿童价",
    "intensity": 2,
    "summary": "草原、湖泊与峡谷的慢行线路。",
    "itinerary_brief": "D1 乌鲁木齐集合；D2 赛里木湖……",
    "min_adult_price_in_window": 5780,
    "has_seats_in_window": True,
    "highlights": ["喀拉峻草原", "赛里木湖"],
    "fit_tags": ["亲子", "纯玩"],
}

DEPARTURE = {
    "departure_id": "DP-1021-20261014",
    "route_id": "RT-1021",
    "depart_date": "2026-10-14",
    "return_date": "2026-10-21",
    "seats_total": 12,
    "seats_left": 5,
    "group_status": "confirmed",
    "booking_deadline": "2026-10-10",
    "adult_price": 5780,
    "child_price": 3480,
    "single_supplement": 1200,
    "hold_ttl_minutes": 30,
    "party_quote_total": 18520,
}

HOLD = {
    "hold_id": "HOLD-9F2C11A0",
    "departure_id": "DP-1021-20261014",
    "adults": 2,
    "children": 2,
    "total_price": 18520,
    "expires_at": "2026-10-01T10:30:00+00:00",
    "seats_left_after": 1,
}


def make_client(handler) -> HttpErpClient:
    return HttpErpClient(BASE_URL, TOKEN, transport=httpx.MockTransport(handler))


def responder(payload, status: int = 200):
    """A handler that keeps every request it saw and always answers the same way."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if payload is None:
            return httpx.Response(status)
        return httpx.Response(status, json=payload)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def full_query() -> RouteQuery:
    return RouteQuery(
        destination="新疆伊犁",
        depart_from=date(2026, 10, 11),
        depart_to=date(2026, 10, 20),
        days_min=8,
        days_max=10,
        adults=2,
        children=2,
        child_ages=(5, 9),
        departure_city="上海",
        no_shopping=True,
        hotel_level="四钻",
        max_adult_price=8000.0,
        limit=5,
    )


# -- search ----------------------------------------------------------------------------


async def test_search_sends_every_stated_filter_and_the_bearer_token():
    handler = responder({"routes": []})
    await make_client(handler).search_routes(full_query())

    request = handler.seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/routes/search"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(request.content)
    assert body == {
        "destination": "新疆伊犁",
        "adults": 2,
        "children": 2,
        "limit": 5,
        "depart_from": "2026-10-11",
        "depart_to": "2026-10-20",
        "days_min": 8,
        "days_max": 10,
        "child_ages": [5, 9],
        "departure_city": "上海",
        "no_shopping": True,
        "hotel_level": "四钻",
        "max_adult_price": 8000.0,
    }
    assert all(isinstance(age, int) for age in body["child_ages"])
    assert isinstance(body["no_shopping"], bool)


async def test_search_maps_a_route_and_leaves_absent_optionals_none():
    client = make_client(responder({"routes": [ROUTE]}))
    (route,) = await client.search_routes(full_query())

    assert route.route_id == "RT-1021"
    assert route.min_adult_price_in_window == 5780.0
    assert route.has_seats_in_window is True
    assert route.highlights == ("喀拉峻草原", "赛里木湖")
    assert route.fit_tags == ("亲子", "纯玩")
    assert route.supplier_name is None
    assert route.max_drive_hours_per_day is None
    assert route.meals is None
    assert route.meeting_point is None
    assert route.image_url is None


async def test_search_drops_a_route_missing_a_required_field_and_logs_it(caplog):
    broken = {key: value for key, value in ROUTE.items() if key != "summary"}
    broken["route_id"] = "RT-9999"
    client = make_client(responder({"routes": [broken, ROUTE]}))

    with caplog.at_level(logging.WARNING, logger="tour.api.http_erp"):
        routes = await client.search_routes(full_query())

    assert [r.route_id for r in routes] == ["RT-1021"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert "summary" in caplog.text


# -- departures ------------------------------------------------------------------------


async def test_list_departures_query_string_and_record_mapping():
    handler = responder({"departures": [DEPARTURE]})
    client = make_client(handler)
    (departure,) = await client.list_departures(
        "RT-1021", date(2026, 10, 11), date(2026, 10, 20), 2, 2, [5, 9]
    )

    request = handler.seen[0]
    assert request.url.path == "/v1/routes/RT-1021/departures"
    assert dict(request.url.params) == {
        "adults": "2",
        "children": "2",
        "child_ages": "5,9",
        "depart_from": "2026-10-11",
        "depart_to": "2026-10-20",
        "limit": "24",
    }
    assert departure.departure_id == "DP-1021-20261014"
    assert departure.depart_date == date(2026, 10, 14)
    assert departure.booking_deadline == date(2026, 10, 10)
    assert departure.party_quote_total == 18520.0
    assert departure.child_bed_price is None


async def test_get_departure_returns_none_when_the_erp_does_not_know_the_id():
    client = make_client(
        responder({"code": "DEPARTURE_NOT_FOUND", "message": "找不到该团期。"}, status=404)
    )
    assert await client.get_departure("DP-1021-20991231", 2, 0, []) is None


async def test_get_departure_maps_the_single_record():
    handler = responder(DEPARTURE | {"child_bed_price": 4680})
    departure = await make_client(handler).get_departure("DP-1021-20261014", 2, 2, [5, 9])

    assert handler.seen[0].url.path == "/v1/departures/DP-1021-20261014"
    assert departure is not None
    assert departure.child_bed_price == 4680.0


# -- holds -----------------------------------------------------------------------------


async def test_create_hold_sends_the_idempotency_key_and_maps_the_record():
    handler = responder(HOLD, status=201)
    hold = await make_client(handler).create_hold("DP-1021-20261014", 2, 2, ADVISOR, SESSION)

    request = handler.seen[0]
    expected = hashlib.sha256(f"{SESSION}DP-1021-2026101422".encode()).hexdigest()
    assert request.headers["Idempotency-Key"] == expected
    assert json.loads(request.content) == {
        "departure_id": "DP-1021-20261014",
        "adults": 2,
        "children": 2,
        "advisor_id": ADVISOR,
        "session_key": SESSION,
    }
    assert hold.hold_id == "HOLD-9F2C11A0"
    assert hold.total_price == 18520.0
    assert hold.expires_at == datetime(2026, 10, 1, 10, 30, tzinfo=UTC)
    assert hold.expires_at.tzinfo is not None


async def test_create_hold_repeats_the_key_for_the_same_party_and_changes_it_otherwise():
    handler = responder(HOLD, status=201)
    client = make_client(handler)
    await client.create_hold("DP-1021-20261014", 2, 2, ADVISOR, SESSION)
    await client.create_hold("DP-1021-20261014", 2, 2, ADVISOR, SESSION)
    await client.create_hold("DP-1021-20261014", 3, 2, ADVISOR, SESSION)

    keys = [request.headers["Idempotency-Key"] for request in handler.seen]
    assert keys[0] == keys[1] != keys[2]


async def test_create_hold_maps_sold_out_with_its_sibling_departures():
    client = make_client(
        responder(
            {
                "code": "SOLD_OUT",
                "message": "DP-1021-20261014 只剩 1 个位置，不够 4 人。",
                "sibling_departure_ids": ["DP-1021-20261017", "DP-1021-20261010"],
            },
            status=409,
        )
    )
    with pytest.raises(ErpSoldOut) as excinfo:
        await client.create_hold("DP-1021-20261014", 2, 2, ADVISOR, SESSION)

    assert "只剩 1 个位置" in str(excinfo.value)
    assert excinfo.value.sibling_departure_ids == ["DP-1021-20261017", "DP-1021-20261010"]


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (409, "DEADLINE_PASSED", ErpDeadlinePassed),
        (409, "HOLD_LIMIT", ErpHoldLimit),
        (404, "DEPARTURE_NOT_FOUND", ErpHoldNotFound),
    ],
)
async def test_create_hold_maps_the_remaining_refusals(status, code, expected):
    client = make_client(responder({"code": code, "message": "该团期无法占位。"}, status=status))
    with pytest.raises(expected) as excinfo:
        await client.create_hold("DP-1021-20261014", 2, 2, ADVISOR, SESSION)
    assert str(excinfo.value) == "该团期无法占位。"


async def test_an_unknown_4xx_code_stays_a_plain_erp_error():
    client = make_client(responder({"code": "BAD_PARTY", "message": "人数不合法。"}, status=400))
    with pytest.raises(ErpError) as excinfo:
        await client.create_hold("DP-1021-20261014", 0, 0, ADVISOR, SESSION)
    assert type(excinfo.value) is ErpError
    assert str(excinfo.value) == "人数不合法。"


async def test_release_hold_names_the_advisor_in_a_header():
    handler = responder(None, status=204)
    await make_client(handler).release_hold("HOLD-9F2C11A0", ADVISOR)

    request = handler.seen[0]
    assert request.method == "DELETE"
    assert request.url.path == "/v1/holds/HOLD-9F2C11A0"
    assert request.headers["X-Advisor-Id"] == ADVISOR


@pytest.mark.parametrize(("status", "code"), [(403, "NOT_OWNER"), (404, "HOLD_NOT_FOUND")])
async def test_release_hold_maps_both_refusals_to_hold_not_found(status, code):
    client = make_client(responder({"code": code, "message": "找不到该占位记录。"}, status=status))
    with pytest.raises(ErpHoldNotFound):
        await client.release_hold("HOLD-9F2C11A0", ADVISOR)


async def test_list_holds_filters_on_the_advisor_and_the_conversation():
    handler = responder({"holds": [HOLD]})
    holds = await make_client(handler).list_holds(ADVISOR, SESSION)

    request = handler.seen[0]
    assert request.url.path == "/v1/holds"
    assert dict(request.url.params) == {"advisor_id": ADVISOR, "session_key": SESSION}
    assert [hold.hold_id for hold in holds] == ["HOLD-9F2C11A0"]
    assert holds[0].expires_at.tzinfo is not None


# -- outages ---------------------------------------------------------------------------


async def test_a_server_error_is_an_outage():
    client = make_client(responder({"code": "BOOM", "message": "internal"}, status=500))
    with pytest.raises(ErpUnavailable):
        await client.search_routes(full_query())


async def test_a_connection_failure_is_an_outage():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(ErpUnavailable):
        await make_client(handler).search_routes(full_query())


async def test_a_timeout_is_an_outage():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ErpUnavailable):
        await make_client(handler).create_hold("DP-1021-20261014", 2, 2, ADVISOR, SESSION)


async def test_a_body_that_is_not_json_is_an_outage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(ErpUnavailable):
        await make_client(handler).list_holds(ADVISOR, SESSION)


# -- the seam and the host -------------------------------------------------------------


def test_the_http_client_satisfies_the_erp_protocol():
    client: ErpClient = HttpErpClient(BASE_URL, TOKEN)
    assert isinstance(client, HttpErpClient)


def test_the_host_picks_the_http_client_only_when_a_base_url_is_set(main, monkeypatch):
    monkeypatch.setenv("TOUR_ERP_BASE_URL", BASE_URL)
    monkeypatch.setenv("TOUR_ERP_TOKEN", TOKEN)
    assert isinstance(main.build_erp(), HttpErpClient)

    monkeypatch.delenv("TOUR_ERP_BASE_URL")
    assert isinstance(main.build_erp(), MockErpClient)
    # The demo boots without the variable, so the module-level client is the mock.
    assert isinstance(main.erp, MockErpClient)
