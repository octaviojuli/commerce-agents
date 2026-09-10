# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour backend's mapping onto ``StorefrontBackend``: 线路 as families, 团期 as variants
quoted for the searched party, the 预留 orders this conversation wrote as cart lines, and the
relaxation that answers a request the catalog cannot meet exactly. Every backend runs on a
fixed ``today``, so the fixture's dates and ids are the same whatever day the suite runs."""

from dataclasses import replace
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
from tour.api.tour_backend import ROUTE_FIRST_GATE, TourBackend, TourToolExecutor, _number

TODAY = date(2026, 9, 6)
ADVISOR = "demo-user"
CUSTOMER_ID = 4101
MOBILE = "13900000001"
ROUTE = "RT-1021"
FULL = "DP-3007"  # 伊犁北疆环线, 10/13, no seats left
OPEN = "DP-3008"  # 伊犁北疆环线, 10/17, four seats left
XINJIANG = 2  # the department every 新疆 线路 and its 团期 belong to
QINGHAI = 5  # 青海部, which RT-1051 and DP-3063 belong to
ELSEWHERE = "DP-3063"  # 青海湖·茶卡, 10/16, five seats left, another department

# What a 同业价 below the 团期's 市场价 costs, for the ERP that quotes one.
TRADE_CUT = 300.0

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


def executor(backend, session, state: ShoppingSessionState | None = None) -> TourToolExecutor:
    """The executor the runtime builds for one turn. ``state`` is the host's, so passing the
    same one twice is the next turn of the same conversation and a fresh one is a new host."""
    return TourToolExecutor(
        backend=backend,
        config=ShoppingAgentConfig(brand_name="ACME"),
        skills=SkillRegistry([]),
        session=session,
        state=state if state is not None else ShoppingSessionState(),
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
    assert route.labels == ["四钻酒店", "纯玩无购物", "亲子", "乌鲁木齐出发"]
    assert route.attributes["tags"].startswith("纯玩|小团")
    assert route.attributes["destination"] == "伊犁"
    # The two dates are the span the advisor means, whichever order they state them in.
    backwards = await search_yili(
        backend, session, depart_from="2026-10-20", depart_to="2026-10-11"
    )
    assert [(p.product_id, p.attributes["match"]) for p in backwards] == [
        (product.product_id, "exact") for product in products
    ]


async def test_a_destination_no_route_name_carries_is_found_on_the_rest_of_the_line(
    backend, erp, session
):
    """The ERP searches 线路 names, and its editors do not put the destination in every one:
    on the agency's own catalog 欧洲 names no route and 欧洲部 sells them all. So a named query
    that is not a shortlist is followed by one broad read of the window, and a route carrying
    the destination anywhere else — here the department — is an exact match, not a relaxed
    one. The broad read is made once per window, so the relaxation steps add no calls."""
    route = erp._routes[1051]
    route["routeName"] = "茶卡盐湖·环线 5 日亲子小团"
    route["features"] = ["茶卡盐湖", "塔尔寺", "黑马河日出"]
    assert "青海" not in route["routeName"] + "".join(route["features"])
    assert "青海" in route["companyName"]
    broad: list[str] = []
    original = erp.search_routes

    async def counted(query):
        broad.append(query.route_name)
        return await original(query)

    erp.search_routes = counted
    products = await search(
        backend,
        session,
        "青海湖",
        destination="青海",
        depart_from="2026-10-05",
        depart_to="2026-10-20",
    )
    assert ids(products) == ["RT-1051"]
    assert products[0].attributes["match"] == "exact"
    assert "mismatch" not in products[0].attributes
    assert products[0].brand == "ACME 旅行社 青海部"
    # The stated window and the widened one, and nothing more: the four relaxation steps
    # share what the widened one already fetched, and the last of them reads no window of
    # its own.
    assert broad.count("") == 2


async def test_a_hotel_standard_is_matched_however_the_advisor_spells_it(backend, session):
    """The ERP states the standard only as a word in the name or the tags, and 五钻, 5钻,
    五星 and 5星 are the same standard."""
    for level in ("五钻", "5钻", "五星", "5星"):
        products = await search_yili(backend, session, hotel_level=level)
        exact = [p.product_id for p in products if p.attributes["match"] == "exact"]
        assert exact == ["RT-1024"], level


async def test_a_private_line_is_not_a_search_result(backend, erp, session):
    """The catalog carries the 包团 a customer chartered and the 会销 one salesperson runs
    beside what anyone may sell, and the ERP has no field saying which is which. So the name
    is read (``data/private-lines.json``), and such a line is kept out of every shortlist —
    the named pass and the broad one — and out of the boot listing snapshot."""
    before = await search_yili(backend, session, no_shopping="")
    assert "RT-1023" in ids(before)
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    products = await search_yili(backend, session, no_shopping="")
    assert "RT-1023" not in ids(products)
    assert "RT-1021" in ids(products)
    await backend.load_listings()
    assert "RT-1023" not in backend.products and "RT-1021" in backend.products


async def test_a_private_line_opens_by_its_id_and_by_its_full_name(backend, erp, session):
    """The salesperson whose 会销 it is can still work it: pasted by id or named in full it is
    the record it always was, and ``line_type`` says what it is so the model says so."""
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    details = await backend.get_product_details(session, "RT-1023")
    assert details is not None and details.attributes["line_type"] == "包团"
    products = await search(
        backend,
        session,
        "王鑫包团-伊犁定制行程 9 日",
        depart_from="2026-10-11",
        depart_to="2026-10-20",
    )
    # The mock scores every 伊犁 line against the name; the ERP's own substring match answers
    # the one line. Either way the named line is offered, and says what it is.
    named = next(p for p in products if p.product_id == "RT-1023")
    assert named.attributes["line_type"] == "包团"
    # A line on general sale carries no line_type at all.
    public = await search_yili(backend, session)
    assert all("line_type" not in p.attributes for p in public)


async def test_the_erps_sale_type_is_read_ahead_of_the_name(backend, erp, session):
    """Once the ERP carries ``saleType``, it decides: a plainly named line it calls 包团 is
    private, and a 包团-named line it calls public is on sale."""
    erp._routes[1022]["saleType"] = "包团"
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    erp._routes[1023]["saleType"] = "公开"
    products = await search_yili(backend, session, no_shopping="")
    assert "RT-1022" not in ids(products)
    assert "RT-1023" in ids(products)


async def test_the_cut_is_ranked_not_the_catalogs_order(backend, erp, session, monkeypatch):
    """A 团期 the party fits into comes first, then the nearest to the middle of the window,
    then 已成团 before 待成团; the ERP's own order, which is the newest 线路 first, decides
    nothing. And a cut smaller than the matches takes at most two lines of one 线路系."""
    products = await search(
        backend,
        session,
        "新疆",
        destination="新疆",
        depart_from="2026-10-11",
        depart_to="2026-10-20",
        adults="2",
    )
    assert len(products) >= 4
    # Hand every 团期 of RT-1032 the party cannot fit into: it ranks last however near.
    for row in erp._departures.values():
        if row["routeId"] == 1032:
            row["availableSeats"] = 1
    products = await search(
        backend,
        session,
        "新疆",
        destination="新疆",
        depart_from="2026-10-11",
        depart_to="2026-10-20",
        adults="2",
    )
    assert ids(products)[-1] == "RT-1032"
    # Cut to three of the seven, 伊犁's four lines take at most two seats in it.
    monkeypatch.setattr(tour_backend, "MAX_BROAD_MATCHES", 3)
    cut = await search(
        backend,
        session,
        "新疆",
        destination="新疆",
        depart_from="2026-10-11",
        depart_to="2026-10-20",
        adults="2",
    )
    regions = [p.attributes["region"] for p in cut]
    assert len(cut) == 3 and regions.count("伊犁") <= 2, regions
    assert all(p.attributes["catalog_matches"] == "7" for p in cut)


async def test_a_region_narrows_the_shortlist_and_a_price_ceiling_too(backend, session):
    """The 线路系 an overview offered goes back as ``region`` and holds; ``price_max`` is a
    ceiling on the 起价, and a 起价 the ERP has not published (0) is not over it."""
    products = await search(
        backend,
        session,
        "新疆",
        destination="新疆",
        depart_from="2026-10-11",
        depart_to="2026-10-20",
        region="喀纳斯",
    )
    assert ids(products) and all(p.attributes["region"] == "喀纳斯" for p in products)
    cheap = await search(
        backend,
        session,
        "新疆",
        destination="新疆",
        depart_from="2026-10-11",
        depart_to="2026-10-20",
        price_max="4000",
    )
    # The ceiling is on the ERP's 起价, which the fixtures put under 4000 on one 新疆 line.
    assert ids(cheap) == ["RT-1032"]


async def test_too_many_matches_hand_the_model_an_overview_not_a_shortlist(backend, session):
    """Above ``OVERVIEW_ABOVE`` matches the executor appends the 目录概览 to the search
    result: the total, the groups by 线路系, 出发城市, 天数, 起价 and 成团, each value one the
    model sends back as a filter, and the instruction to ask one narrowing question. A search
    that fits carries none."""
    tour = executor(backend, session)
    filters = {
        "attributes": {
            "destination": "新疆",
            "depart_from": "2026-10-11",
            "depart_to": "2026-10-20",
        }
    }
    outcome = await tour.dispatch(
        "search_products", {"query": "新疆", "filters": filters, "limit": 2}
    )
    text = outcome.result_text
    assert "目录概览" in text and "上面只是其中 2 条的样本" in text
    assert "按线路系（filter region）" in text and "伊犁 4" in text
    assert "按出发城市（filter departure_city）" in text
    assert "按天数（filter days_min/days_max）" in text
    assert "ask ONE narrowing question" in text
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.total > overview.shown == 2
    # Narrowed to one 线路系, the search fits and the overview is gone.
    narrowed = {"attributes": {**filters["attributes"], "region": "伊犁"}}
    outcome = await tour.dispatch("search_products", {"query": "新疆", "filters": narrowed})
    assert "目录概览" not in outcome.result_text
    assert backend.overview(session.session_id) is None


async def test_the_named_matches_are_never_the_whole_shortlist(backend, erp, session):
    """On the agency's catalog, 欧洲 names three of the thirty-odd lines the 欧洲部 departs
    in a month. So two name matches do not close the search: the broad pass runs whenever a
    destination is stated, a line that carries it only in its tags is kept beside the named
    ones, and every result says how many lines met the request before the cut."""
    route = erp._routes[1024]
    route["routeName"] = "五钻轻奢 8 日私享小团"
    assert "伊犁" not in route["routeName"]
    products = await search_yili(backend, session, days_min="8", days_max="10")
    exact = [p.product_id for p in products if p.attributes["match"] == "exact"]
    assert "RT-1024" in exact and "RT-1021" in exact
    assert {p.attributes["catalog_matches"] for p in products} == {str(len(exact))}


async def test_a_destination_only_the_normalised_tags_carry_is_found(backend, erp, session):
    """The ERP's extraction of the itinerary is where a destination usually is, and it writes
    the place names rather than the region: 禾木村 and 白哈巴 are 喀纳斯, and ``tag-rules.json``
    is what says so. Nothing else on this line says 喀纳斯 at all, so the ERP's own name search
    misses it and the broad pass keeps it on the normalised destination."""
    route = erp._routes[1031]
    route["routeName"] = "禾木秋色 7 日纯玩"
    route["features"] = ["禾木村", "白哈巴", "五彩滩"]
    route["itineraryTags"] = [
        "禾木村",
        "白哈巴",
        "五彩滩",
        "乌鲁木齐出发",
        "四钻酒店",
        "纯玩无购物",
    ]
    assert "喀纳斯" not in str(route)
    products = await search(backend, session, "喀纳斯", destination="喀纳斯")
    found = next(product for product in products if product.product_id == "RT-1031")
    assert found.attributes["match"] == "exact"
    assert found.attributes["destination"] == "喀纳斯"


async def test_no_shopping_reads_the_shopping_tag_before_the_names_wording(backend, session):
    """A line whose extraction lists a market is selling one, whatever its name says, and one
    whose extraction says 纯玩无购物 is 纯玩 even where the name does not."""
    products = await search_yili(backend, session)
    # RT-1023's itinerary tags carry 市集购物, so the filter drops it.
    assert "RT-1023" not in ids(products)
    assert all(product.attributes["shopping"] == "none" for product in products)
    relaxed = await search(
        backend, session, "喀纳斯", destination="喀纳斯", no_shopping="yes", hotel_level="五钻"
    )
    selling = next(product for product in relaxed if product.product_id == "RT-1032")
    assert selling.attributes["shopping"] == "some"
    assert "标签标注含购物" in selling.attributes["mismatch"]


async def test_a_line_whose_tags_name_no_hotel_standard_is_only_a_relaxed_match(backend, session):
    """RT-1051 stays at a 民宿 and its tags name no 钻 standard. That is not a no, so the line
    is not an exact match for 四钻 and the note says the tags do not state it."""
    products = await search(
        backend, session, "青海湖", destination="青海", hotel_level="四钻", adults="2"
    )
    found = next(product for product in products if product.product_id == "RT-1051")
    assert found.attributes["hotel_grade"] == "unknown"
    assert found.attributes["match"] == "similar_route"
    assert found.attributes["mismatch"] == "未标注四钻"


async def test_a_departure_city_keeps_only_the_lines_that_leave_from_it(backend, session):
    """The city is a tag on the itinerary (上海出发, 昆明直飞) and a field on the 线路; either
    one answers, and a line leaving from anywhere else is not offered at all."""
    products = await search(backend, session, "", depart_from="2026-10-01", depart_to="2026-10-31")
    assert len(ids(products)) > 1
    from_kashgar = await search(
        backend,
        session,
        "",
        departure_city="喀什",
        depart_from="2026-10-01",
        depart_to="2026-10-31",
    )
    assert ids(from_kashgar) == ["RT-1041"]
    assert from_kashgar[0].attributes["departure_cities"] == "喀什"


async def test_family_orders_the_shortlist_and_filters_nothing(backend, session):
    """A party with children is a preference and not a condition: the lines whose tags claim
    亲子 come first, and the rest stay on the shortlist for the advisor to weigh."""
    window = {"depart_from": "2026-10-01", "depart_to": "2026-10-31"}
    plain = await search(backend, session, "", **window)
    preferred = await search(backend, session, "", family="yes", **window)
    assert set(ids(preferred)) == set(ids(plain))
    claimed = [p.product_id for p in preferred if p.attributes["family"] == "yes"]
    assert ids(preferred)[: len(claimed)] == claimed
    assert ids(plain)[: len(claimed)] != claimed


async def test_a_cards_labels_are_what_the_line_claims_not_its_first_tags(backend, session):
    """The raw tags are dozens of attraction names, so the badges are the normalised
    attributes in the order an advisor reads them out."""
    products = await search_yili(backend, session)
    labels = {product.product_id: product.labels for product in products}
    assert labels["RT-1024"] == ["五钻酒店", "纯玩无购物", "乌鲁木齐出发", "含景点首道门票"]
    assert labels["RT-1021"][:3] == ["四钻酒店", "纯玩无购物", "亲子"]
    assert all(len(found) <= 4 for found in labels.values())


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
        # Every variant was quoted for this customer, and the fixtures price the 同业价 at the
        # 市场价, so both prices are on the record and equal.
        assert variant.attributes["quote_source"] == "customer"
        assert variant.attributes["market_adult_price"] == variant.attributes["adult_price"]
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

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> Quote:
        raise ErpNotFound("找不到该团期的报价")


class TradePriced(MockErpClient):
    """An ERP whose 同业价 is below the 市场价 the 团期 lists, as a real one's is."""

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> Quote:
        listed = (await super().quote(period_id, customer_id, company_id)).price
        trade = PriceInfo(
            listed.adult - TRADE_CUT,
            listed.child - TRADE_CUT,
            listed.elder - TRADE_CUT,
            listed.single_room_diff,
        )
        return Quote(price=trade, price_type="同行价", is_external=False)


class Unquotable(Unpriced):
    """No price of either kind: the detail call the 市场价 comes from finds nothing either."""

    async def get_departure(self, period_id: int):
        return None


async def test_a_variant_is_quoted_at_the_trade_price_and_carries_the_market_one(session):
    """The advisor settles at the 同业价, so that is what a variant is priced and totalled at;
    the 市场价 rides along for the customer's share page and nothing else."""
    backend = build(TradePriced(today=TODAY, now=FakeClock()))
    await search_yili(backend, session)
    details = await backend.get_product_details(session, ROUTE)
    variant = next(v for v in details.variants if v.product_id == OPEN)
    assert variant.attributes["quote_source"] == "customer"
    assert variant.price == 5780.0 - TRADE_CUT
    assert variant.attributes["adult_price"] == _number(5780.0 - TRADE_CUT)
    assert variant.attributes["child_price"] == _number(3880.0 - TRADE_CUT)
    assert (variant.attributes["market_adult_price"], variant.attributes["market_child_price"]) == (
        "5780",
        "3880",
    )
    total = 2 * (5780.0 - TRADE_CUT) + 2 * (3880.0 - TRADE_CUT)
    assert float(variant.attributes["party_quote_total"]) == total
    # The family's 起价 is the cheapest 同业价 among the departures the padded window priced,
    # which is 10/24's, not this one's.
    assert details.price == 5580.0 - TRADE_CUT
    # And one departure read on its own is quoted the same way.
    alone = await backend.get_product_details(session, OPEN)
    assert (alone.price, alone.attributes["market_adult_price"]) == (5780.0 - TRADE_CUT, "5780")


async def test_a_departure_without_a_customer_price_falls_back_to_the_list_price(session):
    backend = build(Unpriced(today=TODAY, now=FakeClock()))
    details = await backend.get_product_details(session, OPEN)
    assert details.attributes["quote_source"] == "list"
    assert details.price == 5780.0
    assert details.attributes["market_adult_price"] == "5780"
    assert "市场价" in details.short_description


async def test_a_departure_with_neither_price_says_it_has_no_quote(session):
    backend = build(Unquotable(today=TODAY, now=FakeClock()))
    details = await backend.get_product_details(session, ROUTE)
    variant = next(v for v in details.variants if v.product_id == OPEN)
    assert variant.attributes["quote_source"] == "none"
    assert (variant.price, variant.attributes["party_quote_total"]) == (0.0, "0")
    assert "报价待查" in variant.short_description
    # Nothing was quoted, so the family falls back to the 线路's own 起价.
    assert details.price == 5580.0


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
        company_id=XINJIANG,
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
    # The tags name a standard on both, so the note says which one, not that it is missing.
    assert notes["RT-1031"] == "标签标注四钻，要求五钻"
    assert notes["RT-1032"] == "标签标注三钻，要求五钻；标签标注含购物"


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
    assert notes["RT-1031"] == "无 10/3–10/5 团期，最近为 10/1；标签标注四钻，要求五钻"
    # RT-1032 does depart on 10/3, so the window is not one of the conditions it misses.
    assert notes["RT-1032"] == "标签标注三钻，要求五钻；标签标注含购物"


async def test_a_window_the_catalog_has_no_departure_in_offers_the_default_window(backend, session):
    """The week before the earliest 团期 the catalog holds. Widening by a week reaches nothing
    either, so the fourth step searches the whole default window: what the advisor can act on
    is the nearest date the line runs, not an empty shortlist."""
    products = await search(
        backend,
        session,
        "青海湖",
        destination="青海",
        depart_from="2026-09-06",
        depart_to="2026-09-07",
    )
    assert ids(products) == ["RT-1051"]
    assert products[0].attributes["match"] == "adjacent_date"
    assert products[0].attributes["mismatch"] == "无 9/6–9/7 团期，最近为 9/18"
    # The default window's own 团期, so the dates say when the line actually runs.
    assert products[0].options["depart_date"][0] == "2026-09-18"
    assert products[0].options["depart_date"][-1] == "2026-11-02"


async def test_the_exact_matches_come_before_the_relaxed_ones(backend, session):
    """Whichever step admitted them: the advisor reads the shortlist from the top, and a line
    that meets the window they stated is the first thing they should read."""
    products = await search(backend, session, "", depart_from="2026-11-02", depart_to="2026-11-02")
    matches = [product.attributes["match"] for product in products]
    assert matches[0] == "exact" and matches.count("exact") == 1
    assert set(matches[1:]) == {"adjacent_date"}


class LooseDates(MockErpClient):
    """``route/list`` as the production ERP answers it: its date filter is loose, so a window's
    routes include 线路 whose 团期 are all outside it — five 斯里兰卡 lines for a week only two
    of them depart in. The fixtures filter strictly, so the looseness is added here, by dropping
    the window from the query and keeping the ERP's own text match."""

    async def search_routes(self, q):
        return await super().search_routes(replace(q, depart_from=None, depart_to=None))


async def test_a_route_with_no_departure_in_the_window_is_not_an_exact_match(session):
    """The loose filter returns 青海湖 for a week it does not depart in. A card built from that
    row would carry no date, no seat count and no price while claiming to be what the advisor
    asked for, so the route is dropped from the stated pass; the fourth step takes it back with
    its nearest 团期 named."""
    backend = build(LooseDates(today=TODAY, now=FakeClock()))
    products = await search(
        backend,
        session,
        "青海湖",
        destination="青海",
        depart_from="2026-09-06",
        depart_to="2026-09-07",
    )
    assert [product for product in products if product.attributes["match"] == "exact"] == []
    assert ids(products) == ["RT-1051"]
    assert products[0].attributes["match"] == "adjacent_date"
    assert products[0].attributes["mismatch"] == "无 9/6–9/7 团期，最近为 9/18"
    assert products[0].options["depart_date"]
    # A window the route does depart in is untouched by the drop.
    october = await search(
        backend,
        session,
        "青海湖",
        destination="青海",
        depart_from="2026-10-01",
        depart_to="2026-10-07",
    )
    assert [product.attributes["match"] for product in october] == ["exact"]
    assert october[0].options["depart_date"] == ["2026-10-02"]


class Windowed(MockErpClient):
    """A fixture ERP that reads a window whole, as ``HttpErpClient`` does, and counts what the
    backend asked of it. The real one has no ``routeId`` filter on its period list, so a search
    that weighed each candidate on its own would cost a call per 线路; ``calls`` is what holds
    this side to reading the window once instead."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: dict[str, int] = {}

    def _called(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    async def list_window(self, depart_from, depart_to):
        self._called("list_window")
        rows = []
        for route_id in self._routes:
            rows.extend(
                await MockErpClient.list_departures(self, route_id, "", depart_from, depart_to)
            )
        return rows

    async def list_departures(self, route_id, route_name, depart_from, depart_to):
        self._called("list_departures")
        return await super().list_departures(route_id, route_name, depart_from, depart_to)

    async def search_routes(self, q):
        self._called("search_routes")
        return await super().search_routes(q)


async def test_a_search_reads_the_windows_departures_once_for_every_route_it_weighs(session):
    """One window read answers every candidate: which 线路 have a 团期 inside the window, and
    which dates and seats each card carries. Nothing in the pass asks the ERP about one route."""
    erp = Windowed(today=TODAY, now=FakeClock())
    backend = build(erp)
    products = await search_yili(backend, session)
    assert set(ids(products)) == {"RT-1021", "RT-1022", "RT-1024"}
    assert erp.calls["list_window"] == 1
    assert "list_departures" not in erp.calls


class LooseWindowed(Windowed, LooseDates):
    """Both production shapes at once: ``route/list`` answers with 线路 whose 团期 are all
    outside the window, and the 团期 themselves are read a window at a time."""


async def test_a_relaxation_step_costs_a_window_read_only_when_it_moves_the_window(session):
    """The four steps repeat a window more often than they change it: widening the day count or
    dropping the preferences leaves the dates alone, and only the dates decide what is read — and
    the first step's week wider window was read around at the start. Every pass here has
    candidates to weigh, because the ERP's date filter answers with 线路 that have no 团期 inside
    the window at all."""
    erp = LooseWindowed(today=TODAY, now=FakeClock())
    backend = build(erp)
    products = await search(
        backend,
        session,
        "青海湖",
        destination="青海",
        depart_from="2026-09-06",
        depart_to="2026-09-07",
    )
    assert ids(products) == ["RT-1051"]
    # The stated window, read a week around it, answers the first step as well; the two steps
    # after it relax nothing the ERP filters on, and only the fourth step's whole default window
    # reaches past what was read.
    assert erp.calls["list_window"] == 2
    assert "list_departures" not in erp.calls


async def test_a_route_the_advisor_opens_reads_that_routes_own_departures(session):
    """The window read is the search's; a 线路 the advisor opened is one route over a padded
    window, and its seat counts are the freshest read the process has made."""
    erp = Windowed(today=TODAY, now=FakeClock())
    backend = build(erp)
    await search_yili(backend, session)
    details = await backend.get_product_details(session, ROUTE)
    assert details is not None and details.variants
    assert erp.calls["list_departures"] == 1


async def test_an_id_the_catalog_does_not_carry_is_scanned_for_once_and_remembered(session):
    """A pasted id nothing answers for costs one scan of the catalog, not one per read: no
    conversation adds a 线路 to the ERP, and a boot reload is what forgets the miss."""
    erp = Windowed(today=TODAY, now=FakeClock())
    backend = build(erp)
    assert await backend.get_product_details(session, "RT-999999") is None
    scanned = erp.calls["search_routes"]
    assert scanned >= 1
    assert await backend.get_product_details(session, "RT-999999") is None
    assert erp.calls["search_routes"] == scanned
    await backend.load_listings()
    reloaded = erp.calls["search_routes"]
    assert await backend.get_product_details(session, "RT-999999") is None
    assert erp.calls["search_routes"] > reloaded


# -- the route-first gate ----------------------------------------------------------------


async def searched(backend, session, state) -> TourToolExecutor:
    """One turn that searched 伊犁 through the executor, so the state holds the families."""
    tour = executor(backend, session, state)
    await tour.dispatch(
        "search_products", {"query": "伊犁亲子游", "filters": {"attributes": dict(YILI)}}
    )
    return tour


async def test_a_searched_route_opens_only_after_the_model_has_presented_it(backend, session):
    """The advisor picks the 线路 off the cards; a route's 团期 are the step after the pick,
    so a search followed straight by details is held with what to do instead."""
    state = ShoppingSessionState()
    tour = await searched(backend, session, state)
    held = await tour.dispatch("get_product_details", {"product_id": ROUTE})
    assert held.blocked == ROUTE_FIRST_GATE
    assert ROUTE in held.result_text
    assert "present_products" in held.result_text
    # A payload that is not the tool's shape presents nothing, and holds nothing open.
    malformed = await tour.dispatch("present_products", {"picks": ROUTE})
    assert malformed.refused
    assert (await tour.dispatch("get_product_details", {"product_id": ROUTE})).blocked
    shown = await tour.dispatch(
        "present_products", {"picks": [{"product_id": ROUTE}, {"product_id": "RT-1022"}]}
    )
    assert not shown.refused
    assert backend.presented(session.session_id) == {ROUTE, "RT-1022"}
    # The executor is built again for every turn, and the record is the conversation's.
    opened = await executor(backend, session, state).dispatch(
        "get_product_details", {"product_id": ROUTE}
    )
    assert opened.blocked is None and not opened.is_error
    assert ROUTE in opened.result_text


async def test_the_gate_holds_only_the_routes_this_conversation_searched(backend, session):
    """A 团期 id is the step after the pick and a route id the advisor pasted was never on a
    card of ours, so neither is held; a route this session searched and did not show is."""
    state = ShoppingSessionState()
    pasted = await executor(backend, session, state).dispatch(
        "get_product_details", {"product_id": ROUTE}
    )
    assert pasted.blocked is None and not pasted.is_error
    tour = await searched(backend, session, ShoppingSessionState())
    departure = await tour.dispatch("get_product_details", {"product_id": OPEN})
    assert departure.blocked is None and not departure.is_error
    assert (await tour.dispatch("get_product_details", {"product_id": "RT-1024"})).blocked == (
        ROUTE_FIRST_GATE
    )


async def test_the_departures_a_card_showed_are_recorded_beside_the_routes(backend, session):
    """A route's 团期 are cards too, so the ids a card showed are recorded whichever kind they
    are; ``present_shortlist`` reads that record to keep the customer's list behind the
    advisor's pick. Anything in ``picks`` that is not an id of ours is recorded as nothing."""
    state = ShoppingSessionState()
    tour = await searched(backend, session, state)
    await tour.dispatch("present_products", {"picks": [{"product_id": ROUTE}]})
    await executor(backend, session, state).dispatch("get_product_details", {"product_id": ROUTE})
    shown = await executor(backend, session, state).dispatch(
        "present_products", {"picks": [{"product_id": OPEN}, {"product_id": "客人指定"}]}
    )
    assert not shown.refused
    assert backend.presented(session.session_id) == {ROUTE, OPEN}


async def test_resetting_a_session_forgets_the_routes_it_showed(backend, session):
    state = ShoppingSessionState()
    tour = await searched(backend, session, state)
    await tour.dispatch("present_products", {"picks": [{"product_id": ROUTE}]})
    assert backend.presented(session.session_id) == {ROUTE}
    backend.reset_session(session.session_id)
    assert backend.presented(session.session_id) == set()
    again = await (await searched(backend, session, ShoppingSessionState())).dispatch(
        "get_product_details", {"product_id": ROUTE}
    )
    assert again.blocked == ROUTE_FIRST_GATE


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


async def test_an_add_writes_the_order_in_the_departures_own_department(backend, erp, session):
    """The ERP refuses a write made in any other department, so the order names the 团期's own
    — even for a 团期 in a department the conversation never searched, which is read first."""
    written: list[int] = []
    original = erp.create_order

    async def counted(request):
        written.append(request.company_id)
        return await original(request)

    erp.create_order = counted
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    await backend.add_to_cart(session, ELSEWHERE, 2)
    assert written == [XINJIANG, QINGHAI]
    assert {item.product_id for item in (await backend.get_cart(session)).items} == {
        OPEN,
        ELSEWHERE,
    }
    # A 团期 the ERP does not have is the ERP's own rule, in its own words.
    with pytest.raises(ErpNotFound, match="找不到该团期"):
        await backend.add_to_cart(session, "DP-999999", 2)


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
        # The fixtures log nobody in, so the one department is the store's own.
        "department": "ACME 旅行社",
        "departments": 1,
        "customer": str(CUSTOMER_ID),
        "active_holds": 0,
    }
    await search_yili(backend, session)
    await backend.add_to_cart(session, OPEN, 4)
    assert (await backend.get_account_context(session))["active_holds"] == 1


async def test_a_chinese_question_finds_the_rule_it_asks_about(backend, session):
    """The advisor types the customer's own words, which carry no ASCII token to split on."""
    found = await backend.search_policies(session, "退团怎么退")
    assert [policy.title for policy in found][:1] == ["退改政策"]
    assert len(found) <= 3
    assert [p.title for p in await backend.search_policies(session, "不成团怎么办")] == ["成团规则"]
    # Nothing this agency has a rule for, so nothing is quoted back as one.
    assert await backend.search_policies(session, "今天天气怎么样") == []


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
