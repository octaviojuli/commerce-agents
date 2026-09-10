# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A 包团, a 会销 or a 定制 line is not on general sale: the name says so until the ERP's
``saleType`` does, and ``data/private-lines.json`` holds the words."""

import pytest

from tour.api.mock_erp import MockErpClient, _route_record
from tour.api.private_lines import PrivateLineRules, load_private_line_rules


def line(name: str, sale_type: str = ""):
    """A fixture 线路 under another name, and under the ERP's ``saleType`` once it has one."""
    row = {**MockErpClient()._routes[1021], "routeName": name, "saleType": sale_type}
    return _route_record(row)


def test_the_file_names_the_words_the_agencys_editors_use():
    rules = load_private_line_rules()
    assert {"包团", "会销", "定制"} <= set(rules.name_keywords)
    assert "" in rules.public_sale_types


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("王鑫包团-定制行程", "包团"),
        ("周莉馨-会销B-单宫单船菲斯特", "会销"),
        ("昆明东航8天6晚（定制团）", "定制"),
        ("伊犁北疆环线 8 日纯玩小团", ""),
        ("加班-HO4-【畅行经典】卢德比法意瑞", ""),
    ],
)
def test_the_name_says_what_the_line_is(name, kind):
    rules = load_private_line_rules()
    assert rules.line_type(line(name)) == kind
    assert rules.is_private(line(name)) is bool(kind)


def test_the_erps_own_field_beats_the_name_once_it_arrives():
    rules = load_private_line_rules()
    # A line the ERP calls public is public whatever its name says.
    assert rules.line_type(line("王鑫包团-定制行程", sale_type="公开")) == ""
    # And one the ERP calls 包团 is, whatever its name says.
    assert rules.line_type(line("伊犁北疆环线 8 日纯玩小团", sale_type="包团")) == "包团"


def test_a_file_with_no_words_makes_every_line_public():
    rules = PrivateLineRules(name_keywords=(), public_sale_types=frozenset({""}))
    assert not rules.is_private(line("王鑫包团-定制行程"))
    assert rules.is_private(line("王鑫包团-定制行程", sale_type="包团"))
