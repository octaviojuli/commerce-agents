# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""A 线路's .docx or .pdf 行程附件 read into a ``RouteDoc``.

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
from .pdf_source import PDF_MAGIC, PDF_MODES, pdf_lines
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

PARSER_VERSION = "docx-rules-3"

# ``MU6017``, also glued to its times (``MU601714:25-19:00``).
_FLIGHT_NO = re.compile(r"\b([A-Z]{2}\d{2,4})(?:(?!\d)|(?=\d{1,2}:\d{2}))")
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
# ``参考航班：``, ``参考航班：：`` (the attachments write the colon twice), ``火车参考班次：``.
_FLIGHT_MARK = re.compile(r"(?:火车|轮渡|船)?参考(?:航班|航段|船班|船次|班次|车次)[：:]{0,2}")
# The 参考 notes that are not a flight: a train, a ferry, and a flight the attachment has not
# booked yet. Each is the day's ``transport`` as written and never a place of its title.
_TRANSPORT_NOTE = re.compile(
    r"(?:火车|轮渡|船)?参考(?:船[班次]|班次|车次)[：:]{0,2}\s*[^/；;。]*|参考航班[：:]{0,2}\s*待定"
)
_DATE_COLUMN = re.compile(r"^\s*\d{1,2}[/.]\d{1,2}\s*")
# ``9KM`` counts: a one-digit distance between two short place names is where the reading used
# to put the figure into ``places``.
_KM = re.compile(r"(\d{1,4})\s*KM", re.IGNORECASE)
_PAREN = re.compile(r"[（(][^（）()]*[）)]")
_DRIVE = re.compile(r"车程[约]?\s*[\d.]+\s*(?:小时|H|h|分钟)")
# A hyphen between two Latin words (McArthurGlen Paris-Giverny) is part of the name.
_PLACE_SPLIT = re.compile(r"\s*(?:(?<![A-Za-z])-|-(?![A-Za-z])|[–—/✈🛳⛴🚢🚄🚌])+\s*|\s*→\s*")
# A title whose connector is the character 一: it stands before a KM figure at least once,
# which is the only place no name could be.
_CN_LINK_KM = re.compile(r"(?<=[一-鿿])一\s*\d{1,4}\s*KM", re.IGNORECASE)
_CN_LINK = re.compile(r"(?<=[一-鿿])一(?=[\s\d一-鿿])")
_BRACKET = re.compile(r"【([^【】]{1,40})】")
# ``（约2H）``, ``【约60分钟】``, ``（总观光+自由活动时间不少于2小时）``, ``（打卡时间约10-15分钟）``,
# ``（参观时间约1小时）``: a parenthesis that is about time, whatever it puts before the figure.
# Such a parenthesis is the duration of the 【】 it follows and nothing else — the 自由活动 in
# it is part of that stop's time and not a 自由活动 of its own (``_masked``).
_DURATION = re.compile(
    r"[（(【](?:[^（）()【】]*?(?:时间|观光|游览|停留|打卡|参观|活动))?[^（）()【】\d]*?"
    r"(\d+(?:\.\d+)?(?:\s*[-~]\s*\d+)?\s*(?:个?小时|分钟|H|h|min))[^（）()【】]*[)）】]"
)
_GRADE = re.compile(r"([三四五3-5](?:\s*[-~至]\s*[三四五3-5])?)\s*([钻星])")
# The grade written after the hotel names (AMAYA LAKE 或五钻): it is the standard and not the
# tail of the last name.
# ``… 或五钻同级酒店``: only a tail introduced by 或 is the standard beside the names;
# ``科伦坡网评五钻酒店`` and ``当地五钻`` are the name as written.
_GRADE_TAIL = re.compile(r"\s*或[三四五3-5](?:\s*[-~至]\s*[三四五3-5])?\s*[钻星]级?(?:酒店)?\s*$")
_MEAL_TRIPLE = re.compile(r"(早餐?|午餐|中餐?|晚餐?)\s*[：:]\s*")
_NOT_INCLUDED = {"", "x", "×", "✕", "无", "自理", "不含", "-", "—", "/"}
_PRICE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:美金|美元|欧元|欧|英镑|镑|磅|元|RMB|USD|EUR|GBP)\s*(?:/\s*(?:人|位))?)"
)
# The price column of a 另行付费 table: a figure, a band (150-250) or a floor (120起), the unit
# standing in the column's header (价格（欧元/人）) rather than in the cell.
_PRICE_CELL = re.compile(r"^\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?\s*(?:起|左右)?$")
# A 退门票 sentence is what the 线路 gives back when a booking does not come through; it is
# never an item the customer can buy.
_REFUND = re.compile(r"退(?:门票|票款|费用)|直退|退还门票")
# What tells a sentence from a name in a 另行付费 line with no columns.
_SENTENCE_NAME = re.compile(r"[：:，。！]")
_NUMBERED = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)\s*[、.．:：)]\s*")
_SECTION_HEAD = re.compile(
    r"^\s*(包含项目|不包含项目|费用包含|费用不含|费用不包含|服\s*务\s*所\s*包\s*含\s*项\s*目"
    r"|服\s*务\s*所\s*不\s*含\s*项\s*目|报价包含|报价不含|购物安排|购物说明|购物|自费项目|自费|另行付费项目"
    r"|另行付费|预\s*定\s*须\s*知|报\s*名\s*注\s*意\s*事\s*项|旅行团须知|温馨提示|注意事项|特别注意"
    r"|退改|取消|签证|[^，。]{0,4}(?:旅行须知|出行须知|行前须知))"
)
# A heading that is a note inside a day as often as a section of the terms; it ends the
# itinerary only where no day follows it.
_SOFT_HEADS = ("温馨提示", "注意事项", "特别提醒", "特别注意", "贴心提示", "特别说明")
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
# Words beside a 【】 that make it a shop whatever its name says (瑞士著名的购物广场). They are
# looked for in the rest of the sentence the 【】 stands in, not in a fixed window: the
# attachments write the phrase at the end of a long description (米兰最受欢迎的购物广场).
_SHOP_CONTEXT = ("购物广场", "购物中心", "购物村", "售卖", "门店", "血拼", "专柜", "名品奥特莱斯")
# Words beside a 【】 that say a place is somewhere to shop without saying the 线路 stops there
# to shop; the parser keeps the sight and asks the reviewer (``_doubts``).
_SHOP_DOUBT_CONTEXT = ("购物场所", "购物街", "购物天堂", "百货公司", "露天市场")
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
# A sentence the day does not do: the 温馨提示's alternative for a booking that does not come
# through. Its 【】 is a place the group may see instead of the one above, so it is not a sight
# of the day; the sentence stays in ``text``, where a reviewer reads it.
_CONDITIONAL = re.compile(r"^\s*(?:若|如遇|如预约|如果|如因|倘若)|将调整为|则改为|改为|代替|替代")
# ``所含景点首道门票（其余景点均为外观）：A、B、C``: the 包含 list naming which sights the price's
# 首道门票 covers. ``_apply_ticket_list`` reads the names after the colon.
_TICKET_LIST = re.compile(r"^[^：:]{0,20}(?:首道门票|含门票|所含门票)[^：:]{0,20}[：:]\s*(.+)$")
_ALL_OUTSIDE_TAIL = ("均为外观", "均外观", "均为车游", "皆为外观")
# ``日内瓦游览（均外观）：联合国区，英国花园的大花钟，杰特大喷泉``: a group of sights written as
# prose, with no 【】 around any of them, and the parenthesis saying what they all are.
_PROSE_OUTSIDE = re.compile(
    r"[（(][^（）()]{0,8}(?:外观|车游)[^（）()]{0,8}[)）]\s*[：:]\s*([^。；;]+)"
)
_PROSE_NAME_MAX = 12
# ``赠送观鲸``, ``特别赠送海边小火车``: a gift named in prose, with no 【】 around it.
_GIFT_PROSE = re.compile(
    r"(?:特别|独家|额外)?赠送(?:体验|游览|参观|品尝)?\s*([^，。；;、！：:\s（()）【】]{2,12})"
)
# What follows 赠送 where the sentence is about the gift and does not name one.
_NOT_A_GIFT = ("项目", "如下", "如上", "内容", "标准", "价值", "一次", "服务", "景点", "门票")
# 含官导 and 含讲解 are a guide and not a ticket: an attachment that writes 龙达含官导 in its
# 包含 list is paying the guide, and the 首道门票 is the visitor's. Only 含门票, 首道门票, 入内
# and a named 含X票 (含船票, 含缆车) put the ticket in the price.
_TICKET_WORDS = ("含门票", "入内", "首道门票")
_TICKET_RE = re.compile(r"含[^，。）)【】]{0,6}(?:票|缆车|上塔|小火车|快艇|游船)")
# 不入内 makes the stop an 外观; 不含门票 and 不含园内门票 only say the ticket is not in the price.
_NO_ENTRY = re.compile(r"不入内|非入内|不登顶")
_NO_TICKET = re.compile(r"不含[^，。）)】]{0,4}门票|不含首道")
# What is glued to a sight's name and is not part of it: the ticket and guide notes after it,
# and the 独家安排---/打卡机位1--- the attachments write before it.
# ``独家安排---【X】`` outside the bracket and ``【特别安排-彩色岛含船票】``/``【特别赠送：X】``
# inside it: the label before the dash or colon is not the name.
_NAME_LEAD = re.compile(
    r"^(?:[^【】]{0,8}?-{2,}\s*)+|^(?:特别安排|独家安排|特别赠送|独家赠送|贴心安排|升级安排)\s*[-—–:：]\s*"
)
_NAME_TAIL = re.compile(
    r"(?:\s*[，,]?\s*(?:不?含[^，。\s（）()【】]{0,4}?(?:门票|票|官导|讲解|导游)\*?|门票|入内))+$"
)
_COVER_LABELS = {
    "airline": ("航空公司", "航空", "行", "交通篇", "航空篇", "航班篇"),
    "hotel_standard": ("酒店标准", "酒店", "住", "住宿篇", "酒店篇"),
    "meal_standard": ("用餐安排", "用餐", "餐饮", "吃", "美食篇", "餐饮篇"),
}
_HIGHLIGHT_LABELS = ("行程亮点", "产品特色", "特别安排", "亮点")
_HOME_WORDS = ("家", "无", "结束", "温馨的家")
_FLIGHT_STAY = ("飞机上", "机上", "夜宿飞机")
_SHIP_STAY = ("邮轮", "游轮", "船上", "夜船")
# A 出发城市 written against the first stop with no separator between them (上海巴塞罗那): the
# overview rows and some day titles write the flight day that way.
_DEPART_CITIES = (
    "上海",
    "北京",
    "广州",
    "深圳",
    "成都",
    "重庆",
    "杭州",
    "南京",
    "武汉",
    "西安",
    "长沙",
    "青岛",
    "厦门",
    "昆明",
    "郑州",
    "天津",
    "宁波",
    "香港",
)
# A remainder that is not a second place: the airport and the words about leaving from it.
_NOT_A_SECOND_PLACE = re.compile(r"机场|集合|出发|浦东|虹桥|首都|大兴|天府|市区|返回|归程|结束")
# The countries a 线路 goes to, each with the words a day title writes instead of the country:
# its cities and the landmark a title names. A word counts where the attachment says where the
# line goes — the 线路 name, a day title, a day's places, the cover — and not in a day's prose,
# where a country word belongs to a sight's name (英国花园的大花钟, 土耳其时代的旧建筑) and not
# to the itinerary. Region words (东欧, 巴尔干, 北欧) are not countries and are dropped from
# ``summary.countries``; ``facets.region`` is where the 线路系 is kept.
_COUNTRY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("列支敦士登", ("列支敦士登", "瓦杜茨")),
    ("安道尔", ("安道尔",)),
    ("圣马力诺", ("圣马力诺",)),
    ("梵蒂冈", ("梵蒂冈", "圣彼得大教堂")),
    ("摩纳哥", ("摩纳哥", "蒙特卡洛")),
    ("卢森堡", ("卢森堡",)),
    ("北马其顿", ("北马其顿", "马其顿", "斯科普里", "奥赫里德")),
    ("波黑", ("波黑", "波斯尼亚", "萨拉热窝", "莫斯塔尔")),
    ("黑山", ("黑山", "科托尔", "布德瓦", "波德戈里察")),
    ("阿尔巴尼亚", ("阿尔巴尼亚", "地拉那")),
    ("塞尔维亚", ("塞尔维亚", "贝尔格莱德", "诺维萨德")),
    ("克罗地亚", ("克罗地亚", "萨格勒布", "杜布罗夫尼克", "普利特维采", "十六湖", "扎达尔")),
    ("斯洛文尼亚", ("斯洛文尼亚", "卢布尔雅那", "布莱德")),
    ("斯洛伐克", ("斯洛伐克", "布拉迪斯拉发")),
    ("匈牙利", ("匈牙利", "布达佩斯", "多瑙河")),
    ("捷克", ("捷克", "布拉格", "卡罗维发利", "克鲁姆洛夫", "布杰约维采")),
    ("波兰", ("波兰", "华沙", "克拉科夫", "弗罗茨瓦夫", "格但斯克")),
    ("爱沙尼亚", ("爱沙尼亚", "塔林")),
    ("拉脱维亚", ("拉脱维亚", "里加")),
    ("立陶宛", ("立陶宛", "维尔纽斯", "特拉凯")),
    ("罗马尼亚", ("罗马尼亚", "布加勒斯特", "布拉索夫")),
    ("保加利亚", ("保加利亚", "索非亚")),
    ("希腊", ("希腊", "雅典", "圣托里尼", "米克诺斯", "圣岛")),
    ("冰岛", ("冰岛", "雷克雅未克", "黄金圈")),
    ("挪威", ("挪威", "奥斯陆", "卑尔根", "峡湾", "盖朗格", "弗洛姆")),
    ("瑞典", ("瑞典", "斯德哥尔摩", "哥德堡")),
    ("丹麦", ("丹麦", "哥本哈根", "欧登塞")),
    ("芬兰", ("芬兰", "赫尔辛基", "罗瓦涅米")),
    ("爱尔兰", ("爱尔兰", "都柏林")),
    ("英国", ("英国", "伦敦", "爱丁堡", "曼彻斯特", "牛津", "剑桥", "约克")),
    ("荷兰", ("荷兰", "阿姆斯特丹", "鹿特丹", "海牙", "桑斯安斯", "羊角村")),
    ("比利时", ("比利时", "布鲁塞尔", "布鲁日", "根特", "安特卫普")),
    ("瑞士", ("瑞士", "苏黎世", "琉森", "卢塞恩", "因特拉肯", "日内瓦", "伯尔尼", "少女峰")),
    ("奥地利", ("奥地利", "维也纳", "萨尔茨堡", "因斯布鲁克", "哈尔施塔特")),
    ("德国", ("德国", "法兰克福", "慕尼黑", "柏林", "科隆", "海德堡", "新天鹅堡", "富森")),
    ("法国", ("法国", "巴黎", "尼斯", "里昂", "第戎", "科尔马", "斯特拉斯堡", "安纳西")),
    ("意大利", ("意大利", "罗马", "米兰", "威尼斯", "佛罗伦萨", "比萨", "那不勒斯", "五渔村")),
    ("西班牙", ("西班牙", "马德里", "巴塞罗那", "塞维利亚", "格拉纳达", "瓦伦西亚", "龙达")),
    ("葡萄牙", ("葡萄牙", "里斯本", "波尔图", "辛特拉", "罗卡角")),
    ("摩洛哥", ("摩洛哥", "卡萨布兰卡", "马拉喀什", "舍夫沙万", "拉巴特", "菲斯")),
    ("土耳其", ("土耳其", "伊斯坦布尔", "卡帕多奇亚", "棉花堡")),
    ("埃及", ("埃及", "开罗", "卢克索", "阿斯旺")),
    ("迪拜", ("迪拜", "阿布扎比", "阿联酋")),
    ("斯里兰卡", ("斯里兰卡", "科伦坡", "康提", "加勒", "尼甘布", "丹布勒", "锡吉里耶")),
    ("马尔代夫", ("马尔代夫", "马累")),
    ("泰国", ("泰国", "曼谷", "普吉", "清迈")),
    ("新加坡", ("新加坡",)),
    ("马来西亚", ("马来西亚", "吉隆坡", "沙巴")),
    ("日本", ("日本", "东京", "大阪", "京都", "北海道", "冲绳")),
    ("韩国", ("韩国", "首尔", "济州")),
    ("澳大利亚", ("澳大利亚", "悉尼", "墨尔本", "凯恩斯", "布里斯班")),
    ("新西兰", ("新西兰", "奥克兰", "皇后镇", "基督城")),
)
_REGION_WORDS = ("东欧", "西欧", "中欧", "南欧", "北欧", "欧洲", "巴尔干")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


# -- flights ---------------------------------------------------------------------------------


def _flights(text: str, day: int) -> list[Flight]:
    """Every 参考航班 segment a day writes: the flight number, the route beside it and the
    times, each as written. A line with several segments (夏令时/冬令时 pairs, a connection)
    yields one per flight number. ``raw`` is the sentence as the attachment wrote it, the
    参考航班 label and its full-width parentheses kept: the fields are a reading of it and the
    reviewer reads the original beside them."""
    found: list[Flight] = []
    for label, chunk in _flight_chunks(text):
        chunk = re.split(re.escape(PARAGRAPH_SEPARATOR) + r"|[;；。]", chunk)[0]
        if _FLIGHT_NO.search(chunk) is None:
            chunk = _nfkc(chunk)
        numbers = _FLIGHT_NO.findall(chunk)
        if not numbers:
            continue
        pieces = re.split(r"(?=\b[A-Z]{2}\d{2,4}\b)", chunk)
        for index, piece in enumerate(pieces):
            piece = piece.strip(" ;；,，")
            number = _FLIGHT_NO.search(piece)
            if number is None:
                continue
            after = _nfkc(piece[number.end() :])
            # The route is usually after the number (MU6017 上海浦东-科伦坡) and sometimes
            # before it (上海-科伦坡 MU231 14:25/19:00), which is the piece before this one.
            previous = pieces[index - 1] if index else ""
            before = _nfkc(previous)
            # ``参考航班： 上海-科伦坡 MU231 …``: the words before the number are part of the
            # segment as written, and belong to its ``raw`` too.
            head = previous.lstrip() if index == 1 and _FLIGHT_NO.search(previous) is None else ""
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
                    raw=(label + head + re.split(r"[;；★]", piece)[0].strip())[:160],
                )
            )
    return found


def _flight_chunks(text: str) -> list[tuple[str, str]]:
    """The text as (label, chunk) pairs: one per 参考航班 mark with the label as written, or
    the whole text under no label where a flight number stands on its own."""
    marks = list(_FLIGHT_MARK.finditer(text))
    if not marks:
        return [("", text)] if _FLIGHT_NO.search(_nfkc(text)) else []
    ends = [*(m.start() for m in marks[1:]), len(text)]
    return [(m[0], text[m.end() : end]) for m, end in zip(marks, ends, strict=True)]


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


# The carrier as the cover writes it in full, which is what the card shows beside the flight.
_CARRIERS = {
    "MU": "中国东方航空",
    "CA": "中国国际航空",
    "CZ": "中国南方航空",
    "HU": "海南航空",
    "HO": "吉祥航空",
    "FM": "上海航空",
    "3U": "四川航空",
    "MF": "厦门航空",
    "ZH": "深圳航空",
    "SC": "山东航空",
    "GS": "天津航空",
    "JD": "首都航空",
    "9C": "春秋航空",
    "KN": "中国联合航空",
    "CX": "国泰航空",
    "UL": "斯里兰卡航空",
    "SK": "北欧航空",
    "JU": "塞尔维亚航空",
    "FI": "冰岛航空",
    "AY": "芬兰航空",
    "TK": "土耳其航空",
    "EK": "阿联酋航空",
    "QR": "卡塔尔航空",
    "LH": "汉莎航空",
    "AF": "法国航空",
    "KL": "荷兰皇家航空",
    "BA": "英国航空",
    "LX": "瑞士国际航空",
    "OS": "奥地利航空",
    "AZ": "意大利航空",
    "IB": "西班牙国家航空",
    "TP": "葡萄牙航空",
    "SU": "俄罗斯航空",
    "EY": "阿提哈德航空",
    "SQ": "新加坡航空",
    "TG": "泰国国际航空",
    "NH": "全日空",
    "JL": "日本航空",
    "KE": "大韩航空",
    "OZ": "韩亚航空",
    "QF": "澳洲航空",
    "NZ": "新西兰航空",
    "VN": "越南航空",
    "MH": "马来西亚航空",
}


def _carrier(flight_no: str) -> str:
    return _CARRIERS.get(flight_no[:2], "")


# -- the day title ---------------------------------------------------------------------------


def _places(title: str) -> tuple[list[str], list[int], str]:
    """The A-B-C route of a title, the KM figures between the places, and the 车程 notes.
    A 参考航班 tail and a 火车参考班次 note are not part of the route. A KM figure leaves a
    separator where it stood, so a title writing two of them against one dash
    (威尼斯--40KM-Noventa购物村) keeps every place it names."""
    head = _cn_links(_DATE_COLUMN.sub("", _FLIGHT_MARK.split(title)[0]))
    kms = [int(km) for km in _KM.findall(head)]
    drives = "；".join(_DRIVE.findall(head))
    # A decorative run of dashes (独家安排---) ends the route; the KM figures inside it do not.
    clean = _KM.sub("-", _PAREN.sub("", re.split(r"-{3,}|——", head)[0]))
    places = [_first_word(p.strip(" ，,。.、")) for p in _PLACE_SPLIT.split(clean)]
    places = [
        p for p in places if p and not p.isdigit() and len(re.sub(r"[A-Za-z\s]", "", p)) <= 20
    ]
    return [part for place in places for part in _unglue(place)], kms, drives


def _cn_links(head: str) -> str:
    """``成都一马德里一 110km 阿维拉``: a title that draws its connector as the character 一.
    It is read as one only where the title uses it before a KM figure, which is where no place
    name could stand; a title that writes 一 inside a name (路易一世大桥) and never before a
    figure keeps it."""
    return _CN_LINK.sub("-", head) if _CN_LINK_KM.search(head) else head


def _first_word(place: str) -> str:
    """``布达佩斯 点缀多瑙河的颗颗明珠`` is the city; a short tail (曼彻斯特 市区) stays."""
    parts = place.split(None, 1)
    return parts[0] if len(parts) == 2 and re.search(r"[一-鿿]{5,}", parts[1]) else place


def _unglue(place: str) -> list[str]:
    """``上海巴塞罗那``, ``米兰重庆``: the 出发城市 and the stop it flies to, written against
    each other with nothing between them, as the flight days of an overview row are. The city
    is split off only where what is left is a place of its own — 上海浦东机场 is one place."""
    if len(place) < 4 or re.search(r"[A-Za-z\s]", place) or _NOT_A_SECOND_PLACE.search(place):
        return [place]
    for city in _DEPART_CITIES:
        rest = place[len(city) :] if place.startswith(city) else ""
        if len(rest) >= 2:
            return [city, rest]
        rest = place[: -len(city)] if place.endswith(city) else ""
        if len(rest) >= 2:
            return [rest, city]
    return [place]


# -- sights ----------------------------------------------------------------------------------


_NOT_SIGHT = re.compile(
    r"^(?:约\s*\d|约\s*[一二三四五六七八九十半]|不少于|不低于|\d)|^(?:特别提醒|温馨提示|备注|注意|例如|如遇|以上|以下|特别说明"
    r"|重要提醒|今日特别安排|特别安排|贴心提示|特别提示|注[：:])|"
    r"(?:餐|三道式)$"
)


def _not_a_sight(name: str) -> bool:
    """A 【…】 that is a duration, a reminder, a meal or a sentence rather than a place. A
    sentence is 29 Chinese characters or more; a long Latin name is not one."""
    return bool(_NOT_SIGHT.search(name)) or len(re.sub(r"[A-Za-z0-9\s\-&'.]", "", name)) >= 29


def _masked(text: str) -> str:
    """The text with every parenthesis blanked and its length kept. A parenthesis after a
    【】 is that stop's own note — （总观光+自由活动时间不少于2小时） is how long the group has
    there — so the words in it are read as the 【】's duration and never as a programme of
    their own."""
    return _PAREN.sub(lambda found: " " * len(found[0]), text)


def _sights(text: str) -> list[Sight]:
    """Every 【名】 in a day's programme with what the words around it say. The bracket's own
    words count first (【龙达斗牛场（外观）】, 【圣家族大教堂 含门票和官导*】), then the text up
    to the next bracket, which is where 约2H and 含门票 are written. Rules read the day around
    the bracket: a sentence ending 均为外观或车游 makes every 【】 in it an 外观, a 推荐自费套餐
    head makes every 【】 after it 自费, a 为赠送项目 note names its 赠送, a 自费推荐 right after
    a bracket makes it 自费, and a 若…/将调整为… sentence is an alternative the day does not do,
    so its 【】 is no sight at all. A 自由活动 the prose states is an entry of its own; one
    inside a parenthesis is a duration and is not."""
    found: list[tuple[int, Sight]] = []
    seen: set[str] = set()
    masked = _masked(text)
    sentences = list(_SENTENCE.finditer(text))
    outside = [m for m in sentences if _ALL_OUTSIDE.search(m[0])]
    conditional = [m for m in sentences if _CONDITIONAL.search(m[0])]
    gifts = [m[0] for m in sentences if "赠送" in m[0] and "【" not in m[0]]
    zone = _OPTIONAL_ZONE.search(text) if "自由活动" in masked else None
    zone_end = len(text)
    if zone is not None and not re.search(r"(?:全天|一整天|整天|全日)自由活动", masked):
        zone = None
    if zone is not None:
        gift = re.search(r"【(?:特别|独家|额外)?赠送", text[zone.end() :])
        zone_end = zone.end() + gift.start() if gift else len(text)
    for match in _BRACKET.finditer(text):
        if any(m.start() <= match.start() < m.end() for m in conditional):
            continue
        inner = match[1].strip()
        tail = text[match.end() : match.end() + 80]
        after = tail.split("【")[0]
        # What the words right after the bracket say is the stop's own; the next sentence is
        # about something else (a 均外观 group of its own) and does not reach back into it.
        near = re.split(r"[。！\n]", after)[0]
        sentence = next((m for m in sentences if m.start() <= match.start() < m.end()), None)
        window = (text[match.end() : sentence.end()] if sentence else after).split("【")[0]
        before = re.split(r"[。；;/！]", text[max(0, match.start() - 12) : match.start()])[-1]
        name = _PAREN.sub("", re.split(r"\s+(?=不?含|外观)|[，,]", inner)[0]).strip(" *")
        free = _FREE_PREFIX.match(_NAME_LEAD.sub("", name)) is not None
        name = _FREE_PREFIX.sub("", _NAME_LEAD.sub("", name)).strip(" ：:")
        name = _NAME_TAIL.sub("", name).strip(" *·")
        if not name and free:
            # ``【特别赠送】列支敦士登公国入境纪念章``: the bracket is the label and the gift is
            # named beside it, up to the first punctuation.
            named = re.match(r"\s*([^，。；;、（()）【】]{2,20})", after)
            name = named[1].strip() if named else ""
        if not name or name in seen or len(name) < 2 or _not_a_sight(name):
            continue
        seen.add(name)
        kind = "景点"
        ticket: bool | None = None
        # ``【奥斯曼大道】（自由活动，时间不少于1小时）``: a parenthesis that opens with 自由活动
        # and a comma names the stop a 自由活动, whatever the sentence says about shops around
        # it; ``（自由活动约30分钟）`` is a duration on a sight and leaves its kind alone.
        leisure_note = re.match(r"\s*[（(]\s*自由活动\s*[，,、]", near) is not None
        if leisure_note and not any(word in name for word in _SHOP_WORDS):
            kind = "自由活动"
        elif (
            any(word in inner or word in before for word in _OPTIONAL_WORDS)
            or any(word in near[:12] for word in _OPTIONAL_WORDS)
            or (zone is not None and zone.start() < match.start() < zone_end)
        ):
            kind = "自费"
            ticket = False
        elif any(word in name for word in _SHOP_WORDS) or any(
            word in window for word in _SHOP_CONTEXT
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
            any(word in inner + near[:12] for word in _OUTSIDE_WORDS)
            or any(m.start() <= match.start() < m.end() for m in outside)
            or _NO_ENTRY.search(inner + near[:16])
        ):
            kind = "外观" if kind == "景点" else kind
            ticket = False
        elif _NO_TICKET.search(inner + near[:16]):
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
    found.extend(_prose_sights(text, seen, [m.span() for m in _BRACKET.finditer(text)]))
    leisure = _FREE_ACTIVITY.search(masked)
    if leisure is not None:
        span = {"一整天": "全天", "整天": "全天", "全日": "全天"}.get(
            leisure[1] or "", leisure[1] or ""
        )
        found.append((leisure.start(), Sight(name=f"{span}自由活动", kind="自由活动")))
    return [sight for _, sight in sorted(found, key=lambda pair: pair[0])]


def _prose_sights(
    text: str, seen: set[str], brackets: list[tuple[int, int]]
) -> list[tuple[int, Sight]]:
    """``日内瓦游览（均外观）：联合国区，英国花园的大花钟，杰特大喷泉``: a group of stops written
    as prose with no 【】 around any of them, the parenthesis before the colon saying what they
    all are. Each name up to the next comma is one 外观, a long one being a sentence and not a
    name. A 赠送观鲸 written the same way — the word and the gift after it — is a 赠送."""
    found: list[tuple[int, Sight]] = []
    for group in _PROSE_OUTSIDE.finditer(text):
        at = group.start(1)
        for name in re.split(r"[，,、]", group[1]):
            name = name.strip(" 。；;")
            if 2 <= len(name) <= _PROSE_NAME_MAX and name not in seen and "【" not in name:
                seen.add(name)
                found.append((at, Sight(name=name, kind="外观", ticket_included=False)))
    for gift in _GIFT_PROSE.finditer(text):
        name = gift[1].strip()
        if any(start <= gift.start() < end for start, end in brackets):
            continue
        if name in seen or any(word in name for word in _NOT_A_GIFT):
            continue
        seen.add(name)
        found.append((gift.start(1), Sight(name=name, kind="赠送")))
    return found


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
        return _meals_short(text)
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


_MEALS_SHORT = re.compile(r"^[早中午晚/X×x自理无、，,\s]+$")


def _meals_short(text: str) -> Meals:
    """``早午晚``, ``早、/、/``, ``X``: the .pdf attachments' way of writing the three meals
    as the ones served, in order, with / or X for one not served."""
    clean = text.strip()
    if not clean or not _MEALS_SHORT.match(clean):
        return Meals()
    served = {"早": "早" in clean, "午": "午" in clean or "中" in clean, "晚": "晚" in clean}
    meals = Meals()
    for label, field in (("早", "breakfast"), ("午", "lunch"), ("晚", "dinner")):
        setattr(meals, field, Meal(text=label if served[label] else "X", included=served[label]))
    return meals


def _hotel(text: str | None) -> tuple[Hotel | None, str]:
    """The night's hotel and where the night is spent: ``hotel``, ``flight`` (飞机上),
    ``home`` (无 on the last day) or ``unknown``."""
    if text is None:
        return None, "unknown"
    clean = _nfkc(text).strip()
    if not clean or clean in ("/", "-", "—", "无酒店", "X", "x", "×"):
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
    # ``AMAYA LAKE / CINNAMON LODGE 或五钻同级酒店``: the standard written after the names is
    # the grade and not part of the last name.
    name = _GRADE_TAIL.sub("", re.sub(r"[或]?同级.*$", "", clean)).strip(" /、，,")
    grade = _GRADE.search(clean)
    return Hotel(
        name=name or clean,
        or_similar=or_similar,
        grade=_grade(grade[1], grade[2]) if grade else "",
    ), "hotel"


def _grade(figure: str, unit: str) -> str:
    """``4星``, ``4-5星``, ``五钻``: the figure and the unit as the attachment wrote them. The
    figure keeps its digits — an overview column says 4星酒店 and a card that answers 四星 is
    answering something the attachment does not say; ``route_docs.hotel_grade`` reads either."""
    return re.sub(r"\s+", "", _nfkc(figure)) + unit


# -- front matter and terms -----------------------------------------------------------------


def _split(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """The cover before the first day header, the day lines, and the terms from the heading
    that ends the itinerary on: the same cut ``itinerary_source.split_days`` makes. A 温馨提示
    or 注意事项 standing alone in the middle of the itinerary is that day's own note — the .pdf
    readings write one on a line of its own — and ends nothing while days still follow it; the
    费用包含/不含 family never stands inside a day and ends them wherever it is."""
    chinese = any(_header(line, True) for line in lines)
    headers = [i for i, line in enumerate(lines) if _header(line, chinese)]
    if not headers:
        return lines, [], []
    first, last = headers[0], headers[-1]
    end = len(lines)
    for i in range(first + 1, len(lines)):
        if not _is_heading(lines[i]) or _header(lines[i], chinese):
            continue
        if i < last and re.sub(r"\s+", "", lines[i]).startswith(_SOFT_HEADS):
            continue
        end = i
        break
    return lines[:first], lines[first:end], lines[end:]


def _is_heading(line: str) -> bool:
    """A terms heading: one of the section words, standing alone on a short line. A
    programme line that happens to open with 购物 or 自费 is long, and is not one. The
    attachments space a heading out one character at a time (欧 洲 旅 行 须 知), so the
    spacing is taken out before the line is matched."""
    compact = re.sub(r"\s+", "", line)
    return bool(_SECTION_HEAD.match(compact)) and len(compact) <= 12


_INLINE_LABEL = re.compile(r"^\s*([一-鿿]{1,4})\s*[：:]?\s+(\S.*)$")
_OVERVIEW_ROW = re.compile(r"^\s*(?:DAY|D)\s*-?\s*(\d+)\b", re.IGNORECASE)
# ``行 程 概 览``: a heading spaced out one character at a time.
_SPACED_HEADING = re.compile(r"(?:[一-鿿]\s+){2,}[一-鿿]")


def _cover(lines: list[str]) -> Cover:
    """The front matter as the labelled rows and the highlights. A label the attachment
    invents (美食篇 for 用餐, 交通篇 for 航空公司) is read as the field it names, and the line
    under a bare 产品特色 heading is a highlight even though it carries no label of its own."""
    cover = Cover()
    under_head = False
    for line in lines:
        if _OVERVIEW_ROW.match(line) or _SPACED_HEADING.fullmatch(line.strip()):
            continue
        cells = [c.strip() for c in line.split(CELL_SEPARATOR) if c.strip()]
        if cells and cells[0].upper().startswith("DATE"):
            continue
        if len(cells) == 1 and re.sub(r"[\s：:]", "", cells[0]) in _HIGHLIGHT_LABELS:
            under_head = True
            continue
        if len(cells) < 2:
            inline = _INLINE_LABEL.match(line)
            if inline is not None:
                cells = [inline[1], inline[2]]
            elif line.strip().startswith(("★", "#")) or under_head:
                cover.highlights.extend(_items(line))
                under_head = False
                continue
            else:
                continue
        under_head = False
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


_COLUMN_INCLUDE = ("报价包含", "费用包含", "包含项目", "服务所包含", "费用已含", "价格包含")
_COLUMN_EXCLUDE = ("报价不含", "费用不含", "不包含项目", "服务所不含", "费用未含", "价格不含")


def _two_column_terms(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """A 报价包含 ｜ 报价不含 table printed as two columns, which a .pdf reads as one line per
    printed row with a cell for each column. Read row by row the two columns run into one
    list; read column by column each is its own, and a cell that does not close with ；or 。 is
    the row above wrapping and joins it. The lines the table does not hold are given back for
    the section-by-section reading."""
    head = next(
        (
            index
            for index, line in enumerate(lines)
            if (cells := [re.sub(r"\s+", "", c) for c in line.split(CELL_SEPARATOR)])
            and len(cells) >= 2
            and any(word in cells[0] for word in _COLUMN_INCLUDE)
            and any(word in cell for cell in cells[1:] for word in _COLUMN_EXCLUDE)
        ),
        None,
    )
    if head is None:
        return lines, [], []
    columns: tuple[list[str], list[str]] = ([], [])
    end = len(lines)
    for index in range(head + 1, len(lines)):
        cells = [c.strip() for c in lines[index].split(CELL_SEPARATOR) if c.strip()]
        if not cells:
            continue
        if len(cells) == 1 and _is_heading(cells[0]):
            end = index
            break
        if len(cells) >= 2:
            _wrapped(columns[0], cells[0])
            _wrapped(columns[1], cells[1])
        else:
            _wrapped(
                columns[1] if _closed(columns[0]) and not _closed(columns[1]) else columns[0],
                cells[0],
            )
    kept = [*lines[:head], *lines[end:]]
    return kept, _closed_items(columns[0]), _closed_items(columns[1])


def _closed_items(column: list[str]) -> list[str]:
    return [clean for item in column if (clean := item.strip(" ；;、/"))]


def _closed(column: list[str]) -> bool:
    """Whether the column's last item is a finished sentence, so the next cell starts a new
    one rather than continuing it. A row printed in one column only goes to the column that is
    still open; where both are closed there is nothing in the text to tell them apart and it
    goes to the 包含 one, which is the column the attachments print first."""
    return not column or column[-1].rstrip()[-1:] in "；;。！!"


def _wrapped(column: list[str], cell: str) -> None:
    text = cell.strip()
    if not text:
        return
    if _closed(column):
        column.append(_NUMBERED.sub("", text.lstrip(" ★#·•>-—")))
    else:
        column[-1] = f"{column[-1]}{text}"


def _terms(
    lines: list[str],
) -> tuple[list[str], list[str], list[ShoppingStop], list[OptionalItem], list[str]]:
    """The terms as the four lists the schema keeps and the notices left over, each section
    read from its heading to the next. A 报价包含 ｜ 报价不含 table is read by column first,
    because its two columns are printed side by side and read as one line."""
    lines, inclusions, exclusions = _two_column_terms(lines)
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
                bare = next((c for c in cells[1:] if _PRICE_CELL.match(c)), "")
                if price is None and not bare and any(w in item for w in ("名称", "价格", "备注")):
                    found = re.search(r"价格\s*[（(]\s*([^）)]+)[）)]", item)
                    unit = found[1].replace(" ", "") if found else unit
                    continue
                if _REFUND.search(item) and len(cells) == 1:
                    notices.append(item)
                    continue
                # A row of the table is an item whatever its name's length: a name with a
                # half-width parenthesis in it (凡尔赛宫(不含后花园，约 1.5 小时)) is long and
                # punctuated and is a name all the same. Only a line the table did not write
                # in columns is read as a sentence.
                if (
                    len(cells) == 1
                    and price is None
                    and (len(name) > 15 or _SENTENCE_NAME.search(name))
                ):
                    notices.append(item)
                    continue
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
# A 签证 sentence that is about what the visa costs, rather than about handing a passport back.
_VISA_COST = re.compile(r"签证费|签证[^。]{0,10}(?:费用|元|办理|代办)|(?:费用|办理)[^。]{0,10}签证")
# ``出发前30-15日，收取团款5%``: the day bands of a 游客取消规则 table.
_DAY_BANDS = re.compile(r"\d{1,2}\s*[-–—~至]\s*\d{1,2}\s*(?:日|天)")
# The 退改 policy is two texts joined; a table of bands is long, and the field is one sentence
# a card can show.
MAX_CANCELLATION = 400


def _policies(exclusions: list[str], inclusions: list[str], notices: list[str]) -> Policies:
    """The sentence each policy is written in. Two of them are read more narrowly than the
    subject word alone: 签证 says what a visa costs only where the notice is about the fee
    (a 销签 reminder is about handing the passport back, not about money), and a 儿童 clause
    that says 与成人同价 is a 另行付费 note and never the 儿童 policy where the terms also
    write a 周岁/不占床 reduction. The 退改 policy is the 团体订位 sentence with the 取消规则
    table's own day bands after it, because the bands are where the money actually is."""
    ranked = [
        (tier, clause.strip())
        for tier, sentences in ((0, exclusions), (0, inclusions), (1, notices))
        for sentence in sentences
        for clause in re.split(r";\s+|；\s+|；(?=\D)|；(?=\d{1,2}\s*(?:周岁|岁))", sentence)
        if clause.strip() and not _POLICY_HEADING.fullmatch(clause.strip())
    ]

    def first(
        *groups: str | tuple[str, ...],
        notices_first: bool = False,
        skip: tuple[str, ...] = (),
        needs: re.Pattern[str] | None = None,
    ) -> str:
        """The shortest sentence naming the subject, a sentence matching an earlier word group
        before one matching a later, the 费用 lists before the notices unless told otherwise.
        ``needs`` is what a notice has to say as well; a 费用包含/不含 line states a cost by
        standing where it stands and is not held to it."""
        words = [(g,) if isinstance(g, str) else g for g in groups]
        hits = []
        for tier, sentence in ranked:
            if any(w in sentence for w in skip):
                continue
            if tier and needs is not None and not needs.search(sentence):
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

    cancellation = first(
        ("概不退回", "不退回", "团体订位", "退改"),
        ("退款", "退还", "改期", "退团"),
        notices_first=True,
        skip=("另行付费", "自费"),
    )
    bands = _cancel_bands(notices)
    if bands and bands not in cancellation:
        cancellation = f"{cancellation}；{bands}".strip("；")[:MAX_CANCELLATION]
    return Policies(
        single_room=first("单房差", "单人间房差", "单间差"),
        child=first(("不占床", "周岁"), ("儿童", "小孩"), skip=("与成人同价",))
        or first("儿童", "小孩", "周岁"),
        visa=first("签证", skip=("销签",), needs=_VISA_COST),
        cancellation=cancellation,
        deposit=first("定金", "订金"),
    )


def _cancel_bands(notices: list[str]) -> str:
    """The 游客取消规则 the terms write as a table of day bands (出发前30-15日 收取5%…), as one
    text. The 团体订位 sentence says the air ticket is not refunded and says nothing about the
    land part, which is what these bands price."""
    rows = [
        re.sub(r"\s+", " ", item).strip()
        for item in notices
        if ("取消" in item or "退改" in item) and _DAY_BANDS.search(item)
    ]
    return "；".join(rows[:6])[:MAX_CANCELLATION] if rows else ""


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
    # A 火车参考班次, a 参考船次 and a 参考航班：待定 are the day's transport as written: none of
    # them is a flight to read fields off, and none of them is a place of the title.
    notes = [
        clean
        for found in _TRANSPORT_NOTE.finditer(record.title + PARAGRAPH_SEPARATOR + text)
        if (clean := found[0].strip(" ，,、"))
    ]
    transport = "；".join([drives, *notes]).strip("；") if notes else drives
    for line in raw_lines:
        if "交通：" in line or "交通:" in line:
            tail = line.split("交通")[-1].split(CELL_SEPARATOR)[0].strip("：: ")
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
    when only the word stands there. A price in a sentence about 退门票 — 若均预约不上，退门票
    16欧/人, 境外直退门票18欧/人 — is money the 线路 gives back and is no item at all."""
    items = []
    clean = _nfkc(text)
    for match in _OPTIONAL_PRICED.finditer(clean):
        if "退" in re.split(r"[。；;]", clean[: match.end()])[-1]:
            continue
        name = _OPTIONAL_TAIL.sub("", _OPTIONAL_LEAD.sub("", match[1].strip(" ★#"))).strip()
        if not name:
            clause = re.split(r"[，,。；：:、/]", clean[: match.start()].rstrip("，,。；：:、/ "))[
                -1
            ]
            name = _OPTIONAL_LEAD.sub("", clause.strip(" ★#")).strip()
        if name and all(i.name != name for i in items):
            items.append(OptionalItem(name=name[:80], price=match[2].replace(" ", ""), day=day))
    return items


def _apply_ticket_list(days: list[Day], inclusions: list[str]) -> None:
    """``所含景点首道门票（其余景点均为外观）：马德里皇宫、塞哥维亚古城…``: the 包含 list names
    which sights the price's 首道门票 covers. A sight it names has its ticket; a 景点 it does
    not name has none. Its kind stays 景点 even where the line says 其余景点均为外观: the
    second review round found 211 squares, bridges and 市区观光 entries turned into 外观 by
    that tail, and a 景点 with ``ticket_included`` False is what the reviewers write."""
    for item in inclusions:
        listed_in = _TICKET_LIST.match(item)
        if listed_in is None:
            continue
        listed = [
            clean
            for name in re.split(r"[、，,/；;]", listed_in[1])
            if len(clean := _NAME_TAIL.sub("", _PAREN.sub("", name).strip()).strip()) >= 2
        ]
        if not listed:
            continue
        for day in days:
            for sight in day.sights:
                if sight.kind not in ("景点", "外观") or sight.ticket_included is not None:
                    continue
                if any(name in sight.name or sight.name in name for name in listed):
                    sight.ticket_included = True
                elif sight.kind == "景点":
                    # The kind stays 景点: a 广场, a bridge or a 市区观光 entry the list leaves
                    # out is not an 外观 in the reviewers' reading, whatever the line's tail
                    # says; the tail only settles that no ticket is in the price.
                    sight.ticket_included = False


def _countries(name: str, days: list[Day], cover: Cover, tagged: list[str]) -> list[str]:
    """The countries the itinerary goes to, read off what states where it goes: the 线路 name,
    the day titles, the places those titles name and the cover's own rows. A country named in
    a day's prose alone is part of a sight's name (英国花园的大花钟) and is not one of them.
    Where none of those names a country the line's tags answer instead, without the region
    words (东欧, 北欧, 巴尔干) they carry, which are not countries."""
    found: list[tuple[int, int, str]] = []
    # The cover's meal row names dishes (土耳其烤肉卷), not countries; the rest of it counts.
    texts = [
        name,
        *(part for day in days for part in (day.title, " ".join(day.places))),
        *(
            value
            for label, value in cover.fields.items()
            if not any(word in label for word in ("餐", "吃", "美食"))
        ),
    ]
    for index, text in enumerate(texts):
        for country, words in _COUNTRY_WORDS:
            at = min((text.find(word) for word in words if word in text), default=-1)
            if at >= 0:
                found.append((index, at, country))
    ordered = list(dict.fromkeys(country for _, _, country in sorted(found)))
    return ordered or [tag for tag in tagged if tag not in _REGION_WORDS]


def _doubts(days: list[Day], cover: Cover) -> list[str]:
    """What the parser cannot decide and a product person can: a 茶园 or 香料园 kept as a
    sight, a sight the prose calls a 购物场所 without the 线路 listing it as a stop, a line
    saying 此处不算购物店, and a 赠送 promised on the cover."""
    notes = []
    for day in days:
        for sight in day.sights:
            if sight.kind != "购物" and any(w in sight.name for w in _SHOP_DOUBT_WORDS):
                notes.append(
                    f"第{day.day}天【{sight.name}】疑似购物点，附件未列为购物店，请产品确认"
                )
        for sentence in _SENTENCE.finditer(day.text):
            word = next((w for w in _SHOP_DOUBT_CONTEXT if w in sentence[0]), "")
            for sight in day.sights:
                if word and sight.kind not in ("购物", "自由活动") and sight.name in sentence[0]:
                    notes.append(
                        f"第{day.day}天【{sight.name}】附件称其为{word}，疑似购物点，请产品确认"
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
    reader cannot open, as ``itinerary_source.document_lines`` does. A .pdf is read in each
    of pdftotext's two orders and the more complete document is kept."""
    if data.startswith(PDF_MAGIC):
        docs = [
            _build(record, pdf_lines(data, mode), data, etag, sale_type, f"pdf-{mode}")
            for mode in PDF_MODES
        ]
        return max(docs, key=lambda d: d.quality.completeness)
    return _build(record, document_lines(data), data, etag, sale_type, "docx")


def _build(
    record: RouteRecord,
    lines: list[str],
    data: bytes,
    etag: str | None,
    sale_type: str,
    reading: str,
) -> RouteDoc:
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
    _apply_ticket_list(days, inclusions)
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
            countries=_countries(record.route_name, days, cover, list(facets.destinations)),
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
            parser=f"{PARSER_VERSION}/{reading}",
        ),
        quality=Quality(completeness=0.0),
    )
    completeness, notes = score(doc)
    doc.quality = Quality(completeness=completeness, needs_review=notes + _doubts(days, cover))
    return doc
