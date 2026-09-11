# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_departures``, the 团期卡: which dates one 线路 runs, read live from the ERP, with
the 同业价 this customer is quoted and the provenance a 占位 order needs. The 线路 is the
fixture catalog's, because a 团期 belongs to the ERP's own 线路 and not to an invented one."""

import pytest

from commerce_common.skills import SkillRegistry
from shopping_agent import Product, ShoppingAgentConfig, ShoppingSessionContext
from shopping_agent import ShoppingSessionState as State
from tour.api.departures import NO_DEPARTURES, NO_ROUTE, UNSEEN, build_departures_extension
from tour.api.mock_erp import MockErpClient
from tour.api.tests.test_tour_backend import TODAY, FakeClock, build
from tour.api.tour_backend import MAX_DEPARTURE_ITEMS, TourToolExecutor

ROUTE = "RT-1021"  # 伊犁北疆环线 8 日纯玩小团
YILI = {
    "destination": "伊犁",
    "depart_from": "2026-10-11",
    "depart_to": "2026-10-20",
    "adults": "2",
    "children": "2",
}


@pytest.fixture
def backend():
    return build(MockErpClient(today=TODAY, now=FakeClock()))


@pytest.fixture
def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(session_id="dp-1", user_id="demo-user")


@pytest.fixture
def state() -> State:
    return State()


@pytest.fixture
def executor(backend, session, state) -> TourToolExecutor:
    return TourToolExecutor(
        backend=backend,
        config=ShoppingAgentConfig(brand_name="ACME"),
        skills=SkillRegistry([]),
        session=session,
        state=state,
        extensions=[build_departures_extension()],
    )


async def searched(executor) -> None:
    """The turn before: the advisor's search, which is what puts the 线路 in provenance."""
    await executor.execute(
        "search_products", {"query": "伊犁", "filters": {"attributes": dict(YILI)}}
    )


def _ui(result):
    return next(event for event in result.events if event.type == "ui").data


def test_the_tool_is_advertised_with_its_input_schema():
    extension = build_departures_extension()
    assert extension.component == "departures"
    assert extension.input_schema["required"] == ["route_id"]
    schema = extension.payload_model.model_json_schema()
    assert extension.input_schema["properties"].keys() == schema["properties"].keys()
    assert "团期" in extension.description


async def test_the_card_is_the_erps_dates_for_the_line_the_advisor_named(executor, session):
    await searched(executor)
    result = await executor.execute(
        "present_departures",
        {"route_id": ROUTE, "depart_from": "2026-10-01", "depart_to": "2026-10-31"},
    )
    assert not result.is_error, result.result_text
    ui = _ui(result)
    assert ui["component"] == "departures"
    card = ui["payload"]
    assert card["route"]["product_id"] == ROUTE
    assert card["window"] == {"from": "2026-10-01", "to": "2026-10-31"}
    dates = [item["date"] for item in card["items"]]
    assert dates == [
        "2026-10-03",
        "2026-10-10",
        "2026-10-13",
        "2026-10-17",
        "2026-10-24",
        "2026-10-31",
    ]
    assert [item["weekday"] for item in card["items"]][:2] == ["周六", "周六"]
    # The first three are 满员 for this 2 大 2 小 party; 10/17 has four seats left and a group.
    assert [item["status"] for item in card["items"]] == [
        "满员",
        "满员",
        "满员",
        "已成团",
        "已成团",
        "可报名",
    ]
    quoted = card["items"][3]
    assert quoted["departure"]["product_id"] == "DP-3008"
    assert quoted["price_adult"] == quoted["departure"]["price"] == 5780.0
    assert quoted["departure"]["attributes"]["quote_source"] == "customer"


async def test_the_dates_it_shows_can_be_booked_and_sent(executor, backend, session, state):
    """Every 团期 on the card is a record the advisor can act on: it enters provenance, so a
    占位 order may be written on it, and counts as presented, so the 分享清单 may carry it."""
    await searched(executor)
    await executor.execute("present_departures", {"route_id": ROUTE})
    assert "DP-3008" in state.seen_products
    assert "DP-3008" in backend.presented(session.session_id)
    cart = await backend.add_to_cart(session, "DP-3008", 4)
    assert cart.items and cart.items[0].product_id == "DP-3008"


async def test_the_window_is_the_conversations_own_when_the_advisor_names_none(executor):
    await searched(executor)
    card = _ui(await executor.execute("present_departures", {"route_id": ROUTE}))["payload"]
    assert card["window"] == {"from": "2026-10-11", "to": "2026-10-20"}
    assert [item["date"] for item in card["items"]] == ["2026-10-13", "2026-10-17"]


async def test_a_long_running_line_is_capped_around_the_middle_of_the_window(executor):
    await searched(executor)
    card = _ui(
        await executor.execute(
            "present_departures",
            {"route_id": ROUTE, "depart_from": "2026-09-06", "depart_to": "2027-09-06"},
        )
    )["payload"]
    assert 0 < len(card["items"]) <= MAX_DEPARTURE_ITEMS


async def test_a_window_with_no_departures_says_so_rather_than_showing_an_empty_card(executor):
    await searched(executor)
    result = await executor.execute(
        "present_departures",
        {"route_id": ROUTE, "depart_from": "2026-12-01", "depart_to": "2026-12-07"},
    )
    assert result.is_error and NO_DEPARTURES.split("{")[0] in result.result_text
    assert not [event for event in result.events if event.type == "ui"]


async def test_a_line_this_session_has_not_shown_is_refused(executor):
    result = await executor.execute("present_departures", {"route_id": "RT-1041"})
    assert result.is_error and UNSEEN.format(route_id="RT-1041") in result.result_text


async def test_an_id_the_catalog_does_not_carry_is_refused(executor, state):
    state.remember_products([Product(product_id="RT-999999", title="不存在的线路", price=0.0)])
    result = await executor.execute("present_departures", {"route_id": "RT-999999"})
    assert result.is_error and NO_ROUTE.format(route_id="RT-999999") in result.result_text
