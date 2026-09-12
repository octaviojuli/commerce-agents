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
# The dimensions a card's chips are grouped by, and the filter each one goes back through.
FILTERS = {
    "目的地": "region/destination",
    "天数": "days_min/days_max",
    "出发城市": "departure_city",
    "出发月份": "depart_from/depart_to",
    "酒店标准": "hotel_level",
    "纯玩": "no_shopping",
    "特色": "destination",
    "起价": "price_max",
}
# What a group with nothing to say writes instead of a value.
UNSTATED = "未标注"
NO_SHOPPING = "纯玩"
SOME_SHOPPING = "含购物店"
# What a destination the advisor wrote as several places is split on: a chip sends the
# countries joined (法国·意大利·瑞士) and the line must carry every one of them.
_SEPARATORS = "·、,，/ 　+&"


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
        depart_city=doc.summary.depart_city,
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


def _mentions(facts: RouteFacts, text: str) -> bool:
    """Whether the destination the advisor stated is written anywhere on the line: its
    countries, its 线路系, its name, the department that sells it, the places its days pass
    through or the sights it names. A distinctive word (观鲸, 一价全含) lands on the name and
    the sights, which is where the catalog writes what tells one version from another."""
    fields = (
        *facts.countries,
        facts.region,
        facts.name,
        facts.catalog_name,
        facts.department,
        facts.depart_city,
        *facts.places,
        *facts.sight_names,
    )
    return all(any(part in field for field in fields if field) for part in _parts(text))


def _has_feature(facts: RouteFacts, word: str) -> bool:
    """A feature the advisor tapped or typed: one of the words that tell the versions of a
    trip apart, else anything the line's name or its sights carry."""
    wanted = word.strip()
    return bool(wanted) and (wanted in facts.feature_words or _mentions(facts, wanted))


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
        return sorted(found, key=lambda facts: self._rank(facts, request))

    def _fits(self, facts: RouteFacts, request: Request) -> bool:
        """Every condition the request states, each met by any one of the values it lists."""
        if request.destinations and not any(
            _mentions(facts, value) for value in request.destinations
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


def _counts(values: Iterable[str]) -> list[tuple[str, int]]:
    """Each value with how many lines carry it, the commonest first."""
    counted: dict[str, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return sorted(counted.items(), key=lambda item: (-item[1], item[0]))
