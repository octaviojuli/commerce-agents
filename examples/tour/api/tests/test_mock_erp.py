# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The mock ERP's filters, per-party quotes, and hold lifecycle. Every client is built on
a fixed ``today`` and a fake clock, so the fixture's dates and ids are the same whatever
day the suite runs."""

from datetime import UTC, date, datetime, timedelta

import pytest

from tour.api.erp_client import (
    ErpDeadlinePassed,
    ErpHoldLimit,
    ErpHoldNotFound,
    ErpSoldOut,
    RouteQuery,
)
from tour.api.mock_erp import MockErpClient

TODAY = date(2026, 9, 6)
ADVISOR = "advisor-1"
SESSION = "session-1"


class FakeClock:
    def __init__(self, on: date = TODAY) -> None:
        self.current = datetime(on.year, on.month, on.day, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def erp(clock: FakeClock) -> MockErpClient:
    return MockErpClient(today=TODAY, now=clock)


def query(**overrides) -> RouteQuery:
    defaults = {
        "destination": "新疆",
        "depart_from": TODAY,
        "depart_to": date(2026, 11, 30),
        "adults": 2,
    }
    return RouteQuery(**{**defaults, **overrides})


async def test_search_routes_keeps_only_routes_with_a_departure_in_the_window(erp):
    routes = await erp.search_routes(query(depart_to=date(2026, 9, 13)))
    # The other five 新疆 routes' first bookable departure falls after 9-13.
    assert {r.route_id for r in routes} == {"RT-1021", "RT-1024", "RT-1032"}
    assert all(r.min_adult_price_in_window > 0 for r in routes)
    # A query that states no window at all keeps every 新疆 route.
    unbounded = await erp.search_routes(query(depart_from=None, depart_to=None))
    assert len(unbounded) == 7


async def test_search_routes_matches_destination_against_region_either_way(erp):
    # 新疆伊犁 keeps every 新疆 route, and the text score floats the 伊犁 ones to the top.
    found = [r.route_id for r in await erp.search_routes(query(destination="新疆伊犁"))]
    assert set(found[:4]) == {"RT-1021", "RT-1022", "RT-1023", "RT-1024"}
    assert set(found[4:]) == {"RT-1031", "RT-1032", "RT-1041"}
    assert "RT-1051" not in found  # 青海
    assert [r.route_id for r in await erp.search_routes(query(destination="青海"))] == ["RT-1051"]


async def test_search_routes_ranks_by_text_score_then_price(erp):
    found = [r.route_id for r in await erp.search_routes(query(destination="伊犁纯玩亲子"))]
    # RT-1021 alone is tagged both 纯玩 and 亲子; RT-1022 and RT-1024 tie and sort by price.
    assert found == ["RT-1021", "RT-1022", "RT-1024", "RT-1023"]


async def test_search_routes_drops_routes_whose_child_minimum_is_above_the_child(erp):
    young = {r.route_id for r in await erp.search_routes(query(children=1, child_ages=(4,)))}
    older = {r.route_id for r in await erp.search_routes(query(children=1, child_ages=(10,)))}
    assert "RT-1032" not in young  # child_min_age 8
    assert "RT-1021" in young  # child_min_age 3
    assert "RT-1032" in older
    # A child whose age the advisor has not stated cannot be ruled out by the minimum.
    assert "RT-1032" in {r.route_id for r in await erp.search_routes(query(children=1))}


async def test_search_routes_no_shopping_keeps_only_zero_stop_routes(erp):
    with_stops = {r.route_id for r in await erp.search_routes(query())}
    without = {r.route_id for r in await erp.search_routes(query(no_shopping=True))}
    assert {"RT-1023", "RT-1032"} <= with_stops  # 2 and 1 shopping stops
    assert without == with_stops - {"RT-1023", "RT-1032"}


async def test_search_routes_applies_days_hotel_price_and_limit(erp):
    assert all(r.days <= 8 for r in await erp.search_routes(query(days_max=8)))
    assert all(r.hotel_level == "五钻" for r in await erp.search_routes(query(hotel_level="五钻")))
    cheap = await erp.search_routes(query(max_adult_price=5000))
    assert [r.route_id for r in cheap] == ["RT-1032", "RT-1031"]  # untied, so by price
    assert len(await erp.search_routes(query(limit=2))) == 2
    # A limit below one is no limit the advisor stated, so the default applies.
    assert len(await erp.search_routes(query(limit=0))) == len(await erp.search_routes(query()))


async def test_list_departures_filters_by_window_and_keeps_closed_groups(erp):
    rows = await erp.list_departures("RT-1021", TODAY, date(2026, 9, 30), 2, 0, [])
    assert [r.departure_id for r in rows] == [
        "DP-1021-20260908",
        "DP-1021-20260912",
        "DP-1021-20260919",
        "DP-1021-20260926",
    ]
    assert rows[0].group_status == "closed"


async def test_party_quote_totals_adults_and_children(erp):
    departure = await erp.get_departure("DP-1021-20260919", 2, 1, [7])
    assert (departure.adult_price, departure.child_price) == (6180.0, 3880.0)
    assert departure.party_quote_total == 2 * 6180 + 3880
    assert await erp.get_departure("DP-9999-20260101", 1, 0, []) is None


async def test_create_hold_takes_seats_and_shows_up_in_list_holds(erp, clock):
    hold = await erp.create_hold("DP-1021-20260919", 2, 1, ADVISOR, SESSION)
    assert hold.hold_id.startswith("HOLD-")
    assert hold.total_price == 2 * 6180 + 3880
    departure = await erp.get_departure("DP-1021-20260919", 1, 0, [])
    assert hold.expires_at == clock() + timedelta(minutes=departure.hold_ttl_minutes)
    assert departure.seats_left == 1  # 4 in the fixture, 3 held
    assert [h.hold_id for h in await erp.list_holds(ADVISOR, SESSION)] == [hold.hold_id]
    assert await erp.list_holds(ADVISOR, "other-session") == []


async def test_sold_out_names_nearby_departures_of_the_same_route(erp):
    with pytest.raises(ErpSoldOut) as raised:
        await erp.create_hold("DP-1021-20261013", 4, 0, ADVISOR, SESSION)
    assert raised.value.sibling_departure_ids == ["DP-1021-20261017", "DP-1021-20261024"]
    assert "DP-1021-20261013" in str(raised.value)


async def test_hold_expiry_restores_seats_and_leaves_one_notification(erp, clock):
    hold = await erp.create_hold("DP-1021-20260919", 2, 0, ADVISOR, SESSION)
    departure = await erp.get_departure("DP-1021-20260919", 1, 0, [])
    assert departure.seats_left == 2
    clock.advance(departure.hold_ttl_minutes * 60 + 1)
    assert (await erp.get_departure("DP-1021-20260919", 1, 0, [])).seats_left == 4
    assert await erp.list_holds(ADVISOR, SESSION) == []
    notices = erp.collect_notifications()
    assert [(n.kind, n.hold_id, n.departure_id) for n in notices] == [
        ("hold_expired", hold.hold_id, "DP-1021-20260919")
    ]
    assert notices[0].message.startswith("占位已过期：")
    assert erp.collect_notifications() == []


async def test_a_fourth_hold_in_one_session_is_refused(erp):
    for departure_id in ("DP-1032-20260919", "DP-1032-20260926", "DP-1032-20261010"):
        await erp.create_hold(departure_id, 1, 0, ADVISOR, SESSION)
    with pytest.raises(ErpHoldLimit):
        await erp.create_hold("DP-1032-20261017", 1, 0, ADVISOR, SESSION)
    # A different conversation has its own allowance.
    assert await erp.create_hold("DP-1032-20261017", 1, 0, ADVISOR, "session-2")


async def test_holding_the_same_departure_twice_is_refused(erp):
    await erp.create_hold("DP-1032-20260919", 1, 0, ADVISOR, SESSION)
    with pytest.raises(ErpHoldLimit, match="已经占位"):
        await erp.create_hold("DP-1032-20260919", 1, 0, ADVISOR, SESSION)


async def test_a_closed_departure_cannot_be_held(erp):
    with pytest.raises(ErpDeadlinePassed, match="已关闭"):
        await erp.create_hold("DP-1021-20260908", 1, 0, ADVISOR, SESSION)


async def test_a_closed_group_and_a_passed_deadline_read_differently(erp):
    """The fixture's closed departure is also past its deadline, so each refusal is
    reached by moving one field on the client's own copy of a row."""
    erp._departures["DP-1021-20260919"]["group_status"] = "closed"  # deadline still ahead
    with pytest.raises(ErpDeadlinePassed, match="已关闭，停止收客"):
        await erp.create_hold("DP-1021-20260919", 1, 0, ADVISOR, SESSION)
    erp._departures["DP-1021-20260926"]["booking_deadline"] = TODAY - timedelta(days=1)
    with pytest.raises(ErpDeadlinePassed, match="已过报名截止日（2026-09-05）"):
        await erp.create_hold("DP-1021-20260926", 1, 0, ADVISOR, SESSION)


async def test_release_hold_gives_the_seats_back(erp):
    hold = await erp.create_hold("DP-1021-20260919", 2, 0, ADVISOR, SESSION)
    with pytest.raises(ErpHoldNotFound):
        await erp.release_hold(hold.hold_id, "advisor-2")
    await erp.release_hold(hold.hold_id, ADVISOR)
    assert (await erp.get_departure("DP-1021-20260919", 1, 0, [])).seats_left == 4
    assert await erp.list_holds(ADVISOR, SESSION) == []
    with pytest.raises(ErpHoldNotFound):
        await erp.release_hold(hold.hold_id, ADVISOR)
    with pytest.raises(ErpHoldNotFound):
        await erp.create_hold("DP-0000-20260101", 1, 0, ADVISOR, SESSION)


async def test_dates_and_ids_shift_forward_with_today():
    later = date(2026, 9, 20)
    erp = MockErpClient(today=later, now=FakeClock(later))
    assert await erp.get_departure("DP-1021-20260919", 1, 0, []) is None
    shifted = await erp.get_departure("DP-1021-20261003", 1, 0, [])
    assert shifted.depart_date == date(2026, 10, 3)  # authored 9-19, plus two weeks
    assert shifted.return_date == date(2026, 10, 10)
    assert shifted.booking_deadline == date(2026, 9, 28)
    assert shifted.adult_price == 6180.0  # the same row, not the authored 10-03 one
