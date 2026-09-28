"""Conservative additive fields from explicit native source sentences."""

import re
from decimal import Decimal

from cloud_warehouse.route_doc import CancellationTier, Money, SourceVariant

MONEY = re.compile(
    r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<currency>人民币|美元|美金|欧元|元|USD|EUR|CNY)\s*(?:[/／每]\s*(?P<unit>人|间|晚))?"
)


def money(raw, line=None, label=None):
    portion = raw
    if label:
        match = re.search(label, raw)
        portion = raw[match.start() :] if match else ""
    match = MONEY.search(portion)
    if not match:
        return Money(raw=raw, source_lines=[line] if line else [])
    return Money(
        amount=Decimal(match["amount"]),
        currency={"元": "CNY", "人民币": "CNY", "美元": "USD", "美金": "USD", "欧元": "EUR"}.get(
            match["currency"], match["currency"]
        ),
        unit={"人": "person", "间": "room", "晚": "night"}.get(match["unit"]),
        raw=raw,
        source_lines=[line] if line else [],
    )


def enrich(doc, lines, overview):
    """Unknown or ambiguous values stay empty; each addition retains its sentence."""
    doc.schema_version = "3.0"
    doc.source.lines = [str(line) for line in lines]
    doc.source.overview_meals = {str(day): value[0] for day, value in overview.items()}
    starts = []
    for number, line in enumerate(lines, 1):
        label = re.match(
            r"^\s*((?:[A-ZＡ-Ｚ][ /／]?版)|(?:\d{1,2}\s*[–—至~-]\s*\d{1,2}\s*月版)|(?:春|夏|秋|冬)(?:季)?版)\s*[：:]?\s*$",
            line,
        )
        if label:
            starts.append((number, label[1]))
    # A repeated complete itinerary start is evidence even without a version label.
    if len(starts) < 2:
        headers = [
            (n, re.match(r"^\s*(?:第\s*(\d+)\s*天|D(?:AY)?\s*[- ]?(\d+)\b)", line, re.I))
            for n, line in enumerate(lines, 1)
            if "||" not in line
        ]
        headers = [(n, int(match[1] or match[2])) for n, match in headers if match]
        firsts = [n for n, day in headers if day == 1]
        # A duplicate D1 alone is an anomaly, not proof of two complete versions.
        if len(firsts) > 1 and not all(
            any(day == doc.summary.days and start <= n < stop for n, day in headers)
            for start, stop in zip(firsts, firsts[1:] + [len(lines) + 1], strict=True)
        ):
            firsts = []
        if len(firsts) > 1:
            starts = [(n, f"原文版本 {i + 1}") for i, n in enumerate(firsts)]
    if len(starts) > 1:
        doc.source.variants = [
            SourceVariant(
                label=label,
                start_line=n,
                end_line=starts[i + 1][0] - 1 if i + 1 < len(starts) else len(lines),
            )
            for i, (n, label) in enumerate(starts)
        ]
    for n, line in enumerate(lines, 1):

        def evidence(field, line=line, n=n):
            field.raw = (field.raw + "\n" + line).strip()
            if n not in field.source_lines:
                field.source_lines.append(n)

        match = re.search(r"(\d+)\s*天\s*(\d+)\s*[晚夜]", line)
        if match and int(match[1]) == doc.summary.days and not doc.source.variants:
            doc.summary.nights = int(match[2])
        match = re.search(r"(?<![未不])(?:最少|最低|至少|满)\s*(\d+)\s*人(?:起)?成团", line)
        if match and doc.formation.minimum_travelers is None:
            doc.formation.minimum_travelers = int(match[1])
            evidence(doc.formation)
        if re.search(r"不成团|未成团|不足.{0,8}成团", line):
            doc.formation.failure_action = line
            evidence(doc.formation)
        if re.search(r"截止收客|收客截止", line):
            doc.formation.booking_deadline = line
            evidence(doc.formation)
        if re.search(r"司导服务费|导游司机服务费|小费", line) and doc.service_fee.amount is None:
            doc.service_fee = money(line, n, r"司导服务费|导游司机服务费|小费")
        if "单房差" in line and doc.single_room_supplement.amount is None:
            doc.single_room_supplement = money(line, n, "单房差")
        tier = re.search(
            r"出发前\s*(\d+)\s*(?:[–—至~-]\s*(\d+))?\s*天(?:取消|退团)?[，,:：\s]*(?:收取|扣除)\s*(\d+(?:\.\d+)?)\s*(%|％|元|美元|欧元)",
            line,
        )
        if tier:
            amount = Decimal(tier[3])
            doc.cancellation_tiers.append(
                CancellationTier(
                    days_before_min=min(int(tier[1]), int(tier[2] or tier[1])),
                    days_before_max=max(int(tier[1]), int(tier[2] or tier[1])),
                    penalty_percent=amount if tier[4] in {"%", "％"} else None,
                    penalty_money=money(tier[3] + tier[4], n)
                    if tier[4] not in {"%", "％"}
                    else None,
                    raw=line,
                    source_lines=[n],
                )
            )
        for key, pattern in [("child", r"儿童|小童"), ("senior", r"老人|老年|长者")]:
            if re.search(pattern, line):
                field = getattr(doc.traveler_requirements, key)
                evidence(field)
                field.conditions = field.raw
                ages = re.search(r"(\d+)\s*[–—至~-]\s*(\d+)\s*岁", line)
                if ages:
                    field.minimum_age = int(ages[1])
                    field.maximum_age = int(ages[2])
                if re.search(r"不占床|占床", line):
                    field.bed_policy = line
        if re.search(r"孕妇|妊娠", line):
            evidence(doc.traveler_requirements.pregnancy)
        visa = doc.traveler_requirements.visa
        if re.search(r"申根|电子签|落地签|免签|送签|护照有效", line):
            evidence(visa)
            types = [
                v
                for word, v in [
                    ("申根", "schengen"),
                    ("电子签", "electronic"),
                    ("落地签", "on_arrival"),
                    ("免签", "exempt"),
                ]
                if word in line
            ]
            if len(types) == 1:
                visa.type = types[0]
            if "送签" in line:
                visa.submission_deadline = line
            if "护照有效" in line:
                visa.passport_validity = line
        if "保险" in line:
            field = doc.traveler_requirements.insurance
            evidence(field)
            field.description = field.raw
            if re.search(r"不含.{0,8}保险|保险.{0,6}自理", line):
                field.included = False
            elif re.search(r"含.{0,8}保险", line):
                field.included = True
        if re.search(r"集合地点|集合时间|国内联运", line):
            field = doc.meeting
            evidence(field)
            for label, key in [
                ("集合地点", "location"),
                ("集合时间", "time"),
                ("国内联运", "domestic_connection"),
            ]:
                m = re.search(label + r"\s*[：:]\s*([^；;。]+)", line)
                if m:
                    setattr(field, key, m[1].strip())
        total = re.search(r"(?:共计|共|购物店总数[：: ]*)\s*(\d+)\s*(?:家|个)?\s*购物店", line)
        if total:
            doc.shopping_total = int(total[1])
    for day in doc.days:
        day.cities = list(day.places)
        day.location_raw = day.title
        explicit = re.search(r"(?:所在国家|国家)[：:]\s*([^，,；;。\n]+)", day.text)
        if explicit:
            day.countries = [x.strip() for x in re.split(r"[/、]", explicit[1]) if x.strip()]
        vehicle = re.search(
            r"(?:乘坐|用车[：:]?)\s*(巴士|大巴|旅游巴士|火车|高铁|游轮|邮轮|小巴|商务车)", day.text
        )
        if vehicle:
            day.vehicle_type = vehicle[1]
            day.vehicle_raw = vehicle[0]
        if day.hotel:
            day.hotel.raw = next(
                (line for line in day.text.splitlines() if "住宿" in line), day.hotel.name
            )
            room = re.search(r"双床房|大床房|单人房|三人房", day.text)
            nights = re.search(r"连住\s*(\d+)\s*晚", day.text)
            if room:
                day.hotel.room_type = room[0]
                day.hotel.raw += " " + room[0]
            if nights:
                day.hotel.consecutive_nights = int(nights[1])
                day.hotel.raw += " " + nights[0]
        for flight in day.flights:
            t = re.search(
                r"(\d{2}):?(\d{2})\s*[-—–]\s*(\d{2}):?(\d{2})(?:\s*\+(\d))?", flight.times
            )
            if t and all(int(t[i]) < 24 for i in [1, 3]) and all(int(t[i]) < 60 for i in [2, 4]):
                flight.departure_local_time = f"{t[1]}:{t[2]}"
                flight.arrival_local_time = f"{t[3]}:{t[4]}"
                flight.arrival_day_offset = int(t[5]) if t[5] else None
        for sight in day.sights:
            sight.raw = next(
                (s.strip() for s in re.split(r"[。\n]", day.text) if sight.name in s), ""
            )
    doc.transport = [flight for day in doc.days for flight in day.flights]
    for item in doc.optional:
        item.name = item.name.strip("【】 ")
        item.raw = item.price
        item.money = money(item.price)
    for shop in doc.shopping:
        raw = next((s for s in lines if shop.name in s), "")
        shop.raw = raw
        categories = re.search(r"经营品类[：:]\s*([^。；;]+)", raw)
        if categories:
            shop.categories = [s.strip() for s in re.split(r"[、,，]", categories[1]) if s.strip()]
    return doc
