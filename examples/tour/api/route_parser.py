# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A 线路's .docx 行程附件 read into a ``RouteDoc``.

``itinerary_source`` reads the document's lines and splits them into days with a title, a
programme, a 住宿 and a 用餐; this module reads further into each of those and into the
front matter and the terms around them: the 参考航班 segments, the A-B-C places and KM figures
of a title, the 【景点】 with what the words around them say (含门票, 外观, 购物, 自费, 赠送),
the three meals and whether each is included, the hotel names and 或同级, the 包含/不含 lists,
the 购物店 and 自费项目, and the sentences the terms write about 单房差, 儿童, 签证 and 退改.

Everything is read by rule, so the reading is deterministic and testable and never invents a
field; where a rule finds nothing the field stays empty and ``needs_review`` says so. The two
production layouts (``itinerary_source``'s docstring) are the ones the rules were written
against; a document in a third layout yields fewer fields and a lower ``completeness``, which
is the signal to look at it. ``score`` is the completeness the batch ranks the catalog by."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from .erp_client import ItineraryDay, RouteRecord
from .itinerary_source import (
    CELL_SEPARATOR,
    PARAGRAPH_SEPARATOR,
    _header,
    document_lines,
    split_days,
)
from .route_doc import (
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
from .tags import normalize

PARSER_VERSION = "docx-rules-1"

_FLIGHT_NO = re.compile(r"\b([A-Z]{2}\d{2,4})\b")
# ``1425-1900``, ``01:20-07:40``, ``14:25/19:00`` and a ``+1`` day: the separator is a dash or,
# between two clock times, a slash.
_FLIGHT_TIMES = re.compile(
    r"\d{1,2}:?\d{2}\s*(?:[-–—]|/(?=\s*\d{1,2}:\d{2}))\s*\d{1,2}:?\d{2}(?:\+\d)?"
)
_FLIGHT_ROUTE = re.compile(r"([A-Za-z一-鿿]{2,})\s*[-–—]\s*([A-Za-z一-鿿]{2,})")
_FLIGHT_MARK = re.compile(r"参考航班[：:]?")
_KM = re.compile(r"(\d{2,4})\s*KM", re.IGNORECASE)
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_DRIVE = re.compile(r"车程[约]?\s*[\d.]+\s*(?:小时|H|h|分钟)")
_PLACE_SPLIT = re.compile(r"\s*[-–—/]+\s*|\s*→\s*")
_BRACKET = re.compile(r"【([^【】]{1,40})】")
_DURATION = re.compile(
    r"[（(【]\s*(?:观光时间|游览时间|自由活动时间|停留时间)?\s*(?:约|不少于|不低于)?\s*"
    r"([\d.]+\s*(?:小时|H|h|分钟|min)|\d+\s*[-~]\s*\d+\s*(?:小时|分钟))\s*[)）】]"
)
_GRADE = re.compile(r"([三四五3-5])\s*[钻星]")
_MEAL_TRIPLE = re.compile(r"(早餐?|午餐|中餐?|晚餐?)\s*[：:]\s*")
_NOT_INCLUDED = {"", "x", "×", "✕", "无", "自理", "不含", "-", "—", "/"}
_PRICE = re.compile(r"(\d+(?:\.\d+)?\s*(?:美金|美元|欧元|欧|元|RMB|USD|EUR)\s*/\s*(?:人|位))")
_NUMBERED = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)\s*[、.．:：)]\s*")
_SECTION_HEAD = re.compile(
    r"^\s*(包含项目|不包含项目|费用包含|费用不含|费用不包含|服\s*务\s*所\s*包\s*含\s*项\s*目"
    r"|服\s*务\s*所\s*不\s*含\s*项\s*目|报价包含|报价不含|购物安排|购物说明|购物|自费项目|自费|另行付费项目"
    r"|另行付费|预\s*定\s*须\s*知|报\s*名\s*注\s*意\s*事\s*项|旅行团须知|温馨提示|注意事项|特别注意"
    r"|退改|取消|签证)"
)
_INCLUDE_HEADS = ("包含项目", "费用包含", "服务所包含项目", "报价包含")
_EXCLUDE_HEADS = ("不包含项目", "费用不含", "费用不包含", "服务所不含项目", "报价不含")
_SHOPPING_HEADS = ("购物安排", "购物说明", "购物")
_OPTIONAL_HEADS = ("自费项目", "自费", "另行付费项目", "另行付费")
_SHOP_WORDS = ("购物", "名品", "奥特莱斯", "免税", "宝石店", "珠宝", "丝绸", "特产", "工厂", "专卖")
_FREE_WORDS = ("赠送",)
_OPTIONAL_WORDS = ("自费",)
_OUTSIDE_WORDS = ("外观",)
_TICKET_WORDS = ("含门票", "入内", "含官导", "含讲解", "首道门票")
_COVER_LABELS = {
    "airline": ("航空公司", "航空", "行"),
    "hotel_standard": ("酒店标准", "酒店", "住"),
    "meal_standard": ("用餐安排", "用餐", "餐饮", "吃"),
}
_HIGHLIGHT_LABELS = ("行程亮点", "产品特色", "特别安排", "亮点")
_HOME_WORDS = ("家", "无", "结束", "温馨的家")
_FLIGHT_STAY = ("飞机上", "机上", "夜宿飞机")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


# -- flights ---------------------------------------------------------------------------------


def _flights(text: str, day: int) -> list[Flight]:
    """Every 参考航班 segment a day writes: the flight number, the route beside it and the
    times, each as written. A line with several segments (夏令时/冬令时 pairs, a connection)
    yields one per flight number."""
    found: list[Flight] = []
    for chunk in _FLIGHT_MARK.split(text)[1:] or ([text] if _FLIGHT_NO.search(text) else []):
        chunk = _nfkc(chunk).split(PARAGRAPH_SEPARATOR)[0]
        numbers = _FLIGHT_NO.findall(chunk)
        if not numbers:
            continue
        pieces = re.split(r"(?=\b[A-Z]{2}\d{2,4}\b)", chunk)
        for index, piece in enumerate(pieces):
            piece = piece.strip(" ;；,，")
            number = _FLIGHT_NO.search(piece)
            if number is None:
                continue
            after = piece[number.end() :]
            # The route is usually after the number (MU6017 上海浦东-科伦坡) and sometimes
            # before it (上海-科伦坡 MU231 14:25/19:00), which is the piece before this one.
            before = pieces[index - 1] if index else ""
            route = _FLIGHT_ROUTE.search(_FLIGHT_TIMES.sub("", after)) or _FLIGHT_ROUTE.search(
                _FLIGHT_TIMES.sub("", before)
            )
            times = " / ".join(_FLIGHT_TIMES.findall(after) or _FLIGHT_TIMES.findall(before))
            found.append(
                Flight(
                    day=day,
                    flight_no=number[1],
                    carrier=_carrier(number[1]),
                    from_place=route[1] if route else "",
                    to_place=route[2] if route else "",
                    times=times,
                    raw=piece[:160],
                )
            )
    return found


_CARRIERS = {"MU": "东航", "CA": "国航", "CZ": "南航", "HU": "海航", "HO": "吉祥", "FM": "上航"}


def _carrier(flight_no: str) -> str:
    return _CARRIERS.get(flight_no[:2], "")


# -- the day title ---------------------------------------------------------------------------


def _places(title: str) -> tuple[list[str], list[int], str]:
    """The A-B-C route of a title, the KM figures between the places, and the 车程 notes.
    A 参考航班 tail is not part of the route."""
    head = _FLIGHT_MARK.split(title)[0]
    kms = [int(km) for km in _KM.findall(head)]
    drives = "；".join(_DRIVE.findall(head))
    clean = _KM.sub("", _PAREN.sub("", head))
    places = [p.strip(" ，,。.、") for p in _PLACE_SPLIT.split(clean)]
    places = [p for p in places if p and not p.isdigit() and len(p) <= 20]
    return places, kms, drives


# -- sights ----------------------------------------------------------------------------------


_NOT_SIGHT = re.compile(
    r"^(?:约|不少于|不低于|\d)|^(?:特别提醒|温馨提示|备注|注意|例如|如遇|以上|以下|特别说明)|"
    r"(?:餐|三道式)$|.{29,}"
)


def _not_a_sight(name: str) -> bool:
    """A 【…】 that is a duration, a reminder, a meal or a sentence rather than a place."""
    return bool(_NOT_SIGHT.search(name))


def _sights(text: str) -> list[Sight]:
    """Every 【名】 in a day's programme with what the words around it say. The bracket's own
    words count first (【龙达斗牛场（外观）】, 【圣家族大教堂 含门票和官导*】), then the fifty
    characters after it, which is where 约2H and 自费 are written."""
    sights: list[Sight] = []
    seen: set[str] = set()
    for match in _BRACKET.finditer(text):
        inner = match[1].strip()
        after = text[match.end() : match.end() + 60]
        before = text[max(0, match.start() - 12) : match.start()]
        name = _PAREN.sub("", re.split(r"\s+(?=含|外观)", inner)[0]).strip(" *")
        if not name or name in seen or len(name) < 2 or _not_a_sight(name):
            continue
        seen.add(name)
        scope = inner + after
        kind = "景点"
        ticket: bool | None = None
        if any(word in inner or word in before for word in _OPTIONAL_WORDS):
            kind = "自费"
        elif any(word in name for word in _SHOP_WORDS):
            kind = "购物"
        elif any(word in before for word in _FREE_WORDS) or "赠送" in inner:
            kind = "赠送"
        if any(word in scope[: len(inner) + 12] for word in _OUTSIDE_WORDS):
            kind = "外观" if kind == "景点" else kind
            ticket = False
        elif any(word in scope for word in _TICKET_WORDS):
            ticket = True
        duration = _DURATION.search(after)
        sights.append(
            Sight(
                name=name,
                kind=kind,
                duration=duration[1].replace(" ", "") if duration else "",
                ticket_included=ticket,
            )
        )
    if "自由活动" in text and not sights:
        sights.append(Sight(name="自由活动", kind="自由活动"))
    return sights


# -- meals and hotels ------------------------------------------------------------------------


def _meal(text: str) -> Meal:
    clean = _nfkc(text).strip(" ：:；;，,")
    included = clean.lower() not in _NOT_INCLUDED and not clean.startswith(("自理", "不含"))
    return Meal(text=clean, included=included if clean else None)


def _meals(line: str | None) -> Meals:
    """The three meals of ``早：X || 中：当地餐食 || 晚：酒店内晚餐`` or ``早餐：酒店内 午餐：自理
    晚餐：<牛尾餐>``; a line with no labels is left as three unknown meals."""
    if not line:
        return Meals()
    text = _nfkc(line).replace(CELL_SEPARATOR, " ")
    parts = _MEAL_TRIPLE.split(text)
    if len(parts) < 3:
        return Meals()
    meals = Meals()
    for label, value in zip(parts[1::2], parts[2::2], strict=False):
        value = value.strip()
        if label.startswith("早"):
            meals.breakfast = _meal(value)
        elif label.startswith(("午", "中")):
            meals.lunch = _meal(value)
        elif label.startswith("晚"):
            meals.dinner = _meal(value)
    return meals


def _hotel(text: str | None) -> tuple[Hotel | None, str]:
    """The night's hotel and where the night is spent: ``hotel``, ``flight`` (飞机上),
    ``home`` (无 on the last day) or ``unknown``."""
    if text is None:
        return None, "unknown"
    clean = _nfkc(text).strip()
    if not clean:
        return None, "unknown"
    if any(word in clean for word in _FLIGHT_STAY):
        return None, "flight"
    if "机场集合" in clean:
        return None, "unknown"
    if clean in _HOME_WORDS or clean.startswith("无"):
        return None, "home"
    or_similar = "或同级" in clean or "同级" in clean
    name = re.sub(r"[或]?同级.*$", "", clean).strip(" /、，,")
    grade = _GRADE.search(clean)
    return Hotel(
        name=name or clean, or_similar=or_similar, grade=_grade(grade[1]) if grade else ""
    ), "hotel"


def _grade(digit: str) -> str:
    return {"3": "三钻", "4": "四钻", "5": "五钻"}.get(_nfkc(digit), f"{digit}钻")


# -- front matter and terms -----------------------------------------------------------------


def _split(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """The cover before the first day header, the day lines, and the terms from the first
    section heading inside a day on: the same cut ``itinerary_source.split_days`` makes."""
    chinese = any(_header(line, True) for line in lines)
    first = next((i for i, line in enumerate(lines) if _header(line, chinese)), None)
    if first is None:
        return lines, [], []
    end = len(lines)
    for i in range(first + 1, len(lines)):
        if _is_heading(lines[i]) and not _header(lines[i], chinese):
            end = i
            break
    return lines[:first], lines[first:end], lines[end:]


def _is_heading(line: str) -> bool:
    """A terms heading: one of the section words, standing alone on a short line. A
    programme line that happens to open with 购物 or 自费 is long, and is not one."""
    return bool(_SECTION_HEAD.match(line)) and len(re.sub(r"\s+", "", line)) <= 12


_INLINE_LABEL = re.compile(
    r"^\s*(吃|住|行|航空公司|酒店标准|用餐安排|行程亮点|产品特色|特别安排|贴心赠送)\s*[：:]?\s+(\S.*)$"
)
_OVERVIEW_ROW = re.compile(r"^\s*(?:DAY|D)\s*-?\s*(\d+)\b", re.IGNORECASE)


def _cover(lines: list[str]) -> Cover:
    cover = Cover()
    for line in lines:
        if _OVERVIEW_ROW.match(line):
            continue
        cells = [c.strip() for c in line.split(CELL_SEPARATOR) if c.strip()]
        if len(cells) < 2:
            inline = _INLINE_LABEL.match(line)
            if inline is not None:
                cells = [inline[1], inline[2]]
            elif line.strip().startswith(("★", "#")):
                cover.highlights.extend(_items(line))
                continue
            else:
                continue
        label, value = cells[0], " ".join(cells[1:])
        cover.fields[label] = value
        for field, labels in _COVER_LABELS.items():
            if label in labels and not getattr(cover, field):
                setattr(cover, field, value)
        if label in _HIGHLIGHT_LABELS:
            cover.highlights.extend(_items(value))
    return cover


def _overview(lines: list[str]) -> dict[int, tuple[Meals, str]]:
    """The English overview table some attachments carry above the detail: ``DAY-2 || 上海
    巴塞罗那 || 早餐：× 午餐：× 晚餐：升级 || 国际连锁4星酒店``. Its meals and hotel cells are
    the fallback for a detail day that writes neither."""
    rows: dict[int, tuple[Meals, str]] = {}
    for line in lines:
        found = _OVERVIEW_ROW.match(line)
        if found is None:
            continue
        cells = [c.strip() for c in line.split(CELL_SEPARATOR)]
        meal_cell = next((c for c in cells if _MEAL_TRIPLE.search(c)), "")
        hotel_cell = cells[-1] if len(cells) >= 4 else ""
        rows[int(found[1])] = (_meals(meal_cell), hotel_cell)
    return rows


def _items(text: str) -> list[str]:
    """A block as its items: one per paragraph, the numbering and the ★/# markers stripped."""
    items = []
    for part in re.split(re.escape(PARAGRAPH_SEPARATOR) + r"|\n", text):
        part = _NUMBERED.sub("", part).strip(" ★#·•;；")
        if part:
            items.append(part)
    return items


def _terms(
    lines: list[str],
) -> tuple[list[str], list[str], list[ShoppingStop], list[OptionalItem], list[str]]:
    """The terms as the four lists the schema keeps and the notices left over, each section
    read from its heading to the next."""
    inclusions: list[str] = []
    exclusions: list[str] = []
    shopping: list[ShoppingStop] = []
    optional: list[OptionalItem] = []
    notices: list[str] = []
    current = "notices"
    for line in lines:
        if not line.strip():
            continue
        head = _SECTION_HEAD.match(line) if _is_heading(line) else None
        if head is not None:
            label = re.sub(r"\s+", "", head[1])
            if label in _INCLUDE_HEADS:
                current = "inclusions"
            elif label in _EXCLUDE_HEADS:
                current = "exclusions"
            elif label in _SHOPPING_HEADS:
                current = "shopping"
            elif label in _OPTIONAL_HEADS:
                current = "optional"
            else:
                current = "notices"
                notices.append(line.strip())
            continue
        for item in _items(line):
            if current == "inclusions":
                inclusions.append(item)
            elif current == "exclusions":
                exclusions.append(item)
            elif current == "shopping":
                shopping.append(ShoppingStop(name=item[:60]))
            elif current == "optional":
                price = _PRICE.search(item)
                optional.append(OptionalItem(name=item[:80], price=price[1] if price else ""))
            else:
                notices.append(item)
    return inclusions, exclusions, shopping, optional, notices


def _policies(exclusions: list[str], inclusions: list[str], notices: list[str]) -> Policies:
    ranked = [(0, s) for s in exclusions] + [(0, s) for s in inclusions] + [(1, s) for s in notices]

    def first(*words: str) -> str:
        """The shortest sentence naming the subject, the 费用 lists before the notices."""
        hits = [(tier, len(s), s) for tier, s in ranked if any(w in s for w in words)]
        return min(hits)[2][:160] if hits else ""

    return Policies(
        single_room=first("单房差", "单人间房差", "单间差"),
        child=first("儿童", "小孩", "周岁"),
        visa=first("签证"),
        cancellation=first("退改", "取消", "退团", "退款"),
        deposit=first("定金", "订金"),
    )


# -- the day ---------------------------------------------------------------------------------


def _day(
    record: ItineraryDay, raw_lines: list[str], fallback: tuple[Meals, str] | None, last: bool
) -> Day:
    places, kms, drives = _places(record.title)
    hotel, overnight = _hotel(record.hotel)
    text = record.text
    if overnight == "unknown" and fallback is not None and fallback[1]:
        hotel, overnight = _hotel(fallback[1])
    if overnight == "unknown":
        if any(word in text for word in ("夜宿飞机", "宿飞机", "机上过夜")):
            overnight = "flight"
        elif last or "抵达上海" in text or "结束愉快" in text or "回到温馨" in text:
            overnight = "home"
    meals = _meals(record.meals)
    if meals.breakfast.included is None and fallback is not None:
        meals = fallback[0]
    flights = _flights(record.title + " " + text, record.day_no)
    transport = drives
    for line in raw_lines:
        if "交通：" in line or "交通:" in line:
            tail = line.split("交通")[-1].strip("：: ")
            transport = f"{transport}；{tail}" if transport else tail
            break
    return Day(
        day=record.day_no,
        title=record.title,
        places=places,
        distances_km=kms,
        transport=transport,
        overnight=overnight,
        flights=flights,
        sights=_sights(text),
        meals=meals,
        hotel=hotel,
        text=text,
    )


def _day_raw_lines(day_lines: list[str]) -> dict[int, list[str]]:
    """The raw lines of each day, by day number, for the fields ``split_days`` drops (交通)."""
    chinese = any(_header(line, True) for line in day_lines)
    grouped: dict[int, list[str]] = {}
    current: int | None = None
    for line in day_lines:
        found = _header(line, chinese)
        if found is not None:
            current = found[0]
            grouped[current] = []
        elif current is not None:
            grouped[current].append(line)
    return grouped


_OPTIONAL_PRICED = re.compile(
    r"([^，。；：:、\n/]{2,40}?)\s*自费\s*(\d+(?:\.\d+)?\s*(?:美金|美元|欧元|欧|元|RMB|USD|EUR)\s*/\s*(?:人|位))"
)


def _optional_in_text(text: str, day: int) -> list[OptionalItem]:
    """A 自费 item priced inline in a day's programme: ``加勒古堡+海龟抚育中心 自费 120 美金/人``
    is the item before the word and the price after it."""
    items = []
    for match in _OPTIONAL_PRICED.finditer(_nfkc(text)):
        name = re.sub(r"^(?:推荐|参考)?(?:自费)?(?:套餐|项目|行程)?[：:\s]*", "", match[1]).strip(
            " ★#"
        )
        if name:
            items.append(OptionalItem(name=name[:80], price=match[2].replace(" ", ""), day=day))
    return items


# -- score -----------------------------------------------------------------------------------


def score(doc: RouteDoc) -> tuple[float, list[str]]:
    """The completeness of a document in [0, 1] and what a reviewer should look at. Each
    check is one thing a product person would notice missing on a card."""
    checks: list[tuple[float, bool, str]] = []
    days = doc.days
    stated = doc.summary.days
    checks.append(
        (
            0.20,
            bool(days) and len(days) == stated,
            f"附件天数 {len(days)} 与 ERP 天数 {stated} 不一致",
        )
    )
    hotel_ok = [d for d in days if d.overnight in ("hotel", "flight", "home")]
    checks.append((0.15, bool(days) and len(hotel_ok) == len(days), "有的天没有读到住宿"))
    fed = [d for d in days if d.overnight != "home"]
    meals_ok = [d for d in fed if d.meals.breakfast.included is not None]
    checks.append((0.15, bool(fed) and len(meals_ok) == len(fed), "有的天没有读到用餐"))
    checks.append((0.10, bool(doc.transport), "没有读到参考航班"))
    checks.append((0.10, bool(doc.inclusions), "没有读到费用包含"))
    checks.append((0.10, bool(doc.exclusions), "没有读到费用不含"))
    middle = [d for d in days if d.overnight == "hotel"]
    with_sights = [d for d in middle if d.sights]
    checks.append(
        (
            0.10,
            bool(middle) and len(with_sights) >= max(1, len(middle) - 1),
            "有的行程日没有读到景点",
        )
    )
    checks.append(
        (
            0.05,
            bool(doc.cover.hotel_standard or doc.cover.airline),
            "封面没有读到酒店标准或航空公司",
        )
    )
    checks.append((0.05, all(d.places for d in days) if days else False, "有的天标题没有读到地点"))
    total = sum(weight for weight, ok, _ in checks if ok)
    return round(total, 2), [note for _, ok, note in checks if not ok]


# -- entry -----------------------------------------------------------------------------------


def parse_route(
    record: RouteRecord, data: bytes, *, etag: str | None = None, sale_type: str = ""
) -> RouteDoc:
    """The 线路's attachment as a ``RouteDoc``. Raises ``ValueError`` for a document the
    reader cannot open, as ``itinerary_source.document_lines`` does."""
    lines = document_lines(data)
    cover_lines, day_lines, term_lines = _split(lines)
    raw_by_day = _day_raw_lines(day_lines)
    overview = _overview(cover_lines)
    records = split_days(day_lines)
    days = [
        _day(d, raw_by_day.get(d.day_no, []), overview.get(d.day_no), i == len(records) - 1)
        for i, d in enumerate(records)
    ]
    inclusions, exclusions, shopping, optional, notices = _terms(term_lines)
    for day in days:
        for sight in day.sights:
            if sight.kind == "购物" and all(s.name != sight.name for s in shopping):
                shopping.append(ShoppingStop(name=sight.name, day=day.day, duration=sight.duration))
            if sight.kind == "自费" and all(o.name != sight.name for o in optional):
                price = _PRICE.search(day.text)
                optional.append(
                    OptionalItem(name=sight.name, price=price[1] if price else "", day=day.day)
                )
        for item in _optional_in_text(day.text, day.day):
            if all(o.name != item.name for o in optional):
                optional.append(item)

    facets = normalize(
        (*record.tags, *record.itinerary_tags), record.price_tags, name=record.route_name
    )
    nights = sum(1 for d in days if d.overnight == "hotel") or None
    doc = RouteDoc(
        route_id=record.route_id,
        route_code=record.route_code,
        name=record.route_name.strip(),
        department=record.company_name,
        sale_type=sale_type or record.sale_type,
        summary=Summary(
            days=record.days,
            nights=nights,
            depart_city=record.depart_city
            or (facets.departure_cities[0] if facets.departure_cities else ""),
            countries=list(facets.destinations),
            region=facets.region,
        ),
        cover=_cover(cover_lines),
        transport=[f for d in days for f in d.flights],
        days=days,
        inclusions=inclusions,
        exclusions=exclusions,
        shopping=shopping,
        optional=optional,
        policies=_policies(exclusions, inclusions, notices),
        notices=notices[:40],
        source=Source(
            attachment_name=record.attachment_name or "",
            attachment_url=record.attachment_url or "",
            etag=etag,
            bytes=len(data),
            parsed_at=datetime.now(UTC),
            parser=PARSER_VERSION,
        ),
        quality=Quality(completeness=0.0),
    )
    completeness, notes = score(doc)
    doc.quality = Quality(completeness=completeness, needs_review=notes)
    return doc
