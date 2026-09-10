# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``api/tags.py`` against ``data/tag-rules.json``: the ERP's free-text 行程标签 as the
attributes a search filters on. The rules are matched by substring and the extraction they
read is imperfect, so what is tested is the rule families — a destination, a departure city,
a hotel standard, 购物, 亲子, 含-inclusions, a budget band — and the precedence between them.

The coverage test runs the rules over a snapshot of the agency's own catalog, which is not in
this repo: it holds the agency's real 线路 names. Point ``TOUR_TAG_SNAPSHOT`` at a JSON array
of ``route/list`` rows to run it; without it, it skips. It last measured a whole production
``route/list`` — 269 线路, 232 of them carrying the itinerary extraction at all — as 231 rows
placed at a destination the rules name, 168 with a hotel standard, 71 with a departure city,
108 直飞, 54 纯玩, 44 selling 购物, 14 亲子, 94 with an inclusion and 83 with a budget band."""

import json
import os
from pathlib import Path

import pytest

from tour.api.tags import UNKNOWN, load_rules, normalize

SNAPSHOT = os.environ.get("TOUR_TAG_SNAPSHOT", "")

# One 线路's extraction as the ERP writes it, in the fictional catalog's own vocabulary.
LINE = [
    "伊犁",
    "上海出发",
    "东航直飞",
    "赛里木湖",
    "那拉提草原",
    "网评5钻酒店",
    "纯玩无购物",
    "含签证",
    "含景点首道门票",
    "亲子友好",
    "中文导游",
]


def test_the_rules_file_names_every_attribute_the_facets_carry():
    rules = load_rules()
    assert set(rules) == {
        "destination",
        "departure_city",
        "hotel_grade",
        "shopping",
        "family",
        "direct_flight",
    }
    for name, attribute in rules.items():
        assert attribute.rules, name
        assert all(value and words for value, words in attribute.rules), name


def test_one_line_normalises_into_every_facet():
    facets = normalize(LINE, ["预算约5800—6600元"])
    assert facets.destinations == ("伊犁",)
    assert facets.departure_cities == ("上海",)
    assert facets.direct_flight is True
    assert facets.hotel_grade == "五钻"
    assert facets.shopping == "none"
    assert facets.family is True
    assert facets.inclusions == ("含签证", "含景点首道门票")
    assert facets.budget == ("预算约5800—6600元",)


def test_a_line_with_no_tags_at_all_claims_nothing():
    facets = normalize([])
    assert facets == normalize(["", "  "])
    assert (facets.destinations, facets.shopping, facets.hotel_grade) == ((), UNKNOWN, UNKNOWN)
    assert (facets.family, facets.direct_flight) == (False, False)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["斯里兰卡7天5晚"], ("斯里兰卡",)),
        (["科伦坡市区游", "康提湖"], ("斯里兰卡",)),
        (["锡兰红茶品鉴"], ("斯里兰卡",)),
        (["斯里兰卡", "马尔代夫快艇上岛"], ("斯里兰卡", "马尔代夫")),
        (["马尔代夫自由行", "斯里兰卡"], ("马尔代夫", "斯里兰卡")),
        (["格鲁吉亚", "亚美尼亚"], ("格鲁吉亚", "亚美尼亚")),
        (["泰姬陵", "新德里"], ("印度",)),
    ],
)
def test_destinations_are_the_values_the_tags_name_in_the_order_they_name_them(tags, expected):
    assert normalize(tags).destinations == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["埃菲尔铁塔", "塞纳河游船"], ("法国",)),
        (["罗马斗兽场", "叹息桥"], ("意大利",)),
        (["少女峰", "卡佩尔桥"], ("瑞士",)),
        (["新天鹅堡外观"], ("德国",)),
        (["圣家堂官导", "巴塞罗那兰布拉斯大街"], ("西班牙",)),
        (["贝伦塔", "莱罗书店"], ("葡萄牙",)),
        (["布鲁塞尔大广场"], ("比利时",)),
        (["卢森堡大公国"], ("卢森堡",)),
        (["瓦杜茨小火车"], ("列支敦士登",)),
        (["雅典卫城", "圣托里尼岛"], ("希腊",)),
        (["大英博物馆", "爱丁堡"], ("英国",)),
        (["马拉喀什不眠广场", "卡萨布兰卡"], ("摩洛哥",)),
        (["伊斯坦布尔", "棉花堡"], ("土耳其",)),
        (["富士山"], ("日本",)),
        (["悉尼歌剧院入内"], ("澳大利亚",)),
        (["地中海邮轮"], ("邮轮",)),
        # A European multi-country line names every country its attractions are in, in tag
        # order, which is what an advisor searching 法国 or 瑞士 has to find it by.
        (
            ["埃菲尔铁塔", "罗马", "琉森", "新天鹅堡", "维也纳"],
            ("法国", "意大利", "瑞士", "德国", "奥地利"),
        ),
    ],
)
def test_the_agencys_own_catalog_places_a_line_by_its_attractions(tags, expected):
    """The extraction writes down attractions, not countries, so the attractions are the
    evidence: no 线路 name in this catalog says 法国 and every 法国 line says 埃菲尔铁塔."""
    assert normalize(tags).destinations == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # A place name that sits inside another place's name goes to the rule listed first.
        ("马拉喀什", ("摩洛哥",)),
        ("喀什古城", ("南疆",)),
        ("布加勒斯特", ("东欧",)),
        ("加勒古堡", ("斯里兰卡",)),
        ("罗马尼亚", ("东欧",)),
        ("古罗马废墟", ("意大利",)),
        ("都柏林尖塔", ("爱尔兰",)),
        ("柏林墙", ("德国",)),
        ("菲斯特雪山", ("瑞士",)),
        ("菲斯老城", ("摩洛哥",)),
        ("水城威尼斯", ("意大利",)),
    ],
)
def test_a_place_name_inside_another_place_name_goes_to_the_more_specific_rule(tag, expected):
    assert normalize([tag]).destinations == expected


def test_a_destination_a_negative_rules_out_is_not_one():
    """印度教寺庙 and 印度洋海景 are on half the 斯里兰卡 lines and neither is a trip to 印度."""
    facets = normalize(["斯里兰卡", "印度教寺庙", "印度洋海景", "印度文化"])
    assert facets.destinations == ("斯里兰卡",)


@pytest.mark.parametrize(
    "tag",
    [
        # A meal on a European line, on 65 线路 of the production catalog.
        "土耳其烤肉卷",
        "升级土耳其烤肉卷",
        # Two squares in Rome, which the 意大利 rule already places by 罗马.
        "西班牙广场",
        "西班牙阶梯",
        # The Dutch fort in 加勒, which is a 斯里兰卡 line.
        "荷兰殖民古城",
        "荷兰遗风",
    ],
)
def test_a_tag_naming_a_dish_or_a_square_is_not_a_destination(tag):
    assert normalize(["斯里兰卡", tag]).destinations == ("斯里兰卡",)


def test_a_line_the_rules_place_nowhere_keeps_its_first_tag_as_its_destination():
    assert normalize(["国旅环球", "电子发票"]).destinations == ("国旅环球",)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("上海出发", ("上海",)),
        ("昆明直飞", ("昆明",)),
        ("乌鲁木齐出发", ("乌鲁木齐",)),
        # An airline is not a city, however the extraction words it.
        ("东航直飞", ()),
        ("国航直飞", ()),
        ("全国联运", ()),
    ],
)
def test_a_departure_city_is_a_city_and_not_an_airline(tag, expected):
    assert normalize([tag]).departure_cities == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("网评5钻酒店", "五钻"),
        ("五钻酒店", "五钻"),
        ("国际五星", "五钻"),
        ("海边五钻连住", "五钻"),
        # A mixed itinerary is quoted at the higher standard, as the agency sells it.
        ("网评4钻+5钻酒店", "五钻"),
        ("四钻酒店", "四钻"),
        ("三钻酒店", "三钻"),
        ("特色民宿两晚", UNKNOWN),
    ],
)
def test_a_hotel_standard_is_one_value_however_the_extraction_spells_it(tag, expected):
    assert normalize([tag]).hotel_grade == expected


def test_the_best_standard_any_tag_names_is_the_lines_own():
    assert normalize(["四钻酒店", "海边五钻酒店"]).hotel_grade == "五钻"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("纯玩无购物", "none"),
        ("无购物", "none"),
        ("全程无购物", "none"),
        ("无强制购物", "none"),
        ("无购物承诺", "none"),
        ("零购物", "none"),
        ("市集购物", "some"),
        ("超市购物", "some"),
        ("免税店购物", "some"),
        ("宝石店参观", UNKNOWN),
    ],
)
def test_shopping_reads_the_tag_the_extraction_wrote(tag, expected):
    assert normalize([tag]).shopping == expected


def test_no_shopping_beats_shopping_because_the_extraction_lists_markets_loosely():
    """The extractor writes down a market it walks past as an attraction, so a line claiming
    无购物 and naming a market is 纯玩; the rules' own order is what decides it."""
    assert normalize(["纯玩无购物", "市集购物"]).shopping == "none"
    assert normalize(["市集购物", "纯玩无购物"]).shopping == "none"
    assert normalize(["免税店购物", "无购物承诺"]).shopping == "none"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("亲子友好", True),
        ("家庭亲子游", True),
        ("亲子研学", True),
        ("儿童友好", True),
        ("12岁以下儿童", True),
        # The child fare and the extra bed are on every line and say nothing about the trip.
        ("儿童价格", False),
        ("儿童加床", False),
        ("儿童政策", False),
    ],
)
def test_family_is_what_the_line_claims_and_not_its_child_pricing(tag, expected):
    assert normalize([tag]).family is expected


def test_inclusions_are_kept_as_the_erp_wrote_them_deduplicated_and_capped():
    tags = [
        "含签证",
        "含签证",
        "全程含餐",
        "含机票税",
        "景点门票包含",
        *[f"含项目{i}" for i in range(9)],
    ]
    inclusions = normalize(tags).inclusions
    assert inclusions[:3] == ("含签证", "全程含餐", "含机票税")
    # 景点门票包含 does not start with 含, so it is a tag and not an inclusion.
    assert "景点门票包含" not in inclusions
    assert len(inclusions) == 8


@pytest.mark.skipif(not SNAPSHOT, reason="TOUR_TAG_SNAPSHOT names no route/list snapshot")
def test_the_rules_cover_the_agencys_own_catalog():
    """Coverage over every row of a live ``route/list`` snapshot. The extraction is not on
    every 线路 — a route with no itinerary attachment carries none of it — so these are floors
    the rules have to clear, not totals."""
    rows = json.loads(Path(SNAPSHOT).read_text())
    facets = [
        normalize(row.get("itineraryTags") or (), row.get("periodPriceTags") or ()) for row in rows
    ]
    values = {value for value, _ in load_rules()["destination"].rules}
    extracted = [f for row, f in zip(rows, facets, strict=True) if row.get("itineraryTags")]
    placed = [f for f in extracted if any(name in values for name in f.destinations)]
    assert len(rows) >= 60
    # A 线路 the ERP extracted an itinerary for is placed at a destination the rules name; a
    # line with no attachment carries no extraction and cannot be.
    assert len(placed) >= 0.9 * len(extracted), (len(placed), len(extracted))
    # No line is in 新疆 and in Europe at once, which is what a place name matched inside
    # another one looks like (喀什 inside 马拉喀什, 加勒 inside 布加勒斯特).
    domestic, abroad = {"伊犁", "喀纳斯", "南疆", "青海"}, {"法国", "意大利", "西班牙", "摩洛哥"}
    mixed = [
        f.destinations
        for f in facets
        if domestic & set(f.destinations) and abroad & set(f.destinations)
    ]
    assert not mixed, mixed
    assert sum(1 for f in facets if f.destinations) >= 50
    assert sum(1 for f in facets if f.shopping == "none") >= 20
    # Almost nothing admits to selling 购物 in a tag, which is why 未知 is not a no.
    assert sum(1 for f in facets if f.shopping == "some") >= 2
    assert sum(1 for f in facets if f.hotel_grade != UNKNOWN) >= 20
    assert sum(1 for f in facets if f.departure_cities) >= 20
    assert sum(1 for f in facets if f.direct_flight) >= 20
    assert sum(1 for f in facets if f.family) >= 5
    assert sum(1 for f in facets if f.inclusions) >= 20
    assert sum(1 for f in facets if f.budget) >= 5
    # Every value a rule produced is one the rules name; nothing is invented from a tag.
    named = {value for attribute in load_rules().values() for value, _ in attribute.rules}
    placed = [f.destinations[0] for f in facets if f.destinations]
    assert sum(1 for value in placed if value in named) >= 50
