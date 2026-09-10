# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ERP's free-text 行程标签 read as the handful of attributes an advisor filters on.
A 线路 carries about fifty tags its itinerary attachment was auto-extracted into — 斯里兰卡,
上海出发, 网评5钻酒店, 纯玩无购物, 含签证, 亲子友好, and forty place names — in whatever
words the extractor found, and with the extractor's own mistakes in them. ``normalize`` maps
that list onto ``RouteFacets``: one controlled value per attribute, or ``unknown``.

The vocabulary itself is not here. ``data/tag-rules.json`` holds it, so the agency's product
staff extend the synonyms without a code change, and this module is the matching alone: a
tag counts for a rule when it holds one of the rule's substrings, the first rule a tag
matches is the one it counts for, and a tag holding one of the attribute's negative
substrings — 印度教寺庙 on a 斯里兰卡 line — counts for nothing there. ``destinations`` and
``departure_cities`` keep every value their tags name, in the order the tags carry them;
every other attribute keeps one, the earliest rule in the file that any tag matched, which
is what makes 无购物 beat 购物 on a line whose extractor listed a market as an attraction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from demo_common.storefront_fixtures import example_data_dir, load_json

DATA_DIR = example_data_dir(__file__)
RULES_FILE = "tag-rules.json"

# What an attribute reads as when no tag names it: the extractor missed it, or the itinerary
# never said. It is not a "no" — a 线路 with no 钻 tag may still be a 五钻 line.
UNKNOWN = "unknown"
# 含签证, 含机票税, 含景点首道门票 …: kept as the ERP wrote them, because the advisor reads
# them out, and capped because a line lists as many as a dozen.
MAX_INCLUSIONS = 8
_INCLUSION_PREFIXES = ("含", "全程含")


@dataclass(frozen=True)
class RouteFacets:
    """One 线路's tags as attributes. ``shopping`` is ``none``, ``some`` or ``unknown``;
    ``hotel_grade`` is 五钻, 四钻, 三钻 or ``unknown``; the rest are empty when unnamed."""

    destinations: tuple[str, ...] = ()
    shopping: str = UNKNOWN
    hotel_grade: str = UNKNOWN
    family: bool = False
    departure_cities: tuple[str, ...] = ()
    direct_flight: bool = False
    inclusions: tuple[str, ...] = ()
    budget: tuple[str, ...] = ()
    # The 线路系 the trade files a line under — 德法意瑞, 英爱, 西葡, 北欧, 伊犁 … — read off the
    # 线路 name first, and off the tags only when the name says nothing; "" when neither does.
    region: str = ""


@dataclass(frozen=True)
class _Attribute:
    """One attribute's rules in file order, and the substrings that take a tag out of it."""

    rules: tuple[tuple[str, tuple[str, ...]], ...] = ()
    negative: tuple[str, ...] = ()

    def rank(self, tag: str) -> tuple[int, str] | None:
        """The first rule this tag matches, as its file position and its value."""
        if any(word in tag for word in self.negative):
            return None
        for index, (value, words) in enumerate(self.rules):
            if any(word in tag for word in words):
                return index, value
        return None


_EMPTY = _Attribute()


def _attribute(spec: dict) -> _Attribute:
    rules = tuple(
        (str(rule["value"]), tuple(str(word) for word in rule.get("any") or ()))
        for rule in spec.get("rules") or ()
    )
    return _Attribute(rules, tuple(str(word) for word in spec.get("negative") or ()))


@lru_cache(maxsize=4)
def load_rules(data_dir: Path = DATA_DIR) -> dict[str, _Attribute]:
    """``data/tag-rules.json``, read once per process. An attribute the file does not name
    matches nothing, so a rule set can be trimmed without breaking the caller."""
    raw = load_json(data_dir, RULES_FILE)
    return {name: _attribute(spec) for name, spec in raw.items() if isinstance(spec, dict) and name}


def _hits(tags: Sequence[str], attribute: _Attribute) -> list[tuple[int, str]]:
    """Every (rule position, value) the tags name, in the order the tags carry them."""
    return [rank for tag in tags if (rank := attribute.rank(tag)) is not None]


def _one(hits: list[tuple[int, str]]) -> str | None:
    """The value of the earliest rule any tag matched: the file's order is the priority."""
    return min(hits)[1] if hits else None


def _every(hits: list[tuple[int, str]]) -> tuple[str, ...]:
    """Every value the tags name, in tag order, each one once."""
    return tuple(dict.fromkeys(value for _, value in hits))


def normalize(
    tags: Sequence[str],
    price_tags: Sequence[str] = (),
    data_dir: Path = DATA_DIR,
    *,
    name: str = "",
) -> RouteFacets:
    """One 线路's tags as ``RouteFacets``. ``price_tags`` is the ERP's own ``periodPriceTags``
    — 预算约9999—1万元 — which is a band and not a number, so it is carried through as written.
    A line whose tags name no destination the rules know keeps its first tag as one: the
    extractor writes the destination first often enough that it beats saying nothing, and the
    advisor's own text is matched against the line's raw tags as well. ``name`` is the 线路
    name, which is what the ``region`` rules read first: the editors write the walk into the
    name (德法意瑞+五渔村), and the tags of that line list every country on it."""
    rules = load_rules(data_dir)
    clean = tuple(text for tag in tags if (text := str(tag).strip()))
    hits = {name_: _hits(clean, rules.get(name_, _EMPTY)) for name_ in rules}
    destinations = _every(hits.get("destination", [])) or (clean[:1] if clean else ())
    region_rules = rules.get("region", _EMPTY)
    region = _one(_hits((name.strip(),), region_rules)) or _one(hits.get("region", [])) or ""
    return RouteFacets(
        destinations=destinations,
        shopping=_one(hits.get("shopping", [])) or UNKNOWN,
        hotel_grade=_one(hits.get("hotel_grade", [])) or UNKNOWN,
        family=bool(hits.get("family")),
        departure_cities=_every(hits.get("departure_city", [])),
        direct_flight=bool(hits.get("direct_flight")),
        inclusions=tuple(dict.fromkeys(t for t in clean if t.startswith(_INCLUSION_PREFIXES)))[
            :MAX_INCLUSIONS
        ],
        budget=tuple(text for tag in price_tags if (text := str(tag).strip())),
        region=region,
    )
