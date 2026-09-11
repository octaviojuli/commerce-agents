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

PARSER_VERSION = "docx-rules-2"

_FLIGHT_NO = re.compile(r"\b([A-Z]{2}\d{2,4})\b")
# ``1425-1900``, ``01:20-07:40``, ``14:25/19:00`` and a ``+1`` day: the separator is a dash or,
# between two clock times, a slash.
_FLIGHT_TIMES = re.compile(
    r"\d{1,2}:?\d{2}\s*(?:[-–—]|/(?=\s*\d{1,2}:\d{2})|\s(?=\d{4}\b))\s*\d{1,2}:?\d{2}(?:\+\d)?"
)
_FLIGHT_ROUTE = re.compile(r"([A-Za-z一-鿿]{2,})\s*[-–—]\s*([A-Za-z一-鿿]{2,})")
# ``CMBPVG``: two airport codes written together, read through the table below.
_IATA_PAIR = re.compile(r"\b([A-Z]{3}) ?([A-Z]{3})\b")
_IATA = {
    "PVG": "上海浦东",
    "SHA": "上海虹桥",
    "PEK": "北京首都",
    "PKX": "北京大兴",
    "HGH": "杭州",
    "NGB": "宁波",
    "NKG": "南京",
    "CKG": "重庆",
    "CTU": "成都",
    "TFU": "成都天府",
    "CAN": "广州",
    "SZX": "深圳",
    "XIY": "西安",
    "WUH": "武汉",
    "CSX": "长沙",
    "TSN": "天津",
    "HKG": "香港",
    "CMB": "科伦坡",
    "MLE": "马累",
    "BKK": "曼谷",
    "SIN": "新加坡",
    "KUL": "吉隆坡",
    "DXB": "迪拜",
    "DOH": "多哈",
    "IST": "伊斯坦布尔",
    "FCO": "罗马",
    "MXP": "米兰",
    "VCE": "威尼斯",
    "CDG": "巴黎",
    "FRA": "法兰克福",
    "MUC": "慕尼黑",
    "BER": "柏林",
    "ZRH": "苏黎世",
    "GVA": "日内瓦",
    "VIE": "维也纳",
    "PRG": "布拉格",
    "BUD": "布达佩斯",
    "AMS": "阿姆斯特丹",
    "BRU": "布鲁塞尔",
    "LHR": "伦敦希思罗",
    "MAD": "马德里",
    "BCN": "巴塞罗那",
    "LIS": "里斯本",
    "ATH": "雅典",
    "CPH": "哥本哈根",
    "ARN": "斯德哥尔摩",
    "OSL": "奥斯陆",
    "HEL": "赫尔辛基",
    "KEF": "雷克雅未克",
    "SVO": "莫斯科",
    "NRT": "东京成田",
    "HND": "东京羽田",
    "KIX": "大阪",
    "ICN": "首尔",
    "SYD": "悉尼",
    "MEL": "墨尔本",
    "AKL": "奥克兰",
    "VNO": "维尔纽斯",
    "RIX": "里加",
    "TLL": "塔林",
    "WAW": "华沙",
    "DUB": "都柏林",
    "EDI": "爱丁堡",
    "MAN": "曼彻斯特",
    "LGW": "伦敦盖特威克",
    "NCE": "尼斯",
    "LYS": "里昂",
    "DUS": "杜塞尔多夫",
    "HAM": "汉堡",
    "OTP": "布加勒斯特",
    "SOF": "索非亚",
    "BEG": "贝尔格莱德",
    "ZAG": "萨格勒布",
    "DBV": "杜布罗夫尼克",
    "LJU": "卢布尔雅那",
    "TIA": "地拉那",
    "SKP": "斯科普里",
    "SJJ": "萨拉热窝",
    "TGD": "波德戈里察",
    "SVQ": "塞维利亚",
    "OPO": "波尔图",
    "MLA": "马耳他",
}
_FLIGHT_MARK = re.compile(r"参考(?:航班|船班|船次)[：:]?")
_SHIP_MARK = re.compile(r"参考船[班次][：:]?\s*([^/；;]+)")
_DATE_COLUMN = re.compile(r"^\s*\d{1,2}[/.]\d{1,2}\s*")
_KM = re.compile(r"(\d{2,4})\s*KM", re.IGNORECASE)
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_DRIVE = re.compile(r"车程[约]?\s*[\d.]+\s*(?:小时|H|h|分钟)")
# A hyphen between two Latin words (McArthurGlen Paris-Giverny) is part of the name.
_PLACE_SPLIT = re.compile(r"\s*(?:(?<![A-Za-z])-|-(?![A-Za-z])|[–—/✈🛳⛴🚢🚄🚌])+\s*|\s*→\s*")
_BRACKET = re.compile(r"【([^【】]{1,40})】")
# ``（约2H）``, ``【约60分钟】``, ``（总观光+自由活动时间不少于2小时）``, ``（打卡时间约10-15分钟）``:
# a parenthesis that is about time, whatever it puts before the figure.
_DURATION = re.compile(
    r"[（(【](?:[^（）()【】]*?(?:时间|观光|游览|停留|打卡|活动))?[^（）()【】\d]*?"
    r"(\d+(?:\.\d+)?(?:\s*[-~]\s*\d+)?\s*(?:个?小时|分钟|H|h|min))[^（）()【】]*[)）】]"
)
_GRADE = re.compile(r"([三四五3-5](?:\s*[-~至]\s*[三四五3-5])?)\s*([钻星])")
_MEAL_TRIPLE = re.compile(r"(早餐?|午餐|中餐?|晚餐?)\s*[：:]\s*")
_NOT_INCLUDED = {"", "x", "×", "✕", "无", "自理", "不含", "-", "—", "/"}
_PRICE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:美金|美元|欧元|欧|英镑|镑|磅|元|RMB|USD|EUR|GBP)\s*(?:/\s*(?:人|位))?)"
)
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
_SHOP_WORDS = (
    "购物",
    "名品",
    "奥特莱斯",
    "免税",
    "宝石店",
    "珠宝",
    "丝绸",
    "特产",
    "工厂",
    "专卖",
    "百货",
    "商场",
    "DFS",
    "Outlet",
    "outlet",
    "Designer",
    "Lafayette",
    "Galeries",
    "老佛爷",
    "莎玛丽丹",
    "花宫娜",
    "Fragonard",
    "天鹅广场",
    "Cheshire Oaks",
)
# Words beside a 【】 that make it a shop whatever its name says (瑞士著名的购物广场).
_SHOP_CONTEXT = ("购物广场", "购物中心", "购物村", "售卖", "门店", "血拼", "专柜", "名品奥特莱斯")
# A place that is a shop in some 线路 and a sight in others; the parser keeps it a sight and
# asks the reviewer.
_SHOP_DOUBT_WORDS = (
    "茶园",
    "茶厂",
    "茶叶",
    "香料园",
    "香料",
    "特产",
    "工艺品",
    "宝石",
    "珠宝",
    "丝绸",
    "香水",
    "水晶",
    "琥珀",
    "乳胶",
    "皮具",
    "巧克力",
    "钻石",
    "手表",
    "商业街",
    "步行街",
    "市场",
    "集市",
)
_NO_SHOP = re.compile(r"[^。；;/]*(?:不算购物店|不算购物|不进店|不安排购物|非购物店)[^。；;/]*")
_FREE_WORDS = ("赠送",)
_FREE_PREFIX = re.compile(r"^(?:特别|独家|额外)?赠送(?:体验|游览|参观)?")
_OPTIONAL_WORDS = ("自费",)
# The head of a 自费 day: every 【】 after it is part of the package, not the tour.
_OPTIONAL_ZONE = re.compile(r"参考行程如下|参考行程[：:]|推荐自费套餐|自费套餐")
_OUTSIDE_WORDS = ("外观", "远观", "车游", "远眺")
# ``…【A】，【B】，【C】均为外观或车游``: the tail applies to every 【】 in the sentence.
_ALL_OUTSIDE = re.compile(r"(?:均|皆|都|以上)[^【】。；;]{0,12}(?:外观|车游|远观|远眺)")
_SENTENCE = re.compile(r"[^。；;！!\n/]+")
_FREE_ACTIVITY = re.compile(r"(全天|一整天|整天|全日|上午|下午|晚上|晚间|傍晚)?\s*自由活动")
_TICKET_WORDS = ("含门票", "入内", "含官导", "含讲解", "首道门票")
_TICKET_RE = re.compile(r"含[^，。）)【】]{0,6}(?:票|缆车|上塔|小火车|快艇|游船|讲解|官导)")
_NO_TICKET = re.compile(r"不入内|非入内|不含门票|不含首道|不登顶")
_COVER_LABELS = {
    "airline": ("航空公司", "航空", "行"),
    "hotel_standard": ("酒店标准", "酒店", "住"),
    "meal_standard": ("用餐安排", "用餐", "餐饮", "吃"),
}
_HIGHLIGHT_LABELS = ("行程亮点", "产品特色", "特别安排", "亮点")
_HOME_WORDS = ("家", "无", "结束", "温馨的家")
_FLIGHT_STAY = ("飞机上", "机上", "夜宿飞机")
_SHIP_STAY = ("邮轮", "游轮", "船上", "夜船")


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
            origin, destination = (
                _route(_FLIGHT_TIMES.sub("", after))
                or _route(_FLIGHT_TIMES.sub("", before))
                or ("", "")
            )
            times = " / ".join(_FLIGHT_TIMES.findall(after) or _FLIGHT_TIMES.findall(before))
            found.append(
                Flight(
                    day=day,
                    flight_no=number[1],
                    carrier=_carrier(number[1]),
                    from_place=origin,
                    to_place=destination,
                    times=times,
                    raw=re.split(r"[;；★]", piece)[0].strip()[:160],
                )
            )
    return found


def _route(text: str) -> tuple[str, str] | None:
    """``上海浦东-科伦坡``, ``CSX-LHR`` or ``CMBPVG``, as the two places; None when neither
    is written."""
    named = _FLIGHT_ROUTE.search(text)
    if named is not None:
        return _IATA.get(named[1], named[1]), _IATA.get(named[2], named[2])
    codes = _IATA_PAIR.search(text)
    if codes is not None:
        return _IATA.get(codes[1], codes[1]), _IATA.get(codes[2], codes[2])
    return None


_CARRIERS = {"MU": "东航", "CA": "国航", "CZ": "南航", "HU": "海航", "HO": "吉祥", "FM": "上航"}


def _carrier(flight_no: str) -> str:
    return _CARRIERS.get(flight_no[:2], "")


# -- the day title ---------------------------------------------------------------------------


def _places(title: str) -> tuple[list[str], list[int], str]:
    """The A-B-C route of a title, the KM figures between the places, and the 车程 notes.
    A 参考航班 tail is not part of the route."""
    head = _DATE_COLUMN.sub("", _FLIGHT_MARK.split(title)[0])
    kms = [int(km) for km in _KM.findall(head)]
    drives = "；".join(_DRIVE.findall(head))
    clean = re.split(r"-{3,}|——", _KM.sub("", _PAREN.sub("", head)))[0]
    places = [_first_word(p.strip(" ，,。.、")) for p in _PLACE_SPLIT.split(clean)]
    places = [
        p for p in places if p and not p.isdigit() and len(re.sub(r"[A-Za-z\s]", "", p)) <= 20
    ]
    return places, kms, drives


def _first_word(place: str) -> str:
    """``布达佩斯 点缀多瑙河的颗颗明珠`` is the city; a short tail (曼彻斯特 市区) stays."""
    parts = place.split(None, 1)
    return parts[0] if len(parts) == 2 and re.search(r"[一-鿿]{5,}", parts[1]) else place


# -- sights ----------------------------------------------------------------------------------


_NOT_SIGHT = re.compile(
    r"^(?:约|不少于|不低于|\d)|^(?:特别提醒|温馨提示|备注|注意|例如|如遇|以上|以下|特别说明"
    r"|重要提醒|今日特别安排|特别安排|贴心提示|特别提示|注[：:])|"
    r"(?:餐|三道式)$"
)


def _not_a_sight(name: str) -> bool:
    """A 【…】 that is a duration, a reminder, a meal or a sentence rather than a place. A
    sentence is 29 Chinese characters or more; a long Latin name is not one."""
    return bool(_NOT_SIGHT.search(name)) or len(re.sub(r"[A-Za-z0-9\s\-&'.]", "", name)) >= 29


def _sights(text: str) -> list[Sight]:
    """Every 【名】 in a day's programme with what the words around it say. The bracket's own
    words count first (【龙达斗牛场（外观）】, 【圣家族大教堂 含门票和官导*】), then the text up
    to the next bracket, which is where 约2H and 含门票 are written. Three rules read the day
    around the bracket: a sentence ending 均为外观或车游 makes every 【】 in it an 外观, a
    推荐自费套餐 head makes every 【】 after it 自费, and a 为赠送项目 note names its 赠送. A
    自由活动 is an entry of its own."""
    found: list[tuple[int, Sight]] = []
    seen: set[str] = set()
    sentences = list(_SENTENCE.finditer(text))
    outside = [m for m in sentences if _ALL_OUTSIDE.search(m[0])]
    gifts = [m[0] for m in sentences if "赠送" in m[0] and "【" not in m[0]]
    zone = _OPTIONAL_ZONE.search(text) if "自由活动" in text else None
    zone_end = len(text)
    if zone is not None and not re.search(r"(?:全天|一整天|整天|全日)自由活动", text):
        zone = None
    if zone is not None:
        gift = re.search(r"【(?:特别|独家|额外)?赠送", text[zone.end() :])
        zone_end = zone.end() + gift.start() if gift else len(text)
    for match in _BRACKET.finditer(text):
        inner = match[1].strip()
        tail = text[match.end() : match.end() + 60]
        after = tail.split("【")[0]
        before = re.split(r"[。；;/！]", text[max(0, match.start() - 12) : match.start()])[-1]
        name = _PAREN.sub("", re.split(r"\s+(?=含|外观)|[，,]", inner)[0]).strip(" *")
        free = _FREE_PREFIX.match(name) is not None
        name = _FREE_PREFIX.sub("", name).strip(" ：:")
        if not name or name in seen or len(name) < 2 or _not_a_sight(name):
            continue
        seen.add(name)
        kind = "景点"
        ticket: bool | None = None
        if any(word in inner or word in before for word in _OPTIONAL_WORDS) or (
            zone is not None and zone.start() < match.start() < zone_end
        ):
            kind = "自费"
            ticket = False
        elif any(word in name for word in _SHOP_WORDS) or any(
            word in after[:40] for word in _SHOP_CONTEXT
        ):
            kind = "购物"
        elif (
            free
            or any(word in before for word in _FREE_WORDS)
            or "赠送" in inner
            or any(part in gift for part in name.split("+") for gift in gifts)
        ):
            kind = "赠送"
        if (
            any(word in inner + after[:12] for word in _OUTSIDE_WORDS)
            or any(m.start() <= match.start() < m.end() for m in outside)
            or _NO_TICKET.search(inner + after[:16])
        ):
            kind = "外观" if kind == "景点" else kind
            ticket = False
        elif any(word in inner + after for word in _TICKET_WORDS) or _TICKET_RE.search(
            inner + after
        ):
            ticket = True
        duration = _DURATION.search(after) or _DURATION.match(tail.lstrip())
        found.append(
            (
                match.start(),
                Sight(
                    name=name,
                    kind=kind,
                    duration=duration[1].replace(" ", "") if duration else "",
                    ticket_included=ticket,
                ),
            )
        )
    leisure = _FREE_ACTIVITY.search(text)
    if leisure is not None:
        span = {"一整天": "全天", "整天": "全天", "全日": "全天"}.get(
            leisure[1] or "", leisure[1] or ""
        )
        found.append((leisure.start(), Sight(name=f"{span}自由活动", kind="自由活动")))
    return [sight for _, sight in sorted(found, key=lambda pair: pair[0])]


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
    lead = re.sub(r"^\s*(?:餐饮|用餐)\s*[：:]\s*", "", parts[0]).strip()
    if lead and parts[1].startswith(("午", "中")):
        meals.breakfast = _meal(lead)
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
    if not clean or clean in ("/", "-", "—", "无酒店"):
        return None, "unknown"
    if any(word in clean for word in _FLIGHT_STAY):
        return None, "flight"
    if any(word in clean for word in _SHIP_STAY):
        return Hotel(name=clean[:60]), "ship"
    if "机场集合" in clean:
        return None, "unknown"
    if clean in _HOME_WORDS or clean.startswith("无"):
        return None, "home"
    or_similar = "或同级" in clean or "同级" in clean
    name = re.sub(r"[或]?同级.*$", "", clean).strip(" /、，,")
    grade = _GRADE.search(clean)
    return Hotel(
        name=name or clean,
        or_similar=or_similar,
        grade=_grade(grade[1], grade[2]) if grade else "",
    ), "hotel"


def _grade(figure: str, unit: str) -> str:
    """``4星``, ``4-5星``, ``五钻``: the figure with the unit the attachment used."""
    return re.sub(r"\s+", "", _nfkc(figure)).translate(str.maketrans("345", "三四五")) + unit


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


_INLINE_LABEL = re.compile(r"^\s*([一-鿿]{1,4})\s*[：:]?\s+(\S.*)$")
_OVERVIEW_ROW = re.compile(r"^\s*(?:DAY|D)\s*-?\s*(\d+)\b", re.IGNORECASE)
# ``行 程 概 览``: a heading spaced out one character at a time.
_SPACED_HEADING = re.compile(r"(?:[一-鿿]\s+){2,}[一-鿿]")


def _cover(lines: list[str]) -> Cover:
    cover = Cover()
    for line in lines:
        if _OVERVIEW_ROW.match(line) or _SPACED_HEADING.fullmatch(line.strip()):
            continue
        cells = [c.strip() for c in line.split(CELL_SEPARATOR) if c.strip()]
        if cells and cells[0].upper().startswith("DATE"):
            continue
        if len(cells) < 2:
            inline = _INLINE_LABEL.match(line)
            if inline is not None:
                cells = [inline[1], inline[2]]
            elif line.strip().startswith(("★", "#")):
                cover.highlights.extend(_items(line))
                continue
            else:
                continue
        label, value = cells[0], " ".join(cells[1:]).strip(" /")
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
        part = _NUMBERED.sub("", part).strip(" ★#·•;；/>-—")
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
    unit = ""
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
                if "做以下说明" in item or "以下说明" in item:
                    current = "notices"
                    notices.append(item)
                    continue
                cells = [c.strip() for c in item.split(CELL_SEPARATOR)]
                name, price = cells[0], _PRICE.search(item)
                if price is None and any(w in item for w in ("名称", "价格", "备注")):
                    found = re.search(r"价格\s*[（(]\s*([^）)]+)[）)]", item)
                    unit = found[1].replace(" ", "") if found else unit
                    continue
                if price is None and (len(name) > 15 or re.search(r"[：:，。！]", name)):
                    notices.append(item)
                    continue
                bare = next((c for c in cells[1:] if re.fullmatch(r"\d+(?:\.\d+)?", c)), "")
                optional.append(
                    OptionalItem(
                        name=name[:80],
                        price=price[1].replace(" ", "")
                        if price
                        else f"{bare}{unit}"
                        if bare
                        else "",
                    )
                )
            else:
                notices.append(item)
    return inclusions, exclusions, shopping, optional, notices


_POLICY_HEADING = re.compile(r".{1,8}(?:价格|规则|说明|标准|须知|政策|条款)[：:]?")


def _policies(exclusions: list[str], inclusions: list[str], notices: list[str]) -> Policies:
    ranked = [
        (tier, clause.strip())
        for tier, sentences in ((0, exclusions), (0, inclusions), (1, notices))
        for sentence in sentences
        for clause in re.split(r";\s+|；\s+|；(?=\D)", sentence)
        if clause.strip() and not _POLICY_HEADING.fullmatch(clause.strip())
    ]

    def first(
        *groups: str | tuple[str, ...], notices_first: bool = False, skip: tuple[str, ...] = ()
    ) -> str:
        """The shortest sentence naming the subject, a sentence matching an earlier word group
        before one matching a later, the 费用 lists before the notices unless told otherwise."""
        words = [(g,) if isinstance(g, str) else g for g in groups]
        hits = []
        for tier, sentence in ranked:
            if any(w in sentence for w in skip):
                continue
            rank = next((i for i, g in enumerate(words) if any(w in sentence for w in g)), None)
            if rank is not None:
                hits.append((rank, 1 - tier if notices_first else tier, len(sentence), sentence))
        if not hits:
            return ""
        best = min(hits)
        flat = [w for g in words[: best[0] + 1] for w in g]
        clauses = [c for c in re.split(r"(?<=[。！!])", best[3]) if any(w in c for w in flat)]
        return (clauses[0] if clauses else best[3]).strip()[:160]

    return Policies(
        single_room=first("单房差", "单人间房差", "单间差"),
        child=first("儿童", "小孩", "周岁"),
        visa=first("签证"),
        cancellation=first(
            ("概不退回", "不退回", "团体订位", "退改"),
            ("退款", "退还", "改期", "退团"),
            notices_first=True,
            skip=("另行付费", "自费"),
        ),
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
    elif hotel is not None and not hotel.grade and fallback is not None:
        grade = _GRADE.search(_nfkc(fallback[1]))
        if grade is not None:
            hotel.grade = _grade(grade[1], grade[2])
    meals = _meals(record.meals)
    if meals.breakfast.included is None and fallback is not None:
        meals = fallback[0]
    flights = _flights(record.title + PARAGRAPH_SEPARATOR + text, record.day_no)
    if overnight == "unknown":
        if any(word in text for word in ("夜宿飞机", "宿飞机", "机上过夜")):
            overnight = "flight"
        elif last or "抵达上海" in text or "结束愉快" in text or "回到温馨" in text:
            overnight = "home"
        elif record.day_no == 1 and flights:
            overnight = "flight"
    transport = drives
    ship = _SHIP_MARK.search(record.title)
    if ship is not None:
        transport = (
            f"{transport}；船班 {ship[1].strip()}" if transport else f"船班 {ship[1].strip()}"
        )
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
    r"([^，,。；：:、\n/()（）]{2,40}?)\s*(?:自费|收费|费用|价格|另付)?\s*[：:]?\s*"
    r"(\d+(?:\.\d+)?\s*(?:美金|美元|欧元|欧|元|RMB|USD|EUR)\s*/\s*(?:人|位))"
)
_OPTIONAL_LEAD = re.compile(
    r"^(?:可选增加|可选|可参加|推荐|参考|另|也可)?(?:自费|收费)?(?:套餐|项目|行程|活动)?[：:\s]*"
)
_OPTIONAL_TAIL = re.compile(r"(?:自费|收费|费用|价格|另付)$")


def _optional_in_text(text: str, day: int) -> list[OptionalItem]:
    """A priced item in a day's programme: ``加勒古堡+海龟抚育中心 自费 120 美金/人``,
    ``美瑞莎出海观鲸120美金/人(满10人发团)`` and ``可选增加观鲸半日游，收费120美金/人`` are
    the item before the price, with 自费/收费 between them or not, and the clause before
    when only the word stands there."""
    items = []
    clean = _nfkc(text)
    for match in _OPTIONAL_PRICED.finditer(clean):
        name = _OPTIONAL_TAIL.sub("", _OPTIONAL_LEAD.sub("", match[1].strip(" ★#"))).strip()
        if not name:
            clause = re.split(r"[，,。；：:、/]", clean[: match.start()].rstrip("，,。；：:、/ "))[
                -1
            ]
            name = _OPTIONAL_LEAD.sub("", clause.strip(" ★#")).strip()
        if name and all(i.name != name for i in items):
            items.append(OptionalItem(name=name[:80], price=match[2].replace(" ", ""), day=day))
    return items


def _doubts(days: list[Day], cover: Cover) -> list[str]:
    """What the parser cannot decide and a product person can: a 茶园 or 香料园 kept as a
    sight, a line saying 此处不算购物店, and a 赠送 promised on the cover."""
    notes = []
    for day in days:
        for sight in day.sights:
            if sight.kind != "购物" and any(w in sight.name for w in _SHOP_DOUBT_WORDS):
                notes.append(
                    f"第{day.day}天【{sight.name}】疑似购物点，附件未列为购物店，请产品确认"
                )
        for found in _NO_SHOP.finditer(day.text):
            notes.append(f"第{day.day}天附件写“…{found[0].strip()[-60:]}”，购物口径请产品确认")
    for item in cover.highlights:
        if "赠送" in item:
            notes.append(f"封面写明赠送“{item[:60]}”，请核对各天的赠送标记")
    return notes


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
    hotel_ok = [d for d in days if d.overnight != "unknown"]
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
    cover = _cover(cover_lines)
    for day in days:
        for sight in day.sights:
            if sight.kind == "购物" and all(s.name != sight.name for s in shopping):
                shopping.append(ShoppingStop(name=sight.name, day=day.day, duration=sight.duration))
        priced = _optional_in_text(day.text, day.day)
        for item in priced:
            if all(o.name != item.name for o in optional):
                optional.append(item)
        for sight in day.sights:
            if sight.kind != "自费" or any(sight.name in o.name for o in optional):
                continue
            beside = re.search(re.escape(sight.name) + r"】.{0,40}?" + _PRICE.pattern, day.text)
            optional.append(
                OptionalItem(
                    name=sight.name,
                    price=beside[1].replace(" ", "") if beside else "",
                    day=day.day,
                )
            )

    facets = normalize(
        (*record.tags, *record.itinerary_tags), record.price_tags, name=record.route_name
    )
    nights = sum(1 for d in days if d.overnight in ("hotel", "ship")) or None
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
        cover=cover,
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
    doc.quality = Quality(completeness=completeness, needs_review=notes + _doubts(days, cover))
    return doc
