# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``api/catalog.py``: the reviewed 线路文档 as the catalog a search runs over, what it filters
on, the groups it puts a wide request back to the advisor as, and when a handful of matches are
one trip sold several ways. The documents here are invented for the test — four 斯里兰卡
versions of one week, a 德法意瑞 fortnight and a 北欧 eleven days — because an agency's own
catalog is not a fixture."""

from datetime import UTC, date, datetime

import pytest

from tour.api.catalog import Catalog, Request, facts_of
from tour.api.erp_client import RouteRecord
from tour.api.route_doc import (
    Cover,
    Day,
    Flight,
    Hotel,
    Meal,
    Meals,
    OptionalItem,
    Policies,
    Quality,
    RouteDoc,
    ShoppingStop,
    Sight,
    Source,
    Summary,
)
from tour.api.route_docs import RouteDocStore

TODAY = date(2026, 9, 6)
WINDOW = {"depart_from": TODAY, "depart_to": date(2026, 12, 31)}


def _day(number: int, place: str, *, sights: tuple[str, ...] = (), hotel: str = "") -> Day:
    return Day(
        day=number,
        title=f"{place}",
        places=[place],
        transport="车程约 3 小时" if number == 2 else "",
        overnight="hotel" if hotel else "flight",
        flights=(
            [
                Flight(
                    day=number,
                    flight_no="KA912",
                    carrier="国泰",
                    from_place="上海",
                    to_place=place,
                    times="0830-1400",
                    raw="",
                )
            ]
            if number == 1
            else []
        ),
        sights=[Sight(name=name, kind="景点", ticket_included=True) for name in sights],
        meals=Meals(
            breakfast=Meal(text="酒店", included=True),
            lunch=Meal(text="团餐", included=True),
            dinner=Meal(text="自理", included=False),
        ),
        hotel=Hotel(name=hotel, grade="", or_similar=True) if hotel else None,
        text=f"全天在{place}游览。",
    )


def doc(
    route_id: int,
    name: str,
    *,
    days: int,
    countries: list[str],
    region: str = "",
    depart_city: str = "上海",
    hotel_standard: str = "全程网评4钻酒店",
    airline: str = "",
    places: tuple[str, ...] = (),
    sights: tuple[str, ...] = (),
    shopping: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    reviewed: bool = True,
    department: str = "亚洲部",
) -> RouteDoc:
    """One invented 线路文档. ``places`` are the day titles, so a line passes through them in
    order; ``sights`` ride on the second day, where a 门票 is included."""
    stops = places or tuple(countries)
    written = [
        _day(
            number,
            stops[(number - 1) % len(stops)],
            sights=sights if number == 2 else (),
            hotel=f"{stops[0]}酒店" if number < days else "",
        )
        for number in range(1, days + 1)
    ]
    return RouteDoc(
        route_id=route_id,
        route_code=f"RC{route_id}",
        name=name,
        department=department,
        summary=Summary(
            days=days,
            nights=days - 1,
            depart_city=depart_city,
            countries=countries,
            region=region,
        ),
        cover=Cover(
            airline=airline,
            hotel_standard=hotel_standard,
            meal_standard="含早餐与行程所列正餐",
            highlights=[f"{name} 的第一个亮点", "第二个亮点", "第三个亮点", "第四个亮点"],
        ),
        days=written,
        inclusions=["国际机票", "行程所列门票"],
        exclusions=["单房差"],
        shopping=[ShoppingStop(name=stop, day=3, duration="60 分钟") for stop in shopping],
        optional=[OptionalItem(name=item, price="120 美金/人", day=4) for item in optional],
        policies=Policies(single_room="单房差 2400 元/人"),
        source=Source(
            attachment_name=f"{name}.docx",
            attachment_url=f"https://files.example/{route_id}.docx",
            bytes=1024,
            parsed_at=datetime(2026, 8, 1, tzinfo=UTC),
            parser="test",
        ),
        quality=Quality(completeness=1.0, reviewed_by="产品部 · 林薇" if reviewed else ""),
    )


def record(route_id: int, name: str, from_price: float) -> RouteRecord:
    return RouteRecord(
        route_id=route_id,
        route_code=f"RC{route_id}",
        route_name=name,
        days=7,
        depart_city="上海",
        company_name="ACME 旅行社 亚洲部",
        from_price=from_price,
        tags=(),
        itinerary_tags=(),
        price_tags=(),
        features=(),
        image_url=None,
        attachment_name=f"{name}.docx",
        attachment_url=None,
    )


# 斯里兰卡 7 天, sold four ways: the catalog's own way of saying one trip.
ALL_IN = doc(
    201,
    "斯里兰卡 7 天一价全含纯玩",
    days=7,
    countries=["斯里兰卡"],
    region="斯里兰卡一地",
    hotel_standard="全程网评5钻酒店",
    places=("科伦坡", "康提", "努沃勒埃利耶", "加勒"),
    sights=("狮子岩", "佛牙寺"),
)
WHALE = doc(
    202,
    "斯里兰卡 7 天含观鲸",
    days=7,
    countries=["斯里兰卡"],
    region="斯里兰卡一地",
    places=("科伦坡", "美蕊沙", "加勒"),
    sights=("观鲸出海", "海上火车"),
    shopping=("宝石店",),
    optional=("热气球",),
)
CATHAY = doc(
    203,
    "斯里兰卡 7 天国泰联运",
    days=7,
    countries=["斯里兰卡"],
    region="斯里兰卡一地",
    depart_city="广州",
    airline="国泰航空",
    places=("科伦坡", "康提", "加勒"),
    sights=("狮子岩",),
)
DRAFT = doc(
    204,
    "斯里兰卡 7 天精选",
    days=7,
    countries=["斯里兰卡"],
    region="斯里兰卡一地",
    places=("科伦坡", "康提"),
    reviewed=False,
)
EUROPE = doc(
    301,
    "德法意瑞 12 天经典",
    days=12,
    countries=["德国", "法国", "意大利", "瑞士"],
    region="德法意瑞",
    department="欧洲部",
    places=("法兰克福", "巴黎", "卢塞恩", "罗马"),
    sights=("卢浮宫",),
    shopping=("免税店", "皮具中心"),
)
NORDIC = doc(
    302,
    "北欧四国 11 天",
    days=11,
    countries=["挪威", "瑞典", "丹麦", "芬兰"],
    region="北欧",
    department="欧洲部",
    depart_city="北京",
    places=("赫尔辛基", "斯德哥尔摩", "奥斯陆", "哥本哈根"),
)
DOCS = (ALL_IN, WHALE, CATHAY, DRAFT, EUROPE, NORDIC)
PRICES = {201: 12800.0, 202: 9800.0, 203: 10800.0, 204: 8800.0, 301: 18800.0, 302: 26800.0}


@pytest.fixture
def catalog(tmp_path) -> Catalog:
    published = tmp_path / "published"
    published.mkdir(parents=True)
    for written in DOCS:
        (published / f"{written.route_id}.json").write_text(
            written.model_dump_json(), encoding="utf-8"
        )
    store = RouteDocStore.load(tmp_path)
    records = {
        doc_id: record(doc_id, next(d.name for d in DOCS if d.route_id == doc_id), price)
        for doc_id, price in PRICES.items()
    }
    return Catalog(store, records)


def ask(text: str = "", **fields) -> Request:
    """One request, as the advisor's words and their chips reach the catalog: ``text`` is the
    destination they stated, and a field the chip bar answers with several taps is a tuple."""
    destinations = (text,) if text else ()
    return Request(destinations=fields.pop("destinations", destinations), **WINDOW, **fields)


def ids(matches) -> list[int]:
    return [facts.route_id for facts in matches]


# -- what a document says about a 线路 -------------------------------------------------------


def test_the_facts_are_the_documents_own_fields():
    facts = facts_of(WHALE, record(202, WHALE.name, 9800.0))
    assert facts.days == 7 and facts.nights == 6 and facts.depart_city == "上海"
    assert facts.countries == ("斯里兰卡",) and facts.region == "斯里兰卡一地"
    assert facts.shopping_stops == 1 and facts.optional_count == 1
    assert facts.ticket_count == 2 and facts.gift_count == 0
    assert facts.places[:2] == ("科伦坡", "美蕊沙")
    assert facts.sight_names == ("观鲸出海", "海上火车")
    assert facts.highlights == tuple(WHALE.cover.highlights[:3])
    assert facts.hotel_grade == "四钻" and facts.reviewed
    assert facts.from_price == 9800.0 and facts.attachment_name == f"{WHALE.name}.docx"
    # 观鲸 is on the sights and 纯玩 on no part of this line, so only the first is a feature.
    assert facts.feature_words == ("观鲸",)
    # The words are read off the name and the sights; 网评5钻 is in the cover and is not one,
    # and 纯玩 is not a feature word at all — the 纯玩 group already carries it.
    assert facts_of(ALL_IN).feature_words == ("一价全含",)
    # With no ERP row the line is still in the catalog, at 起价未知.
    assert facts_of(WHALE).from_price == 0.0 and facts_of(WHALE).image_url is None


def test_a_line_with_no_document_is_not_in_the_catalog(catalog):
    assert catalog.get(202) is not None and catalog.get(999) is None
    assert len(catalog) == len(DOCS)


# -- the filters ---------------------------------------------------------------------------


def test_a_destination_is_matched_on_every_name_the_document_carries(catalog):
    assert ids(catalog.match(ask(text="斯里兰卡"))) == [202, 203, 201, 204]
    assert ids(catalog.match(ask(text="德法意瑞"))) == [301]
    assert ids(catalog.match(ask(text="巴黎"))) == [301]  # a place its days pass through
    assert ids(catalog.match(ask(text="卢浮宫"))) == [301]  # a sight it names
    assert ids(catalog.match(ask(text="欧洲部"))) == [301, 302]  # the department that sells it
    # A destination written as several places has to be carried in full.
    assert ids(catalog.match(ask(text="德国·瑞士"))) == [301]
    assert catalog.match(ask(text="德国·冰岛")) == []


def test_a_feature_word_narrows_a_family_of_one_trip(catalog):
    assert ids(catalog.match(ask(text="斯里兰卡 观鲸"))) == [202]
    assert ids(catalog.match(ask(text="斯里兰卡 一价全含"))) == [201]


def test_the_documents_own_fields_are_what_the_filters_read(catalog):
    assert ids(catalog.match(ask(days_min=11, days_max=12))) == [301, 302]
    assert ids(catalog.match(ask(text="斯里兰卡", departure_cities=("广州",)))) == [203]
    assert ids(catalog.match(ask(text="斯里兰卡", no_shopping=True))) == [203, 201, 204]
    assert ids(catalog.match(ask(hotel_levels=("五钻",)))) == [201]
    assert ids(catalog.match(ask(hotel_levels=("5星",)))) == [201]
    assert ids(catalog.match(ask(regions=("北欧",)))) == [302]
    assert ids(catalog.match(ask(text="斯里兰卡", price_max=10000))) == [202, 204]


def test_a_filter_the_advisor_tapped_twice_keeps_either(catalog):
    """The chip bar sends every value the advisor tapped, and inside one group they are
    alternatives: 德法意瑞 or 北欧, 11 or 12 days, 上海 or 广州, 四钻 or 五钻, 观鲸 or 一价全含."""
    assert ids(catalog.match(ask(regions=("德法意瑞", "北欧")))) == [301, 302]
    assert ids(catalog.match(ask(destinations=("德国·瑞士", "挪威")))) == [301, 302]
    assert ids(catalog.match(ask(days=(11, 12)))) == [301, 302]
    assert ids(catalog.match(ask(days=(7, 12)))) == [202, 203, 201, 301, 204]
    assert ids(catalog.match(ask(departure_cities=("广州", "北京")))) == [203, 302]
    assert ids(catalog.match(ask(hotel_levels=("四钻", "五钻")))) == [202, 203, 201, 301, 302, 204]
    assert ids(catalog.match(ask(features=("观鲸", "一价全含")))) == [202, 201]
    # Across groups the taps are conditions on each other, not alternatives.
    assert ids(catalog.match(ask(regions=("德法意瑞", "北欧"), days=(12,)))) == [301]


def test_a_price_band_is_a_floor_and_a_ceiling(catalog):
    """The 起价 chips are bands, so the advisor's taps become the lower edge of the lowest and
    the upper edge of the highest; a 起价 the ERP has not published is under neither."""
    assert ids(catalog.match(ask(price_min=10000, price_max=20000))) == [203, 201, 301]
    assert ids(catalog.match(ask(price_max=10000))) == [202, 204]
    assert ids(catalog.match(ask(price_min=20000))) == [302]


def test_the_ranking_is_the_reviewed_the_asked_for_length_then_the_price(catalog):
    # Reviewed first, then the cheaper 起价; the draft is last whatever it costs.
    assert ids(catalog.match(ask(text="斯里兰卡"))) == [202, 203, 201, 204]
    # A line of exactly the length asked for comes ahead of one that merely fits the span.
    assert ids(catalog.match(ask(days_min=11, days_max=11))) == [302]
    assert ids(catalog.match(ask(days=(12,), days_min=7, days_max=12))) == [301]


# -- the chips a wide request goes back as ---------------------------------------------------


def test_the_chips_are_the_groups_that_split_the_matches(catalog):
    matches = catalog.match(ask())
    groups = catalog.chips(matches)
    # One value per line: the 线路系 the document files it under, else the countries joined.
    assert groups["目的地"] == [("斯里兰卡一地", 4), ("北欧", 1), ("德法意瑞", 1)]
    assert groups["天数"] == [("7 天", 4), ("11 天", 1), ("12 天", 1)]
    assert groups["出发城市"] == [("上海", 4), ("北京", 1), ("广州", 1)]
    assert groups["酒店标准"] == [("四钻", 5), ("五钻", 1)]
    assert groups["纯玩"] == [("纯玩", 4), ("含购物店", 2)]
    assert groups["起价"] == [("1万以内", 2), ("1–1.5万", 2), ("1.5–2万", 1), ("2.5万以上", 1)]
    assert groups["出发月份"] == []  # no 团期 were read, so the months say nothing


def test_the_months_are_counted_from_the_departures_the_run_read(catalog):
    matches = catalog.match(ask(text="斯里兰卡"))
    months = {
        201: [date(2026, 10, 2), date(2026, 10, 9)],
        202: [date(2026, 11, 3)],
        203: [],
    }
    groups = catalog.chips(matches, months)
    assert groups["出发月份"] == [("2026年10月", 1), ("2026年11月", 1)]
    # The year is left off the month the advisor is working in, and kept on any other.
    assert catalog.chips(matches, months, year=2026)["出发月份"] == [("10月", 1), ("11月", 1)]


def test_the_dimensions_that_tell_a_family_apart_come_first(catalog):
    matches = catalog.match(ask(text="斯里兰卡"))
    groups = catalog.chips(matches, first=("酒店标准", "纯玩"))
    assert list(groups)[:2] == ["酒店标准", "纯玩"]


# -- one trip sold several ways ---------------------------------------------------------------


def test_a_handful_of_versions_of_one_trip_is_ambiguous(catalog):
    matches = catalog.match(ask(text="斯里兰卡", days_min=7, days_max=7))
    assert catalog.ambiguous(matches)
    assert catalog.differences(matches) == ["出发城市", "酒店标准", "纯玩", "特色"]
    # 特色 is the words that tell the versions apart, and no word another group carries.
    assert catalog.chips(matches)["特色"] == [
        ("一价全含", 1),
        ("国泰", 1),
        ("联运", 1),
        ("观鲸", 1),
    ]


def test_a_choice_of_trips_is_not_ambiguous(catalog):
    # Different countries and different lengths: the advisor is choosing a trip, not a version.
    assert not catalog.ambiguous(catalog.match(ask(days_min=11, days_max=12)))
    # One line is nobody's question.
    assert not catalog.ambiguous(catalog.match(ask(text="德法意瑞")))
