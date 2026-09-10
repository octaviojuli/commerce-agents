# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour example's ``StorefrontBackend`` over the 旅行社 ERP (``docs/erp-contract.md``):
a 线路 is a product family, its dated 团期 are that family's variants, and a cart line is a
预留 order this conversation wrote. A tour price holds only for one party on one date, so the
window and party the advisor last searched are kept per session and restated in every
record's attributes. When the stated window or filters leave the advisor with nothing to
quote, the search relaxes them a step at a time and each relaxed record says in Chinese what
it does not meet, since the advisor reads that back to the customer.

Three shapes of the ERP show through here. Its catalog carries no destination, hotel, vehicle
or shopping field, so a destination is matched against the free text a 线路 carries — its
name, its tags, its 亮点, the department that sells it, the city it leaves from — and the rest
is filtered on this side. An order is the only write it offers: nothing in the cart can cancel or
resize one, so both are refused with the reason, and the advisor does it in the ERP's own
backstage. And a 团期 has two prices — the 市场价 the departure lists, which is what the
customer's share page shows, and the 同业价 the ERP quotes this customer, which is what the
advisor settles at and what an order is booked at — so every record here is quoted at the
同业价 and carries the 市场价 beside it."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from commerce_common.streaming import ToolOutcome
from demo_common.storefront_fixtures import (
    example_data_dir,
    find_product,
    load_json,
    load_policies,
    load_users,
    preferences_of,
)
from shopping_agent import (
    Cart,
    CartItem,
    FulfillmentOption,
    NotOffered,
    Order,
    OrderItem,
    OrderStatus,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    Unavailable,
    UserPreferences,
)
from shopping_agent.executor import ShoppingToolExecutor

from .erp_client import (
    ORDER_STATUS,
    DepartureRecord,
    ErpAuth,
    ErpClient,
    ErpError,
    ErpNotFound,
    ErpRefused,
    ErpThrottled,
    OrderRecord,
    OrderRequest,
    PriceInfo,
    Quote,
    RouteQuery,
    RouteRecord,
)
from .tags import UNKNOWN, RouteFacets, normalize

DATA_DIR = example_data_dir(__file__)
STORE_NAME = "ACME 旅行社"
CURRENCY = "CNY"
CATEGORY = "tour"
ROUTE_PREFIX = "RT-"
DEPARTURE_PREFIX = "DP-"
# The gate that keeps the 线路 cards ahead of a route's 团期, named in the held result.
ROUTE_FIRST_GATE = "route_first"

log = logging.getLogger(__name__)

# The window a search covers when the advisor states none, and the padding a route's
# details add on each side so the neighbouring 团期 are quotable without a second search.
DEFAULT_WINDOW_DAYS = 60
DETAIL_PAD_DAYS = 7
# How far back ``allow_past`` reaches: a beta environment whose 团期 have all departed.
PAST_WINDOW_DAYS = 365
# One fenced details result holds every variant, so a long-running route is trimmed to the
# departures nearest the window the advisor is working in (docs/backends.md, step 4).
MAX_VARIANTS = 24
# A quoted departure costs two ERP calls — its detail for the 市场价 and its counts, and
# ``order/price`` for the 同业价 — so both reads cap how many they price and take the
# departures nearest the middle of the window first.
MAX_LISTING_PRICES = 6
MAX_DETAIL_PRICES = 12
PRICE_CONCURRENCY = 4
# What the boot snapshot fetches from a live ERP, where every route is an HTTP round trip.
MAX_BOOT_ROUTES = 8
DEFAULT_ADULTS = 2
DEFAULT_CHILDREN = 0
MAX_LABELS = 4
MAX_POLICIES = 3
# Fewer than two quotable routes is not a shortlist, so the search relaxes and says so.
MIN_RESULTS = 2
RELAX_WINDOW_DAYS = 7
RELAX_DAYS_SPAN = 2
# How many routes the broad pass may add. Every route kept costs a departure list and up to
# ``MAX_LISTING_PRICES`` priced 团期, and the model is handed a shortlist either way.
MAX_BROAD_MATCHES = 8
# Our own hold window on a 预留 order, whatever the departure's ``reserve_hours`` says: the
# advisor is told the seats are theirs for half an hour, and the line drops after that.
HOLD_TTL_MINUTES = 30

# Where the customer's copy of a shortlist lives: the advisor pastes the link into their
# own chat with the customer. The page is not part of this example; the link and the token
# in it are, because the choose route reads that token back.
DEFAULT_SHARE_BASE_URL = "http://localhost:3004"
SHARE_TOKEN_BYTES = 12

_STATUS_TEXT = {"confirmed": "已成团", "pending": "待成团", "waitlist": "已满候补"}
# The ERP's six order statuses as the shared enum's eight: everything the agency has not
# cancelled is still being worked on, and nothing here ships.
_ORDER_STATE = {
    0: OrderStatus.PROCESSING,  # 预留
    1: OrderStatus.PROCESSING,  # 占位
    2: OrderStatus.PROCESSING,  # 确认
    3: OrderStatus.CANCELLED,  # 取消
    4: OrderStatus.PROCESSING,  # 审批中
    5: OrderStatus.PROCESSING,  # 候补
}
# What a 纯玩 request reads as in a catalog whose only free text is the name and the tags.
_NO_SHOPPING_WORDS = ("纯玩", "零购物", "无购物")
# The hotel standard the advisor may state, in either spelling, as the facets' own value.
_HOTEL_GRADES = {"三": "三钻", "3": "三钻", "四": "四钻", "4": "四钻", "五": "五钻", "5": "五钻"}
# How many of a route's raw tags a record carries: the ERP extracts some fifty, an advisor
# reads a handful, and every one of them rides in a fenced tool result.
MAX_RAW_TAGS = 12
# What a policy entry is scored on: its 标题 and 分类 answer a question more directly than a
# clause buried in the body does.
_HELP_TITLE_WEIGHT = 3.0
_HELP_CONTENT_WEIGHT = 1.0
_HELP_CHAR_WEIGHT = 1.0
_WHITESPACE = re.compile(r"\s+")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _number(value: float) -> str:
    """A price or a count as the advisor says it: ``5980``, ``4.5``."""
    return f"{value:g}"


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _party_label(adults: int, children: int) -> str:
    return f"{adults}大{children}小"


def _int_or_none(raw: str | None) -> int | None:
    """A filter value the model wrote; anything that is not a whole number is no filter."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _int_of(raw: str | None, default: int) -> int:
    parsed = _int_or_none(raw)
    return default if parsed is None else parsed


def _date_of(raw: str | None, default: date) -> date:
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _ages_of(raw: str | None) -> list[int]:
    """``"5|9"`` as whole years; a part that is not a whole number is dropped."""
    parts = (str(raw or "")).split("|")
    return [age for age in (_int_or_none(part) for part in parts) if age is not None]


def route_id_of(record: RouteRecord | int) -> str:
    return f"{ROUTE_PREFIX}{record if isinstance(record, int) else record.route_id}"


def departure_id_of(row: DepartureRecord | int) -> str:
    return f"{DEPARTURE_PREFIX}{row if isinstance(row, int) else row.period_id}"


def _erp_id(product_id: str, prefix: str) -> int | None:
    """The integer the ERP knows behind ``RT-1021`` or ``DP-3001``, or None for anything
    else the model may have written."""
    if not product_id.startswith(prefix):
        return None
    return _int_or_none(product_id[len(prefix) :])


def first_advisor_mobile(data_dir: Path = DATA_DIR) -> str:
    """The ERP mobile of the first advisor in ``users.json``: the contact a deployment
    without ``TOUR_ERP_MOBILE`` writes onto its orders."""
    users = load_json(data_dir, "users.json")["users"]
    return next((str(user.get("mobile") or "") for user in users), "")


def _raw_tags(record: RouteRecord) -> tuple[str, ...]:
    """Every tag a 线路 carries: the editors' own few, then the ERP's auto-extraction of the
    itinerary attachment, which is where a destination and a hotel standard usually are."""
    return (*record.tags, *record.itinerary_tags)


def _text_of(record: RouteRecord) -> str:
    """A route's searchable free text: its name and its tags, which is all the ERP has."""
    return record.route_name + " " + " ".join(_raw_tags(record))


def _mentions(record: RouteRecord, facets: RouteFacets, text: str) -> bool:
    """Whether the destination the advisor stated is written anywhere on the route. The ERP
    matches a search on the 线路 name alone, and its editors do not put the destination in
    every name: 欧洲 is the department that sells the line, 伊犁 a tag, 喀纳斯 a 亮点, and the
    city a group leaves from is its own field. The normalised destinations come first, so
    斯里兰卡 finds a line whose tags say 科伦坡 and nothing else; then a plain substring over
    the raw text, because a destination the advisor typed and one the ERP's editors or its
    extractor wrote are the same characters or nothing."""
    if not text:
        return False
    fields = (
        *facets.destinations,
        record.route_name,
        record.company_name,
        record.depart_city,
        *_raw_tags(record),
        *record.features,
    )
    return any(text in field for field in fields)


def _wanted_grade(level: str) -> str | None:
    """The hotel standard the advisor stated as the one value the facets carry: 五钻, 5钻,
    五星 and 5星 are all 五钻, because the ERP's editors write whichever they like."""
    return _HOTEL_GRADES.get(level.strip()[:1]) if level and level.strip() else None


def _has_hotel_level(record: RouteRecord, facets: RouteFacets, level: str) -> bool:
    """The tags say this standard. A route whose tags name none is not admitted here: it is
    not a no, so the step that drops the preferences takes it with a note saying so."""
    return facets.hotel_grade == _wanted_grade(level)


def _is_no_shopping(record: RouteRecord, facets: RouteFacets) -> bool:
    """纯玩 by the tags where they say anything about 购物 at all; by the words in the name
    and the tags where they do not, which is what the catalog offered before the ERP
    extracted a 购物 tag from the itinerary."""
    if facets.shopping != UNKNOWN:
        return facets.shopping == "none"
    text = _text_of(record)
    return any(word in text for word in _NO_SHOPPING_WORDS)


def _labels(record: RouteRecord, facets: RouteFacets) -> list[str]:
    """The four badges a 线路 card carries, in the order an advisor reads them out: the hotel
    standard, 纯玩, 亲子, the city it leaves from, then what the price includes. The raw tags
    are dozens of attraction names and the first four of them say nothing about the line."""
    labels = [
        f"{facets.hotel_grade}酒店" if facets.hotel_grade != UNKNOWN else "",
        "纯玩无购物" if facets.shopping == "none" else "",
        "亲子" if facets.family else "",
        f"{facets.departure_cities[0]}出发" if facets.departure_cities else "",
        *facets.inclusions[:2],
    ]
    return [label for label in labels if label][:MAX_LABELS]


def _feature_sentence(record: RouteRecord) -> str | None:
    """The one feature written as a sentence; the others are place names."""
    return next((text for text in record.features if "，" in text or "。" in text), None)


def _route_attributes(
    record: RouteRecord, facets: RouteFacets, match: str, mismatch: str | None
) -> dict[str, str]:
    """What the model reads about a 线路: the ERP's own fields, then the attributes its tags
    were normalised into, which are what the advisor filtered on. The raw tags ride along
    capped, because the extraction is evidence the advisor may want to check against the
    itinerary attachment."""
    return {
        "route_code": record.route_code,
        "days": str(record.days),
        "depart_city": record.depart_city,
        "company": record.company_name,
        "destination": "|".join(facets.destinations),
        "shopping": facets.shopping,
        "hotel_grade": facets.hotel_grade,
        "family": "yes" if facets.family else "no",
        "departure_cities": "|".join(facets.departure_cities),
        "inclusions": "|".join(facets.inclusions),
        "budget": "|".join(facets.budget),
        "tags": "|".join(_raw_tags(record)[:MAX_RAW_TAGS]),
        "features": "|".join(record.features),
        "match": match,
        **({"mismatch": mismatch} if mismatch else {}),
    }


def _specs(record: RouteRecord, facets: RouteFacets) -> dict[str, str]:
    """The 行程规格 the advisor reads out; display only, so the keys are Chinese. 酒店, 购物
    and 包含 are what the tags say, and are left off where the tags say nothing."""
    specs = {
        "行程天数": f"{record.days} 天",
        "出发城市": "、".join(facets.departure_cities) or record.depart_city,
    }
    if facets.hotel_grade != UNKNOWN:
        specs["酒店"] = facets.hotel_grade
    if facets.shopping != UNKNOWN:
        specs["购物"] = "纯玩无购物" if facets.shopping == "none" else "含购物店"
    if facets.inclusions:
        specs["包含"] = "、".join(facets.inclusions)
    if facets.budget:
        specs["预算"] = "、".join(facets.budget)
    specs["标签"] = "、".join(_raw_tags(record)[:MAX_RAW_TAGS])
    specs["亮点"] = "、".join(record.features)
    if record.attachment_name:
        specs["行程附件"] = record.attachment_name
    return specs


def _group_status(row: DepartureRecord) -> str:
    """成团 against the ERP's own two counts, and 候补 once the seats are gone and someone
    is already waiting for them."""
    if row.available_seats <= 0 and (row.waitlist_count or 0) > 0:
        return "waitlist"
    return "confirmed" if row.confirm_count >= row.min_group_size else "pending"


def _party_total(price: PriceInfo | None, adults: int, children: int) -> float | None:
    if price is None:
        return None
    return adults * price.adult + children * price.child


def _variant_title(row: DepartureRecord, record: RouteRecord | None) -> str:
    name = record.route_name if record is not None else row.route_name
    return f"{name} {_md(row.depart_date)} 出发"


def _variant_attributes(
    row: DepartureRecord,
    price: PriceInfo | None,
    market: PriceInfo | None,
    source: str,
    adults: int,
    children: int,
) -> dict[str, str]:
    """``price`` is what the advisor quotes and books at — the 同业价, or the 市场价 while only
    that is known — and ``market`` is the departure's own 市场价, which the customer's share
    page shows. ``quote_source`` says which of the two the priced keys hold."""
    total = _party_total(price, adults, children)
    return {
        "period_code": row.period_code,
        "depart_date": row.depart_date.isoformat(),
        "return_date": row.return_date.isoformat(),
        "seats_left": str(row.available_seats),
        "seats_total": str(row.plan_guests),
        "min_group_size": str(row.min_group_size),
        "confirm_count": str(row.confirm_count),
        "group_status": _group_status(row),
        "reserve_hours": str(row.reserve_hours),
        "adult_price": _number(price.adult if price else 0),
        "child_price": _number(price.child if price else 0),
        "elder_price": _number(price.elder if price else 0),
        "single_room_diff": _number(price.single_room_diff if price else 0),
        "market_adult_price": _number(market.adult if market else 0),
        "market_child_price": _number(market.child if market else 0),
        "party_quote_total": _number(total or 0),
        "quote_party": _party_label(adults, children),
        "quote_source": source,
    }


def _variant_summary(
    row: DepartureRecord,
    price: PriceInfo | None,
    market: PriceInfo | None,
    source: str,
    adults: int,
    children: int,
) -> str:
    """The 团期 in one line, on both of its prices: a total the 同业价 made is the advisor's own
    and names the 市场价 beside it, so the sentence says what the order is booked at and what
    the customer is shown; one the 市场价 made says so, because that is the customer's price and
    not what the order would be booked at."""
    status = _STATUS_TEXT.get(_group_status(row), _group_status(row))
    party = _party_label(adults, children)
    seats = f"余位 {row.available_seats}/{row.plan_guests}，{status}"
    total = _party_total(price, adults, children)
    if total is None:
        return f"{seats}，{party}报价待查"
    if source == "list":
        return f"{seats}，市场价 {party}合计 {_number(total)} 元（同业价待查）"
    listed = market.adult if market is not None else 0.0
    beside = f"（市场价成人 {_number(listed)} 元）" if listed > 0 else ""
    return f"{seats}，同业价 {party}合计 {_number(total)} 元{beside}"


def _flat(text: str) -> str:
    return _WHITESPACE.sub("", text.lower())


def _policy_score(query: str, policy: Policy) -> float:
    """How much of the advisor's question one policy entry answers. The shared ``search_help``
    tokenizer splits on ASCII word boundaries and so finds no word at all in a Chinese
    question; this scores the way ``mock_erp`` ranks 线路 instead: every character 2-gram of the
    question scores the best field it appears in, and a bare character scores in a title only,
    which is where a one-word question (退改, 儿童价) lands. A character the question repeats
    counts each time, so 退团怎么退 reads as a question about 退改 and not about 成团."""
    heading = _flat(f"{policy.title} {policy.category}")
    body = _flat(policy.content)
    question = _flat(query)
    grams = (question[at : at + 2] for at in range(len(question) - 1))
    score = sum(
        _HELP_TITLE_WEIGHT if gram in heading else _HELP_CONTENT_WEIGHT if gram in body else 0.0
        for gram in grams
    )
    return score + sum(_HELP_CHAR_WEIGHT for char in question if char in heading)


# -- the advisor's stated request, and how it relaxes when nothing meets it ----------------


@dataclass(frozen=True)
class Request:
    """What the advisor asked for. Only the text and the window reach the ERP; the rest is
    filtered on this side, because the ERP's catalog carries no field for any of it."""

    text: str
    depart_from: date
    depart_to: date
    days_min: int | None = None
    days_max: int | None = None
    no_shopping: bool = False
    hotel_level: str | None = None
    departure_city: str | None = None
    family: bool = False


def _widen_window(stated: Request) -> Request:
    return replace(
        stated,
        depart_from=stated.depart_from - timedelta(days=RELAX_WINDOW_DAYS),
        depart_to=stated.depart_to + timedelta(days=RELAX_WINDOW_DAYS),
    )


def _widen_days(stated: Request) -> Request:
    return replace(
        stated,
        days_min=None if stated.days_min is None else stated.days_min - RELAX_DAYS_SPAN,
        days_max=None if stated.days_max is None else stated.days_max + RELAX_DAYS_SPAN,
    )


def _drop_preferences(stated: Request) -> Request:
    return replace(stated, hotel_level=None, no_shopping=False)


def _whole_window(stated: Request, earliest: date, latest: date) -> Request:
    """The advisor's dates dropped for the whole default window. The destination, the party and
    whatever the steps before it relaxed still stand, so this step answers with the nearest 团期
    the ERP sells at all; the note on a record it admits names the window the advisor stated and
    that nearest date."""
    return replace(stated, depart_from=earliest, depart_to=latest)


# How close a record came to what the advisor stated, for an ordering that keeps the exact
# matches ahead of the relaxed ones whatever else is sorted on.
_MATCH_ORDER = {"exact": 0, "adjacent_date": 1, "similar_route": 2}
# Each step keeps the one before it, so a route found late is measured against everything
# the advisor stated, whichever step first found it. The fourth step is ``_whole_window`` and
# is not listed here: the default window is the backend's own and not a function of the
# request, so ``search_products`` runs it after these three.
_RELAXATIONS = (
    (_widen_window, "adjacent_date"),
    (_widen_days, "similar_route"),
    (_drop_preferences, "similar_route"),
)


def _fits_days(record: RouteRecord, stated: Request) -> bool:
    low = record.days if stated.days_min is None else stated.days_min
    high = record.days if stated.days_max is None else stated.days_max
    return low <= record.days <= high


def _fits_city(record: RouteRecord, facets: RouteFacets, city: str) -> bool:
    """The city the group leaves from, as the tags say it (上海出发, 昆明直飞) or as the ERP's
    own ``departCityName`` does."""
    wanted = city.strip()
    return any(wanted in name for name in (*facets.departure_cities, record.depart_city))


def _fits(record: RouteRecord, facets: RouteFacets, stated: Request) -> bool:
    """The filters the ERP cannot apply: the day count, 纯玩, the hotel standard and the city
    the group leaves from. 亲子 is not one of them — a family that would take a line without
    the tag is better served by it sorting first, so it orders the shortlist instead."""
    if not _fits_days(record, stated):
        return False
    if stated.no_shopping and not _is_no_shopping(record, facets):
        return False
    if stated.departure_city and not _fits_city(record, facets, stated.departure_city):
        return False
    return not (stated.hotel_level and not _has_hotel_level(record, facets, stated.hotel_level))


def _distance(day: date, start: date, end: date) -> int:
    if start <= day <= end:
        return 0
    return min(abs((day - start).days), abs((day - end).days))


def _date_note(stated: Request, dates: list[date]) -> str | None:
    """Nothing departs inside the window the advisor stated; name the nearest one that does."""
    start, end = stated.depart_from, stated.depart_to
    if any(start <= day <= end for day in dates):
        return None
    span = f"{_md(start)}–{_md(end)}"
    nearest = min(dates, key=lambda day: (_distance(day, start, end), day), default=None)
    return f"无 {span} 团期，最近为 {_md(nearest)}" if nearest else f"无 {span} 团期"


def _days_note(record: RouteRecord, stated: Request) -> str | None:
    if _fits_days(record, stated):
        return None
    low = record.days if stated.days_min is None else stated.days_min
    high = record.days if stated.days_max is None else stated.days_max
    return f"天数 {record.days} 天，超出要求的 {low}–{high} 天"


def _preference_notes(record: RouteRecord, facets: RouteFacets, stated: Request) -> list[str]:
    """What the tags say against what the advisor asked for. The tags are the ERP's own
    extraction of the itinerary and can be wrong, so a note states what they say rather than
    that the route fails the condition, and says so plainly where they say nothing at all."""
    notes = []
    if stated.hotel_level and not _has_hotel_level(record, facets, stated.hotel_level):
        wanted = _wanted_grade(stated.hotel_level) or stated.hotel_level
        notes.append(
            f"标签标注{facets.hotel_grade}，要求{wanted}"
            if facets.hotel_grade != UNKNOWN
            else f"未标注{wanted}"
        )
    if stated.no_shopping and not _is_no_shopping(record, facets):
        notes.append("标签标注含购物" if facets.shopping == "some" else "未标注纯玩或零购物")
    return notes


def _mismatch(record: RouteRecord, facets: RouteFacets, stated: Request, dates: list[date]) -> str:
    """What the advisor reads back: every condition they stated that this record fails, in
    the order they stated them. A route admitted by one relaxation usually misses more than
    that step alone relaxed, and a note that named only that step would understate it."""
    notes = [note for note in (_date_note(stated, dates), _days_note(record, stated)) if note]
    return "；".join(notes + _preference_notes(record, facets, stated)) or "与所提条件略有出入"


@dataclass
class Fetched:
    """What one search run has already read from the ERP, so its passes share it: the routes a
    query text and a window came back with — the broad read of a window's whole catalog is that
    query with no name — and each route's departures inside a pass's window. The steps repeat a
    query more often than they change it, since dropping the day count or the preferences
    changes nothing the ERP filters on, and a route's departures are read by the pass that keeps
    it and again by the record it becomes."""

    routes: dict[tuple[str, date, date], list[RouteRecord]] = field(default_factory=dict)
    departures: dict[tuple[int, date, date], list[DepartureRecord]] = field(default_factory=dict)


@dataclass
class SearchContext:
    """The window and party the advisor last searched. Every later quote is made for it:
    ``get_product_details`` prices the departures for this party, and ``add_to_cart`` writes
    the order for it."""

    depart_from: date
    depart_to: date
    adults: int
    children: int
    child_ages: list[int]


@dataclass
class Hold:
    """One order this conversation wrote, as the cart sees it. ``created_at`` is our own
    clock, because the 30-minute hold the advisor is told about is ours and not the ERP's;
    ``order_no`` is filled in by the first cart read that resolves the order."""

    order_id: int
    period_id: int
    created_at: datetime
    is_waitlist: bool
    order_no: str = ""

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(minutes=HOLD_TTL_MINUTES)


@dataclass(frozen=True)
class ShareRecord:
    """One shortlist as the customer's page sees it: the conversation that sent it, the
    advisor who owns that conversation, and the only 团期 ids the page may choose from."""

    session_id: str
    advisor_id: str
    departure_ids: list[str]
    created_at: datetime


def _picked_ids(tool_input: dict[str, Any]) -> list[str]:
    """The 线路 and 团期 ids among a ``present_products`` call's ``picks``: cards of both kinds
    are what the advisor picks off, so both are recorded. The payload is the model's and is
    validated downstream, so a shape that is not the tool's yields no id here rather than
    failing: the base validation is what reports it."""
    picks = tool_input.get("picks")
    if not isinstance(picks, list):
        return []
    chosen = (pick.get("product_id") for pick in picks if isinstance(pick, dict))
    return [
        picked
        for picked in chosen
        if isinstance(picked, str) and picked.startswith((ROUTE_PREFIX, DEPARTURE_PREFIX))
    ]


class TourToolExecutor(ShoppingToolExecutor):
    """The ERP refuses a call with a rule the advisor can act on (a business refusal, an
    unknown record, a login throttle, an account that cannot see the department); relay it so
    the model says what stands in the way instead of reporting an outage. ``ErpUnavailable``
    is an outage and falls through to the base default.

    It also holds the route-first gate, the way ``gates.py`` holds a cart write: the advisor
    picks the 线路 off the cards, and the 团期 of one route open only after they have. A
    conversation's own searches are what the gate measures against, so the record of what was
    presented lives on the backend and not here, where a new executor is built every turn."""

    erp_rule_text = "Nothing changed: {detail}. Tell the advisor and offer what fits."
    route_first_text = (
        "{product_id} came out of a search in this session and the advisor has not seen it "
        "yet. Present the matching routes with present_products first, so the advisor can "
        "pick one; open a route's departures only after the advisor names it. If the advisor "
        "already named this route in their latest message, present it now and open it in the "
        "next round."
    )

    def domain_error(self, error: Exception) -> ToolOutcome | None:
        if isinstance(error, ErpRefused | ErpNotFound | ErpThrottled | ErpAuth):
            return ToolOutcome.error(
                self.erp_rule_text.format(detail=self._sanitize(str(error), 200))
            )
        return super().domain_error(error)

    async def dispatch(self, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
        """The base dispatch, with the route-first gate around the two tools it spans: a
        route's details are held until the model has presented it, and a rendered
        ``present_products`` is what lifts the hold for the ids it showed. The 团期 ids it
        showed are recorded the same way, because ``present_shortlist`` reads that record."""
        if name == "get_product_details" and (held := self._route_first(tool_input)):
            return held
        outcome = await super().dispatch(name, tool_input)
        if name == "present_products" and not outcome.refused:
            self._backend.note_presented(self._session.session_id, _picked_ids(tool_input))
        return outcome

    def _route_first(self, tool_input: dict[str, Any]) -> ToolOutcome | None:
        """The held outcome when the model opens a 线路 this session searched but has not put
        in front of the advisor, else None. A 团期 id is not gated — that is the step after
        the pick — and neither is a route id the advisor pasted, which no search returned and
        no card could have shown."""
        product_id = str(tool_input.get("product_id", ""))
        if not product_id.startswith(ROUTE_PREFIX):
            return None
        if product_id not in self._state.seen_products:
            return None
        if product_id in self._backend.presented(self._session.session_id):
            return None
        return ToolOutcome.held(
            ROUTE_FIRST_GATE, self.route_first_text.format(product_id=product_id)
        )


class TourBackend(StorefrontBackend):
    """The advisor is the customer here: they search on behalf of the customer in front of
    them, and the cart is the 预留 orders their conversation wrote. The ERP logs one
    salesperson in and books for one 同行 customer, so ``customer_id`` and ``contact_mobile``
    are the deployment's, not the model's."""

    def __init__(
        self,
        erp: ErpClient,
        data_dir: Path = DATA_DIR,
        today: date | None = None,
        *,
        customer_id: int,
        contact_mobile: str,
        allow_past: bool = False,
    ) -> None:
        """``today`` is for a host that runs on its own clock; the default is the real date.
        ``allow_past`` lets the windows reach behind today, for a test environment whose 团期
        have all departed; it is off in production."""
        self.erp = erp
        self.today: date = today or _utcnow().date()
        self.customer_id = customer_id
        self.contact_mobile = contact_mobile
        self.allow_past = allow_past
        self.store_name: str = load_json(data_dir, "routes.json").get("store_name", STORE_NAME)
        self._users = load_users(data_dir)
        self._policies = load_policies(data_dir)
        self._contexts: dict[str, SearchContext] = {}
        # What the ERP has already returned this process: a route is looked up by id long
        # after the search that found it, and a cart line names a departure by id alone. A
        # departure's 同业价 is cached beside it, ``None`` where the ERP has no price row for
        # it, so a second read of the same 团期 costs no further call.
        self._routes: dict[int, RouteRecord] = {}
        # Each route's tags as attributes, normalised once: a search runs the filters over
        # every route in the window, and a card, its specs and its notes read them again.
        self._route_facets: dict[int, RouteFacets] = {}
        self._departures: dict[int, DepartureRecord] = {}
        self._quotes: dict[int, Quote | None] = {}
        # The listing snapshot the host's catalog routes read; `load_listings` fills both.
        self.products: dict[str, ProductDetails] = {}
        self._variants: dict[str, ProductDetails] = {}
        # The orders each conversation wrote, newest last.
        self._holds: dict[str, list[Hold]] = {}
        # The 线路 each conversation has put in front of the advisor as cards, which the
        # executor's route-first gate reads. It lives here because the executor is built
        # again for every turn while the conversation is not.
        self._presented: dict[str, set[str]] = {}
        # The shortlists sent to a customer, by the token in their link.
        self._shares: dict[str, ShareRecord] = {}

    # -- the searched window and party ------------------------------------------------

    def _default_context(self) -> SearchContext:
        """What an id pasted into a fresh conversation is quoted for. The party is stated
        back as ``quote_party`` so the advisor sees it is an assumption, not their party."""
        return SearchContext(
            depart_from=self._earliest(),
            depart_to=self.today + timedelta(days=DEFAULT_WINDOW_DAYS),
            adults=DEFAULT_ADULTS,
            children=DEFAULT_CHILDREN,
            child_ages=[],
        )

    def _earliest(self) -> date:
        return self.today - timedelta(days=PAST_WINDOW_DAYS) if self.allow_past else self.today

    def _context(self, session: ShoppingSessionContext) -> SearchContext:
        return self._contexts.get(session.session_id) or self._default_context()

    def _read_context(self, attributes: dict[str, str]) -> SearchContext:
        """The advisor's stated window and party. The model writes these by hand, so a value
        that does not parse falls back to the default rather than failing the search."""
        depart_from = _date_of(attributes.get("depart_from"), self._earliest())
        depart_to = _date_of(
            attributes.get("depart_to"), self.today + timedelta(days=DEFAULT_WINDOW_DAYS)
        )
        # Stated backwards, the two dates are still the span the advisor means.
        if depart_to < depart_from:
            depart_from, depart_to = depart_to, depart_from
        return SearchContext(
            depart_from=depart_from,
            depart_to=depart_to,
            adults=max(1, _int_of(attributes.get("adults"), DEFAULT_ADULTS)),
            children=max(0, _int_of(attributes.get("children"), DEFAULT_CHILDREN)),
            child_ages=_ages_of(attributes.get("child_ages")),
        )

    def _window(self, start: date, end: date) -> tuple[date, date]:
        """A widened or padded window can reach further back than anything is sellable; only
        ``allow_past`` lets a window start behind today."""
        start = max(self._earliest(), start)
        return start, max(start, end)

    # -- the ERP's routes and departures -------------------------------------------------

    def _facets(self, record: RouteRecord) -> RouteFacets:
        """One 线路's tags as the attributes the advisor filters on (``api/tags.py``)."""
        cached = self._route_facets.get(record.route_id)
        if cached is None:
            cached = normalize(_raw_tags(record), record.price_tags)
            self._route_facets[record.route_id] = cached
        return cached

    async def _routes_for(
        self, text: str, window: tuple[date, date], fetched: Fetched | None
    ) -> list[RouteRecord]:
        """The ERP's routes for one query text over one window, kept for the rest of the search
        run that asked: the relaxation steps below repeat a query the step before them already
        made, and the broad read is the same call with no name."""
        key = (text, *window)
        cached = fetched.routes.get(key) if fetched is not None else None
        if cached is None:
            cached = await self.erp.search_routes(
                RouteQuery(route_name=text, depart_from=window[0], depart_to=window[1])
            )
            self._routes.update({record.route_id: record for record in cached})
            if fetched is not None:
                fetched.routes[key] = cached
        return cached

    async def _broad(
        self, window: tuple[date, date], fetched: Fetched, *, read: bool
    ) -> list[RouteRecord]:
        """Every route the ERP sells inside the window: an empty name is not a filter, so one
        call (the client caps its paging) brings back the whole window's catalog, and the run's
        cache holds it, so a widened window costs exactly one further call.

        ``read=False`` spends no call at all and takes the routes the run has already read,
        whichever window each was read for; the pass's own window is then applied by the
        departure list that follows. The last relaxation step widens to the whole default
        window, and one route query is all it may cost."""
        if read:
            return await self._routes_for("", window, fetched)
        seen: dict[int, RouteRecord] = {}
        for records in fetched.routes.values():
            seen.update({record.route_id: record for record in records})
        return list(seen.values())

    async def _departs(
        self, record: RouteRecord, window: tuple[date, date], fetched: Fetched
    ) -> bool:
        """Whether the route has a 团期 inside the window. ``route/list``'s own date filter is
        loose — it answers with routes that have none (``docs/erp-contract.md``) — and a card
        built from one of those carries no date, no seat count and no price. The departures are
        kept in the run's cache, so the record this route becomes costs no second call."""
        return bool(await self._list(record, window, fetched))

    async def _search(
        self,
        stated: Request,
        fetched: Fetched | None = None,
        *,
        broad_read: bool = True,
    ) -> list[RouteRecord]:
        """The ERP's routes for the text and the window, minus the ones the day count, 纯玩
        or the hotel standard rules out — the ERP has no field for any of those three — and
        minus the ones with no 团期 inside the window at all, which its loose date filter
        returns as matches. A route dropped for that is not what the advisor asked for; it
        comes back through a relaxation step, with a note naming its nearest date.

        The ERP's own search is fuzzy on the 线路 name, and a destination its editors never
        wrote into a name matches nothing there however far the window is relaxed. So when the
        named query is not a shortlist, the broad read brings back the window's whole catalog
        and every route that carries the destination anywhere else it is written is kept. Those
        routes do meet what the advisor asked for, so they are exact matches, not relaxed
        ones, and the day count, 纯玩 and the hotel standard filter them the same way.

        ``fetched`` is one search run's own reads. A caller that passes none — a pasted id, the
        boot snapshot — takes the named query alone and reads no departures."""
        window = self._window(stated.depart_from, stated.depart_to)
        records = await self._routes_for(stated.text, window, fetched)
        fits = [record for record in records if _fits(record, self._facets(record), stated)]
        if fetched is None:
            return fits
        found = [record for record in fits if await self._departs(record, window, fetched)]
        if not stated.text or len(found) >= MIN_RESULTS:
            return found
        seen = {record.route_id for record in found}
        for record in await self._broad(window, fetched, read=broad_read):
            if len(found) >= MAX_BROAD_MATCHES:
                break
            facets = self._facets(record)
            if record.route_id in seen or not _mentions(record, facets, stated.text):
                continue
            if _fits(record, facets, stated) and await self._departs(record, window, fetched):
                found.append(record)
        return found

    async def _list(
        self,
        record: RouteRecord,
        window: tuple[date, date],
        fetched: Fetched | None = None,
    ) -> list[DepartureRecord]:
        key = (record.route_id, *window)
        if fetched is not None and key in fetched.departures:
            return fetched.departures[key]
        rows = await self.erp.list_departures(
            record.route_id, record.route_name, window[0], window[1]
        )
        for row in rows:
            # A listed row carries no price; a detail read already cached one, so keep it.
            self._departures.setdefault(row.period_id, row)
        if fetched is not None:
            fetched.departures[key] = rows
        return rows

    async def _route(self, route_id: int) -> RouteRecord | None:
        """The route record, from the search that returned it or, for an id this process has
        not seen, from one broad ERP search over the default window: an empty name is not a
        filter, so it brings back every route selling seats in the next couple of months."""
        if route_id in self._routes:
            return self._routes[route_id]
        default = self._default_context()
        await self._search(
            Request(text="", depart_from=default.depart_from, depart_to=default.depart_to)
        )
        return self._routes.get(route_id)

    def _priced(self, row: DepartureRecord) -> PriceInfo | None:
        """The departure's own 市场价, which only its detail call carries."""
        cached = self._departures.get(row.period_id)
        return cached.price if cached is not None else row.price

    def _department(self, row: DepartureRecord) -> int:
        """The department a write on this 团期 is made in. The ERP carries it on every period
        row, and a fetched detail is the freshest one this process has."""
        cached = self._departures.get(row.period_id)
        return (cached.company_id if cached is not None else 0) or row.company_id

    async def _quote(self, row: DepartureRecord) -> Quote | None:
        """This customer's 同业价 for one departure, asked in the departure's own department. A
        团期 the catalog has no price row for answers not-found, which is a gap in the catalog
        and not a failed call."""
        try:
            return await self.erp.quote(row.period_id, self.customer_id, self._department(row))
        except ErpNotFound:
            return None

    async def _price_one(self, row: DepartureRecord, gate: asyncio.Semaphore) -> None:
        """One departure's two prices, kept in the caches the records read through: the 市场价
        and the seat counts from its detail, then the 同业价 the order would be booked at. A row
        the ERP cannot detail keeps no 市场价, and one it has no price row for keeps no 同业价;
        the record then says which of the two its numbers are."""
        async with gate:
            if self._priced(row) is None:
                detailed = await self.erp.get_departure(row.period_id)
                if detailed is not None:
                    self._departures[detailed.period_id] = detailed
            if row.period_id not in self._quotes:
                self._quotes[row.period_id] = await self._quote(row)

    async def _prices(self, rows: list[DepartureRecord], middle: date, limit: int) -> None:
        """Both prices for at most ``limit`` of the departures, nearest the middle of the
        window first, since that is where the advisor is working. The ERP prices one departure
        per call, so the calls run a few at a time rather than one after another."""
        by_distance = sorted(rows, key=lambda row: abs((row.depart_date - middle).days))
        nearest = by_distance[: max(0, limit)]
        gate = asyncio.Semaphore(PRICE_CONCURRENCY)
        await asyncio.gather(*(self._price_one(row, gate) for row in nearest))

    # -- catalog records -----------------------------------------------------------------

    def _family(
        self,
        record: RouteRecord,
        rows: list[DepartureRecord],
        context: SearchContext,
        match: str,
        mismatch: str | None,
    ) -> Product:
        """One route as a family: its 起价 is the cheapest 同业价 among the departures in the
        searched window that were quoted, the cheapest 市场价 when none of them was, and the
        route's own 起价 when neither price is known. The dates that window holds are the one
        option a variant chooses."""
        facets = self._facets(record)
        party = context.adults + context.children
        quoted = [q.price.adult for row in rows if (q := self._quotes.get(row.period_id))]
        listed = [price.adult for row in rows if (price := self._priced(row))]
        cheapest = (
            min((adult for adult in quoted if adult > 0), default=0.0)
            or min((adult for adult in listed if adult > 0), default=0.0)
            or max(record.from_price, 0.0)
        )
        return Product(
            product_id=route_id_of(record),
            title=record.route_name,
            brand=record.company_name or self.store_name,
            price=cheapest,
            currency=CURRENCY,
            image_url=record.image_url,
            category=CATEGORY,
            labels=_labels(record, facets),
            attributes=_route_attributes(record, facets, match, mismatch),
            in_stock=any(row.available_seats >= party for row in rows),
            short_description=(
                _feature_sentence(record) or f"{record.days} 天 · {record.depart_city}出发"
            ),
            options={"depart_date": [row.depart_date.isoformat() for row in rows]},
        )

    def _variant(
        self, row: DepartureRecord, record: RouteRecord | None, context: SearchContext
    ) -> Product:
        """One departure as a variant, quoted for the party in ``context``. What the advisor
        reads is the 同业价 the order would be booked at; while only the departure's 市场价 is
        known the record is quoted at that instead and says so, and ``quote_source`` —
        ``customer``, ``list``, ``none`` — is which of the two, or neither, the numbers are."""
        market = self._priced(row)
        quote = self._quotes.get(row.period_id)
        price = quote.price if quote is not None else market
        source = "customer" if quote is not None else ("list" if market is not None else "none")
        party = context.adults + context.children
        return Product(
            product_id=departure_id_of(row),
            title=_variant_title(row, record),
            brand=(record.company_name if record else None) or self.store_name,
            price=price.adult if price is not None else 0.0,
            currency=CURRENCY,
            image_url=record.image_url if record else None,
            category=CATEGORY,
            attributes=_variant_attributes(
                row, price, market, source, context.adults, context.children
            ),
            in_stock=row.available_seats >= party,
            short_description=_variant_summary(
                row, price, market, source, context.adults, context.children
            ),
            option_values={"depart_date": row.depart_date.isoformat()},
            variant_of=route_id_of(row.route_id),
        )

    async def _families(
        self,
        records: list[RouteRecord],
        stated: Request,
        context: SearchContext,
        match: str,
        original: Request | None,
        *,
        fetched: Fetched | None = None,
        prices: int = MAX_LISTING_PRICES,
    ) -> list[Product]:
        window = self._window(stated.depart_from, stated.depart_to)
        middle = window[0] + (window[1] - window[0]) / 2
        found = []
        for record in records:
            rows = await self._list(record, window, fetched)
            await self._prices(rows, middle, prices)
            dates = [row.depart_date for row in rows]
            mismatch = (
                _mismatch(record, self._facets(record), original, dates)
                if original is not None
                else None
            )
            found.append(self._family(record, rows, context, match, mismatch))
        return found

    async def _step(
        self,
        relaxed: Request,
        stated: Request,
        context: SearchContext,
        match: str,
        found: list[Product],
        fetched: Fetched,
        *,
        broad_read: bool = True,
    ) -> list[Product]:
        """One relaxation step's own records: the routes this pass finds that the passes before
        it did not, each saying what it does not meet against everything the advisor stated."""
        seen = {product.product_id for product in found}
        records = await self._search(relaxed, fetched, broad_read=broad_read)
        fresh = [record for record in records if route_id_of(record) not in seen]
        return await self._families(fresh, relaxed, context, match, stated, fetched=fetched)

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        """The routes matching the advisor's text over the stated window. ``destination`` is
        matched against the ERP's route names, against the attributes a 线路's tags normalise
        into and against the rest of its free text when the names alone are not a shortlist,
        because the catalog has no destination field; ``departure_city`` and the hotel
        standard are read off those attributes too, ``family`` orders the shortlist rather
        than filtering it, and ``child_ages`` is carried into every quote's party but filters
        nothing, since the ERP states no minimum age."""
        attributes = dict(filters.attributes) if filters is not None else {}
        context = self._read_context(attributes)
        self._contexts[session.session_id] = context
        stated = Request(
            text=(attributes.get("destination") or query or "").strip(),
            depart_from=context.depart_from,
            depart_to=context.depart_to,
            days_min=_int_or_none(attributes.get("days_min")),
            days_max=_int_or_none(attributes.get("days_max")),
            no_shopping=attributes.get("no_shopping", "").strip().lower() == "yes",
            hotel_level=attributes.get("hotel_level") or None,
            departure_city=(attributes.get("departure_city") or "").strip() or None,
            family=attributes.get("family", "").strip().lower() == "yes",
        )
        # This search's own reads, shared by the steps below: a query text and a window are
        # asked for once however many steps repeat them, and the broad pass costs at most two
        # calls for the whole run, since the window widens once.
        fetched = Fetched()
        found = await self._families(
            await self._search(stated, fetched), stated, context, "exact", None, fetched=fetched
        )
        relaxed = stated
        for widen, match in _RELAXATIONS:
            if len(found) >= MIN_RESULTS:
                break
            relaxed = widen(relaxed)
            found += await self._step(relaxed, stated, context, match, found, fetched)
        if len(found) < MIN_RESULTS:
            # The fourth step: the whole default window in place of the advisor's, so a
            # destination whose 团期 all fall outside their dates is still quotable and the note
            # names the nearest one. It spends no second route query, so its broad pass is
            # whatever the steps above already read.
            default = self._default_context()
            relaxed = _whole_window(relaxed, default.depart_from, default.depart_to)
            found += await self._step(
                relaxed, stated, context, "adjacent_date", found, fetched, broad_read=False
            )
        if stated.family:
            # 亲子 is a preference and not a condition: the lines whose tags claim it come
            # first inside each match class, and the rest are still on the shortlist.
            found.sort(
                key=lambda p: (
                    _MATCH_ORDER.get(p.attributes["match"], 3),
                    p.attributes["family"] != "yes",
                )
            )
        return found[:limit]

    # -- details -------------------------------------------------------------------------

    async def _route_details(
        self, route_id: int, context: SearchContext, *, prices: int = MAX_DETAIL_PRICES
    ) -> ProductDetails | None:
        record = await self._route(route_id)
        if record is None:
            return None
        window = self._window(
            context.depart_from - timedelta(days=DETAIL_PAD_DAYS),
            context.depart_to + timedelta(days=DETAIL_PAD_DAYS),
        )
        rows = await self._list(record, window)
        if not rows:
            # The searched window holds no 团期 of the route the advisor named. Quote the
            # default window instead, once: the dates say the route runs elsewhere, which
            # the advisor can act on, and a family with no variants does not.
            default = self._default_context()
            window = self._window(default.depart_from, default.depart_to)
            rows = await self._list(record, window)
        middle = window[0] + (window[1] - window[0]) / 2
        if len(rows) > MAX_VARIANTS:
            nearest = sorted(rows, key=lambda row: abs((row.depart_date - middle).days))
            rows = nearest[:MAX_VARIANTS]
        rows = sorted(rows, key=lambda row: row.depart_date)
        await self._prices(rows, middle, prices)
        family = self._family(record, rows, context, "exact", None)
        details = ProductDetails(
            **family.model_dump(),
            long_description="\n".join(record.features)[:1200] or None,
            specs=_specs(record, self._facets(record)),
        )
        details.variants = [self._variant(row, record, context) for row in rows]
        return details

    async def _departure_details(
        self, period_id: int, context: SearchContext
    ) -> ProductDetails | None:
        """The one departure, in the same two calls a route's variants take: its detail for the
        市场价 and the seat counts, and its 同业价 for the customer this deployment books for. A
        departure the ERP has no price row for keeps the 市场价 it carries."""
        row = await self.erp.get_departure(period_id)
        if row is None:
            return None
        self._departures[row.period_id] = row
        self._quotes[row.period_id] = await self._quote(row)
        record = await self._route(row.route_id)
        variant = self._variant(row, record, context)
        return ProductDetails(
            **variant.model_dump(),
            long_description=("\n".join(record.features)[:1200] or None) if record else None,
            specs=_specs(record, self._facets(record)) if record else {},
        )

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        context = self._context(session)
        route_id = _erp_id(product_id, ROUTE_PREFIX)
        if route_id is not None:
            return await self._route_details(route_id, context)
        period_id = _erp_id(product_id, DEPARTURE_PREFIX)
        if period_id is not None:
            return await self._departure_details(period_id, context)
        return None

    # -- what the conversation has shown the advisor ----------------------------------------

    def note_presented(self, session_id: str, product_ids: list[str]) -> None:
        """Remember the 线路 and 团期 this conversation has put in front of the advisor as
        cards."""
        self._presented.setdefault(session_id, set()).update(product_ids)

    def presented(self, session_id: str) -> set[str]:
        """The 线路 and 团期 ids this conversation has already shown as cards: the route-first
        gate reads the routes, ``present_shortlist`` the departures."""
        return set(self._presented.get(session_id, ()))

    # -- cart: the 预留 orders this conversation wrote -------------------------------------

    def _live_holds(self, session_id: str) -> list[Hold]:
        """The session's orders, minus the 预留 whose half hour has run out. Nothing is asked
        of the ERP: the hold the advisor was told about is ours, and the ERP's own 预留 runs
        on ``reserve_hours`` whatever we do here."""
        now = _utcnow()
        holds = [h for h in self._holds.get(session_id, ()) if h.is_waitlist or h.expires_at > now]
        self._holds[session_id] = holds
        return holds

    def _advisor(self, session: ShoppingSessionContext) -> UserPreferences:
        return preferences_of(self._users, session.user_id)

    def _contact_name(self, session: ShoppingSessionContext) -> str:
        """Who the ERP writes on the order: the salesperson the client logged in as, or the
        advisor's own name from the profile when the client names nobody."""
        user_info = getattr(self.erp, "user_info", None) or {}
        logged_in = str(user_info.get("userName") or "")
        if logged_in:
            return logged_in
        display = self._advisor(session).display_name or session.user_id
        return display.split("（")[0]

    def _store_name(self, session: ShoppingSessionContext) -> str:
        """The 门店 a 同行 order is written through. The profile states the 门店 and then what
        it sells; the store is the first clause."""
        profile = self._advisor(session)
        return profile.preferences.get("门店", "").split("，")[0] or self.store_name

    async def _line(self, hold: Hold, order: OrderRecord) -> CartItem:
        row = self._departures.get(hold.period_id)
        if row is None:
            row = await self.erp.get_departure(hold.period_id)
            if row is not None:
                self._departures[row.period_id] = row
        quantity = max(1, order.adults + order.children + order.elders)
        depart = order.depart_date or (row.depart_date if row is not None else None)
        title = order.route_name if depart is None else f"{order.route_name} {_md(depart)} 出发"
        return CartItem(
            product_id=departure_id_of(hold.period_id),
            title=f"{title}（候补）" if hold.is_waitlist else title,
            # The ERP totals the order; a cart line is per head, so the total is split
            # evenly and the line total comes back to the ERP's figure.
            price=round(order.total_amount / quantity, 2),
            quantity=quantity,
            option_values=({"depart_date": depart.isoformat()} if depart is not None else {}),
            variant_of=route_id_of(row.route_id) if row is not None else None,
        )

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        items = []
        standing = []
        for hold in self._live_holds(session.session_id):
            order = await self.erp.get_order(hold.order_id)
            if order is None:
                continue  # written, then removed inside the ERP: it is no longer a line
            hold.order_no = order.order_no
            standing.append(hold)
            items.append(await self._line(hold, order))
        self._holds[session.session_id] = standing
        return Cart(items=items, currency=CURRENCY)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        """Write the 预留 order for this 团期, in the 团期's own department: the ERP refuses a
        write made in any other, so the departure is read first when this process has not seen
        it. The quantity the model asked for is heads; the split into 成人 and 儿童 comes from
        the searched party, because the ERP prices and seats them differently, and at least one
        head is an adult. A party larger than the seats left comes back as a 候补 order rather
        than a refusal, and its line says so."""
        period_id = _erp_id(product_id, DEPARTURE_PREFIX)
        if period_id is None:
            # The executor's gate already holds an add of a family; this is the second layer,
            # and it also catches an id that is neither a route nor a departure.
            raise Unavailable(product_id)
        row = self._departures.get(period_id) or await self.erp.get_departure(period_id)
        if row is None:
            raise ErpNotFound(f"找不到该团期：{period_id}")
        self._departures[period_id] = row
        context = self._context(session)
        heads = max(1, quantity)
        children = min(context.children, heads - 1)
        result = await self.erp.create_order(
            OrderRequest(
                period_id=period_id,
                customer_id=self.customer_id,
                company_id=self._department(row),
                adults=heads - children,
                children=children,
                elders=0,
                rooms=0,
                single_room_diff_count=0,
                contact_name=self._contact_name(session),
                contact_mobile=self.contact_mobile,
                store_name=self._store_name(session),
            )
        )
        self._holds.setdefault(session.session_id, []).append(
            Hold(
                order_id=result.order_id,
                period_id=period_id,
                created_at=_utcnow(),
                is_waitlist=result.is_waitlist,
            )
        )
        return await self.get_cart(session)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        del session, product_id, quantity
        raise NotOffered(
            "在对话里改人数（订单已写进 ERP，只能由顾问在 ERP 后台改单，或另占一个团期）"
        )

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        del session, product_id
        raise NotOffered("在对话里取消占位（ERP 没有取消接口，请顾问在 ERP 后台处理该订单）")

    # -- advisor, orders, help content, fulfillment ---------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return self._advisor(session)

    async def get_account_context(self, session: ShoppingSessionContext) -> dict[str, Any] | None:
        """``department`` is the one the ERP logged this account into and ``departments`` how
        many it reads across; a client that logs nobody in — the fixtures — is one department,
        the store's own."""
        user_info = getattr(self.erp, "user_info", None) or {}
        companies = getattr(self.erp, "companies", None) or ()
        return {
            "advisor": session.user_id,
            "store": self._store_name(session),
            "department": str(user_info.get("companyName") or "") or self.store_name,
            "departments": len(companies) or 1,
            "customer_id": self.customer_id,
            "active_holds": len(self._live_holds(session.session_id)),
        }

    def _order(self, record: OrderRecord) -> Order:
        """One ERP order as the shared record. The ERP's own status word rides along with the
        departure date, because the enum has no 预留 and the advisor works in the ERP's."""
        quantity = max(1, record.adults + record.children + record.elders)
        depart = f"出发 {record.depart_date.isoformat()}" if record.depart_date else ""
        status_text = record.status_text or ORDER_STATUS.get(record.status, "")
        return Order(
            order_id=str(record.order_id),
            status=_ORDER_STATE.get(record.status, OrderStatus.PROCESSING),
            placed_at=record.created_at,
            items=[
                OrderItem(
                    product_id=departure_id_of(record.period_id),
                    title=f"{record.route_name}（{record.period_code}）",
                    quantity=quantity,
                    price=round(record.total_amount / quantity, 2),
                    option_values=(
                        {"depart_date": record.depart_date.isoformat()}
                        if record.depart_date
                        else {}
                    ),
                )
            ],
            total=record.total_amount,
            currency=CURRENCY,
            estimated_delivery="，".join(part for part in (status_text, depart) if part) or None,
        )

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        """The salesperson's own orders: the ERP's credentials name one, so there is no id
        to filter on here."""
        del session
        return [self._order(record) for record in await self.erp.list_orders()][:limit]

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        """One order by the ERP's own id, or by the 订单号 the advisor reads off a screen."""
        del session
        numeric = _int_or_none(order_id)
        if numeric is not None:
            record = await self.erp.get_order(numeric)
            return None if record is None else self._order(record)
        listed = [row for row in await self.erp.list_orders() if row.order_no == order_id]
        return self._order(listed[0]) if listed else None

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        """The agency's own rules, best answer first. The advisor types the customer's question
        in Chinese, so the entries are ranked by what its characters share with them; a question
        this agency has no rule for gets nothing rather than the least bad entry."""
        del session
        scored = [
            (score, policy)
            for policy in self._policies
            if (score := _policy_score(query, policy)) > 0
        ]
        scored.sort(key=lambda pair: -pair[0])
        return [policy for _, policy in scored[:MAX_POLICIES]]

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        """Nothing ships: the customer joins the group at its 集合地点. The tool is switched
        off in the config; this is the second layer."""
        del session, product_ids
        raise NotOffered("Delivery, pickup, and shipping for a tour booking")

    # -- the demo host's view: a listing snapshot, per-session cleanup, live holds --------

    async def load_listings(self) -> None:
        """Fill ``products`` with one record per route, its ``variants`` the departures in
        the default window. This is a boot snapshot for the host's catalog routes and the
        demo's listing pages; the agent's own tools call the ERP on every turn, so seats,
        prices, and party quotes in the conversation are live and these are not. Against a
        live ERP every route is a round trip, so the snapshot takes the first few routes and
        no list prices; an ERP that refuses or is down leaves the snapshot empty rather than
        stopping the host from booting."""
        context = self._default_context()
        stated = Request(text="", depart_from=context.depart_from, depart_to=context.depart_to)
        listings: dict[str, ProductDetails] = {}
        variants: dict[str, ProductDetails] = {}
        try:
            records = await self._search(stated)
            live = self._live_erp()
            for record in records[: MAX_BOOT_ROUTES if live else len(records)]:
                details = await self._route_details(
                    record.route_id, context, prices=0 if live else MAX_DETAIL_PRICES
                )
                if details is None:
                    continue
                listings[details.product_id] = details
                for variant in details.variants:
                    variants[variant.product_id] = ProductDetails(**variant.model_dump())
        except ErpError as error:
            log.warning("tour listings not loaded: %s: %s", type(error).__name__, error)
            listings, variants = {}, {}
        self.products, self._variants = listings, variants

    def _live_erp(self) -> bool:
        """A client that logs a salesperson in is the agency's own ERP over HTTP; the
        fixtures answer in memory and cost nothing to page through."""
        return hasattr(self.erp, "user_info")

    def product(self, product_id: str) -> ProductDetails | None:
        """A loaded route or one of its departures by id, from the snapshot ``load_listings``
        took; the conversation's own reads go to the ERP instead."""
        return find_product(self.products, self._variants, product_id)

    def reset_session(self, session_id: str) -> None:
        """Forget what the conversation accumulated: the window and party it searched, the
        线路 it showed as cards, and the orders its cart showed. The orders themselves stand
        in the ERP, which offers no way to take one back; the advisor handles that in the
        ERP's own backstage."""
        self._contexts.pop(session_id, None)
        self._holds.pop(session_id, None)
        self._presented.pop(session_id, None)

    # -- the shortlist the advisor sends the customer -------------------------------------

    async def create_share_link(
        self, session_id: str, advisor_id: str, departure_ids: list[str]
    ) -> str:
        """Mint the customer's link to a shortlist of 团期. The token and the ids behind it
        stay on the server: the model asks for a card and never sees this call's arguments
        or its result, and the customer's page can only choose among the ids stored here."""
        token = secrets.token_urlsafe(SHARE_TOKEN_BYTES)
        self._shares[token] = ShareRecord(
            session_id=session_id,
            advisor_id=advisor_id,
            departure_ids=list(departure_ids),
            created_at=_utcnow(),
        )
        # Read per call: the host loads .env after this module is imported.
        base = os.environ.get("TOUR_SHARE_BASE_URL", DEFAULT_SHARE_BASE_URL).rstrip("/")
        return f"{base}/s/{token}"

    def share_record(self, token: str) -> ShareRecord | None:
        """The shortlist behind a link's token, for the route the customer's page calls."""
        return self._shares.get(token)

    def recent_orders(self, limit: int = 6) -> list[Order]:
        """The cross-user feed a merchant portal would show. This example has no portal, and
        the ERP's order list belongs to the logged-in salesperson, not to the demo."""
        del limit
        return []

    def holds_snapshot(self, session_id: str) -> list[tuple[str, str, datetime]]:
        """``(order_no, product_id, expires_at)`` for the 预留 this session still holds. The
        host builds its cart extras synchronously, so it reads this rather than the ERP; a
        候补 order is not a hold and does not count down."""
        return [
            (hold.order_no, departure_id_of(hold.period_id), hold.expires_at)
            for hold in self._live_holds(session_id)
            if not hold.is_waitlist
        ]
