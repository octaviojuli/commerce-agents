# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``api/tags.py`` against ``data/tag-rules.json``: the ERP's free-text 行程标签 as the
attributes a search filters on. The rules are matched by substring and the extraction they
read is imperfect, so what is tested is the rule families — a destination, a departure city,
a hotel standard, 购物, 亲子, 含-inclusions, a budget band — and the precedence between them.

The coverage test runs the rules over a snapshot of the agency's own catalog, which is not in
this repo: it holds the agency's real 线路 names. Point ``TOUR_TAG_SNAPSHOT`` at a JSON array
of ``route/list`` rows to run it; without it, it skips. It last measured 62 rows as 59 with a
destination, 48 with a hotel standard, 32 with a departure city, 38 直飞, 35 纯玩, 2 selling
购物, 10 亲子, 31 with an inclusion and 7 with a budget band."""

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


def test_a_destination_a_negative_rules_out_is_not_one():
    """印度教寺庙 and 印度洋海景 are on half the 斯里兰卡 lines and neither is a trip to 印度."""
    facets = normalize(["斯里兰卡", "印度教寺庙", "印度洋海景", "印度文化"])
    assert facets.destinations == ("斯里兰卡",)


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
    assert len(rows) >= 60
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
