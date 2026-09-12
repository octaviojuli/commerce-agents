# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The 线路 catalog the advisor searches: the agency's 线路文档 (``route_docs.py``), reviewed
and draft alike, read into the few facts a search filters and groups on.

What a 线路 *is* — the countries, the length, the city it leaves from, the hotel standard, the
购物店, the day-by-day programme — is the document's, because the document is the 行程附件 read
into fields; a reviewed one has the agency's product staff behind it and is ranked ahead of the
drafts, and a card says which of the two it is. The ERP is asked only for the dynamic
half: which 团期 run, their status, their seats and the 同业价. A 线路 with no document is not
searched at all; nothing else in the catalog is trustworthy enough to put in front of a
customer.

``Catalog.match`` is the whole filter, ``Catalog.chips`` the groups a request too wide to
shortlist is put back to the advisor as, and ``Catalog.ambiguous`` says when a handful of
matches are the same trip sold four ways and the advisor has to say which."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from .erp_client import RouteRecord
from .route_doc import RouteDoc
from .route_docs import RouteDocStore, hotel_grade, is_reviewed, shopping_stops

# How many of a document's own place names a card carries: an advisor reads the first few of
# them out and the rest are in the 逐日行程.
MAX_PLACES = 10
MAX_HIGHLIGHTS = 3
# The hotel standard the advisor may state, in either spelling, as the one value a document's
# 酒店标准 normalises into.
_HOTEL_GRADES = {"三": "三钻", "3": "三钻", "四": "四钻", "4": "四钻", "五": "五钻", "5": "五钻"}
# The words that tell one 线路 of a family from its siblings: a 斯里兰卡 7 天 catalog holds
# 一价全含, 升网评5钻, 含观鲸 and 国泰联运 versions of one trip, and these are what the advisor
# asks the customer to choose between. A word another group already carries (纯玩) or one that
# says nothing on its own (升级, 连住) is not one of them: it would be a chip nobody can act on.
FEATURE_WORDS = (
    "一价全含",
    "网评5钻",
    "网评五钻",
    "观鲸",
    "联运",
    "直飞",
    "国泰",
    "亲子",
    "摄影",
    "温泉",
)
# The 起价 bands an advisor quotes in; a 线路 whose 起价 the ERP has not published is its own
# band, because 0 is 未发布 and not free.
PRICE_BANDS = ("1万以内", "1–1.5万", "1.5–2万", "2–2.5万", "2.5万以上", "起价未知")
# The dimensions a card's chips are grouped by, the filter each one goes back through, and —
# because the dict is ordered — the order an advisor asks them in: the widest question first,
# and each of them only while the customer has not answered it already.
FILTERS = {
    "目的地": "region/destination",
    "出发月份": "depart_from/depart_to",
    "天数": "days_min/days_max",
    "出发城市": "departure_city",
    "酒店标准": "hotel_level",
    "纯玩": "no_shopping",
    "特色": "feature",
    "起价": "price_max",
}
# What a group with nothing to say writes instead of a value. It is never a chip: there is no
# filter that asks for 未标注.
UNSTATED = "未标注"
NO_SHOPPING = "纯玩"
SOME_SHOPPING = "含购物店"
# The tail of a long group, as one chip that says how much was folded into it. Nobody taps it:
# 其他 is not a filter, it is the count the primary row would otherwise have hidden.
OTHER = "其他"
# One question is at most two rows: the dimension it asks about, and the next one down as a
# hint of what comes after it.
PRIMARY_VALUES = 8
SECONDARY_VALUES = 5
# How many months a card offers when the advisor's own dates hold no 团期 at all.
NEAREST_MONTHS = 3
# The 天数 bands a longer catalog is asked in, with the span each one sends back: twenty
# lengths are not a question, and four are.
DAY_BANDS = (
    ("7 天以内", None, 7),
    ("8–10 天", 8, 10),
    ("11–13 天", 11, 13),
    ("14 天以上", 14, None),
)
# More distinct lengths than this and 天数 is asked as those bands rather than as exact days.
BAND_DAYS_ABOVE = 6
# A city the ERP wrote where it had none: 中国 is not a place a group leaves from, so the
# line's own 参考航班 is asked instead and a line with neither says 未标注.
VAGUE_CITIES = frozenset({"中国", "国内", "全国", "不限", "待定", "多地"})
# What a destination the advisor wrote as several places is split on: a chip sends the
# countries joined (法国·意大利·瑞士) and the line must carry every one of them.
_SEPARATORS = "·、,，/ 　+&"
# The European 线路系, named as ``data/tag-rules.json``'s own region vocabulary: what a
# customer saying 欧洲 means, beside the departments that sell it.
EUROPE_REGIONS = frozenset(
    {
        "西欧多国",
        "德法意瑞",
        "法意瑞",
        "德奥捷",
        "英爱",
        "西葡",
        "北欧",
        "东欧巴尔干",
        "希腊",
        "土耳其",
        "意大利一地",
        "法国一地",
        "瑞士一地",
        "德国一地",
        "俄罗斯",
    }
)


@dataclass(frozen=True)
class Scope:
    """One of the wide words a customer's request arrives as — 欧洲, 东南亚, 美洲 — as the
    lines it covers: the departments that sell it, the 线路系 it holds, and the countries.
    A line inside any one of the three is inside the scope."""

    departments: tuple[str, ...] = ()
    regions: frozenset[str] = frozenset()
    countries: frozenset[str] = frozenset()

    def holds(self, facts: RouteFacts) -> bool:
        return (
            any(facts.department.startswith(prefix) for prefix in self.departments)
            or facts.region in self.regions
            or bool(self.countries.intersection(facts.countries))
        )


# The scopes themselves. A scope is not a substring: no document writes 欧洲 into a field, so
# a 欧洲 matched as text could only land on a day's prose or a cover — which is how a 南美
# 邮轮 and a 美国 6 天 arrived on a 欧洲 shortlist. 日本 names no department, because the
# agency's 日本事业部 also sells 澳新; 澳新部 sells its own and is named.
SCOPES: dict[str, Scope] = {
    "欧洲": Scope(("欧洲部",), EUROPE_REGIONS),
    "欧洲多国": Scope(("欧洲部",), EUROPE_REGIONS),
    "南亚": Scope(
        ("斯里兰卡",),
        frozenset({"南亚", "斯里兰卡", "马尔代夫"}),
        frozenset({"斯里兰卡", "马尔代夫", "印度", "尼泊尔"}),
    ),
    "东南亚": Scope(
        (),
        frozenset({"东南亚"}),
        frozenset(
            {"泰国", "新加坡", "马来西亚", "越南", "柬埔寨", "印度尼西亚", "菲律宾", "缅甸", "老挝"}
        ),
    ),
    "日本": Scope((), frozenset({"日本"}), frozenset({"日本"})),
    "澳新": Scope(("澳新",), frozenset({"澳新"}), frozenset({"澳大利亚", "新西兰"})),
    "美洲": Scope(
        (),
        frozenset({"美洲"}),
        frozenset({"美国", "加拿大", "南美", "巴西", "阿根廷", "智利", "秘鲁", "墨西哥"}),
    ),
}


@dataclass(frozen=True)
class Request:
    """What the advisor asked for. Every field but the dates is answered off the documents,
    and the several-valued ones are what a chip bar sends when the advisor taps more than one:
    inside a field the values are alternatives (德法意瑞 or 法意瑞), and the fields are
    conditions on each other (that 线路系, and 12 days, and leaving from 上海)."""

    depart_from: date
    depart_to: date
    destinations: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    # Exact lengths the advisor named or tapped, and the span they gave in words.
    days: tuple[int, ...] = ()
    days_min: int | None = None
    days_max: int | None = None
    departure_cities: tuple[str, ...] = ()
    hotel_levels: tuple[str, ...] = ()
    # The words that tell one version of a trip from another (观鲸, 一价全含).
    features: tuple[str, ...] = ()
    no_shopping: bool = False
    family: bool = False
    # The floor and the ceiling on the 起价, from the bands the advisor tapped, and how many
    # travel — the last decides whether a 团期 is 满员 for this party. The months the advisor
    # chose are the ERP's to answer and are not here: ``TourBackend`` reads them as the window.
    price_min: int | None = None
    price_max: int | None = None
    party: int = 0

    @property
    def named(self) -> tuple[str, ...]:
        """The destinations as written, which is where a 线路 named in full arrives."""
        return tuple(value.strip() for value in self.destinations if value.strip())


@dataclass(frozen=True)
class RouteFacts:
    """One 线路 as the search reads it: the document's own fields, with the two the ERP's
    catalog row carries beside them (the 起价 and the cover photo)."""

    route_id: int
    route_code: str
    name: str
    # What the ERP's own catalog row calls the line, where the process has read one: an
    # advisor pastes whichever of the two names they know.
    catalog_name: str
    department: str
    countries: tuple[str, ...]
    region: str
    days: int
    nights: int | None
    depart_city: str
    airline: str
    hotel_standard: str
    meal_standard: str
    hotel_grade: str
    shopping_stops: int
    optional_count: int
    ticket_count: int
    gift_count: int
    places: tuple[str, ...]
    sight_names: tuple[str, ...]
    highlights: tuple[str, ...]
    feature_words: tuple[str, ...]
    reviewed: bool
    reviewed_by: str
    attachment_name: str
    from_price: float
    image_url: str | None
    doc: RouteDoc

    @property
    def destination(self) -> str:
        """The one value a 目的地 chip carries: the 线路系 the document files the line under
        (德法意瑞, 北欧), else its countries joined. A chip is a word the advisor narrows with,
        and a six-country list is not one."""
        return self.region or "·".join(self.countries) or UNSTATED

    @property
    def no_shopping(self) -> bool:
        return self.shopping_stops == 0


def wanted_grade(level: str) -> str | None:
    """The hotel standard the advisor stated as the one value a document normalises into:
    五钻, 5钻, 五星 and 5星 are all 五钻, because the attachments write whichever they like."""
    return _HOTEL_GRADES.get(level.strip()[:1]) if level and level.strip() else None


def price_band(price: float) -> str:
    """The 起价 as the band an advisor quotes in; a 0 is a 起价 the ERP has not published."""
    if price <= 0:
        return PRICE_BANDS[5]
    edges = (10000, 15000, 20000, 25000)
    return PRICE_BANDS[next((i for i, edge in enumerate(edges) if price <= edge), 4)]


def day_band(days: int) -> str:
    """The length as the band an advisor asks in, where the catalog holds too many lengths to
    put them all on a card."""
    return next(
        label
        for label, low, high in DAY_BANDS
        if (low is None or days >= low) and (high is None or days <= high)
    )


def band_span(label: str) -> tuple[int | None, int | None]:
    """The 天数 band a chip sent, as the ``days_min``/``days_max`` it goes back through."""
    return next(((low, high) for name, low, high in DAY_BANDS if name == label), (None, None))


def month_label(day: date, year: int | None = None) -> str:
    """The month a chip says: bare where it is the year the advisor is working in, and with
    the year where it is not, because 1月 next year is a different question."""
    return (
        f"{day.month}月" if year is not None and day.year == year else f"{day.year}年{day.month}月"
    )


def short_text(text: str, limit: int) -> str:
    """A cover field as a badge: the first clause of it, capped."""
    for separator in ("，", "。", "；", " ", "/"):
        text = text.split(separator)[0]
    return text[:limit]


def _feature_words(doc: RouteDoc) -> tuple[str, ...]:
    text = doc.name + " " + " ".join(s.name for day in doc.days for s in day.sights)
    return tuple(word for word in FEATURE_WORDS if word in text)


def _depart_city(doc: RouteDoc) -> str:
    """The city the group leaves from, as a city and not as a country: an ERP row that wrote
    中国 into the field said nothing, so the first day's 参考航班 is read instead, and a line
    with neither leaves it empty rather than filtering and grouping on 中国."""
    stated = doc.summary.depart_city.strip()
    if stated and stated not in VAGUE_CITIES:
        return stated
    for day in doc.days[:1]:
        for flight in day.flights:
            city = flight.from_place.strip()
            if city and city not in VAGUE_CITIES:
                return city
    return ""


def _places(doc: RouteDoc) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for day in doc.days:
        for place in day.places:
            if place:
                seen.setdefault(place, None)
    return tuple(seen)[:MAX_PLACES]


def facts_of(doc: RouteDoc, record: RouteRecord | None = None) -> RouteFacts:
    """One document as the search reads it. ``record`` is the ERP's catalog row for the same
    线路 where the process has read one: the 起价, the cover photo and the 行程附件's own file
    name are the ERP's, and every other field here is the document's."""
    sights = [sight for day in doc.days for sight in day.sights]
    return RouteFacts(
        route_id=doc.route_id,
        route_code=doc.route_code,
        name=doc.name,
        catalog_name=record.route_name if record is not None else "",
        department=doc.department,
        countries=tuple(doc.summary.countries),
        region=doc.summary.region,
        days=doc.summary.days,
        nights=doc.summary.nights,
        depart_city=_depart_city(doc),
        airline=doc.cover.airline,
        hotel_standard=doc.cover.hotel_standard,
        meal_standard=doc.cover.meal_standard,
        hotel_grade=hotel_grade(doc),
        shopping_stops=shopping_stops(doc),
        optional_count=len(doc.optional),
        ticket_count=sum(1 for sight in sights if sight.ticket_included),
        gift_count=sum(1 for sight in sights if sight.kind == "赠送"),
        places=_places(doc),
        sight_names=tuple(dict.fromkeys(sight.name for sight in sights if sight.name)),
        highlights=tuple(doc.cover.highlights[:MAX_HIGHLIGHTS]),
        feature_words=_feature_words(doc),
        reviewed=is_reviewed(doc),
        reviewed_by=doc.quality.reviewed_by,
        attachment_name=(record.attachment_name if record is not None else "")
        or doc.source.attachment_name,
        from_price=max(record.from_price, 0.0) if record is not None else 0.0,
        image_url=record.image_url if record is not None else None,
        doc=doc,
    )


def _parts(text: str) -> list[str]:
    """The destination as the advisor or a chip wrote it: 法国·意大利·瑞士 is three places and
    the line has to carry all of them, 德法意瑞 is one word and is matched as written."""
    parts = [text]
    for separator in _SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip() for part in parts if part.strip()]


def _filed_under(facts: RouteFacts, part: str) -> bool:
    """Whether one word of a destination is what this line is filed as: a scope it is inside
    (欧洲, 东南亚 …), else its countries, its 线路系, either of its names or the department
    that sells it.

    The days' place names, the sights and the cover are deliberately not among them. A
    destination is what the line *is*, and a word that merely appears somewhere in twenty days
    of prose is not that: a 南美 邮轮 whose cover compares itself to 欧洲 is not a 欧洲 line,
    and it took a 欧洲 shortlist to find that out. A place inside a country is asked for as a
    ``feature`` instead, which is the filter that reads the sights."""
    scope = SCOPES.get(part)
    if scope is not None:
        return scope.holds(facts)
    fields = (*facts.countries, facts.region, facts.name, facts.catalog_name, facts.department)
    return any(part in field for field in fields if field)


def _mentions(facts: RouteFacts, text: str) -> bool:
    """Whether the destination the advisor stated is what the line is filed as; a destination
    written as several places (法国·意大利) has to carry every one of them."""
    return all(_filed_under(facts, part) for part in _parts(text))


def _has_feature(facts: RouteFacts, word: str) -> bool:
    """A feature the advisor tapped or typed: one of the words that tell the versions of a
    trip apart, else anything the line's name, its sights or the places its days pass through
    carry — which is where the catalog writes what tells one version from another."""
    wanted = word.strip()
    fields = (facts.name, facts.catalog_name, *facts.sight_names, *facts.places)
    if not wanted:
        return False
    return wanted in facts.feature_words or any(wanted in field for field in fields if field)


@dataclass(frozen=True)
class Stated:
    """What the customer has already said, which is the half of a narrowing question nobody
    asks twice: ``dimensions`` are the groups no chip is offered on, and ``labels`` are the
    same facts in the words a card shows above the question (欧洲 · 国庆 10/01–10/07 · 4 人)."""

    dimensions: frozenset[str] = frozenset()
    labels: tuple[str, ...] = ()


def _days_label(request: Request) -> str:
    """The length the advisor stated, however they stated it: exact lengths as they were
    tapped, a span as its two edges, and a one-sided span as the edge it has."""
    if request.days:
        return "、".join(f"{days} 天" for days in sorted(request.days))
    low, high = request.days_min, request.days_max
    if low is not None and high is not None:
        return f"{low} 天" if low == high else f"{low}–{high} 天"
    return f"{low} 天以上" if low is not None else f"{high} 天以内"


def _price_label(request: Request) -> str:
    low, high = request.price_min, request.price_max
    if low and high:
        return f"{low}–{high} 元"
    return f"{low} 元以上" if low else f"{high} 元以内"


def stated_of(request: Request, dates: str = "") -> Stated:
    """The request as the facts it fixed. ``dates`` is the window in the advisor's own words
    (国庆 10/01–10/07, 10月、11月), which the backend holds and the catalog does not: a
    ``Request`` carries dates whether or not anybody stated any."""
    locked: list[str] = []
    labels: list[str] = []

    def fix(dimension: str, label: str) -> None:
        locked.append(dimension)
        labels.append(label)

    # A 线路系 the advisor tapped settles 目的地; a wider word (欧洲, 斯里兰卡) is said back
    # but leaves the 线路系 inside it open to ask — and where it holds only one, the ≥2 rule
    # in ``question_of`` skips the dimension anyway.
    if request.regions:
        fix("目的地", "、".join(dict.fromkeys(request.regions)))
    elif request.named:
        labels.append("、".join(dict.fromkeys(request.named)))
    if dates:
        fix("出发月份", dates)
    if request.days or request.days_min is not None or request.days_max is not None:
        fix("天数", _days_label(request))
    if request.departure_cities:
        fix("出发城市", "、".join(request.departure_cities) + "出发")
    if request.hotel_levels:
        fix("酒店标准", "、".join(wanted_grade(level) or level for level in request.hotel_levels))
    if request.no_shopping:
        fix("纯玩", NO_SHOPPING)
    if request.features:
        fix("特色", "、".join(request.features))
    if request.price_min or request.price_max:
        fix("起价", _price_label(request))
    # The party is a fact the card says back and no dimension the catalog groups on.
    if request.party:
        labels.append(f"{request.party} 人")
    return Stated(frozenset(locked), tuple(labels))


@dataclass(frozen=True)
class Question:
    """The one question a search too wide to shortlist goes back to the advisor as.

    ``dimension`` is what it asks about — the first dimension the customer has not already
    answered and that actually splits the matches — and ``groups`` is that dimension's values
    as the chips, with the next dimension down as a second row, and nothing else: a bar of
    eight groups and seventy chips asks everything at once, which is to ask nothing.
    ``counts`` is every dimension that splits the set, for the model to read rather than the
    advisor to tap."""

    dimension: str
    groups: dict[str, list[tuple[str, int]]]
    counts: dict[str, list[tuple[str, int]]]


def _fits_region(facts: RouteFacts, region: str) -> bool:
    """The 线路系 the advisor narrowed to, either way round: 法意瑞 finds a 德法意瑞 line and
    德法意瑞 a 法意瑞 one, because the trade says both for the same walk."""
    wanted = region.strip()
    return bool(facts.region) and (wanted in facts.region or facts.region in wanted)


class Catalog:
    """The documents as one searchable catalog. ``records`` is what the ERP's own
    catalog says about the same 线路 — the 起价 and the cover photo — and a 线路 the process
    has read no row for is still searched, priced at 起价未知."""

    def __init__(self, store: RouteDocStore, records: Mapping[int, RouteRecord]) -> None:
        self._facts = {
            doc.route_id: facts_of(doc, records.get(doc.route_id)) for doc in store.docs()
        }

    def __len__(self) -> int:
        return len(self._facts)

    def all(self) -> list[RouteFacts]:
        return list(self._facts.values())

    def get(self, route_id: int) -> RouteFacts | None:
        return self._facts.get(route_id)

    def ids(self) -> list[int]:
        return list(self._facts)

    def match(self, request: Request) -> list[RouteFacts]:
        """Every line meeting the request, best first: the reviewed documents ahead of the
        drafts, a line of exactly the length asked for ahead of the rest, then the cheaper
        起价, then the name — so the order is the catalog's own and not a read's."""
        found = [facts for facts in self._facts.values() if self._fits(facts, request)]
        if not found and request.destinations:
            # A word no line is filed under (白哈巴, 卢浮宫) is a place inside a line: read
            # the places and the sights for it, but only once the filed names gave nothing,
            # so a wide word (欧洲) never reaches a line's prose.
            found = [
                facts for facts in self._facts.values() if self._fits(facts, request, inside=True)
            ]
        return sorted(found, key=lambda facts: self._rank(facts, request))

    def _fits(self, facts: RouteFacts, request: Request, inside: bool = False) -> bool:
        """Every condition the request states, each met by any one of the values it lists.
        ``inside`` lets a destination match the places and sights a line holds."""
        if request.destinations and not any(
            _mentions(facts, value)
            or (inside and all(_has_feature(facts, part) for part in _parts(value)))
            for value in request.destinations
        ):
            return False
        if request.regions and not any(_fits_region(facts, value) for value in request.regions):
            return False
        if request.features and not any(_has_feature(facts, word) for word in request.features):
            return False
        if request.days and facts.days not in request.days:
            return False
        low = facts.days if request.days_min is None else request.days_min
        high = facts.days if request.days_max is None else request.days_max
        if not low <= facts.days <= high:
            return False
        if request.departure_cities and not any(
            city.strip() in facts.depart_city for city in request.departure_cities
        ):
            return False
        if request.no_shopping and not facts.no_shopping:
            return False
        if request.hotel_levels and facts.hotel_grade not in {
            wanted_grade(level) for level in request.hotel_levels
        }:
            return False
        # A 起价 the ERP has not published is 0, which is neither under a ceiling nor over a
        # floor: the line stays, and the card says 起价未知.
        if facts.from_price <= 0:
            return True
        if request.price_max and facts.from_price > request.price_max:
            return False
        return not (request.price_min and facts.from_price < request.price_min)

    def _rank(self, facts: RouteFacts, request: Request) -> tuple:
        asked = set(request.days) | (
            {request.days_min}
            if request.days_min is not None and request.days_min == request.days_max
            else set()
        )
        return (
            not facts.reviewed,
            not (bool(asked) and facts.days in asked),
            facts.from_price <= 0,
            facts.from_price,
            facts.name,
        )

    # -- the groups a request too wide to shortlist goes back to the advisor as ------------

    def chips(
        self,
        matches: Sequence[RouteFacts],
        months: Mapping[int, Sequence[date]] | None = None,
        first: Sequence[str] = (),
        year: int | None = None,
    ) -> dict[str, list[tuple[str, int]]]:
        """The matches grouped over every dimension that splits them, each value one the
        advisor taps and the model sends back as a filter. ``months`` is the 团期 dates read
        for each line, where the run read any; with none the 出发月份 group is empty.
        ``first`` names the dimensions that go at the front — the ones that tell an ambiguous
        handful apart."""
        groups = {
            "目的地": self._destinations(matches),
            "天数": _counts(f"{facts.days} 天" for facts in matches),
            "出发城市": _counts(facts.depart_city or UNSTATED for facts in matches),
            "出发月份": self._months(matches, months, year),
            "酒店标准": _counts(facts.hotel_grade or UNSTATED for facts in matches),
            "纯玩": _counts(
                NO_SHOPPING if facts.no_shopping else SOME_SHOPPING for facts in matches
            ),
            "特色": _counts(word for facts in matches for word in facts.feature_words),
            "起价": self._prices(matches),
        }
        groups["天数"].sort(key=lambda item: int(item[0].split(" ")[0]))
        ordered = [*first, *(label for label in groups if label not in first)]
        return {label: groups[label] for label in ordered if label in groups}

    def _destinations(self, matches: Sequence[RouteFacts]) -> list[tuple[str, int]]:
        """One value per line: the 线路系 it is filed under, else its countries joined."""
        return _counts(facts.destination for facts in matches)

    def _months(
        self,
        matches: Sequence[RouteFacts],
        months: Mapping[int, Sequence[date]] | None,
        year: int | None = None,
    ) -> list[tuple[str, int]]:
        """How many lines depart in each month the run read a 团期 in, in date order; a line
        departing twice in one month counts once, because the chip narrows lines and not
        dates."""
        if not months:
            return []
        counted: dict[tuple[int, int], int] = {}
        for facts in matches:
            for stamp in {(day.year, day.month) for day in months.get(facts.route_id, ())}:
                counted[stamp] = counted.get(stamp, 0) + 1
        return [
            (month_label(date(year_of, month, 1), year), count)
            for (year_of, month), count in sorted(counted.items())
        ]

    def _prices(self, matches: Sequence[RouteFacts]) -> list[tuple[str, int]]:
        counted = _counts(price_band(facts.from_price) for facts in matches)
        return sorted(counted, key=lambda item: PRICE_BANDS.index(item[0]))

    # -- one trip sold four ways ----------------------------------------------------------

    def ambiguous(self, matches: Sequence[RouteFacts]) -> bool:
        """Whether these are versions of one trip rather than a choice of trips: the same
        countries over the same number of days, told apart only by where they leave from, the
        hotel standard, the 购物店 or a feature word. The advisor has to say which one."""
        if len(matches) < 2:
            return False
        first = matches[0]
        same = all(
            facts.days == first.days and set(facts.countries) == set(first.countries)
            for facts in matches
        )
        return same and bool(self.differences(matches))

    def differences(self, matches: Sequence[RouteFacts]) -> list[str]:
        """The dimensions that tell these lines apart, in the order the advisor asks about
        them; empty when nothing here splits them."""
        dimensions = (
            ("出发城市", lambda facts: facts.depart_city),
            ("酒店标准", lambda facts: facts.hotel_grade),
            ("纯玩", lambda facts: facts.no_shopping),
            ("特色", lambda facts: facts.feature_words),
        )
        return [label for label, read in dimensions if len({read(f) for f in matches}) > 1]


def _band_counts(matches: Sequence[RouteFacts]) -> list[tuple[str, int]]:
    counted = _counts(day_band(facts.days) for facts in matches)
    order = [label for label, _, _ in DAY_BANDS]
    return sorted(counted, key=lambda item: order.index(item[0]))


def question_of(
    catalog: Catalog,
    matches: Sequence[RouteFacts],
    stated: Stated,
    months: Mapping[int, Sequence[date]] | None = None,
    year: int | None = None,
    first: Sequence[str] = (),
    nearest_months: bool = False,
) -> Question:
    """The one question these matches go back as. Every dimension is counted (``counts``); the
    card gets the first dimension in ``FILTERS`` order — after ``first``, the ones an
    ambiguous handful differ on — that the customer has not answered and that splits the set,
    with its top ``PRIMARY_VALUES`` values and the rest folded into 其他, and the next such
    dimension as a second row. 天数 is asked in bands once the set holds more lengths than
    ``BAND_DAYS_ABOVE``; 未标注 is never a chip. ``nearest_months`` reopens 出发月份 although the
    dates were stated: nothing runs in them, so the nearest ``NEAREST_MONTHS`` are the
    question."""
    counts = catalog.chips(matches, months, first=first, year=year)
    if len(counts.get("天数", [])) > BAND_DAYS_ABOVE:
        counts["天数"] = _band_counts(matches)
    counts = {
        label: [(value, count) for value, count in values if value and value != UNSTATED]
        for label, values in counts.items()
    }
    if nearest_months:
        counts["出发月份"] = counts.get("出发月份", [])[:NEAREST_MONTHS]
    order = [*first, *(label for label in FILTERS if label not in first)]
    open_to = [
        label
        for label in order
        if len(counts.get(label, [])) >= 2
        and (label not in stated.dimensions or (label == "出发月份" and nearest_months))
    ]
    if not open_to:
        open_to = [label for label in order if counts.get(label)]
    primary = open_to[0] if open_to else "目的地"
    values = counts.get(primary, [])
    if len(values) > PRIMARY_VALUES:
        folded = sum(count for _, count in values[PRIMARY_VALUES:])
        values = [*values[:PRIMARY_VALUES], (OTHER, folded)]
    groups = {primary: values}
    if len(open_to) > 1:
        groups[open_to[1]] = counts[open_to[1]][:SECONDARY_VALUES]
    return Question(dimension=primary, groups=groups, counts=counts)


def _counts(values: Iterable[str]) -> list[tuple[str, int]]:
    """Each value with how many lines carry it, the commonest first."""
    counted: dict[str, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))
