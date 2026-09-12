# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The .docx 行程附件 read into a ``RouteDoc``: the two production layouts as the fixtures in
``test_itinerary_source`` build them, extended with the flights, the KM figures, the 【景点】,
the terms and the front matter the parser reads on top of the days."""

from tour.api.erp_client import RouteRecord
from tour.api.route_parser import _places, parse_route, score
from tour.api.tests.test_itinerary_source import docx, paragraph, table


def record(days: int = 7, name: str = "东航直飞斯里兰卡7天5晚") -> RouteRecord:
    return RouteRecord(
        route_id=421,
        route_code="WXEY",
        route_name=name,
        days=days,
        depart_city="上海",
        company_name="斯里兰卡",
        from_price=16999,
        tags=(),
        itinerary_tags=("斯里兰卡", "上海出发", "网评5钻酒店"),
        price_tags=(),
        features=(),
        image_url=None,
        attachment_name="斯里兰卡7天5晚.docx",
        attachment_url="https://files.example/a.docx",
    )


# Layout one: a cover table, then one table whose rows are the day header, 用餐, 住宿 and the
# programme, then the terms as paragraphs.
LAYOUT_ONE = docx(
    table(
        [
            ["航空公司", "优选东航直飞航班，默认上海出发"],
            ["酒店标准", "全程网评5钻酒店，2晚海边五钻酒店连住"],
            ["用餐安排", "酒店含早晚餐，午餐当地特色餐"],
            ["行程亮点", ["★【天空之城】攀登狮子岩", "★【野趣锡兰】乘坐吉普车探索国家公园"]],
        ]
    ),
    table(
        [
            [
                "第 1 天 上海-科伦坡 参考航班：MU6017 上海浦东-科伦坡 夏令时1425-1900/冬令时1355-1900"
            ],
            ["用餐", "早：X", "中：X", "晚：X"],
            ["住宿", "AVENRA ARDEN/GOLDI SANDS或同级", "交通：飞机+旅游用车"],
            ["★ 于指定时间在上海浦东国际机场集合；抵达后接机送往酒店。"],
            ["第 2 天 科伦坡-（车程约3小时）丹布勒"],
            ["用餐", "早：酒店内早餐", "中：当地餐食", "晚：酒店内晚餐"],
            ["住宿", "CAMELLIA RESORT或同级", "交通：旅游用车"],
            [
                "早餐后游览【狮子岩】（约2H），随后参观【著名品牌宝石店】【约60分钟】，之后【龙达斗牛场（外观）】。"
            ],
            ["第 3 天 丹布勒-科伦坡机场 参考航班：MU6018 科伦坡-上海浦东 2020-0535+1"],
            ["用餐", "早：打包早", "中：中式桌餐", "晚：X"],
            ["住宿", "飞机上", "交通：旅游用车+飞机"],
            [
                "一整天自由活动，也可参加自费项目：推荐自费套餐 加勒古堡+海龟抚育中心 自费 120 美金/人。赠送观鲸。"
            ],
            ["第 4 天 上海浦东机场"],
            ["用餐", "早：X", "中：X", "晚：X"],
            ["住宿", "无", "交通：无"],
            ["到达上海浦东国际机场，结束愉快旅程！"],
        ]
    ),
    paragraph("包含项目"),
    paragraph("1、行程中所含上海-科伦坡-上海机票和燃油税；"),
    paragraph("2、斯里兰卡签证；"),
    paragraph("不包含项目"),
    paragraph("服务费1000元/人"),
    paragraph("如产生单人需补单房差"),
    paragraph("购物"),
    paragraph("宝石店 60分钟"),
    paragraph("旅行团须知"),
    paragraph("1、出发当天请严守团队集合时间。"),
)

# Layout two: inline 吃/住/行 cover lines, an English overview table, then the detail table
# whose days carry 餐饮/住宿 lines, then 预定须知 with spaced headings.
LAYOUT_TWO = docx(
    paragraph("产品特色"),
    paragraph("吃 中式用餐7菜1汤，另外特别安排6大特色餐"),
    paragraph("住 全程精选国际连锁4星酒店含自助早餐"),
    paragraph("行 优选MU中国东方航空，巴塞罗那进马德里出"),
    table(
        [
            ["DATE", "ITINERARY", "MEAL", "HOTEL"],
            ["DAY-1", "上海", "早餐：× 午餐：× 晚餐：×", "机场集合"],
            ["DAY-2", "上海巴塞罗那", "早餐：× 午餐：× 晚餐：升级", "国际连锁4星酒店"],
            ["DAY-3", "巴塞罗那-瓦伦西亚", "早餐：含 午餐：× 晚餐：含", "国际连锁4星酒店"],
            ["DAY-4", "上海", "早餐：× 午餐：× 晚餐：", "家"],
        ]
    ),
    table(
        [
            ["第一天", "上海"],
            ["上海浦东国际机场集合，乘坐中国东方航空公司航班飞往巴塞罗那，夜宿飞机上。"],
            ["第二天", "上海巴塞罗那 参考航班：MU249 PVG-BCN 01:20-07:40"],
            [
                "【巴塞罗那 城市观光】（观光时间不少于2小时）【圣家族大教堂 含门票和官导*】（观光时间不少于 1 小时）【米拉之家（外观）】特别安排---【McArthurGlen Malaga购物村】（自由活动时间不少于2小时）"
            ],
            ["餐饮：早餐：自理 午餐：自理 晚餐：&lt;伊比利亚火腿餐&gt;"],
            ["住宿：巴塞罗那或周边"],
            ["第三天", "巴塞罗那-350KM-瓦伦西亚"],
            ["【科学艺术城】【瓦伦西亚大教堂（外观）】"],
            ["餐饮：早餐：酒店内 午餐：自理 晚餐：7菜1汤"],
            ["住宿：瓦伦西亚或周边"],
            ["第四天", "上海"],
            ["抵达上海浦东机场，请将您的护照交予领队销签。"],
        ]
    ),
    paragraph("预 定 须 知"),
    paragraph("服 务 所 包 含 项 目"),
    paragraph("全程国际机票费用、燃油附加税； / 全程国际连锁4星酒店住宿；"),
    paragraph("服 务 所 不 含 项 目"),
    paragraph("单人间房差：5000元/间; 6周岁以下儿童不占床减2000元； / 申根ADS签证费用"),
)


def test_layout_one_reads_flights_places_sights_meals_hotels_and_terms():
    doc = parse_route(record(4), LAYOUT_ONE)
    assert doc.route_id == 421 and doc.summary.days == 4 and doc.summary.nights == 2
    assert doc.cover.airline.startswith("优选东航") and doc.cover.hotel_standard.startswith(
        "全程网评5钻"
    )
    assert doc.cover.highlights == ["【天空之城】攀登狮子岩", "【野趣锡兰】乘坐吉普车探索国家公园"]
    # Flights: both segments, with the number, the route and the times as written.
    assert [(f.day, f.flight_no, f.from_place, f.to_place) for f in doc.transport] == [
        (1, "MU6017", "上海浦东", "科伦坡"),
        (3, "MU6018", "科伦坡", "上海浦东"),
    ]
    assert doc.transport[0].carrier == "中国东方航空" and "1425-1900" in doc.transport[0].times
    assert doc.transport[1].times == "2020-0535+1"
    d1, d2, d3, d4 = doc.days
    assert d1.places == ["上海", "科伦坡"] and d1.overnight == "hotel"
    assert (
        d1.hotel is not None and d1.hotel.or_similar and d1.hotel.name == "AVENRA ARDEN/GOLDI SANDS"
    )
    assert d1.transport == "飞机+旅游用车"
    assert d2.places == ["科伦坡", "丹布勒"] and d2.transport.startswith("车程约3小时")
    assert [(s.name, s.kind, s.duration, s.ticket_included) for s in d2.sights] == [
        ("狮子岩", "景点", "2H", None),
        ("著名品牌宝石店", "购物", "60分钟", None),
        ("龙达斗牛场", "外观", "", False),
    ]
    assert d2.meals.breakfast.included and d2.meals.lunch.text == "当地餐食"
    assert not d1.meals.breakfast.included and d1.meals.breakfast.text == "X"
    assert d3.overnight == "flight" and d3.hotel is None and not d3.meals.dinner.included
    assert d4.overnight == "home"
    assert doc.inclusions == ["行程中所含上海-科伦坡-上海机票和燃油税", "斯里兰卡签证"]
    assert doc.exclusions == ["服务费1000元/人", "如产生单人需补单房差"]
    # The 购物店 from the day and the 购物 section, the 自费 with its price.
    assert [(s.name, s.day) for s in doc.shopping] == [
        ("宝石店 60分钟", None),
        ("著名品牌宝石店", 2),
    ]
    assert [(o.name, o.price, o.day) for o in doc.optional] == [
        ("加勒古堡+海龟抚育中心", "120美金/人", 3)
    ]
    assert (
        doc.policies.single_room == "如产生单人需补单房差" and doc.policies.visa == "斯里兰卡签证"
    )
    assert doc.notices[0] == "旅行团须知"
    assert doc.quality.completeness == 1.0 and doc.quality.needs_review == []
    assert doc.source.parser == "docx-rules-3/docx" and doc.source.bytes == len(LAYOUT_ONE)


def test_layout_two_reads_inline_cover_km_figures_and_the_overview_fallback():
    doc = parse_route(record(4, "伊比利亚狂曲 西葡深度"), LAYOUT_TWO)
    assert doc.cover.meal_standard.startswith("中式用餐7菜1汤")
    assert doc.cover.hotel_standard.startswith("全程精选国际连锁4星")
    assert doc.cover.airline.startswith("优选MU")
    d1, d2, d3, d4 = doc.days
    # Day 1 has no 住宿 line: 夜宿飞机上 says the night, the overview says the meals.
    assert d1.overnight == "flight" and d1.meals.breakfast.included is False
    assert d2.flights[0].flight_no == "MU249" and d2.flights[0].from_place == "上海浦东"
    assert d2.hotel is not None and d2.hotel.name == "巴塞罗那或周边" and not d2.hotel.or_similar
    assert d2.meals.dinner.text == "<伊比利亚火腿餐>" and d2.meals.dinner.included
    names = [(s.name, s.kind, s.ticket_included) for s in d2.sights]
    assert ("圣家族大教堂", "景点", True) in names and ("米拉之家", "外观", False) in names
    assert ("McArthurGlen Malaga购物村", "购物", None) in names
    assert d3.places == ["巴塞罗那", "瓦伦西亚"] and d3.distances_km == [350]
    assert d4.overnight == "home"
    # Spaced headings are headings; the 费用 lists read through them.
    assert doc.inclusions == ["全程国际机票费用、燃油附加税", "全程国际连锁4星酒店住宿"]
    assert doc.exclusions[1] == "申根ADS签证费用"
    assert (
        doc.policies.single_room.startswith("单人间房差：5000元")
        and doc.policies.visa == "申根ADS签证费用"
    )
    assert doc.quality.completeness == 1.0, doc.quality.needs_review


def test_a_document_that_names_no_day_scores_zero_and_says_why():
    doc = parse_route(record(7), docx(paragraph("这是一份没有行程的文件")))
    assert doc.days == [] and doc.quality.completeness == 0.0
    assert "附件天数 0 与 ERP 天数 7 不一致" in doc.quality.needs_review
    assert "没有读到参考航班" in doc.quality.needs_review


def test_the_score_names_each_missing_field():
    doc = parse_route(record(4), LAYOUT_ONE)
    doc.transport = []
    doc.days[1].hotel = None
    doc.days[1].overnight = "unknown"
    completeness, notes = score(doc)
    # Day 2 was the one hotel day with sights, so that check falls too.
    assert completeness == 0.65
    assert notes == ["有的天没有读到住宿", "没有读到参考航班", "有的行程日没有读到景点"]


def test_a_flight_written_route_first_with_slashed_times_is_read_too():
    """``参考航班： 上海-科伦坡 MU231 14：25/19:00``: the route before the number, the times
    joined by a slash and a full-width colon."""
    doc = parse_route(
        record(2),
        docx(
            table(
                [
                    ["第 1 天 上海-科伦坡 参考航班： 上海-科伦坡 MU231 14：25/19:00"],
                    ["用餐", "早：X", "中：X", "晚：X"],
                    ["住宿", "酒店或同级", "交通：飞机"],
                    ["第 2 天 科伦坡-上海"],
                    ["用餐", "早：酒店内", "中：X", "晚：X"],
                    ["住宿", "无", "交通：飞机"],
                ]
            )
        ),
    )
    flight = doc.transport[0]
    assert (flight.flight_no, flight.from_place, flight.to_place, flight.times) == (
        "MU231",
        "上海",
        "科伦坡",
        "14:25/19:00",
    )


# The lines the first review round found the rules reading wrong: a 均为外观 tail, a 自费
# package's 参考行程, a 赠送 note, a 自由活动, a priced line without the word 自费, airport
# codes, a night on a ship, and the 退改 sentence in the 旅游责任 notice.
REVIEWED = docx(
    table([["航空公司", "东航直飞"], ["行程亮点", ["★【网红打卡】赠送体验海边小火车"]]]),
    table(
        [
            ["第 1 天 上海-科伦坡 参考航班：MU6018 CMBPVG 2030-0610+1"],
            ["用餐", "早：X", "中：X", "晚：X"],
            ["住宿", "酒店或同级", "交通：飞机"],
            ["★ 于指定时间集合；抵达后接机。"],
            ["第 2 天 科伦坡-加勒"],
            ["用餐", "早：酒店内", "中：当地餐", "晚：酒店内"],
            ["住宿", "酒店或同级", "交通：旅游用车"],
            [
                "一整天自由活动，也可参加自费项目 / 推荐自费套餐：加勒古堡+海龟抚育中心 自费 120 美金/人"
                " / 参考行程如下: / 早餐后前往【海龟保育园】，之后参观【加勒古堡】。随后【赠送体验海边火车】"
                "返回，参观【红树林】。备注：红树林为赠送项目。途中经过【茶园】。"
            ],
            ["第 3 天 加勒-科伦坡"],
            ["用餐", "早：酒店内", "中：当地餐", "晚：X"],
            ["住宿", "豪华夜邮轮", "交通：旅游用车"],
            [
                "上午自由活动，可选增加美瑞莎观鲸半日游，收费120美金/人（满10人发团）。下午市区游，"
                "【独立广场】，【印度教寺庙】，【会议中心】均为外观或车游。"
            ],
            ["第 4 天 科伦坡-上海"],
            ["用餐", "早：打包", "中：X", "晚：X"],
            ["住宿", "无", "交通：飞机"],
        ]
    ),
    paragraph("包含项目"),
    paragraph("往返机票"),
    paragraph("不包含项目"),
    paragraph("因不可抗力的客观原因（如航班取消）而产生的额外的费用"),
    paragraph("旅游责任"),
    paragraph("行程中所安排之机票，均属团体订位，一经确认，概不退回任何款项。"),
)


def test_the_rules_the_first_review_round_added():
    doc = parse_route(record(4), REVIEWED)
    flight = doc.transport[0]
    assert (flight.from_place, flight.to_place, flight.raw) == (
        "科伦坡",
        "上海浦东",
        "参考航班：MU6018 CMBPVG 2030-0610+1",
    )
    d1, d2, d3, d4 = doc.days
    assert [(s.name, s.kind, s.ticket_included) for s in d2.sights] == [
        ("全天自由活动", "自由活动", None),
        ("海龟保育园", "自费", False),
        ("加勒古堡", "自费", False),
        ("海边火车", "赠送", None),
        ("红树林", "赠送", None),
        ("茶园", "景点", None),
    ]
    assert [(s.name, s.kind, s.ticket_included) for s in d3.sights] == [
        ("上午自由活动", "自由活动", None),
        ("独立广场", "外观", False),
        ("印度教寺庙", "外观", False),
        ("会议中心", "外观", False),
    ]
    assert d3.overnight == "ship" and doc.summary.nights == 3 and d4.overnight == "home"
    assert [(o.name, o.price, o.day) for o in doc.optional] == [
        ("加勒古堡+海龟抚育中心", "120美金/人", 2),
        ("海龟保育园", "", 2),
        ("美瑞莎观鲸半日游", "120美金/人", 3),
    ]
    assert (
        doc.policies.cancellation
        == "行程中所安排之机票，均属团体订位，一经确认，概不退回任何款项。"
    )
    assert doc.quality.needs_review == [
        "第2天【茶园】疑似购物点，附件未列为购物店，请产品确认",
        "封面写明赠送“【网红打卡】赠送体验海边小火车”，请核对各天的赠送标记",
    ]


# The lines the second review round found the rules reading wrong. One document per group of
# findings: the day programmes here, the terms in ``ROUND_TWO_TERMS``.
ROUND_TWO = docx(
    paragraph("产品特色"),
    paragraph("坚持真纯玩，推自费退团费郑重写入合同"),
    paragraph("美食篇 中式六菜一汤，另安排三道式火腿餐"),
    paragraph("住宿篇 全程国际连锁4-5星酒店"),
    table(
        [
            ["第 1 天 上海卡斯蒂亚-9KM-蓝湾镇 参考航班：：AC201 PVGBCN （01:20-07:40）"],
            ["餐饮 ：早餐：酒店内 午餐 ：六菜一汤 晚餐 ：火腿餐"],
            ["住宿", "蓝湾镇精选酒店 4-5星", "交通：旅游用车"],
            [
                " 【卡斯蒂亚老城 含官导*】（总观光+自由活动时间不少于2小时），"
                "打卡机位1---【钟塔（外观）】（打卡时间约10-15分钟）。"
                "【石桥公园 不含园内门票项目】（自由活动约30分钟）。"
                "【圣谷修道院（入内）含门票】。蓝湾游览（均外观）：议会广场，老港灯塔，橄榄园大门。"
                "【特别赠送】蓝湾镇入境纪念章（每人限盖一次）。"
                "温馨提示：若预约申请未获批，将调整为入内参观【王室画廊】代替原定游览内容。"
                "若均预约不上，退门票16 欧元/人。"
            ],
            [
                "第 2 天 蓝湾镇--40KM-北岸购物村-200KM-雪顶镇 火车参考班次：2100-0729+1（约10小时29分）"
            ],
            ["餐饮 ：早餐：酒店内 午餐 ：自理 晚餐 ：自理"],
            ["住宿", "雪顶镇山景酒店或同级 3-4星", "交通：火车"],
            [
                "上午自由活动。【北岸购物村】（自由活动时间不少于2小时）。"
                "【雪顶缆车】自费推荐，可在山顶用餐。【王室花园】（自费游览，周一闭馆）。"
                "【回廊长街】，这条长街自开幕以来一直是雪顶镇最受欢迎的购物广场，值得一游。"
                "【老码头市集】是当地居民常去的露天市场。"
            ],
            ["第 3 天 雪顶镇-上海 参考航班: 待定"],
            ["餐饮 ：早餐：打包 午餐 ：X 晚餐 ：X"],
            ["住宿", "无", "交通：飞机"],
            ["抵达上海浦东机场，结束愉快旅程！"],
        ]
    ),
    paragraph("包含项目"),
    paragraph("所含景点首道门票（其余景点均为外观）：圣谷修道院、卡斯蒂亚老城含官导"),
    paragraph("不包含项目"),
    paragraph("单人间房差：7000元/间；6周岁（不含6周岁）以下儿童不占床费用为成人费用基础打9折"),
    paragraph("另行付费"),
    paragraph("项目名称 || 价格（欧元/人） || 备注"),
    paragraph("王室花园(不含后花园，约 1.5 小时) || 110 || 含司机服务"),
    paragraph("雪顶缆车(约 2-2.5 小时) || 150-250 || 山顶用餐另计"),
    paragraph("夜航船巡游 || 120起 || 需提前一天预约"),
    paragraph("若均预约不上圣谷修道院，退门票 26 欧元/人，王室画廊退门票 11 欧元/人。"),
    paragraph("温馨提示"),
    paragraph("二、另行付费项目中，儿童、老人等人群与成人同价。"),
    paragraph("◎ 6 周岁以下不占床的儿童报名，优惠1000元/人。"),
    paragraph("行程中所安排之机票，均属团体订位，一经确认，概不退回任何款项。"),
    paragraph("游客取消规则：出发前30-15日收取团款5%，14-7日收取团款30%，出发当天收取70%。"),
    paragraph("送签后如遇使馆要求面试，请客人予以理解配合，销签时护照统一交领队。"),
)


def test_a_duration_parenthesis_is_not_a_free_afternoon():
    """``（总观光+自由活动时间不少于2小时）`` after a 【】 is that stop's duration; only the
    prose (上午自由活动) makes a 自由活动 entry of its own."""
    doc = parse_route(record(3, "西班牙卡斯蒂亚三日"), ROUND_TWO)
    d1, d2, _ = doc.days
    assert [s.name for s in d1.sights if s.kind == "自由活动"] == []
    assert (d1.sights[0].name, d1.sights[0].duration) == ("卡斯蒂亚老城", "2小时")
    assert (d1.sights[1].name, d1.sights[1].duration) == ("钟塔", "10-15分钟")
    assert [s.name for s in d2.sights if s.kind == "自由活动"] == ["上午自由活动"]


def test_a_ticket_is_the_门票_and_never_the_官导():
    """含官导 is a guide: it leaves the ticket unstated. 不含园内门票 states it is not in the
    price and keeps the stop a 景点; the ticket and guide notes are not part of the name."""
    doc = parse_route(record(3), ROUND_TWO)
    d1 = doc.days[0]
    by_name = {s.name: s for s in d1.sights}
    assert by_name["卡斯蒂亚老城"].ticket_included is True  # named in the 首道门票 list
    assert by_name["石桥公园"].kind == "景点" and by_name["石桥公园"].ticket_included is False
    assert by_name["圣谷修道院"].ticket_included is True
    assert by_name["钟塔"].kind == "外观" and by_name["钟塔"].ticket_included is False


def test_a_首道门票_list_and_a_prose_均外观_group_reach_the_sights():
    doc = parse_route(record(3), ROUND_TWO)
    d1 = doc.days[0]
    prose = [
        (s.name, s.kind, s.ticket_included)
        for s in d1.sights
        if s.name.endswith(("场", "塔", "门"))
    ]
    assert ("议会广场", "外观", False) in prose and ("老港灯塔", "外观", False) in prose
    # The list says 其余景点均为外观, so a 景点 it does not name is one.
    assert [s.name for s in d1.sights if s.kind == "外观"] != []


def test_a_conditional_bracket_is_no_sight_and_a_特别赠送_names_its_gift():
    doc = parse_route(record(3), ROUND_TWO)
    names = [s.name for day in doc.days for s in day.sights]
    assert "王室画廊" not in names
    assert ("蓝湾镇入境纪念章", "赠送") in [(s.name, s.kind) for s in doc.days[0].sights]


def test_自费推荐_beside_a_bracket_makes_it_自费():
    doc = parse_route(record(3), ROUND_TWO)
    kinds = {s.name: s.kind for s in doc.days[1].sights}
    assert kinds["雪顶缆车"] == "自费" and kinds["王室花园"] == "自费"


def test_the_另行付费_table_is_read_by_column_and_a_退门票_is_no_item():
    """A row with cells is an item whatever its name's length; the price is its column's,
    with the unit off the header, ranges and 起 as written. A 退门票 sentence is a refund."""
    doc = parse_route(record(3), ROUND_TWO)
    priced = {o.name: o.price for o in doc.optional}
    assert priced["王室花园(不含后花园，约 1.5 小时)"] == "110欧元/人"
    assert priced["雪顶缆车(约 2-2.5 小时)"] == "150-250欧元/人"
    assert priced["夜航船巡游"] == "120起欧元/人"
    assert not any("退门票" in name for name in priced)
    assert any("退门票" in notice for notice in doc.notices)


def test_the_title_reads_a_wingdings_airplane_a_double_dash_and_a_9km():
    doc = parse_route(record(3), ROUND_TWO)
    d1, d2, d3 = doc.days
    assert d1.places == ["上海", "卡斯蒂亚", "蓝湾镇"] and d1.distances_km == [9]
    assert d2.places == ["蓝湾镇", "北岸购物村", "雪顶镇"] and d2.distances_km == [40, 200]
    assert "" not in d1.title and "" not in d1.text
    # A 火车参考班次 and a 参考航班：待定 are the day's transport and no place of its title.
    assert "火车参考班次：2100-0729+1" in d2.transport
    assert "参考航班: 待定" in d3.transport and d3.places == ["雪顶镇", "上海"]


def test_the_flight_raw_keeps_its_label_and_its_full_width_parentheses():
    doc = parse_route(record(3), ROUND_TWO)
    flight = doc.transport[0]
    assert flight.flight_no == "AC201" and flight.from_place == "上海浦东"
    assert flight.raw == "参考航班：：AC201 PVGBCN （01:20-07:40）"


def test_a_grade_keeps_the_digits_the_attachment_wrote():
    doc = parse_route(record(3), ROUND_TWO)
    assert doc.days[0].hotel is not None and doc.days[0].hotel.grade == "4-5星"
    assert doc.days[1].hotel is not None and doc.days[1].hotel.grade == "3-4星"


def test_the_meals_row_is_read_through_the_space_before_its_colon():
    doc = parse_route(record(3), ROUND_TWO)
    meals = doc.days[0].meals
    assert (meals.breakfast.text, meals.lunch.text, meals.dinner.text) == (
        "酒店内",
        "六菜一汤",
        "火腿餐",
    )


def test_the_cover_reads_an_invented_label_and_the_line_under_产品特色():
    doc = parse_route(record(3), ROUND_TWO)
    assert doc.cover.meal_standard.startswith("中式六菜一汤")
    assert doc.cover.hotel_standard.startswith("全程国际连锁4-5星")
    assert doc.cover.highlights == ["坚持真纯玩，推自费退团费郑重写入合同"]


def test_the_policies_split_the_semicolon_and_skip_与成人同价_and_销签():
    doc = parse_route(record(3), ROUND_TWO)
    assert doc.policies.single_room == "单人间房差：7000元/间"
    assert doc.policies.child == "6周岁（不含6周岁）以下儿童不占床费用为成人费用基础打9折"
    assert doc.policies.visa == ""
    assert doc.policies.cancellation.startswith("行程中所安排之机票")
    assert "30-15日收取团款5%" in doc.policies.cancellation


def test_the_countries_come_from_the_titles_and_never_from_a_region_word():
    doc = parse_route(record(3, "东欧巴尔干风情·西班牙三日"), ROUND_TWO)
    assert doc.summary.countries == ["西班牙"]
    # A line whose titles name no country at all falls back to its tags, region words dropped.
    assert parse_route(record(3), ROUND_TWO).summary.countries == ["斯里兰卡"]


def test_a_购物场所_beside_a_sight_is_a_doubt_and_a_购物广场_is_a_shop():
    doc = parse_route(record(3), ROUND_TWO)
    kinds = {s.name: s.kind for s in doc.days[1].sights}
    assert kinds["回廊长街"] == "购物"
    assert kinds["老码头市集"] != "购物"
    assert any("老码头市集" in note and "露天市场" in note for note in doc.quality.needs_review)


def test_a_温馨提示_between_two_days_does_not_end_the_itinerary():
    """A .pdf reading writes 温馨提示 on a line of its own inside a day; the itinerary ends at
    the heading that has no day after it, and the 费用包含 family ends it wherever it stands."""
    doc = parse_route(
        record(3),
        docx(
            table(
                [
                    ["第 1 天 上海-卡斯蒂亚"],
                    ["用餐", "早：X", "中：X", "晚：X"],
                    ["住宿", "蓝湾镇精选酒店", "交通：飞机"],
                    ["第 2 天 卡斯蒂亚-蓝湾镇"],
                    ["用餐", "早：酒店内", "中：当地餐", "晚：酒店内"],
                    ["住宿", "蓝湾镇精选酒店", "交通：旅游用车"],
                    ["游览【议会广场】。"],
                ]
            ),
            paragraph("温馨提示"),
            paragraph("旺季车程可能延长，请客人预留时间。"),
            table(
                [
                    ["第 3 天 蓝湾镇-上海"],
                    ["用餐", "早：打包", "中：X", "晚：X"],
                    ["住宿", "无", "交通：飞机"],
                    ["抵达上海浦东机场，结束愉快旅程！"],
                ]
            ),
            paragraph("包含项目"),
            paragraph("往返机票"),
        ),
    )
    assert [day.day for day in doc.days] == [1, 2, 3]
    assert doc.inclusions == ["往返机票"]
    assert "旺季车程可能延长" in doc.days[2].text or "旺季车程可能延长" in doc.days[1].text


def test_a_赠送_written_without_a_bracket_is_a_sight_of_its_own():
    doc = parse_route(record(2), REVIEWED)
    assert ("观鲸", "赠送") not in [(s.name, s.kind) for s in doc.days[0].sights]
    gift = parse_route(record(4), LAYOUT_ONE).days[2].sights
    assert ("观鲸", "赠送") in [(s.name, s.kind) for s in gift]


def test_a_two_column_报价包含_table_is_read_by_column():
    """The 报价包含 ｜ 报价不含 table is printed side by side and read as one line per row;
    by column each list is its own, and a cell that does not close with ；or 。 is the row
    above wrapping."""
    doc = parse_route(
        record(2),
        docx(
            table(
                [
                    ["第 1 天 上海-卡斯蒂亚"],
                    ["用餐", "早：X", "中：X", "晚：X"],
                    ["住宿", "蓝湾镇精选酒店", "交通：飞机"],
                    ["第 2 天 卡斯蒂亚-上海"],
                    ["用餐", "早：酒店内", "中：X", "晚：X"],
                    ["住宿", "无", "交通：飞机"],
                ]
            ),
            table(
                [
                    ["报价包含", "报价不含"],
                    ["机票：全程国际机票及燃油附加费；", "单房差：如产生自然单间，请补单房差；"],
                    ["酒店：全程国际连锁四星酒店，除蓝湾镇", "个人消费：洗衣、电话及行李超重，"],
                    ["外均为市区酒店；", ""],
                    ["", "以及全程司机导游服务费。"],
                ]
            ),
        ),
    )
    assert doc.inclusions == [
        "机票：全程国际机票及燃油附加费",
        "酒店：全程国际连锁四星酒店，除蓝湾镇外均为市区酒店",
    ]
    assert doc.exclusions == [
        "单房差：如产生自然单间，请补单房差",
        "个人消费：洗衣、电话及行李超重，以及全程司机导游服务费。",
    ]
    assert doc.policies.single_room == "单房差：如产生自然单间，请补单房差"


def test_a_title_that_draws_its_connector_as_the_character_一():
    assert _places("成都一马德里一 110km 阿维拉")[:2] == (["成都", "马德里", "阿维拉"], [110])
    # A title that never writes 一 before a figure keeps the character inside its names.
    assert _places("波尔图-路易一世大桥")[0] == ["波尔图", "路易一世大桥"]


def test_a_hotel_row_that_writes_its_grade_after_the_names():
    """``AMAYA LAKE / CINNAMON LODGE 或五钻同级酒店``: the standard is the grade and not the
    tail of the last name, and a 赠送 named in prose is the gift and not the words after a
    colon."""
    doc = parse_route(
        record(2),
        docx(
            table(
                [
                    ["第 1 天 上海-科伦坡"],
                    ["用餐", "早：X", "中：X", "晚：X"],
                    ["住宿", "AMAYA LAKE / CINNAMON LODGE 或五钻同级酒店", "交通：飞机"],
                    [
                        "抵达后入住酒店。特别赠送：传统的【康提歌舞表演】。赠送观鲸：美蕊沙坐落在南部海岸。"
                    ],
                    ["第 2 天 科伦坡-上海"],
                    ["用餐", "早：酒店内", "中：X", "晚：X"],
                    ["住宿", "无", "交通：飞机"],
                ]
            )
        ),
    )
    hotel = doc.days[0].hotel
    assert hotel is not None and hotel.name == "AMAYA LAKE / CINNAMON LODGE"
    assert hotel.grade == "五钻" and hotel.or_similar
    assert [(s.name, s.kind) for s in doc.days[0].sights] == [
        ("康提歌舞表演", "赠送"),
        ("观鲸", "赠送"),
    ]
