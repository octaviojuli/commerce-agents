# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour backend's mapping onto ``StorefrontBackend``: routes as families, departures as
variants quoted for the searched party, holds as cart lines, and the relaxation that answers
a request the catalog cannot meet exactly. Every backend runs on a fixed ``today`` and a fake
clock, so the fixture's dates and ids are the same whatever day the suite runs."""

import re
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
from tour.api.erp_client import DepartureRecord, ErpHoldLimit, ErpUnavailable
from tour.api.mock_erp import MockErpClient
from tour.api.tour_backend import TourBackend, TourToolExecutor

TODAY = date(2026, 9, 6)
ADVISOR = "demo-user"
ROUTE = "RT-1021"
FULL = "DP-1021-20261013"
OPEN = "DP-1021-20261017"
CJK = re.compile(r"[一-鿿]")

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


@pytest.fixture
def erp() -> MockErpClient:
    return MockErpClient(today=TODAY, now=FakeClock())


@pytest.fixture
def backend(erp: MockErpClient) -> TourBackend:
    return TourBackend(erp, today=TODAY)


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


# -- search ------------------------------------------------------------------------------


async def test_search_returns_route_families_for_the_stated_window(backend, session):
    products = await search_yili(backend, session)
    assert len(products) >= 3
    assert ROUTE in {product.product_id for product in products}
    # RT-1023 has two 购物店, so 纯玩 rules it out and nothing relaxes far enough to add it.
    assert "RT-1023" not in {product.product_id for product in products}
    for product in products:
        assert product.variant_of is None
        assert product.options["depart_date"]
        assert product.attributes["match"] == "exact"
        assert product.category == "tour"
        assert product.currency == "CNY"
    # The two dates are the span the advisor means, whichever order they state them in, so
    # the same search backwards is met exactly rather than relaxed onto a single day.
    backwards = await search_yili(
        backend, session, depart_from="2026-10-20", depart_to="2026-10-11"
    )
    assert [(p.product_id, p.attributes["match"]) for p in backwards] == [
        (product.product_id, "exact") for product in products
    ]


async def test_a_price_floor_drops_the_routes_under_it(backend, session):
    """``max_price`` is the ERP's own filter; ``min_price`` is the advisor asking for the
    better tier, and the relaxation that follows cannot put a cheaper route back."""
    filters = SearchFilters(min_price=9000, attributes=YILI)
    products = await backend.search_products(session, "伊犁亲子游", filters)
    assert [product.product_id for product in products] == ["RT-1024"]


# -- details -----------------------------------------------------------------------------


async def test_route_details_quote_the_searched_party_over_the_padded_window(backend, session):
    await search_yili(backend, session)
    details = await backend.get_product_details(session, ROUTE)
    assert 0 < len(details.variants) <= 24
    assert details.specs["住宿标准"] == "四钻"
    for variant in details.variants:
        assert variant.variant_of == ROUTE
        departs = date.fromisoformat(variant.attributes["depart_date"])
        assert date(2026, 10, 4) <= departs <= date(2026, 10, 27)
        assert variant.attributes["quote_party"] == "2大2小"
        quote = 2 * float(variant.attributes["adult_price"]) + 2 * float(
            variant.attributes["child_price"]
        )
        assert float(variant.attributes["party_quote_total"]) == quote
    full = next(v for v in details.variants if v.product_id == FULL)
    assert full.in_stock is False


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
    """``ROUTE`` runs every day of whatever window it is asked about; every other route
    answers from the fixtures."""

    async def list_departures(self, route_id, depart_from, depart_to, adults, children, child_ages):
        if route_id != ROUTE:
            return await super().list_departures(
                route_id, depart_from, depart_to, adults, children, child_ages
            )
        span = (depart_to - depart_from).days + 1
        return [_daily(depart_from + timedelta(days=day)) for day in range(span)]


def _daily(depart: date) -> DepartureRecord:
    return DepartureRecord(
        departure_id=f"DP-1021-{depart:%Y%m%d}",
        route_id=ROUTE,
        depart_date=depart,
        return_date=depart + timedelta(days=7),
        seats_total=16,
        seats_left=16,
        group_status="confirmed",
        booking_deadline=depart - timedelta(days=3),
        adult_price=6980.0,
        child_price=4980.0,
        single_supplement=1200.0,
        hold_ttl_minutes=30,
        party_quote_total=23920.0,
    )


async def test_a_route_that_runs_every_day_is_trimmed_around_the_searched_window(session):
    """One fenced result cannot hold a 团期 a day, so the details keep the 24 nearest the
    middle of the window the advisor is working in and drop the far ends of the run."""
    backend = TourBackend(DailyDepartures(today=TODAY, now=FakeClock()), today=TODAY)
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


async def test_a_departure_id_returns_that_one_variant(backend, session):
    await search_yili(backend, session)
    details = await backend.get_product_details(session, OPEN)
    assert details.variant_of == ROUTE
    assert details.variants == []
    assert details.option_values == {"depart_date": "2026-10-17"}
    assert await backend.get_product_details(session, "DP-1021-20261111") is None
    assert await backend.get_product_details(session, "not-an-id") is None


# -- cart --------------------------------------------------------------------------------


async def test_a_route_id_cannot_be_held(backend, session):
    await search_yili(backend, session)
    with pytest.raises(Unavailable):
        await backend.add_to_cart(session, ROUTE, 4)


async def test_a_full_departure_names_its_siblings_in_ids_only(backend, session):
    await search_yili(backend, session)
    with pytest.raises(Unavailable) as raised:
        await backend.add_to_cart(session, FULL, 4)
    message = str(raised.value)
    assert FULL in message and "DP-1021-2026" in message.replace(FULL, "")
    assert not CJK.search(message)


async def test_a_hold_becomes_one_cart_line_priced_per_head(backend, erp, session):
    await search_yili(backend, session)
    cart = await backend.add_to_cart(session, OPEN, 4)
    assert len(cart.items) == 1
    line = cart.items[0]
    assert line.product_id == OPEN
    assert line.quantity == 4
    assert line.variant_of == ROUTE
    holds = await erp.list_holds(advisor_id=ADVISOR, session_key=session.session_id)
    assert abs(cart.subtotal - holds[0].total_price) <= 1


async def test_removing_a_line_gives_the_seats_back(backend, erp, session):
    before = await erp.get_departure(OPEN, 2, 2, [5, 9])
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    held = await erp.get_departure(OPEN, 2, 2, [5, 9])
    assert held.seats_left == before.seats_left - 4
    cart = await backend.remove_from_cart(session, OPEN)
    assert cart.items == []
    released = await erp.get_departure(OPEN, 2, 2, [5, 9])
    assert released.seats_left == before.seats_left


async def test_a_smaller_party_rewrites_the_hold_and_gives_the_seat_back(backend, erp, session):
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    held = await erp.get_departure(OPEN, 2, 2, [5, 9])
    cart = await backend.update_cart_item(session, OPEN, 3)
    assert [line.quantity for line in cart.items] == [3]
    after = await erp.get_departure(OPEN, 2, 2, [5, 9])
    assert after.seats_left == held.seats_left + 1


async def test_a_party_that_no_longer_fits_leaves_the_original_hold_standing(backend, erp, session):
    """The seats go back before the ERP is asked for the larger party, so a refusal must
    not cost the advisor the hold they already had."""
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    held = await erp.get_departure(OPEN, 2, 2, [5, 9])
    with pytest.raises(Unavailable):
        await backend.update_cart_item(session, OPEN, 6)
    cart = await backend.get_cart(session)
    assert [(line.product_id, line.quantity) for line in cart.items] == [(OPEN, 4)]
    after = await erp.get_departure(OPEN, 2, 2, [5, 9])
    assert after.seats_left == held.seats_left


async def test_a_hold_keeps_one_adult_whatever_the_model_asked_for(backend, erp, session):
    """The searched party is 2 大 2 小, but one head is one 成人: the ERP seats no child on
    their own, and a hold for nobody is not a hold."""
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 1)
    holds = await erp.list_holds(advisor_id=ADVISOR, session_key=session.session_id)
    assert [(hold.adults, hold.children) for hold in holds] == [(1, 0)]
    await backend.remove_from_cart(session, OPEN)
    await backend.add_to_cart(session, OPEN, 0)
    holds = await erp.list_holds(advisor_id=ADVISOR, session_key=session.session_id)
    assert [(hold.adults, hold.children) for hold in holds] == [(1, 0)]


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
    assert [product.product_id for product in products] == ["RT-1051"]
    assert products[0].attributes["match"] == "adjacent_date"
    assert products[0].attributes["mismatch"] == "无 10/3–10/8 团期，最近为 10/2"


async def test_a_day_count_nothing_matches_offers_the_nearest_length(backend, session):
    products = await search(
        backend, session, "南疆", destination="南疆", days_min="12", days_max="12"
    )
    assert [product.product_id for product in products] == ["RT-1041"]
    assert products[0].attributes["match"] == "similar_route"
    assert products[0].attributes["mismatch"] == "天数 11 天，超出要求的 12–12 天"


async def test_dropping_a_preference_names_the_one_it_dropped(backend, session):
    products = await search(
        backend, session, "喀纳斯", destination="喀纳斯", hotel_level="五钻", no_shopping="yes"
    )
    notes = {product.product_id: product.attributes["mismatch"] for product in products}
    assert set(notes) == {"RT-1031", "RT-1032"}
    assert all(product.attributes["match"] == "similar_route" for product in products)
    assert notes["RT-1031"] == "酒店为四钻，要求五钻"
    assert notes["RT-1032"] == "酒店为三钻，要求五钻；含 1 个购物店"


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
    assert notes["RT-1031"] == "无 10/3–10/5 团期，最近为 10/1；酒店为四钻，要求五钻"
    # RT-1032 does depart on 10/3, so the window is not one of the conditions it misses.
    assert notes["RT-1032"] == "酒店为三钻，要求五钻；含 1 个购物店"


# -- executor, account, fulfillment ------------------------------------------------------


def test_the_advisor_hears_the_erp_rule_instead_of_an_outage(backend, session):
    relayed = executor(backend, session).domain_error(
        ErpHoldLimit("同一会话最多同时占位 3 个团期。")
    )
    assert relayed.is_error
    assert relayed.result_text.startswith("Nothing changed:")


def test_an_unreachable_erp_stays_an_outage(backend, session):
    """``ErpUnavailable`` is the ERP being down, which the base executor already words."""
    tour = executor(backend, session)
    error = ErpUnavailable("ERP 暂时无法访问。")
    assert tour.domain_error(error) == ShoppingToolExecutor.domain_error(tour, error)


async def test_nothing_ships_for_a_tour_booking(backend, session):
    with pytest.raises(NotOffered):
        await backend.get_fulfillment_options(session, [OPEN])


async def test_the_account_context_counts_the_live_holds(backend, session):
    assert (await backend.get_account_context(session))["active_holds"] == 0
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    account = await backend.get_account_context(session)
    assert account == {"advisor": ADVISOR, "store": "上海徐汇门店", "active_holds": 1}


# -- the demo host's view ----------------------------------------------------------------


async def test_the_listing_snapshot_holds_every_route_with_its_departures(backend):
    await backend.load_listings()
    assert backend.store_name == "ACME 旅行社"
    assert len(backend.products) == 8
    for family in backend.products.values():
        assert family.variants
        assert all(variant.variant_of == family.product_id for variant in family.variants)


async def test_the_snapshot_resolves_a_route_id_and_a_departure_id(backend):
    await backend.load_listings()
    assert backend.product(ROUTE).product_id == ROUTE
    assert backend.product(OPEN).variant_of == ROUTE
    assert backend.product("RT-9999") is None


async def test_resetting_a_session_forgets_the_window_and_party_it_searched(backend, session):
    await search_yili(backend, session)
    held = await backend.get_product_details(session, OPEN)
    assert held.attributes["quote_party"] == "2大2小"
    backend.reset_session(session.session_id)
    fresh = await backend.get_product_details(session, OPEN)
    assert fresh.attributes["quote_party"] == "2大0小"


async def test_the_hold_snapshot_follows_the_carts_lines(backend, session):
    assert backend.holds_snapshot(session.session_id) == []
    await search_yili(backend, session)
    cart = await backend.add_to_cart(session, OPEN, 4)
    snapshot = backend.holds_snapshot(session.session_id)
    assert [hold.departure_id for hold in snapshot] == [item.product_id for item in cart.items]
    assert snapshot[0].adults == 2 and snapshot[0].children == 2
    backend.reset_session(session.session_id)
    assert backend.holds_snapshot(session.session_id) == []


def test_the_demo_starts_with_no_booked_orders(backend):
    """Every booking in this demo is a seat hold taken during the conversation."""
    assert backend.recent_orders() == []
