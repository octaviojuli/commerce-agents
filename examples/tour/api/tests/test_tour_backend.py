# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour backend's mapping onto ``StorefrontBackend``: 线路 as families, 团期 as variants
quoted for the searched party, the 预留 orders this conversation wrote as cart lines, and the
relaxation that answers a request the catalog cannot meet exactly. Every backend runs on a
fixed ``today``, so the fixture's dates and ids are the same whatever day the suite runs."""

import asyncio
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
    Itinerary,
    ItineraryDay,
    PriceInfo,
    Quote,
)
from tour.api.mock_erp import MockErpClient
from tour.api.route_docs import RouteDocStore
from tour.api.tests.test_itinerary_source import docx, paragraph, table
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


def build(erp: MockErpClient, *, documents: bool = True) -> TourBackend:
    """The backend over the fixture catalog. ``documents=False`` is a deployment with no 线路
    文档 at all: nothing is searchable, and a 线路's 行程 is read from the ERP or parsed out of
    its 行程附件, which is what the fixtures did before the catalog was documents."""
    return TourBackend(
        erp,
        today=TODAY,
        customer_id=CUSTOMER_ID,
        contact_mobile=MOBILE,
        route_docs=None if documents else RouteDocStore(),
    )


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


# -- search: the 线路文档 are the catalog ---------------------------------------------------


async def test_search_returns_the_lines_whose_document_meets_the_request(backend, session):
    """The advisor's request is answered off the agency's reviewed 线路文档: the countries,
    the length, the 购物店 and the hotel standard are the document's, and every card states
    them. The ERP is asked for the dates alone."""
    products = await search_yili(backend, session)
    assert set(ids(products)) == {"RT-1021", "RT-1022", "RT-1024"}
    # RT-1023's document lists two 购物店, so 纯玩 rules it out; nothing relaxes it back in.
    for product in products:
        assert product.variant_of is None
        assert product.attributes["match"] == "exact"
        assert product.attributes["doc"] == "reviewed"
        assert product.attributes["countries"] == "新疆"
        assert product.attributes["region"] == "伊犁"
        assert product.attributes["shopping_stops"] == "0"
        assert product.attributes["depart_city"] == "乌鲁木齐"
        assert product.attributes["catalog_matches"] == "3"
        assert product.category == "tour" and product.currency == "CNY"
    route = next(product for product in products if product.product_id == ROUTE)
    assert route.attributes["days"] == "8" and route.attributes["nights"] == "7"
    assert route.attributes["places"].startswith("乌鲁木齐|赛里木湖")
    assert route.attributes["hotel_standard"].startswith("全程当地四钻酒店")
    assert route.price == 5580.0  # the ERP's 起价, which is the only figure a card quotes
    assert route.labels == ["纯玩", "四钻", "已复核"]
    # The two dates are the span the advisor means, whichever order they state them in.
    backwards = await search_yili(
        backend, session, depart_from="2026-10-20", depart_to="2026-10-11"
    )
    assert ids(backwards) == ids(products)


async def test_a_line_the_agency_has_no_document_for_is_not_searched(erp, session):
    """The document is what the agency has checked; a 线路 without one is not offered at all,
    whatever its name and its tags say."""
    backend = build(erp, documents=False)
    assert await search_yili(backend, session) == []
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.empty


async def test_stated_dates_put_the_sellable_departures_on_the_card(backend, session):
    """Once the advisor has given the customer's dates, every 线路 card carries the 团期 that
    run in them and what the advisor may do with each — dates and state only, because seats
    and prices are the 团期卡's."""
    products = await search_yili(backend, session)
    route = next(product for product in products if product.product_id == ROUTE)
    assert route.attributes["departures"] == "2026-10-13:满员|2026-10-17:已成团"
    assert route.attributes["departures_window"] == "2026-10-11..2026-10-20"
    assert route.options["depart_date"] == ["2026-10-13", "2026-10-17"]
    # The party is what 满员 is measured against: 10/17 has four seats left, so a party of
    # six does not fit into it.
    six = await search_yili(backend, session, adults="6", children="0")
    larger = next(product for product in six if product.product_id == ROUTE)
    assert larger.attributes["departures"] == "2026-10-13:满员|2026-10-17:满员"


async def test_a_search_with_no_dates_carries_none_and_the_window_is_remembered(backend, session):
    """A card states sellable 团期 only against dates the advisor gave: with none the card is
    the line alone. The dates the conversation did state stand for the searches after it."""
    undated = await search(backend, session, "伊犁", destination="伊犁")
    assert ids(undated) and all("departures" not in p.attributes for p in undated)
    await search_yili(backend, session)
    again = await search(backend, session, "伊犁", destination="伊犁", days_min="8", days_max="8")
    assert all(p.attributes["departures_window"] == "2026-10-11..2026-10-20" for p in again)


async def test_a_hotel_standard_is_matched_however_the_advisor_spells_it(backend, session):
    """The standard is the one the document states, and 五钻, 5钻, 五星 and 5星 are all 五钻."""
    for level in ("五钻", "5钻", "五星", "5星"):
        assert ids(await search_yili(backend, session, hotel_level=level)) == ["RT-1024"], level


async def test_a_departure_city_keeps_only_the_lines_that_leave_from_it(backend, session):
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
    assert from_kashgar[0].attributes["depart_city"] == "喀什"


async def test_a_region_narrows_the_shortlist_and_a_price_ceiling_too(backend, session):
    """The 线路系 a chip offered goes back as ``region``; ``price_max`` is a ceiling on the
    ERP's 起价, and a 起价 the ERP has not published (0) is not over it."""
    products = await search(backend, session, "新疆", destination="新疆", region="喀纳斯")
    assert ids(products) and all(p.attributes["region"] == "喀纳斯" for p in products)
    cheap = await search(backend, session, "新疆", destination="新疆", price_max="4000")
    assert ids(cheap) == ["RT-1032"]


async def test_a_destination_is_matched_on_everything_the_document_names(backend, session):
    """Not the 线路 name alone: the countries, the 线路系, the department that sells the line,
    the places its days pass through and the sights it names all answer a destination — and a
    destination written as several places has to be carried in full."""
    by_place = await search(backend, session, "白哈巴", destination="白哈巴")
    assert ids(by_place) == ["RT-1031"]
    by_region = await search(backend, session, "南疆", destination="南疆")
    assert ids(by_region) == ["RT-1041"]
    by_department = await search(backend, session, "青海部", destination="青海部")
    assert ids(by_department) == ["RT-1051"]
    joined = await search(backend, session, "", destination="喀纳斯·禾木村")
    assert set(ids(joined)) == {"RT-1031", "RT-1032"}
    assert ids(await search(backend, session, "", destination="冰岛")) == []


async def test_no_shopping_reads_the_documents_own_purchase_stops(backend, session):
    """A line whose document lists a 购物店 is selling one, whatever its name says."""
    selling = await search(backend, session, "伊犁", destination="伊犁", days_min="9", days_max="9")
    assert ids(selling) == ["RT-1023"]
    assert selling[0].attributes["shopping_stops"] == "2"
    assert selling[0].labels[0] == "购物店2家"
    pure = await search(
        backend, session, "伊犁", destination="伊犁", days_min="9", days_max="9", no_shopping="yes"
    )
    assert ids(pure) == []


async def test_a_private_line_is_not_a_search_result(backend, erp, session):
    """The catalog carries the 包团 a customer chartered and the 会销 one salesperson runs
    beside what anyone may sell, and the ERP has no field saying which is which. So the name
    is read (``data/private-lines.json``), and such a line is kept out of every shortlist."""
    before = await search_yili(backend, session, no_shopping="")
    assert "RT-1023" in ids(before)
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    backend._routes.clear()
    products = await search_yili(backend, session, no_shopping="")
    assert "RT-1023" not in ids(products)
    assert "RT-1021" in ids(products)


async def test_a_private_line_opens_by_its_id_and_by_its_full_name(backend, erp, session):
    """The salesperson whose 会销 it is can still work it: pasted by id or named in full it is
    the record it always was, and ``line_type`` says what it is so the model says so."""
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    backend._routes.clear()
    details = await backend.get_product_details(session, "RT-1023")
    assert details is not None and details.attributes["line_type"] == "包团"
    named = await search(backend, session, "", destination="王鑫包团-伊犁定制行程 9 日")
    # The document keeps the line's catalogued name; either name opens it.
    assert ids(named) == ["RT-1023"] and named[0].attributes["line_type"] == "包团"
    public = await search_yili(backend, session)
    assert all("line_type" not in p.attributes for p in public)


async def test_a_request_wider_than_a_shortlist_hands_the_model_an_overview(backend, session):
    """Above ``OVERVIEW_ABOVE`` matches the executor appends the 目录概览 to the search result:
    the total, the groups over every dimension that splits the set, each value one the model
    sends back as a filter, and the instruction to ask one narrowing question."""
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
    assert "目录概览：共 7 条线路符合" in text and "上面是其中 2 条" in text
    assert "按目的地（filter region/destination）：伊犁 4、喀纳斯 2、南疆 1" in text
    assert "按天数（filter days_min/days_max）" in text
    assert "按酒店标准（filter hotel_level）" in text and "按纯玩（filter no_shopping）" in text
    assert "end the reply with present_focus asking ONE narrowing question" in text
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.total == 7 and overview.shown == 2
    # Narrowed to one 线路系, the search fits and the overview is gone.
    narrowed = {"attributes": {**filters["attributes"], "region": "喀纳斯"}}
    outcome = await tour.dispatch("search_products", {"query": "新疆", "filters": narrowed})
    assert "目录概览" not in outcome.result_text
    assert backend.overview(session.session_id) is None


async def test_versions_of_one_trip_are_put_back_to_the_advisor(backend, session):
    """Two 8-day 新疆 lines to the same country are one trip sold twice, and the customer has
    to say which: the overview stands even at two matches and names what tells them apart."""
    products = await search(
        backend, session, "新疆", destination="新疆", days_min="8", days_max="8"
    )
    assert set(ids(products)) == {"RT-1021", "RT-1024"}
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.ambiguous
    assert overview.dimensions == ("酒店标准",)
    assert list(overview.groups)[0] == "酒店标准"
    assert "只差在酒店标准" in overview.text()
    assert "versions of one trip" in overview.text()


async def test_nothing_in_the_catalog_matched_offers_the_directions_it_sells(backend, session):
    """A request the documents do not meet returns nothing — never the nearest thing — and
    the chips beside it are the catalog's own, so the advisor can offer another direction."""
    products = await search(backend, session, "冰岛", destination="冰岛", days_min="14")
    assert products == []
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.empty and overview.total == 0
    assert overview.groups["目的地"][0] == ("伊犁", 4)
    assert "没有符合条件的线路" in overview.text()


async def test_family_orders_the_shortlist_and_filters_nothing(backend, session):
    """A party with children is a preference and not a condition: the lines whose document
    claims 亲子 come first, and the rest stay on the shortlist for the advisor to weigh."""
    window = {"depart_from": "2026-10-01", "depart_to": "2026-10-31"}
    plain = await search(backend, session, "", **window)
    preferred = await search(backend, session, "", family="yes", **window)
    assert set(ids(preferred)) == set(ids(plain))
    # 青海湖·茶卡 5 日亲子小团 is the one line whose document claims 亲子, and it leads.
    assert ids(preferred)[0] == "RT-1051"


async def test_a_cards_labels_are_what_the_document_states(backend, session):
    """The badges are 购物, the hotel standard, the airline where the document names one, and
    whether the document is the agency's word or the parser's draft."""
    products = await search_yili(backend, session, no_shopping="")
    labels = {product.product_id: product.labels for product in products}
    assert labels["RT-1024"] == ["纯玩", "五钻", "已复核"]
    assert labels["RT-1023"] == ["购物店2家", "四钻", "已复核"]
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


# -- 行程来源: the baseline day-by-day itinerary --------------------------------------------


async def test_route_details_carry_the_erps_own_days_under_the_source_line(erp, session):
    """A deployment with no 线路文档: the ERP's own days are the baseline 行程."""
    backend = build(erp, documents=False)
    details = await backend.get_product_details(session, ROUTE)
    assert details.specs["行程来源"] == "ERP"
    assert [key for key in details.specs if key.startswith("第")] == [
        f"第{n}天" for n in range(1, 9)
    ]
    day = details.specs["第6天"]
    assert day.startswith("那拉提连住 · 空中草原与薰衣草田｜")
    assert "｜住宿：那拉提 · 河谷牧歌度假酒店（当地四钻，连住第二晚）" in day
    assert "｜用餐：早餐：酒店；午餐：镇上小馆；晚餐：酒店合菜" in day


async def test_a_departure_carries_its_own_routes_days(erp, session):
    backend = build(erp, documents=False)
    details = await backend.get_product_details(session, OPEN)
    assert details.specs["行程来源"] == "ERP"
    assert details.specs["第1天"].startswith("乌鲁木齐集合｜")


async def test_a_route_with_no_itinerary_at_all_says_so_rather_than_saying_nothing(erp, session):
    backend = build(erp, documents=False)
    details = await backend.get_product_details(session, "RT-1041")
    assert details.specs["行程来源"] == tour_backend.NO_ITINERARY
    assert not [key for key in details.specs if key.startswith("第")]


async def test_search_results_carry_no_day_specs(backend, session):
    products = await search_yili(backend, session)
    assert set(ids(products)) == {"RT-1021", "RT-1022", "RT-1024"}
    for product in products:
        assert not [key for key in product.attributes if key.startswith("第")]
        assert "行程来源" not in product.attributes


async def test_a_days_text_is_capped_and_at_most_twenty_days_ride(erp, session):
    backend = build(erp, documents=False)
    long_day = "程" * (tour_backend.MAX_DAY_CHARS + 400)
    backend._itineraries[1021] = Itinerary(
        1021,
        "attachment",
        '"v9"',
        tuple(ItineraryDay(n, "标题", long_day, None, None) for n in range(1, 27)),
    )
    details = await backend.get_product_details(session, ROUTE)
    days = [key for key in details.specs if key.startswith("第")]
    assert len(days) == tour_backend.MAX_ITINERARY_DAYS
    assert details.specs["第1天"] == "标题｜" + "程" * tour_backend.MAX_DAY_CHARS


class CountingItineraries(MockErpClient):
    """Counts what the backend asks the ERP for a 线路's days."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.reads = 0

    async def get_itinerary(self, route_id: int):
        self.reads += 1
        return await super().get_itinerary(route_id)


async def test_a_second_read_of_one_route_asks_the_erp_for_its_days_once(session):
    erp = CountingItineraries(today=TODAY, now=FakeClock())
    backend = build(erp, documents=False)
    await backend.get_product_details(session, ROUTE)
    await backend.get_product_details(session, ROUTE)
    await backend.get_product_details(session, OPEN)
    assert erp.reads == 1


async def test_two_sessions_opening_one_route_at_once_read_its_days_once(session, other_session):
    erp = CountingItineraries(today=TODAY, now=FakeClock())
    backend = build(erp, documents=False)
    await asyncio.gather(
        backend.get_product_details(session, ROUTE),
        backend.get_product_details(other_session, ROUTE),
    )
    assert erp.reads == 1


class NoErpItinerary(MockErpClient):
    """An ERP with no itinerary endpoint — production, until it has one — whose 线路 carry the
    行程附件 the catalog links instead."""

    async def get_itinerary(self, route_id: int):
        return None

    async def search_routes(self, q):
        return [replace(row, **ATTACHMENT) for row in await super().search_routes(q)]


ATTACHMENT = {
    "attachment_name": "伊犁北疆环线 8 日.docx",
    "attachment_url": "https://files.example/acme/ylbj.docx",
}
# What that file holds, in the layout the agency's product staff write: one table of day rows,
# the terms after it. ``test_itinerary_source.py`` is what holds the parser to both layouts.
ATTACHMENT_DOCX = docx(
    table(
        [
            ["第 1 天 乌鲁木齐集合"],
            ["用餐", "早：自理", "中：自理", "晚：自理"],
            ["住宿", "乌鲁木齐 · 云杉里酒店", "交通：旅游用车"],
            ["全天接站，送酒店办理入住，晚上开行前说明会。"],
            ["第 2 天 乌鲁木齐-赛里木湖"],
            ["用餐", "早：酒店", "中：路餐", "晚：湖畔炖鱼"],
            ["住宿", "赛里木湖 · 湖畔星野度假酒店", "交通：旅游用车"],
            ["上午前往赛里木湖，下午环湖，傍晚在湖边看落日。"],
        ]
    ),
    paragraph("包含项目"),
    paragraph("行程所列门票与用餐。"),
)
# A file the editors uploaded that names no day at all.
ATTACHMENT_EMPTY = docx(paragraph("本文件仅供同行参考，具体行程以出团通知为准。"))


def attachment_backend(monkeypatch, state_dir, fetched, seen: list) -> TourBackend:
    """A backend whose 线路 all carry a 行程附件, over a fetch that answers ``fetched`` and
    counts the calls made to it."""

    async def fetch(url: str, *, etag: str | None, **kwargs):
        seen.append((url, etag))
        return fetched

    monkeypatch.setattr(tour_backend, "fetch_attachment", fetch)
    erp = NoErpItinerary(today=TODAY, now=FakeClock())
    return TourBackend(
        erp,
        today=TODAY,
        customer_id=CUSTOMER_ID,
        contact_mobile=MOBILE,
        state_dir=state_dir,
        route_docs=RouteDocStore(),
    )


async def test_a_routes_days_are_parsed_out_of_its_attachment_and_kept_on_disk(
    monkeypatch, tmp_path, session
):
    seen: list = []
    backend = attachment_backend(monkeypatch, tmp_path, (ATTACHMENT_DOCX, '"v1"'), seen)
    details = await backend.get_product_details(session, ROUTE)
    assert details.specs["行程来源"] == "行程附件 伊犁北疆环线 8 日.docx"
    assert details.specs["第1天"].startswith("乌鲁木齐集合｜全天接站")
    assert (
        "｜住宿：乌鲁木齐 · 云杉里酒店｜用餐：早：自理 中：自理 晚：自理" in details.specs["第1天"]
    )
    assert seen == [("https://files.example/acme/ylbj.docx", None)]
    # A second process over the same state directory reads the file and downloads nothing new,
    # sending the ETag it was written with.
    again = attachment_backend(monkeypatch, tmp_path, None, seen)
    reread = await again.get_product_details(session, ROUTE)
    assert reread.specs == details.specs
    assert seen[1] == ("https://files.example/acme/ylbj.docx", '"v1"')


async def test_an_attachment_that_cannot_be_read_leaves_the_route_saying_so(
    monkeypatch, tmp_path, session
):
    for fetched in ((b"not a .docx", None), (ATTACHMENT_EMPTY, None), None):
        backend = attachment_backend(monkeypatch, tmp_path / str(id(fetched)), fetched, [])
        details = await backend.get_product_details(session, ROUTE)
        assert details.specs["行程来源"] == tour_backend.NO_ITINERARY
        assert details.variants  # the 团期 are still there: only the 行程 is missing


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


# -- what one search costs the ERP ---------------------------------------------------------


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
