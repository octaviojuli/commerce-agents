"""Locatable cross-field conflicts; operator acknowledgements bind to exact content."""

import re

from .integrations import fingerprint

ACKNOWLEDGEABLE = {"IMAGE_TEXT_UNREAD", "MEAL_OVERVIEW_CONFLICT", "HOTEL_GRADE_CONFLICT"}


def duration_label(days, nights=None):
    return f"{days} 天" + (f"（{nights} 晚）" if nights is not None else "")


def departure_mismatch_message(days, nights, departures):
    """Bounded date examples for every duration group, without inferring flight rules."""
    groups = {}
    for _, duration, *span in departures:
        groups.setdefault(duration, []).append(span)
    parts = []
    ordered = sorted(groups.items())
    for duration, spans in ordered[:5]:
        examples = sorted(f"{s[0]} 至 {s[1]}" for s in spans if len(s) == 2 and all(s))
        label = f"{duration} 天，共 {len(spans)} 个团期"
        if examples:
            label += "（" + "、".join(examples[:3]) + ("等" if len(examples) > 3 else "") + "）"
        parts.append(label)
    if len(ordered) > 5:
        parts.append(f"另有 {len(ordered) - 5} 种天数，请在团期列表核对")
    return (
        f"当前整理稿 {duration_label(days, nights)}；适用团期天数不一致："
        + "；".join(parts)
        + "。团期天数按出发、返回日期首尾都计入，请核对原文件与适用范围。"
    )


def basis(doc):
    return fingerprint(doc.model_dump(mode="json", exclude={"quality"}))


def checks(doc, departure_days=()):
    result = []

    def add(path, code, message, **details):
        result.append({"path": path, "code": code, "message": message, **details})

    variants = doc.source.variants
    if variants and (
        doc.applicability.version_label not in {v.label for v in variants}
        or not doc.applicability.start
        or not doc.applicability.end
    ):
        add(
            "applicability",
            "VARIANTS_DETECTED",
            "原文件包含多个版本，请选定原文版本、适用日期并整理对应的完整逐日内容",
            ranges=[v.model_dump() for v in variants],
        )
    if bool(doc.applicability.start) != bool(doc.applicability.end):
        add("applicability", "APPLICABILITY_INCOMPLETE", "适用日期必须同时填写开始和结束日期")
    for page in doc.source.unread_image_pages:
        add(
            f"source/unread_image_pages/{page}",
            "IMAGE_TEXT_UNREAD",
            f"第 {page} 页图片文字未读取，请对照原文件核对酒店标准和航空公司",
            pages=[page],
        )

    def items(rows):
        for n, row in enumerate(rows):
            for item in re.split(r"[；;。\n]", row):
                value = re.sub(
                    r"^(?:\d+[、.．]|费用包含[：:]?|费用不含[：:]?|包含|不含|含)", "", item.strip()
                )
                value = re.sub(r"[\s：:,，]", "", value)
                if value:
                    yield n, value

    excluded = list(items(doc.exclusions))
    for i, left in items(doc.inclusions):
        for j, right in excluded:
            if left == right:
                add(
                    f"inclusions/{i}",
                    "FEE_CONFLICT",
                    "同一项目同时列入费用包含与不含",
                    related_path=f"exclusions/{j}",
                )
    cover_grade = re.search(r"([一二三四五六1-6])\s*星", doc.cover.hotel_standard)

    def normalize(value):
        return value.translate(str.maketrans("一二三四五六", "123456"))

    for i, day in enumerate(doc.days):
        if day.overnight == "flight" and not day.flights:
            add(
                f"days/{i}/flights",
                "OVERNIGHT_FLIGHT_MISSING",
                f"第 {day.day} 天在飞机过夜，但没有参考航班",
            )
        if cover_grade and day.hotel:
            grade = re.search(r"([一二三四五六1-6])\s*星", day.hotel.grade)
            if grade and normalize(grade[1]) != normalize(cover_grade[1]):
                add(
                    f"days/{i}/hotel/grade",
                    "HOTEL_GRADE_CONFLICT",
                    f"第 {day.day} 天酒店标准与封面不同，请保留实际例外并核对",
                    related_path="cover/hotel_standard",
                )
        overview = doc.source.overview_meals.get(str(day.day))
        if overview:
            for meal in ("breakfast", "lunch", "dinner"):
                before = getattr(overview, meal).included
                after = getattr(day.meals, meal).included
                if before is not None and after is not None and before != after:
                    add(
                        f"days/{i}/meals/{meal}",
                        "MEAL_OVERVIEW_CONFLICT",
                        f"第 {day.day} 天用餐与原文概览表不一致",
                        related_path=f"source/overview_meals/{day.day}/{meal}",
                    )
    nights = sum(d.overnight in {"hotel", "ship"} for d in doc.days)
    if doc.summary.nights is not None and nights != doc.summary.nights:
        add("summary/nights", "NIGHTS_MISMATCH", "酒店及船上过夜晚数与标称住宿晚数不一致")
    for departure, days, *span in departure_days:
        if days is not None and days != doc.summary.days:
            add(
                "summary/days",
                "DEPARTURE_DURATION_MISMATCH",
                departure_mismatch_message(
                    doc.summary.days, doc.summary.nights, [(departure, days, *span)]
                ),
                departure_id=str(departure),
                expected_days=days,
            )
    digest = basis(doc)
    acknowledgements = {
        (r.code, r.path, r.basis_hash) for r in doc.quality.resolutions if r.note.strip()
    }
    for item in result:
        item["acknowledgeable"] = item["code"] in ACKNOWLEDGEABLE
        item["basis_hash"] = digest
        item["resolved"] = (
            item["acknowledgeable"] and (item["code"], item["path"], digest) in acknowledgements
        )
    return result
