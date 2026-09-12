# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour example's ``StorefrontBackend`` over the 旅行社 ERP (``docs/erp-contract.md``):
a 线路 is a product family, its dated 团期 are that family's variants, and a cart line is a
预留 order this conversation wrote. A tour price holds only for one party on one date, so the
window and party the advisor last searched are kept per session and restated in every
record's attributes.

What a 线路 *is* comes from the agency's 线路文档 and not from this ERP: its catalog
carries no destination, hotel, vehicle or shopping field, so the search runs over the
documents (``api/catalog.py``) and a 线路 without one is not offered at all. The ERP answers
the dynamic half — which 团期 run, their 成团 state, the seats left and the 同业价 — and two
further shapes of it show through here. An order is the only write it offers: nothing in the
cart can cancel or resize one, so both are refused with the reason, and the advisor does it in
the ERP's own backstage. And a 团期 has two prices — the 市场价 the departure lists, which is
what the customer's share page shows, and the 同业价 the ERP quotes this customer, which is
what the advisor settles at and what an order is booked at — so every record here is quoted at
the 同业价 and carries the 市场价 beside it."""

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

from commerce_common.presentation import PresentationRefused
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

from .advisors import NEED_LOGIN, AdvisorLogin, AdvisorRegistry
from .catalog import (
    FILTERS as CHIP_FILTERS,
)
from .catalog import (
    Catalog,
    Request,
    RouteFacts,
    short_text,
)
from .erp_client import (
    ORDER_STATUS,
    DepartureRecord,
    ErpAuth,
    ErpClient,
    ErpError,
    ErpNotFound,
    ErpRefused,
    ErpThrottled,
    Itinerary,
    OrderRecord,
    OrderRequest,
    PriceInfo,
    Quote,
    RouteQuery,
    RouteRecord,
    WindowReader,
)
from .itinerary_source import cache_read, cache_write, fetch_attachment, parse_docx
from .plans import (
    DayDiff,
    Plan,
    PlanDay,
    PlanVersion,
    ReferencePrice,
    diff_days,
    new_plan_id,
    new_share_token,
)
from .private_lines import load_private_line_rules
from .route_doc import RouteDoc
from .route_docs import (
    RouteDocStore,
    flights_text,
    hotel_grade,
    is_reviewed,
    itinerary_of,
    meals_included,
    shopping_stops,
)
from .store import PlanConflictError, SqliteSessionStore
from .tags import UNKNOWN, RouteFacets, normalize

DATA_DIR = example_data_dir(__file__)
STORE_NAME = "ACME 旅行社"
CURRENCY = "CNY"
CATEGORY = "tour"
ROUTE_PREFIX = "RT-"
DEPARTURE_PREFIX = "DP-"
# The gate that keeps the 线路 cards ahead of a route's 团期, named in the held result.
ROUTE_FIRST_GATE = "route_first"
# The gate that holds a shortlist over a request too broad for one: the 聚焦卡 comes first.
FOCUS_FIRST_GATE = "focus_first"
# Up to this many matches the advisor gets the cards and the chips together; above it the
# reply is the question alone, and a 聚焦卡 may then carry no line at all. Beside a card that
# does ask, a shortlist may stand as this many picks.
FOCUS_ANCHORS = 3
FOCUS_ANCHORS_UP_TO = 12

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
# ``order/price`` for the 同业价 — so every read caps how many it prices and takes the
# departures nearest the middle of the window first.
MAX_DETAIL_PRICES = 12
PRICE_CONCURRENCY = 4
DEFAULT_ADULTS = 2
DEFAULT_CHILDREN = 0
MAX_LABELS = 4
MAX_POLICIES = 3
# How far past the window a 团期 read reaches on each side, so a search around the same dates
# costs no second read.
WINDOW_PAD_DAYS = 7
# How many 团期 one 团期卡 carries: the advisor reads a fortnight of dates out, not a year's.
MAX_DEPARTURE_ITEMS = 12
# How far past the advisor's dates a line that runs in none of them is looked up, so the card
# can name the nearest date it does run: half a year, which is as far ahead as the catalog goes.
NEAREST_WINDOW_DAYS = 180
# Above this many matches the search is an overview and a question, not a shortlist.
OVERVIEW_ABOVE = 5
# How much of a document's cover fields a card carries: 酒店标准 is a paragraph in some
# attachments, and a badge is a few characters.
MAX_COVER_CHARS = 80
MAX_LABEL_CHARS = 10
# Our own hold window on a 预留 order, whatever the departure's ``reserve_hours`` says: the
# advisor is told the seats are theirs for half an hour, and the line drops after that.
HOLD_TTL_MINUTES = 30

# Where the customer's copy of a shortlist lives: the advisor pastes the link into their
# own chat with the customer. The page is not part of this example; the link and the token
# in it are, because the choose route reads that token back.
DEFAULT_SHARE_BASE_URL = "http://localhost:3004"
SHARE_TOKEN_BYTES = 12
# How many 定制方案 one conversation may build: a plan is a piece of work on one 线路 for one
# customer, and a fourth in the same conversation is the next customer's conversation.
MAX_PLANS_PER_SESSION = 3
# How many versions one plan may carry: the advisor revises with the customer on the phone,
# and a plan past this many rounds is a new plan rather than another version.
MAX_PLAN_VERSIONS = 30

# What the plan tool answers with where the deployment kept no plan store, and the four rules
# a call to it must pass. All five are advisor-facing Chinese: the executor relays them into
# the conversation the way it relays the ERP's own refusals.
NO_PLAN_STORE = "此部署未配置方案库，定制方案无法保存。"
TOO_MANY_PLANS = f"本会话最多 {MAX_PLANS_PER_SESSION} 个定制方案，请开新会话再建下一个。"
FOREIGN_PLAN = "该方案不属于本会话，请在建它的会话里改，或在本会话里另建一个。"
TOO_MANY_VERSIONS = f"该方案最多 {MAX_PLAN_VERSIONS} 版，请另建一个方案。"
NO_SUCH_VERSION = "该方案没有第 {version} 版，请按最新版改。"
PLAN_BUSY = "该方案刚被另一轮改过，请再发一次。"

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
# How many of a route's raw tags a record carries: the ERP extracts some fifty, an advisor
# reads a handful, and every one of them rides in a fenced tool result.
MAX_RAW_TAGS = 12
# How much of a 线路's baseline 行程 a details record carries. A details result is fenced whole
# at ``max_fenced_chars`` (12 000), and a route's own fields and its priced 团期 already spend
# two thirds of that, so the days take what is left: 200 characters of programme per day over
# at most 20 days keeps a 12-day 线路 inside the fence with its variants intact. What is cut is
# the tail of a day's own text; the title, the 住宿 and the 用餐 always ride.
MAX_DAY_CHARS = 200
MAX_ITINERARY_DAYS = 20
# What a 线路 whose 行程 could not be read says instead, because an advisor who reads nothing
# here has to lay the days out against the attachment themselves.
NO_ITINERARY = "无（行程附件无法解析或缺失，请对照附件手工摆出）"
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


def _values(raw: str | None) -> tuple[str, ...]:
    """A filter the advisor answered with several taps: the values joined by ``|``, in the
    order they were chosen and without repeats. One tap is one value and reads the same way."""
    return tuple(dict.fromkeys(part.strip() for part in str(raw or "").split("|") if part.strip()))


def _numbers(raw: str | None) -> tuple[int, ...]:
    """``8|12`` as whole numbers; a part that is not one is no filter."""
    return tuple(
        number for number in (_int_or_none(part) for part in _values(raw)) if number is not None
    )


def _months_of(raw: str | None) -> tuple[date, ...]:
    """``2026-10|2026-11`` as the first day of each month, earliest first. The advisor taps a
    month and the ERP is asked for the span those months cover."""
    months = []
    for value in _values(raw):
        try:
            months.append(date.fromisoformat(f"{value}-01"))
        except ValueError:
            continue
    return tuple(sorted(set(months)))


def _month_end(first: date) -> date:
    """The last day of the month ``first`` opens."""
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


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


def _route_attributes(record: RouteRecord, facets: RouteFacets) -> dict[str, str]:
    """What a 线路 the advisor opened or pasted carries beside the document's own fields: the
    ERP's catalog row, and the attributes its tags were normalised into. The raw tags ride
    along capped, because the extraction is evidence the advisor may want to check against the
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
        "region": facets.region,
        "tags": "|".join(_raw_tags(record)[:MAX_RAW_TAGS]),
        "features": "|".join(record.features),
        "match": "exact",
        # The 行程附件's own file name, when the 线路 links one: the workbench's card offers it
        # as a download, and the model says it is there rather than that it cannot send files.
        **({"attachment": record.attachment_name} if record.attachment_name else {}),
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


def _doc_attributes(doc: RouteDoc | None) -> dict[str, str]:
    """What the model reads off a 线路 document beside the tag-derived attributes: whether the
    document is the agency's reviewed word or the parser's draft, the 购物店 count, the meals
    the price includes, the hotel standard the attachment states, and the flights."""
    if doc is None:
        return {}
    included, stated = meals_included(doc)
    return {
        "doc": "reviewed" if is_reviewed(doc) else "draft",
        "shopping_stops": str(shopping_stops(doc)),
        "meals_included": f"{included}/{stated}" if stated else "",
        "doc_hotel_grade": hotel_grade(doc),
        "flights": flights_text(doc)[:200],
    }


def _doc_specs(doc: RouteDoc | None) -> dict[str, str]:
    """The 行程规格 a document adds to the card, in the advisor's words."""
    if doc is None:
        return {}
    specs: dict[str, str] = {}
    if doc.transport:
        specs["参考航班"] = flights_text(doc)
    if doc.cover.hotel_standard:
        specs["酒店标准"] = doc.cover.hotel_standard
    if doc.cover.meal_standard:
        specs["用餐安排"] = doc.cover.meal_standard
    specs["购物店"] = "、".join(s.name for s in doc.shopping) or "无（附件未列购物店）"
    if doc.optional:
        specs["自费项目"] = "、".join(
            f"{o.name}{' ' + o.price if o.price else ''}" for o in doc.optional
        )
    if doc.inclusions:
        specs["费用包含"] = "；".join(doc.inclusions[:8])
    if doc.exclusions:
        specs["费用不含"] = "；".join(doc.exclusions[:6])
    for label, text in (
        ("单房差", doc.policies.single_room),
        ("儿童", doc.policies.child),
        ("签证", doc.policies.visa),
    ):
        if text:
            specs[label] = text
    return specs


def _itinerary_specs(
    record: RouteRecord, itinerary: Itinerary | None, doc: RouteDoc | None = None
) -> dict[str, str]:
    """The 线路's baseline 行程 as one spec per day, under the line saying where it was read.
    行程来源 is stated whichever way the reading went, because a day the advisor cannot see
    here is one they have to open the attachment for, and the record has to say which case it
    is. The day itself is the 线路's and not the 团期's: what a party actually gets is the
    计调's to confirm."""
    if itinerary is None:
        return {"行程来源": NO_ITINERARY}
    attachment = f"行程附件 {record.attachment_name or ''}".strip()
    if doc is not None and itinerary.source_ref == "route-doc":
        attachment = "线路文档（已复核）" if is_reviewed(doc) else "线路文档（解析稿，待复核）"
    specs = {"行程来源": "ERP" if itinerary.source == "erp" else attachment}
    for day in itinerary.days[:MAX_ITINERARY_DAYS]:
        line = f"{day.title}｜{day.text[:MAX_DAY_CHARS]}"
        if day.hotel:
            line += f"｜住宿：{day.hotel}"
        if day.meals:
            line += f"｜用餐：{day.meals}"
        specs[f"第{day.day_no}天"] = line
    return specs


def _group_status(row: DepartureRecord) -> str:
    """成团 against the ERP's own two counts, and 候补 once the seats are gone and someone
    is already waiting for them. A 团期 whose ``min_group_size`` is 0 states no 最低成团人数
    rather than needing nobody, so with no one signed up it is 待成团 and not 已成团."""
    if row.available_seats <= 0 and (row.waitlist_count or 0) > 0:
        return "waitlist"
    if row.min_group_size <= 0 and row.confirm_count <= 0:
        return "pending"
    return "confirmed" if row.confirm_count >= row.min_group_size else "pending"


def _unpublished(price: PriceInfo | None, adults: int, children: int) -> tuple[str, ...]:
    """The fares this party needs that the price row leaves at 0, in the advisor's words. A 0
    in this ERP is 未发布 and not free — the catalog carries 0 for a 儿童价, an 老人价 or a
    单房差 the department has not published — so a party with a child and no 儿童价 has no
    total, while an adults-only party is unaffected by the same row."""
    if price is None:
        return ()
    named = (("成人价", adults, price.adult), ("儿童价", children, price.child))
    return tuple(label for label, heads, fare in named if heads > 0 and fare <= 0)


def _party_total(price: PriceInfo | None, adults: int, children: int) -> float | None:
    """What the party costs, or ``None`` when a fare it needs is 未发布."""
    if price is None or _unpublished(price, adults, children):
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
    price_type: str = "",
) -> dict[str, str]:
    """``price`` is what the advisor quotes and books at — the 同业价, or the 市场价 while only
    that is known — and ``market`` is the departure's own 市场价, which the customer's share
    page shows. ``quote_source`` says which of the two the priced keys hold, or ``partial``
    where a fare the party needs is 未发布 and there is no total. ``price_type`` is the ERP's
    own label on the quote (同行价, 直客价)."""
    total = _party_total(price, adults, children)
    missing = _unpublished(price, adults, children)
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
        "quote_source": "partial" if missing else source,
        "price_type": price_type,
        **({"unpublished_fares": "|".join(missing)} if missing else {}),
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
    not what the order would be booked at. A fare the party needs that the ERP leaves at 0 is
    未发布, so the sentence names it and says the total is 待定."""
    status = _STATUS_TEXT.get(_group_status(row), _group_status(row))
    party = _party_label(adults, children)
    seats = f"余位 {row.available_seats}/{row.plan_guests}，{status}"
    total = _party_total(price, adults, children)
    if missing := _unpublished(price, adults, children):
        label = "市场价" if source == "list" else "同业价"
        adult = f"，{label}成人 {_number(price.adult)} 元" if price and price.adult > 0 else ""
        return f"{seats}{adult}，{'、'.join(missing)}未发布，合计待定"
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


def _facts_attributes(facts: RouteFacts) -> dict[str, str]:
    """What the model reads about a 线路: the agency's own document, field by field. A cover
    field is a paragraph in some attachments, so the three the card carries are capped."""
    return {
        "route_code": facts.route_code,
        "days": str(facts.days),
        "nights": str(facts.nights) if facts.nights else "",
        "depart_city": facts.depart_city,
        "countries": "|".join(facts.countries),
        "region": facts.region,
        "airline": short_text(facts.airline, MAX_COVER_CHARS),
        "hotel_standard": facts.hotel_standard[:MAX_COVER_CHARS],
        "meal_standard": facts.meal_standard[:MAX_COVER_CHARS],
        "shopping_stops": str(facts.shopping_stops),
        "optional_count": str(facts.optional_count),
        "ticket_count": str(facts.ticket_count),
        "gift_count": str(facts.gift_count),
        "places": "|".join(facts.places),
        "highlights": "|".join(facts.highlights),
        "doc": "reviewed" if facts.reviewed else "draft",
        "match": "exact",
        **({"attachment": facts.attachment_name} if facts.attachment_name else {}),
    }


def _facts_labels(facts: RouteFacts) -> list[str]:
    """The badges a 线路 card carries, in the order an advisor reads them out: 购物, the hotel
    standard, the airline, and whether the document is the agency's word or the parser's."""
    labels = [
        "纯玩" if facts.no_shopping else f"购物店{facts.shopping_stops}家",
        # The grade the document states, or no badge at all: a clipped 酒店标准 (全程精选…)
        # reads as a claim the document never made.
        facts.hotel_grade,
        short_text(facts.airline, MAX_LABEL_CHARS),
        "已复核" if facts.reviewed else "解析稿",
    ]
    return [label for label in labels if label][:MAX_LABELS]


def _window_label(window: tuple[date, date]) -> str:
    """The advisor's dates as they read them back: ``10/01–10/07``."""
    return f"{window[0]:%m/%d}–{window[1]:%m/%d}"


def _no_departures_note(window: tuple[date, date], nearest: date | None) -> str:
    """What a line that runs in none of the advisor's dates says instead, in the words the
    advisor reads back to the customer."""
    span = f"{_window_label(window)} 无团期"
    return (
        f"{span}，最近团期 {nearest:%m/%d}"
        if nearest
        else f"{span}，{NEAREST_WINDOW_DAYS} 天内无团期"
    )


def _facts_summary(facts: RouteFacts) -> str:
    """The one line under the title: the document's first 亮点 where it has one, else the
    shape of the trip."""
    nights = f" {facts.nights} 晚" if facts.nights else ""
    return (
        facts.highlights[0][:MAX_COVER_CHARS]
        if facts.highlights
        else (f"{facts.days} 天{nights} · {facts.depart_city or '出发城市未标注'}出发")
    )


# -- the advisor's stated request, and how it relaxes when nothing meets it ----------------


def _inside(
    grouped: dict[int, list[DepartureRecord]], window: tuple[date, date]
) -> dict[int, list[DepartureRecord]]:
    """One window's own view of a wider read: each route's 团期 whose date the window holds,
    and no entry for a route left with none — a route with no departure inside the window is
    not a match, whatever the read around it carried."""
    inside = {
        route_id: [row for row in rows if window[0] <= row.depart_date <= window[1]]
        for route_id, rows in grouped.items()
    }
    return {route_id: rows for route_id, rows in inside.items() if rows}


@dataclass
class Fetched:
    """What one run has already read from the ERP, so its reads are shared: the catalog rows a
    query text and a window came back with — the broad read of a window's whole catalog is that
    query with no name — and each route's departures inside a window.

    A window's 团期 are read whole and once: the ERP's period list carries no ``routeId``, so
    asking it per route costs a call per candidate, and asking it for the window alone costs
    one paged read the whole run shares. ``windows`` holds that read and each pass's own view
    of it, both grouped by route id and keyed by the dates they cover, so a pass inside a
    window already read costs nothing; a route no view names has no departures inside that
    pass's window, and there is nothing to ask again."""

    routes: dict[tuple[str, date, date], list[RouteRecord]] = field(default_factory=dict)
    departures: dict[tuple[int, date, date], list[DepartureRecord]] = field(default_factory=dict)
    windows: dict[tuple[date, date], dict[int, list[DepartureRecord]]] = field(default_factory=dict)


@dataclass
class Overview:
    """What the last search matched, grouped the way an advisor narrows it: by 目的地, by
    length, by the city the group leaves from, by 出发月份, by hotel standard, by 纯玩 and by
    起价 (``Catalog.chips``). Each value is one the model sends straight back as a filter, and
    the 聚焦卡 renders them as the chips the advisor taps. ``shown`` is how many of the
    ``total`` the cards beside it carry.

    An overview stands in three cases and says which: the request is wider than a shortlist,
    the handful it matched are versions of one trip and ``dimensions`` names what tells them
    apart, or nothing in the catalog met it at all and the groups are the whole catalog's."""

    total: int
    shown: int
    groups: dict[str, list[tuple[str, int]]]
    # The advisor has answered a 聚焦卡 already: the question this overview carries is a short
    # one beside the cards, not the whole reply.
    answered: bool = False
    # The matches are one trip sold several ways; ``dimensions`` is what differs.
    ambiguous: bool = False
    dimensions: tuple[str, ...] = ()
    # The request matched nothing and the groups are the catalog's own directions.
    empty: bool = False
    # The dates the advisor stated, where they stated any, and how many of the matches run in
    # them: a line that runs in none of them is on the cards as its nearest date.
    window: str = ""
    with_dates: int = 0

    # The filter each group's values go back through (``catalog.FILTERS``).
    FILTERS = CHIP_FILTERS

    def text(self) -> str:
        return "\n".join([self._head(), *self._rows(), self._instruction()])

    def _head(self) -> str:
        if self.empty:
            return "目录概览：线路文档里没有符合条件的线路，以下是目录本身的分布。"
        if self.ambiguous:
            named = "、".join(self.dimensions)
            return (
                f"目录概览：这 {self.total} 条线路的国家和天数相同，只差在{named}，还不能替客人选。"
            )
        dated = f"，其中 {self.with_dates} 条在 {self.window} 有团期" if self.window else ""
        if self.answered:
            return f"目录概览：缩小范围后仍有 {self.total} 条{dated}，上面是其中 {self.shown} 条。"
        if self.total <= FOCUS_ANCHORS_UP_TO:
            return f"目录概览：共 {self.total} 条线路符合{dated}，上面是其中 {self.shown} 条。"
        return (
            f"目录概览：共 {self.total} 条线路符合{dated}，"
            f"上面只是其中 {self.shown} 条的样本，不是结论。"
        )

    def _rows(self) -> list[str]:
        rows = []
        for label, counts in self.groups.items():
            if counts:
                cells = "、".join(f"{value} {count}" for value, count in counts)
                how = f"filter {key}" if (key := self.FILTERS.get(label, "")) else "无过滤"
                rows.append(f"按{label}（{how}）：{cells}")
        return rows

    def _dates_rule(self) -> str:
        """What the model does with the dates the advisor stated, where they stated any."""
        if not self.window:
            return ""
        return (
            f" Every card says what it sells in {self.window}: lead with the lines whose "
            "departures attribute names dates, and for a line whose match is adjacent_date say "
            "it runs on none of them and give its nearest_departure. The dates are on the "
            "cards, so never say the 团期 have not been checked and never open one line's 团期 "
            "after another to find out."
        )

    def _instruction(self) -> str:
        mapping = (
            "The advisor answers by tapping chips, and the workbench sends one message: "
            "只看 目的地：德法意瑞、法意瑞；天数：12 天；出发月份：10月、11月 — the groups joined by "
            "；and the values inside a group by 、. Values inside a group are alternatives and "
            "the groups are conditions on each other, so send each group as one filter with its "
            'values joined by "|": 目的地 to region (to destination for a value that is not a '
            "线路系), 天数 to days, 出发城市 to departure_city, 出发月份 to months as YYYY-MM in "
            "the year those months next fall in, 酒店标准 to hotel_level, 纯玩 to no_shopping=yes "
            "(含购物店 adds no filter), 特色 to feature, and 起价 bands to price_min and price_max "
            "— the lower edge of the lowest band and the upper edge of the highest. Everything "
            "the advisor stated earlier still holds: send it again with the new filters. "
            "不限条件，直接看这些线路 is not a filter: search again with the conditions unchanged "
            "and present the cards. "
        )
        if self.empty:
            return (
                "Nothing matched: present no cards. Say so in a sentence and call present_focus "
                "with the directions above as chips, so the advisor can send the customer "
                f"somewhere the catalog sells. {mapping}"
            )
        if self.ambiguous:
            return (
                "These are versions of one trip. Do not choose for the customer: say in a "
                "sentence what they share, then call present_focus asking which of the "
                f"differing dimensions above the customer wants, with the lines as picks. "
                f"Skip the question only if the advisor named a route code, an id or the full "
                f"name. {mapping}"
            )
        if self.answered:
            return (
                "The advisor has narrowed once already: present the cards with "
                "present_products, and keep the question to one short line — the chips are "
                f"welcome after the cards while the set is wider than {OVERVIEW_ABOVE}."
                f"{self._dates_rule()} {mapping}"
            )
        if self.total <= FOCUS_ANCHORS_UP_TO:
            return (
                "A set this size is cards and chips together: present the results with "
                "present_products, say the total in a sentence, and end the reply with "
                "present_focus asking ONE narrowing question over the groups above, naming the "
                f"dimension that splits the set best.{self._dates_rule()} {mapping}"
            )
        return (
            "Too many lines for the advisor to read as cards: do not present these as the "
            "answer. State the total in a sentence and call present_focus with ONE narrowing "
            "question, naming the dimension that splits this set best; the card carries the "
            "groups above as chips and no 线路 at all. Cards follow the advisor's answer."
            f"{self._dates_rule()} {mapping}"
        )


@dataclass(frozen=True)
class LineDates:
    """What one search read about its matches' 团期: the rows inside the advisor's dates by
    线路 id, the nearest date a line with none of them does run, and every date read for each
    line, which is what the 出发月份 chips are counted from. ``read_all`` says the run weighed
    every match and not only the ones the cards carry."""

    inside: dict[int, list[DepartureRecord]]
    nearest: dict[int, date]
    read_all: bool
    months: dict[int, list[date]] = field(default_factory=dict)

    def rows(self, route_id: int) -> list[DepartureRecord]:
        return self.inside.get(route_id, [])

    def runs_in_window(self, route_id: int) -> bool:
        return bool(self.inside.get(route_id))

    def month_dates(self) -> dict[int, list[date]]:
        """Every date read, by 线路 id: the stated window's rows, and the later read's dates
        for the lines that had none."""
        read = {
            route_id: [row.depart_date for row in rows] for route_id, rows in self.inside.items()
        }
        return {**read, **self.months}


@dataclass(frozen=True)
class DepartureItem:
    """One 团期 on the 团期卡: the record the cart takes by id, the date it leaves, what the
    advisor may do with it, and the 同业价 per adult where the ERP quoted one."""

    departure: Product
    date: date
    status: str
    price_adult: float | None


@dataclass(frozen=True)
class DeparturesView:
    """One 线路's 团期 inside one window, as ``present_departures`` renders them."""

    route: Product
    window: tuple[date, date]
    items: list[DepartureItem]


@dataclass
class SearchContext:
    """The window and party the advisor last searched. Every later quote is made for it:
    ``get_product_details`` prices the departures for this party, and ``add_to_cart`` writes
    the order for it. ``stated`` says the window is the advisor's own and not this backend's
    default, which is what decides whether a 线路 card carries its sellable 团期."""

    depart_from: date
    depart_to: date
    adults: int
    children: int
    child_ages: list[int]
    stated: bool = False


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
    focus_first_text = (
        "The last search matched {total} 线路 — more than the advisor can read as cards. Call "
        "present_focus with one narrowing question first; cards follow the advisor's answer. "
        "Below {up_to} matches the cards come first and the chips go after them."
    )
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
        if name == "present_products" and (held := self._focus_first(tool_input)):
            return held
        outcome = await super().dispatch(name, tool_input)
        if name == "present_products" and not outcome.refused:
            self._backend.note_presented(self._session.session_id, _picked_ids(tool_input))
        if name == "search_products" and not outcome.is_error:
            overview = self._backend.overview(self._session.session_id)
            if overview is not None:
                outcome = replace(
                    outcome, result_text=f"{outcome.result_text}\n\n{overview.text()}"
                )
        return outcome

    def _focus_first(self, tool_input: dict[str, Any]) -> ToolOutcome | None:
        """The held outcome when the model shortlists a search wider than the advisor can
        read, else None. Up to ``FOCUS_ANCHORS_UP_TO`` matches the cards are the answer and the
        chips go after them, so nothing is held; above it the narrowing question comes first.
        An answered overview gates nothing, and neither does one over a handful that are
        versions of one trip: those lines are the cards the question is asked over."""
        overview = self._backend.overview(self._session.session_id)
        if overview is None or overview.answered or overview.ambiguous:
            return None
        if overview.total <= FOCUS_ANCHORS_UP_TO:
            return None
        return ToolOutcome.held(
            FOCUS_FIRST_GATE,
            self.focus_first_text.format(total=overview.total, up_to=FOCUS_ANCHORS_UP_TO),
        )

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
    them, and the cart is the 预留 orders their conversation wrote. The ERP books for one 同行
    customer, so ``customer_id`` is the deployment's and not the model's.

    Which ERP account a call goes out on is the session's, not the deployment's: every read and
    the one write run on the client the advisor's own login left in the ``registry``
    (``advisors.py``), resolved per call by ``_erp_for``. ``erp`` is the deployment's own
    account and is asked for two things only — the boot snapshot and the 同行 customer's code —
    because both are the deployment's and neither belongs to a session."""

    def __init__(
        self,
        erp: ErpClient,
        data_dir: Path = DATA_DIR,
        today: date | None = None,
        *,
        customer_id: int,
        contact_mobile: str,
        allow_past: bool = False,
        live: bool = False,
        customer_code: str = "",
        store_name: str = "",
        order_store_name: str = "",
        registry: AdvisorRegistry | None = None,
        state_dir: Path | None = None,
        route_docs: RouteDocStore | None = None,
        plans: SqliteSessionStore | None = None,
    ) -> None:
        """``today`` is for a host that runs on its own clock; the default is the real date.
        ``allow_past`` lets the windows reach behind today, for a test environment whose 团期
        have all departed; it is off in production.

        ``live`` says the ERP is the agency's own, and the four values that are the fixtures'
        in a demo are then the deployment's: ``customer_code`` is the 同行 customer's own
        ``csCode``, resolved to an id at boot; ``store_name`` is the agency's own name, which
        the fixture does not carry; ``order_store_name`` is the 门店 a 同行 order is written
        through, which the ERP requires and the fixture's profile must never supply; and the
        advisor's name and department come from the ERP login rather than from users.json.

        ``registry`` is where the logged-in advisors are; with none — the tests, and a host
        that has not built one — every call goes out on ``erp``.

        ``route_docs`` is the 线路文档 the search runs over, which is the catalog itself —
        every document the parser wrote, the reviewed ones ranked ahead of the drafts: with
        none the fixture catalog's own documents under ``data/route-docs`` are read, and an
        empty store is a deployment with no catalog at all.

        ``state_dir`` is where the parsed 行程附件 are kept between restarts; with none the
        attachment is parsed again in every process that reads the 线路.

        ``plans`` is where the 定制方案 are kept, which is the same file the sessions are in;
        with none the deployment builds no plan at all and ``present_itinerary`` says so."""
        self.erp = erp
        self.registry = registry
        self.plans = plans
        self._state_dir = state_dir
        # The 线路 documents (``route_docs.py``), which are the catalog the advisor searches:
        # the agency's own under the state directory, and the fixture catalog's own beside
        # data/routes.json for a deployment that has none of its own.
        self._route_docs = (
            RouteDocStore.load(data_dir / "route-docs") if route_docs is None else route_docs
        )
        self.today: date = today or _utcnow().date()
        self.live = live
        self.customer_id = customer_id
        self.customer_code = customer_code
        # The customer the deployment books for as the account context names it: the id the
        # fixtures book for, ``csCode 名称`` once ``load_listings`` has resolved the code
        # against the agency's own customer book, and empty while there is no customer at all.
        self.customer_label = str(customer_id) if customer_id > 0 else ""
        self.contact_mobile = contact_mobile
        self.allow_past = allow_past
        self.order_store_name = order_store_name
        self.store_name: str = store_name or load_json(data_dir, "routes.json").get(
            "store_name", STORE_NAME
        )
        self._users = load_users(data_dir)
        # 包团, 会销 and 定制 lines: not a search result, reachable by id or full name.
        self._private = load_private_line_rules(data_dir)
        self._policies = load_policies(data_dir)
        self._contexts: dict[str, SearchContext] = {}
        self._overviews: dict[str, Overview] = {}
        # Sessions whose standing overview has been put to the advisor as a 聚焦卡.
        self._focused: set[str] = set()
        # What the ERP has already returned this process: a route is looked up by id long
        # after the search that found it, and a cart line names a departure by id alone. A
        # departure's 同业价 is cached beside it, ``None`` where the ERP has no price row for
        # it, so a second read of the same 团期 costs no further call.
        self._routes: dict[int, RouteRecord] = {}
        # The route ids a whole-catalog read did not carry, so a second read of one costs no
        # second scan; ``load_listings`` clears it.
        self._missing_routes: set[int] = set()
        # Each route's tags as attributes, normalised once: the listing snapshot and a
        # details record read them, and a card, its specs and its notes read them again.
        self._route_facets: dict[int, RouteFacets] = {}
        # The documents as the searchable catalog, built on first use and dropped whenever a
        # read adds the ERP rows behind them (``catalog``).
        self._catalog: Catalog | None = None
        self._departures: dict[int, DepartureRecord] = {}
        self._quotes: dict[int, Quote | None] = {}
        # Each 线路's baseline 行程 once it has been read, ``None`` for one that has none, so a
        # second details read of the same 线路 downloads and parses nothing. One lock per 线路
        # keeps two conversations opening it at the same moment to one reading between them.
        self._itineraries: dict[int, Itinerary | None] = {}
        self._itinerary_locks: dict[int, asyncio.Lock] = {}
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
        stated = bool(attributes.get("depart_from") or attributes.get("depart_to"))
        # Stated backwards, the two dates are still the span the advisor means.
        if depart_to < depart_from:
            depart_from, depart_to = depart_to, depart_from
        return SearchContext(
            depart_from=depart_from,
            depart_to=depart_to,
            adults=max(1, _int_of(attributes.get("adults"), DEFAULT_ADULTS)),
            children=max(0, _int_of(attributes.get("children"), DEFAULT_CHILDREN)),
            child_ages=_ages_of(attributes.get("child_ages")),
            stated=stated,
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
            cached = normalize(_raw_tags(record), record.price_tags, name=record.route_name)
            self._route_facets[record.route_id] = cached
        return cached

    async def _routes_for(
        self, erp: ErpClient, text: str, window: tuple[date, date], fetched: Fetched | None
    ) -> list[RouteRecord]:
        """The ERP's catalog rows for one query text over one window, kept for the rest of the
        run that asked. An advisor's search never comes here — the documents are the catalog —
        but the boot snapshot and a pasted id do, and the broad read is this call with no
        name."""
        key = (text, *window)
        cached = fetched.routes.get(key) if fetched is not None else None
        if cached is None:
            cached = await erp.search_routes(
                RouteQuery(route_name=text, depart_from=window[0], depart_to=window[1])
            )
            self._routes.update({record.route_id: record for record in cached})
            if fetched is not None:
                fetched.routes[key] = cached
        return cached

    async def _listing_window(
        self, erp: ErpClient, window: tuple[date, date], fetched: Fetched
    ) -> dict[int, list[DepartureRecord]] | None:
        """Every 团期 the window holds, by route id, from one paged read the whole search run
        shares; ``None`` for a client that cannot read a window whole, whose departures are
        then read a route at a time. A search puts the sellable dates on every matched line's
        card, and the ERP's period list takes no route id, so a call per line is a fan-out the
        run cannot afford: this is the one call it makes instead.

        The read reaches a week past the window on each side, so a window the run has already
        read around answers for any narrower one, filtered by date here. Those pages are the
        ERP's slowest read, so one of them saved is worth more than every other call in the
        run."""
        if not isinstance(erp, WindowReader):
            return None
        grouped = fetched.windows.get(window)
        if grouped is not None:
            return grouped
        held = self._read_around(window, fetched)
        if held is None:
            read = self._window(
                window[0] - timedelta(days=WINDOW_PAD_DAYS),
                window[1] + timedelta(days=WINDOW_PAD_DAYS),
            )
            held = {}
            for row in sorted(await erp.list_window(*read), key=lambda row: row.depart_date):
                if row.route_id <= 0:
                    # A 团期 the ERP carries no ``routeId`` on belongs to no 线路 this side can
                    # name, price or book: it is a broken row, not a departure.
                    continue
                held.setdefault(row.route_id, []).append(row)
                # A listed row carries no price; a detail read already cached one, so keep it.
                self._departures.setdefault(row.period_id, row)
            fetched.windows[read] = held
        grouped = _inside(held, window)
        fetched.windows[window] = grouped
        return grouped

    def _read_around(
        self, window: tuple[date, date], fetched: Fetched
    ) -> dict[int, list[DepartureRecord]] | None:
        """A read this run already made that spans the whole of ``window``, if it made one."""
        spans = (
            rows
            for read, rows in fetched.windows.items()
            if read[0] <= window[0] and window[1] <= read[1]
        )
        return next(spans, None)

    async def _departs(
        self, erp: ErpClient, record: RouteRecord, window: tuple[date, date], fetched: Fetched
    ) -> bool:
        """Whether the route has a 团期 inside the window. ``route/list``'s own date filter is
        loose — it answers with routes that have none (``docs/erp-contract.md``) — and a card
        built from one of those carries no date, no seat count and no price. The window's read
        is what answers this, so no route costs a call of its own and one the read did not name
        simply has no departures; the record this route becomes reads the same rows again."""
        return bool(await self._list(erp, record, window, fetched))

    async def _listing_rows(
        self, erp: ErpClient, window: tuple[date, date], fetched: Fetched | None = None
    ) -> list[RouteRecord]:
        """Every 线路 the ERP sells inside the window, minus the ones not on general sale. This
        is not the advisor's search — that is the documents — but the boot snapshot's own read
        and the scan behind a pasted id, both of which want the rows the ERP carries.

        ``fetched`` is one run's own reads; with it, a row whose 线路 has no 团期 inside the
        window is dropped, because a snapshot record built from one carries no date, no seat
        count and no price. A caller that passes none reads no departures at all."""
        records = [
            record
            for record in await self._routes_for(erp, "", window, fetched)
            if not self._private.is_private(record)
        ]
        if fetched is None:
            return records
        return [record for record in records if await self._departs(erp, record, window, fetched)]

    async def _list(
        self,
        erp: ErpClient,
        record: RouteRecord,
        window: tuple[date, date],
        fetched: Fetched | None = None,
    ) -> list[DepartureRecord]:
        """One route's 团期 inside the window. Inside a search run they come out of the window's
        own read, which every route in the run shares; a caller with no run behind it — a route
        the advisor opened, a pasted id — asks the ERP for this route alone, which is the read
        whose seat counts are the freshest the process has."""
        key = (record.route_id, *window)
        if fetched is not None:
            if key in fetched.departures:
                return fetched.departures[key]
            grouped = await self._listing_window(erp, window, fetched)
            if grouped is not None:
                rows = grouped.get(record.route_id, [])
                fetched.departures[key] = rows
                return rows
        rows = await erp.list_departures(record.route_id, record.route_name, window[0], window[1])
        for row in rows:
            # A listed row carries no price; a detail read already cached one, so keep it.
            self._departures.setdefault(row.period_id, row)
        if fetched is not None:
            fetched.departures[key] = rows
        return rows

    async def _route(self, erp: ErpClient, route_id: int) -> RouteRecord | None:
        """The route record, from the search that returned it or, for an id this process has
        not seen, from one broad ERP search over the default window: an empty name is not a
        filter, so it brings back every route selling seats in the next couple of months. The
        window on ``route/list`` is a hint and not a filter both ways, so an id the windowed
        read still does not name is looked for once in the catalog with no window at all — a
        线路 the advisor pasted may sell nothing for months and is a record all the same.

        An id neither read names is remembered as missing, so the next read of it costs no
        further scan: nothing in a conversation adds a 线路 to the ERP, and a boot reload
        (``load_listings``) is what forgets that. Id 0 is not an id: a 团期 row that carries no
        ``routeId`` would otherwise cost the whole catalog on every read."""
        if route_id <= 0:
            return None
        if route_id in self._routes:
            return self._routes[route_id]
        if route_id in self._missing_routes:
            return None
        default = self._default_context()
        await self._listing_rows(erp, self._window(default.depart_from, default.depart_to))
        if route_id not in self._routes:
            whole = await erp.search_routes(RouteQuery())
            self._routes.update({record.route_id: record for record in whole})
        if route_id not in self._routes:
            self._missing_routes.add(route_id)
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

    async def _quote(self, erp: ErpClient, row: DepartureRecord) -> Quote | None:
        """This customer's 同业价 for one departure, asked in the departure's own department. A
        团期 the catalog has no price row for answers not-found, which is a gap in the catalog
        and not a failed call. With no customer resolved there is nobody to quote for, and the
        records fall back to the departure's 市场价 (``quote_source=list``)."""
        if self.customer_id <= 0:
            return None
        try:
            return await erp.quote(row.period_id, self.customer_id, self._department(row))
        except ErpNotFound:
            return None

    async def _price_one(
        self, erp: ErpClient, row: DepartureRecord, gate: asyncio.Semaphore
    ) -> None:
        """One departure's two prices, kept in the caches the records read through: the 市场价
        and the seat counts from its detail, then the 同业价 the order would be booked at. A row
        the ERP cannot detail keeps no 市场价, and one it has no price row for keeps no 同业价;
        the record then says which of the two its numbers are."""
        async with gate:
            if self._priced(row) is None:
                detailed = await erp.get_departure(row.period_id)
                if detailed is not None:
                    self._departures[detailed.period_id] = detailed
            if row.period_id not in self._quotes:
                self._quotes[row.period_id] = await self._quote(erp, row)

    async def _prices(
        self,
        erp: ErpClient,
        rows: list[DepartureRecord],
        middle: date,
        limit: int,
        gate: asyncio.Semaphore | None = None,
    ) -> None:
        """Both prices for at most ``limit`` of the departures, nearest the middle of the
        window first, since that is where the advisor is working. The ERP prices one departure
        per call, so the calls run a few at a time rather than one after another. ``gate`` is a
        caller's own semaphore: a shortlist prices every route's departures under one, so the
        routes are not priced one route after another."""
        by_distance = sorted(rows, key=lambda row: abs((row.depart_date - middle).days))
        nearest = by_distance[: max(0, limit)]
        gate = gate or asyncio.Semaphore(PRICE_CONCURRENCY)
        await asyncio.gather(*(self._price_one(erp, row, gate) for row in nearest))

    # -- catalog records -----------------------------------------------------------------

    def _family(
        self, record: RouteRecord, rows: list[DepartureRecord], context: SearchContext
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
            attributes={
                **_route_attributes(record, facets),
                **_doc_attributes(self._doc(record)),
                **({"line_type": kind} if (kind := self._private.line_type(record)) else {}),
            },
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
                row,
                price,
                market,
                source,
                context.adults,
                context.children,
                quote.price_type if quote is not None else "",
            ),
            in_stock=row.available_seats >= party,
            short_description=_variant_summary(
                row, price, market, source, context.adults, context.children
            ),
            option_values={"depart_date": row.depart_date.isoformat()},
            variant_of=route_id_of(row.route_id),
        )

    # -- the catalog the advisor searches -------------------------------------------------

    def catalog(self) -> Catalog:
        """The 线路文档 as one searchable catalog (``api/catalog.py``), built once over the
        documents and the ERP catalog rows this process has read. A search that finds a row
        missing reads the catalog again and drops this, so the 起价 on a card is the ERP's."""
        if self._catalog is None:
            self._catalog = Catalog(self._route_docs, self._routes)
        return self._catalog

    def facts(self, route_id: int) -> RouteFacts | None:
        """One 线路 as the catalog reads it, or None for a 线路 with no document."""
        return self.catalog().get(route_id)

    def route_facts(self, product_id: str) -> RouteFacts | None:
        """The document behind a card's id (``RT-34``), as the catalog reads it; None for an
        id that is not a 线路's, or a 线路 the agency has no document for. The 逐日行程 card
        reads this."""
        route_id = _erp_id(product_id, ROUTE_PREFIX)
        return None if route_id is None else self.facts(route_id)

    async def departures_view(
        self,
        session: ShoppingSessionContext,
        product_id: str,
        depart_from: date | None = None,
        depart_to: date | None = None,
    ) -> DeparturesView | None:
        """One 线路's 团期 inside a window, priced for the searched party: the window the
        advisor stated on this call, else the one the conversation is working in, else this
        backend's default. The 团期 nearest the middle of the window come first, capped at
        ``MAX_DEPARTURE_ITEMS``, because that is where the advisor is working."""
        route_id = _erp_id(product_id, ROUTE_PREFIX)
        facts = self.facts(route_id) if route_id is not None else None
        if route_id is None or facts is None:
            return None
        erp = self._erp_for(session)
        record = await self._route(erp, route_id)
        if record is None:
            return None
        context = self._context(session)
        window = self._window(
            depart_from or context.depart_from,
            depart_to or (context.depart_to if depart_from is None else self._latest(depart_from)),
        )
        rows = sorted(await self._list(erp, record, window), key=lambda row: row.depart_date)
        middle = window[0] + (window[1] - window[0]) / 2
        if len(rows) > MAX_DEPARTURE_ITEMS:
            nearest = sorted(rows, key=lambda row: abs((row.depart_date - middle).days))
            rows = sorted(nearest[:MAX_DEPARTURE_ITEMS], key=lambda row: row.depart_date)
        await self._prices(erp, rows, middle, MAX_DEPARTURE_ITEMS)
        party = max(context.adults + context.children, 1)
        return DeparturesView(
            route=self._card(facts, rows, replace(context, stated=True), window),
            window=window,
            items=[
                DepartureItem(
                    departure=(variant := self._variant(row, record, context)),
                    date=row.depart_date,
                    status=self._departure_state(row, party),
                    price_adult=variant.price if variant.price > 0 else None,
                )
                for row in rows
            ],
        )

    def _latest(self, start: date) -> date:
        """Where a window that states only its start ends: the default window's own length."""
        return start + timedelta(days=DEFAULT_WINDOW_DAYS)

    async def _matches(self, erp: ErpClient, stated: Request) -> list[RouteFacts]:
        """The documents meeting the request, minus the lines this advisor may not offer. A
        matched 线路 whose ERP row this process has never read costs one catalog read — the
        row carries the 起价 and the cover photo — and the ids that read does not name are
        remembered, so the next search asks for none of them again."""
        matches = self.catalog().match(stated)
        unread = [
            facts.route_id
            for facts in matches
            if facts.route_id not in self._routes and facts.route_id not in self._missing_routes
        ]
        if unread:
            await self._route(erp, unread[0])
            self._missing_routes.update(id for id in unread if id not in self._routes)
            self._catalog = None
            matches = self.catalog().match(stated)
        return [facts for facts in matches if self._offerable(facts, stated)]

    def _offerable(self, facts: RouteFacts, stated: Request) -> bool:
        """Whether a search may offer the line: any line on general sale, and a private one
        (包团, 会销, 定制) only where the advisor named it in full."""
        record = self._routes.get(facts.route_id)
        if record is None or not self._private.is_private(record):
            return True
        # The 会销 its own salesperson opens by name is theirs to see, and nobody else's 欧洲
        # shortlist: it is offered only where the advisor wrote the line's name in full.
        names = {record.route_name.strip(), facts.name.strip()}
        return any(named in names for named in stated.named)

    async def _line_dates(
        self,
        erp: ErpClient,
        matches: list[RouteFacts],
        window: tuple[date, date],
        fetched: Fetched,
        *,
        stated: bool,
        limit: int,
        chosen: tuple[date, ...] = (),
    ) -> LineDates:
        """Which of the matched lines run in the advisor's dates, and for the ones that do not,
        the nearest date they do run.

        A stated window is a condition and not a decoration: a card that said nothing about the
        dates the advisor asked for would leave them to find out one 线路 at a time. So every
        match is weighed against the window, and the lines with no 团期 in it are looked up once
        more over the ``NEAREST_WINDOW_DAYS`` after it — one read for all of them together,
        like the first. A client that reads a window whole (``WindowReader``) answers both from
        two paged reads; one that cannot is asked per line, which the catalog's size bounds.

        ``chosen`` are the months the advisor tapped, which need not run together: the read is
        the span they cover and a 团期 outside any of them is not inside the window.

        With no window stated there is nothing to weigh: the 团期 are read for the lines the
        cards will carry, and the months are then counted off those alone."""
        inside = await self._window_rows(
            erp, matches if stated else matches[:limit], window, fetched, months=chosen
        )
        if not stated:
            return LineDates(inside=inside, nearest={}, read_all=len(inside) == len(matches))
        missing = [facts for facts in matches if not inside.get(facts.route_id)]
        nearest: dict[int, date] = {}
        read = {route_id: [row.depart_date for row in rows] for route_id, rows in inside.items()}
        if missing:
            later = self._window(window[0], window[0] + timedelta(days=NEAREST_WINDOW_DAYS))
            found = await self._window_rows(erp, missing, later, fetched)
            for facts in missing:
                dates = sorted(row.depart_date for row in found.get(facts.route_id, ()))
                read[facts.route_id] = dates
                if dates:
                    nearest[facts.route_id] = dates[0]
        return LineDates(inside=inside, nearest=nearest, read_all=True, months=read)

    async def _window_rows(
        self,
        erp: ErpClient,
        matches: list[RouteFacts],
        window: tuple[date, date],
        fetched: Fetched,
        months: tuple[date, ...] = (),
    ) -> dict[int, list[DepartureRecord]]:
        """Each of these lines' 团期 inside one window, from the window's own paged read where
        the client has one and a read per line where it has not; a line with none has no entry.
        ``months`` keeps only the 团期 inside the months the advisor tapped, which is what makes
        10 月 and 12 月 two months rather than the whole of the autumn."""
        wanted = {(month.year, month.month) for month in months}
        grouped = await self._listing_window(erp, window, fetched)
        rows: dict[int, list[DepartureRecord]] = {}
        for facts in matches:
            if grouped is not None:
                found = grouped.get(facts.route_id, [])
            else:
                record = self._routes.get(facts.route_id)
                found = await self._list(erp, record, window, fetched) if record is not None else []
            if wanted:
                found = [
                    row for row in found if (row.depart_date.year, row.depart_date.month) in wanted
                ]
            if found:
                rows[facts.route_id] = sorted(found, key=lambda row: row.depart_date)
        return rows

    def _card(
        self,
        facts: RouteFacts,
        rows: list[DepartureRecord],
        context: SearchContext,
        window: tuple[date, date],
        nearest: date | None = None,
    ) -> Product:
        """One 线路 as a card: every fact the agency's own document states, and what the line
        sells in the advisor's dates beside them. A line that runs in them carries the 团期 as
        dates and states (seats and prices are the 团期卡's, because a card that quoted them
        would be quoting a window the customer has not chosen in); one that runs in none of
        them says so — ``match=adjacent_date``, an empty ``departures`` and the nearest date it
        does run — because the advisor may still sell that date and has to be told."""
        record = self._routes.get(facts.route_id)
        party = max(context.adults + context.children, 1)
        attributes = _facts_attributes(facts)
        dates = [row.depart_date for row in rows]
        if context.stated:
            attributes["departures"] = "|".join(
                f"{row.depart_date.isoformat()}:{self._departure_state(row, party)}" for row in rows
            )
            attributes["departures_window"] = f"{window[0].isoformat()}..{window[1].isoformat()}"
            if not rows:
                attributes["match"] = "adjacent_date"
                attributes["nearest_departure"] = nearest.isoformat() if nearest else ""
                attributes["mismatch"] = _no_departures_note(window, nearest)
        return Product(
            product_id=route_id_of(facts.route_id),
            title=facts.name,
            brand=facts.department or self.store_name,
            price=facts.from_price,
            currency=CURRENCY,
            image_url=facts.image_url,
            category=CATEGORY,
            labels=_facts_labels(facts),
            attributes=attributes
            | (
                {"line_type": kind}
                if record is not None and (kind := self._private.line_type(record))
                else {}
            ),
            in_stock=not rows or any(row.available_seats >= party for row in rows),
            short_description=_facts_summary(facts),
            options={"depart_date": [day.isoformat() for day in dates]} if dates else {},
        )

    def _departure_state(self, row: DepartureRecord, party: int) -> str:
        """A 团期 as the advisor reads it off a card: 截止 once its date has passed, 满员 when
        the party does not fit into what is left, else the ERP's own 成团 state."""
        if row.depart_date < self.today:
            return "截止"
        if row.available_seats < party or _group_status(row) == "waitlist":
            return "满员"
        return "已成团" if _group_status(row) == "confirmed" else "可报名"

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        """The 线路 whose document meets the advisor's request. Everything but the dates is
        answered off the documents (``api/catalog.py``): ``destination`` is matched against a
        line's countries, its 线路系, its name, the department that sells it, the places its
        days pass through and the sights it names, and ``region``, ``days``,
        ``days_min``/``days_max``, ``departure_city``, ``hotel_level``, ``feature``,
        ``no_shopping``, ``price_min`` and ``price_max`` filter on what the document states. A
        线路 the agency has no document for is not searched at all, and a request the documents
        do not meet returns nothing rather than something near it — the chips beside it are
        then the catalog's own directions.

        The advisor answers a 聚焦卡 by tapping several chips at once, so ``destination``,
        ``region``, ``days``, ``departure_city``, ``hotel_level``, ``feature`` and ``months``
        each take several values joined by ``|``: inside one the values are alternatives, and
        between them they are conditions on each other.

        The ERP is asked for the dynamic half only: which 团期 each matched line runs in the
        window — the advisor's dates, or the months they tapped — so a card can carry the
        sellable dates once the advisor has given any."""
        attributes = dict(filters.attributes) if filters is not None else {}
        months = _months_of(attributes.get("months"))
        context = self._stated_context(session, attributes, months)
        stated = Request(
            depart_from=context.depart_from,
            depart_to=context.depart_to,
            destinations=_values(attributes.get("destination")) or _values(query.strip() or None),
            regions=_values(attributes.get("region")),
            days=_numbers(attributes.get("days")),
            days_min=_int_or_none(attributes.get("days_min")),
            days_max=_int_or_none(attributes.get("days_max")),
            departure_cities=_values(attributes.get("departure_city")),
            hotel_levels=_values(attributes.get("hotel_level")),
            features=_values(attributes.get("feature")),
            no_shopping=attributes.get("no_shopping", "").strip().lower() == "yes",
            family=attributes.get("family", "").strip().lower() == "yes",
            price_min=_int_or_none(attributes.get("price_min")),
            price_max=_int_or_none(attributes.get("price_max")),
            party=context.adults + context.children,
        )
        erp = self._erp_for(session)
        matches = await self._matches(erp, stated)
        if stated.family:
            # 亲子 is a preference and not a condition: the lines whose document claims it
            # come first, and the rest are still on the shortlist.
            matches.sort(key=lambda facts: "亲子" not in facts.feature_words)
        window = self._window(context.depart_from, context.depart_to)
        fetched = Fetched()
        dates = await self._line_dates(
            erp, matches, window, fetched, stated=context.stated, limit=limit, chosen=months
        )
        if context.stated:
            # The lines that run in the advisor's dates lead, whatever else ranked them: a line
            # the customer cannot travel on is not the first thing the advisor reads out.
            matches.sort(key=lambda facts: not dates.runs_in_window(facts.route_id))
        shown = matches[:limit]
        found = [
            self._card(
                facts,
                dates.rows(facts.route_id),
                context,
                window,
                dates.nearest.get(facts.route_id),
            )
            for facts in shown
        ]
        for product in found:
            product.attributes["catalog_matches"] = str(len(matches))
        self._note_overview(session, matches, dates, len(found), window if context.stated else None)
        return found

    def _stated_context(
        self,
        session: ShoppingSessionContext,
        attributes: dict[str, str],
        months: tuple[date, ...] = (),
    ) -> SearchContext:
        """The window and party this search quotes for. Months the advisor tapped are the
        window, from the first of the earliest to the last of the latest; otherwise it is the
        dates they stated. A search that states neither keeps what the conversation already
        stated, because a card carries the sellable 团期 only while the advisor has given the
        customer's dates at all."""
        context = self._read_context(attributes)
        if months:
            return self._keep(
                session,
                replace(
                    context,
                    depart_from=months[0],
                    depart_to=_month_end(months[-1]),
                    stated=True,
                ),
            )
        earlier = self._contexts.get(session.session_id)
        if not context.stated and earlier is not None and earlier.stated:
            context = replace(
                context,
                depart_from=earlier.depart_from,
                depart_to=earlier.depart_to,
                stated=True,
            )
        return self._keep(session, context)

    def _keep(self, session: ShoppingSessionContext, context: SearchContext) -> SearchContext:
        self._contexts[session.session_id] = context
        return context

    def _note_overview(
        self,
        session: ShoppingSessionContext,
        matches: list[RouteFacts],
        dates: LineDates,
        shown: int,
        window: tuple[date, date] | None,
    ) -> None:
        """What the executor hands the model beside the cards. An overview stands while the
        set is wider than a shortlist, while the handful that matched are one trip sold several
        ways, and when nothing matched at all — and in the last case it is the whole catalog's
        own groups, so the model can offer the customer another direction. ``window`` is the
        advisor's dates where they stated any: the head then says how many of the matches run
        in them. A search made after a 聚焦卡 is the advisor's answer to it and its overview
        says so: cards now, and the question a short line beside them."""
        catalog = self.catalog()
        answered = session.session_id in self._focused
        self._focused.discard(session.session_id)
        months = dates.month_dates() if dates.read_all else None
        ambiguous = catalog.ambiguous(matches)
        dimensions = tuple(catalog.differences(matches)) if ambiguous else ()
        if not matches:
            whole = catalog.all()
            self._overviews[session.session_id] = Overview(
                total=0, shown=0, groups=catalog.chips(whole, year=self.today.year), empty=True
            )
            return
        if len(matches) <= OVERVIEW_ABOVE and not ambiguous:
            self._overviews.pop(session.session_id, None)
            return
        self._overviews[session.session_id] = Overview(
            total=len(matches),
            shown=shown,
            groups=catalog.chips(matches, months, first=dimensions, year=self.today.year),
            answered=answered,
            ambiguous=ambiguous,
            dimensions=dimensions,
            window=_window_label(window) if window is not None else "",
            with_dates=sum(1 for facts in matches if dates.runs_in_window(facts.route_id)),
        )

    def overview(self, session_id: str) -> Overview | None:
        """The last search's overview, when that search matched more than it could show."""
        return self._overviews.get(session_id)

    def note_focused(self, session_id: str) -> None:
        """A 聚焦卡 was put to the advisor over the standing overview: the next search is
        their answer."""
        if session_id in self._overviews:
            self._focused.add(session_id)

    # -- details -------------------------------------------------------------------------

    def _details(
        self, record: RouteRecord | None, family: Product, itinerary: Itinerary | None
    ) -> ProductDetails:
        """One 线路's family, or one of its 团期, as a details record: the route's own free
        text, the specs its tags normalise into — both read off the record and costing no call
        — and the baseline 行程 the caller read, day by day. The variants, where the caller has
        any, are the caller's to fill in."""
        if record is None:
            return ProductDetails(**family.model_dump(), long_description=None, specs={})
        return ProductDetails(
            **family.model_dump(),
            long_description="\n".join(record.features)[:1200] or None,
            specs=_specs(record, self._facets(record))
            | _doc_specs(self._doc(record))
            | _itinerary_specs(record, itinerary, self._doc(record)),
        )

    def _doc(self, record: RouteRecord) -> RouteDoc | None:
        return self._route_docs.get(record.route_id)

    async def _itinerary(self, erp: ErpClient, record: RouteRecord) -> Itinerary | None:
        """The 线路's baseline 行程, read once per process: the ERP's own days where it has
        them, the 行程附件 parsed where it does not. The lock is what keeps two conversations
        opening the same 线路 at once to one download between them."""
        if record.route_id in self._itineraries:
            return self._itineraries[record.route_id]
        async with self._itinerary_locks.setdefault(record.route_id, asyncio.Lock()):
            if record.route_id not in self._itineraries:
                self._itineraries[record.route_id] = await self._read_itinerary(erp, record)
            return self._itineraries[record.route_id]

    async def _read_itinerary(self, erp: ErpClient, record: RouteRecord) -> Itinerary | None:
        """One reading of a 线路's 行程. Nothing here raises: the attachment is a file on a
        server neither this host nor the ERP owns, and a details read the advisor is waiting on
        must not fail because it is unreachable, oversized or not a readable .docx — such a
        route reads 行程来源：无 and the advisor opens the attachment themselves."""
        doc = self._doc(record)
        if doc is not None and doc.days:
            return itinerary_of(doc)
        try:
            found = await erp.get_itinerary(record.route_id)
        except ErpError as error:
            log.warning("tour: route %s itinerary: %s", record.route_id, error)
            found = None
        if found is not None or not record.attachment_url:
            return found
        url = record.attachment_url
        cached = cache_read(self._state_dir, record.route_id, url) if self._state_dir else None
        try:
            fetched = await fetch_attachment(url, etag=cached[1] if cached else None)
            if fetched is None:
                # Unchanged since the cached reading, or unreachable; either way, what is
                # already known about this 线路 is the best answer there is.
                return cached[0] if cached else None
            days = parse_docx(fetched[0])
        except (ValueError, OSError) as error:
            log.warning("tour: route %s attachment not read: %s", record.route_id, error)
            return cached[0] if cached else None
        if not days:
            log.warning("tour: route %s attachment names no 第N天", record.route_id)
            return None
        itinerary = Itinerary(record.route_id, "attachment", fetched[1], tuple(days))
        if self._state_dir:
            try:
                cache_write(self._state_dir, itinerary, url, fetched[1])
            except OSError as error:
                log.warning("tour: route %s itinerary not cached: %s", record.route_id, error)
        return itinerary

    async def _route_details(
        self,
        erp: ErpClient,
        route_id: int,
        context: SearchContext,
        *,
        prices: int = MAX_DETAIL_PRICES,
    ) -> ProductDetails | None:
        record = await self._route(erp, route_id)
        if record is None:
            return None
        window = self._window(
            context.depart_from - timedelta(days=DETAIL_PAD_DAYS),
            context.depart_to + timedelta(days=DETAIL_PAD_DAYS),
        )
        rows = await self._list(erp, record, window)
        if not rows:
            # The searched window holds no 团期 of the route the advisor named. Quote the
            # default window instead, once: the dates say the route runs elsewhere, which
            # the advisor can act on, and a family with no variants does not.
            default = self._default_context()
            window = self._window(default.depart_from, default.depart_to)
            rows = await self._list(erp, record, window)
        middle = window[0] + (window[1] - window[0]) / 2
        if len(rows) > MAX_VARIANTS:
            nearest = sorted(rows, key=lambda row: abs((row.depart_date - middle).days))
            rows = nearest[:MAX_VARIANTS]
        rows = sorted(rows, key=lambda row: row.depart_date)
        await self._prices(erp, rows, middle, prices)
        family = self._family(record, rows, context)
        details = self._details(record, family, await self._itinerary(erp, record))
        details.variants = [self._variant(row, record, context) for row in rows]
        return details

    async def _departure_details(
        self, erp: ErpClient, period_id: int, context: SearchContext
    ) -> ProductDetails | None:
        """The one departure, in the same two calls a route's variants take: its detail for the
        市场价 and the seat counts, and its 同业价 for the customer this deployment books for. A
        departure the ERP has no price row for keeps the 市场价 it carries. It reads its 线路's
        baseline 行程 as a route's details do, out of the same per-process cache."""
        row = await erp.get_departure(period_id)
        if row is None:
            return None
        self._departures[row.period_id] = row
        self._quotes[row.period_id] = await self._quote(erp, row)
        record = await self._route(erp, row.route_id)
        variant = self._variant(row, record, context)
        itinerary = await self._itinerary(erp, record) if record else None
        return self._details(record, variant, itinerary)

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        context = self._context(session)
        route_id = _erp_id(product_id, ROUTE_PREFIX)
        if route_id is not None:
            return await self._route_details(self._erp_for(session), route_id, context)
        period_id = _erp_id(product_id, DEPARTURE_PREFIX)
        if period_id is not None:
            return await self._departure_details(self._erp_for(session), period_id, context)
        return None

    async def attachment(
        self, session: ShoppingSessionContext, product_id: str
    ) -> tuple[str, str] | None:
        """The 行程附件 behind a 线路 or one of its 团期, as the name the agency gave the file and
        the store's URL; None for an id that is neither, or a 线路 with no attachment. Read on the
        session advisor's own client, like every other record."""
        erp = self._erp_for(session)
        route_id = _erp_id(product_id, ROUTE_PREFIX)
        if route_id is None:
            period_id = _erp_id(product_id, DEPARTURE_PREFIX)
            if period_id is None:
                return None
            route_id = (await erp.get_departure(period_id)).route_id
        record = await self._route(erp, route_id)
        if record is None or not record.attachment_url:
            return None
        name = record.attachment_name or record.attachment_url.rsplit("/", 1)[-1]
        return name, record.attachment_url

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

    def _login_for(self, session: ShoppingSessionContext) -> AdvisorLogin | None:
        """The session advisor's own ERP login, or ``None`` when they have none: nobody signed
        in on this process, the token has run out, or there is no registry at all."""
        return self.registry.get(session.user_id) if self.registry is not None else None

    def _erp_for(self, session: ShoppingSessionContext) -> ErpClient:
        """The ERP client this session's calls go out on: the advisor's own, on the token their
        login bought. Against the agency's own ERP, where the host logs each advisor in, that is
        the only client there is — a session whose advisor has no live login is told to sign in
        again, and the executor relays that into the conversation. The fixtures hold no tokens,
        so every session reads through the one client the host built, and so does a backend
        built with no registry at all."""
        login = self._login_for(session)
        if login is not None:
            return login.client
        if self.live and self.registry is not None:
            raise ErpAuth(NEED_LOGIN)
        return self.erp

    async def _identify(self) -> dict[str, Any]:
        """The salesperson the deployment's own client logged in, which is who an advisor with
        no login of their own falls back to. It is empty until that client has made its first
        call, so a client that can log in on demand is asked to; the fixtures log nobody in and
        answer nothing here."""
        info = getattr(self.erp, "user_info", None) or {}
        identify = getattr(self.erp, "identify", None)
        if not info and identify is not None:
            info = await identify() or {}
        return dict(info)

    def _contact_mobile(self, session: ShoppingSessionContext) -> str:
        """The 联系人 mobile on the order: the one this advisor logged in with, and the
        deployment's own where there is no login to read it off."""
        login = self._login_for(session)
        return (login.mobile if login is not None else "") or self.contact_mobile

    async def _contact_name(self, session: ShoppingSessionContext) -> str:
        """Who the ERP writes on the order: the salesperson whose login this session is, then
        the account the client itself logged in as, and — on the fixtures, which log nobody in
        — the advisor's own name from the profile. The fixture name is never written onto a
        real order, so a live client that names nobody refuses the write instead."""
        login = self._login_for(session)
        if login is not None and login.name:
            return login.name
        logged_in = str((await self._identify()).get("userName") or "")
        if logged_in:
            return logged_in
        if self.live:
            raise NotOffered("未取到 ERP 登录人姓名，暂时无法写入订单联系人")
        display = self._advisor(session).display_name or session.user_id
        return display.split("（")[0]

    def _store_name(self, session: ShoppingSessionContext) -> str:
        """The 门店 a 同行 order is written through. Against the agency's own ERP it is
        ``TOUR_ERP_STORE_NAME`` and nothing else: the fixture profile's 门店 is one this
        example invented, and an order must not be written through it. On the fixtures the
        profile states the 门店 and then what it sells, and the store is the first clause."""
        if self.live:
            return self.order_store_name
        profile = self._advisor(session)
        return profile.preferences.get("门店", "").split("，")[0] or self.store_name

    async def _line(self, erp: ErpClient, hold: Hold, order: OrderRecord) -> CartItem:
        row = self._departures.get(hold.period_id)
        if row is None:
            row = await erp.get_departure(hold.period_id)
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
        """The conversation's own 预留 orders as they stand in the ERP. A conversation that
        wrote none costs no call and needs no login: the advisor whose token has run out is
        told so by the read that needs one, not by an empty cart."""
        holds = self._live_holds(session.session_id)
        if not holds:
            return Cart(items=[], currency=CURRENCY)
        erp = self._erp_for(session)
        items = []
        standing = []
        for hold in holds:
            order = await erp.get_order(hold.order_id)
            if order is None:
                continue  # written, then removed inside the ERP: it is no longer a line
            hold.order_no = order.order_no
            standing.append(hold)
            items.append(await self._line(erp, hold, order))
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
        if self.customer_id <= 0:
            raise NotOffered("未配置下单客户（TOUR_ERP_CUSTOMER_CODE）")
        if self.live and not self.order_store_name:
            raise NotOffered("未配置下单门店（TOUR_ERP_STORE_NAME）")
        erp = self._erp_for(session)
        row = self._departures.get(period_id) or await erp.get_departure(period_id)
        if row is None:
            raise ErpNotFound(f"找不到该团期：{period_id}")
        self._departures[period_id] = row
        context = self._context(session)
        heads = max(1, quantity)
        children = min(context.children, heads - 1)
        result = await erp.create_order(
            OrderRequest(
                period_id=period_id,
                customer_id=self.customer_id,
                company_id=self._department(row),
                adults=heads - children,
                children=children,
                elders=0,
                rooms=0,
                single_room_diff_count=0,
                contact_name=await self._contact_name(session),
                contact_mobile=self._contact_mobile(session),
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
        """Who the advisor is. Against the agency's own ERP that is their own login — the
        salesperson's name and the department it landed in — and nothing else: the habits in
        ``users.json`` are this example's own invention, and a real advisor's belong in memory,
        which they write themselves. The fixtures answer with the profile."""
        if not self.live:
            return self._advisor(session)
        login = self._login_for(session)
        if login is not None:
            name, department = login.name, login.department
        else:
            user_info = await self._identify()
            name = str(user_info.get("userName") or "")
            department = str(user_info.get("companyName") or "")
        return UserPreferences(
            user_id=session.user_id,
            display_name=name or None,
            loyalty_tier=department or None,
            default_location=department or None,
            preferences={},
        )

    async def get_account_context(self, session: ShoppingSessionContext) -> dict[str, Any] | None:
        """``advisor`` is the salesperson whose login this session is, ``department`` the one
        that login landed in and ``departments`` how many the account reads across; a session
        with no login behind it — the fixtures — reads the client's own account, and one
        department, the store's own. ``customer`` is the 同行 customer every quote and order is
        made for, once the code has resolved to one."""
        login = self._login_for(session)
        if login is not None:
            name, department, departments = login.name, login.department, login.departments
        else:
            user_info = await self._identify()
            name = str(user_info.get("userName") or "")
            department = str(user_info.get("companyName") or "")
            departments = len(getattr(self.erp, "companies", None) or ()) or 1
        return {
            "advisor": name or session.user_id,
            "store": self._store_name(session),
            "department": department or self.store_name,
            "departments": departments,
            "customer": self.customer_label,
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
        """The salesperson's own orders: the login behind the session names one, so there is no
        id to filter on here."""
        return [self._order(record) for record in await self._erp_for(session).list_orders()][
            :limit
        ]

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        """One order by the ERP's own id, or by the 订单号 the advisor reads off a screen."""
        erp = self._erp_for(session)
        numeric = _int_or_none(order_id)
        if numeric is not None:
            record = await erp.get_order(numeric)
            return None if record is None else self._order(record)
        listed = [row for row in await erp.list_orders() if row.order_no == order_id]
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
        """Fill ``products`` with one record per route in the default window. This is a boot
        snapshot for the host's catalog routes and the demo's listing pages; the agent's own
        tools call the ERP on every turn, so seats, prices, and party quotes in the
        conversation are live and these are not.

        On the fixtures a record is the whole route: its ``variants`` are the departures in
        the window, priced. Against a live ERP a detail read per route is a round trip per
        线路, and the workbench's home page reads this snapshot whole to name the directions
        the catalog sells — so a live snapshot is the families alone, out of the reads one
        search already makes: the route list, and the window's 团期 read once for every route
        in it. It costs no call per route and carries no variant, no 行程 and no price beyond
        the route's 起价; a 团期 id the host asks for is read from the ERP by the conversation
        instead. An ERP that refuses or is down leaves the snapshot empty rather than stopping
        the host from booting. This is also where the deployment's 同行 customer is resolved,
        because that costs one ERP call too."""
        context = self._default_context()
        window = self._window(context.depart_from, context.depart_to)
        await self._resolve_customer()
        # A reload reads the catalog again, so a route the last read did not carry is worth
        # looking for again.
        self._missing_routes.clear()
        listings: dict[str, ProductDetails] = {}
        variants: dict[str, ProductDetails] = {}
        try:
            if self.live:
                fetched = Fetched()
                for record in await self._listing_rows(self.erp, window, fetched):
                    rows = await self._list(self.erp, record, window, fetched)
                    family = self._family(record, rows, context)
                    snapshot = self._details(record, family, None)
                    # No 行程 either, for the same reason there is no variant and no price: it
                    # is a read per 线路, and a 行程附件 is a download on top of it. A snapshot
                    # record says nothing about the 行程 rather than saying there is none.
                    snapshot.specs.pop("行程来源", None)
                    listings[family.product_id] = snapshot
            else:
                for record in await self._listing_rows(self.erp, window):
                    details = await self._route_details(self.erp, record.route_id, context)
                    if details is None:
                        continue
                    listings[details.product_id] = details
                    for variant in details.variants:
                        variants[variant.product_id] = ProductDetails(**variant.model_dump())
        except ErpError as error:
            log.warning("tour listings not loaded: %s: %s", type(error).__name__, error)
            listings, variants = {}, {}
        self.products, self._variants = listings, variants

    async def _resolve_customer(self) -> None:
        """The 同行 customer this deployment books for, by the ``csCode`` the environment
        names. Only a code resolving to exactly one customer is one: an unset code, a code the
        book does not hold and a code several customers share all leave the backend with no
        customer, which is an error in the deployment and not in the conversation — the reads
        all still work, quotes fall back to the 市场价, and ``add_to_cart`` says which variable
        is missing. The fixtures book for the id they were built with and resolve nothing."""
        if not self.live:
            return
        code = self.customer_code.strip()
        if not code:
            log.error(
                "tour: TOUR_ERP_CUSTOMER_CODE is unset; no 同行 customer to quote or book for"
            )
            self.customer_id, self.customer_label = 0, ""
            return
        try:
            found = [row for row in await self.erp.search_customers(code) if row.code == code]
        except ErpError as error:
            log.error("tour: customer %s not resolved: %s: %s", code, type(error).__name__, error)
            found = []
        if len(found) != 1:
            log.error(
                "tour: TOUR_ERP_CUSTOMER_CODE=%s matched %d customers, not one", code, len(found)
            )
            self.customer_id, self.customer_label = 0, ""
            return
        self.customer_id = found[0].customer_id
        self.customer_label = f"{found[0].code} {found[0].name}".strip()

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

    # -- the 定制方案 the advisor builds on a published 线路 ---------------------------------

    def _plan_store(self) -> SqliteSessionStore:
        """Where the plans are kept. A deployment that configured none builds no plan, rather
        than one that is gone at the next restart."""
        if self.plans is None:
            raise PresentationRefused(NO_PLAN_STORE)
        return self.plans

    async def create_plan(
        self, session: ShoppingSessionContext, route: Product, departure: Product | None
    ) -> Plan:
        """A plan on one published 线路, with the 团期 the advisor opened as its baseline. The
        line's own kind rides along, because a 包团 or 定制 baseline is not on general sale and
        the 计调 has to read that off the plan."""
        store = self._plan_store()
        if len(store.plans_for_session(session.session_id)) >= MAX_PLANS_PER_SESSION:
            raise PresentationRefused(TOO_MANY_PLANS)
        route_id = _erp_id(route.product_id, ROUTE_PREFIX)
        if route_id is None:
            raise PresentationRefused(f"{route.product_id} 不是线路编号，定制方案要建在 RT- 上。")
        plan = Plan(
            plan_id=new_plan_id(),
            session_id=session.session_id,
            user_id=session.user_id,
            route_id=route_id,
            route_name=route.title,
            line_type=route.attributes.get("line_type") or None,
            departure_id=(
                None if departure is None else _erp_id(departure.product_id, DEPARTURE_PREFIX)
            ),
        )
        store.create_plan(plan)
        return plan

    async def add_plan_version(
        self,
        session: ShoppingSessionContext,
        plan: Plan,
        days: list[PlanDay],
        *,
        title: str,
        travel_dates: str | None,
        party: str | None,
        reference_price: ReferencePrice | None,
        base_version: int | None,
    ) -> tuple[PlanVersion, list[DayDiff]]:
        """One card as the next version of ``plan``, with its reading of the version it was
        written against — the latest, or the one ``base_version`` names. The number is taken
        and checked inside the store's own write, so two turns of one conversation racing to
        revise the same plan do not both become v3: the loser numbers again against what the
        winner wrote, and gives up rather than replacing the card the winner sent."""
        store = self._plan_store()
        if plan.session_id != session.session_id:
            raise PresentationRefused(FOREIGN_PLAN)
        for _ in range(2):
            latest = store.latest_version(plan.plan_id)
            if latest >= MAX_PLAN_VERSIONS:
                raise PresentationRefused(TOO_MANY_VERSIONS)
            parent, parent_days = None, None
            if latest:
                parent = base_version or latest
                stored = store.version(plan.plan_id, parent)
                if stored is None:
                    raise PresentationRefused(NO_SUCH_VERSION.format(version=parent))
                parent_days = stored[0].days
            diff = diff_days(parent_days, days)
            version = PlanVersion(
                plan_id=plan.plan_id,
                version=latest + 1,
                parent_version=parent,
                title=title,
                travel_dates=travel_dates,
                party=party,
                days=days,
                reference_price=reference_price,
                share_token=new_share_token(),
            )
            try:
                store.add_version(version, diff)
            except PlanConflictError:
                continue
            return version, diff
        raise PresentationRefused(PLAN_BUSY)

    async def link_plan_route(
        self, session: ShoppingSessionContext, plan_id: str, erp_route_id: str
    ) -> Plan:
        """Record the 线路 the agency built in the ERP for this plan. It is the plan's and not
        a version's, so nothing is versioned here."""
        store = self._plan_store()
        plan = store.plan(plan_id)
        if plan is None or plan.session_id != session.session_id:
            raise PresentationRefused(FOREIGN_PLAN)
        route_id = _erp_id(erp_route_id, ROUTE_PREFIX)
        if route_id is None:
            raise PresentationRefused(f"{erp_route_id} 不是线路编号，请用 ERP 里的 RT- 编号。")
        store.link_route(plan_id, route_id)
        return plan.model_copy(update={"erp_route_id": route_id})

    def plan_share_url(self, token: str) -> str:
        """The customer's link to one version. The token stands for the version, so a customer
        sent v2 keeps reading v2 after the advisor sends v3."""
        base = os.environ.get("TOUR_SHARE_BASE_URL", DEFAULT_SHARE_BASE_URL).rstrip("/")
        return f"{base}/p/{token}"

    def plan_of(self, plan_id: str) -> Plan | None:
        return None if self.plans is None else self.plans.plan(plan_id)

    def plan_versions(self, plan_id: str) -> list[tuple[PlanVersion, list[DayDiff]]]:
        return [] if self.plans is None else self.plans.versions(plan_id)

    def plan_for_token(self, token: str) -> tuple[Plan, PlanVersion, list[DayDiff]] | None:
        return None if self.plans is None else self.plans.version_by_token(token)

    def advisor_name(self, user_id: str) -> str:
        """Who the customer's page says made their plan: the name the ERP login carries, the
        fixture profile's where there is no login, and nothing at all for an id neither
        knows."""
        login = None if self.registry is None else self.registry.get(user_id)
        if login is not None:
            return login.name
        profile = self._users.get(user_id)
        return (profile.display_name or "") if profile is not None else ""

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
