"""Typed, evidence-preserving presentation candidates and customer projections."""

import hashlib
import re

from . import route_kit_content as kit
from .integrations import fingerprint
from .route_doc import ItineraryBlock, RouteDoc
from .route_editing import MEASURE, heading


def day_basis(day):
    """Fields that a daily brief must still describe after an edit."""
    return fingerprint(day.model_dump(exclude={"summary", "summary_basis_hash", "day_id"}))


def _paragraphs(value):
    # A parser's row separators are not always paragraph boundaries. Join wrapped
    # words without dropping any condition; break only at sentence/heading edges.
    parts = re.split(r"[；;\n]|\s/\s", value)
    result = []
    for part in parts:
        part = part.strip().replace(" || ", " · ")
        if not part:
            continue
        starts = re.match(
            r"^(?:今日|今天|早上|上午|中午|下午|晚上|晚间|全天|参考航班|自费推荐|温馨提示|备注|注意|【)",
            part,
        )
        if result and not starts and not result[-1].endswith(("。", "！", "？", "：", ":", "；")):
            result[-1] += part
        else:
            result.append(part)
    return result


def candidate(body, *, normalize_titles=False):
    """Convert once when preparing a draft; never generate new travel promises."""
    if kit.is_kit(body):
        return kit.prepare(body)
    doc = RouteDoc.model_validate(body).model_copy(deep=True)
    doc.schema_version = "3.0" if doc.schema_version == "3.0" else "2.0"
    doc.quality.reviewed_by = ""
    doc.quality.reviewed_at = None
    for day in doc.days:
        original_title = day.title
        if normalize_titles:
            day.title, day.travel_reference = heading(day)
        day.day_id = day.day_id or f"day-{day.day}"
        if not day.blocks:
            for n, paragraph in enumerate(_paragraphs(day.text)):
                kind = "programme"
                for prefix, value in (
                    ("参考航班", "transport"),
                    ("自费推荐", "optional"),
                    ("全天自由活动", "free_time"),
                    ("温馨提示", "notice"),
                    ("备注", "notice"),
                ):
                    if paragraph.startswith(prefix):
                        kind = value
                        break
                day.blocks.append(
                    ItineraryBlock(
                        block_id=f"{day.day_id}-{n + 1}-{hashlib.sha256(paragraph.encode()).hexdigest()[:8]}",
                        type=kind,
                        paragraphs=[paragraph],
                    )
                )
        if not day.summary or (normalize_titles and day.summary == original_title):
            # Exact route heading is a safe initial brief. Richer prose is an
            # operator edit, not an inferred promise or a blindly truncated clause.
            day.summary = day.title[:800]
        day.summary_basis_hash = day_basis(day)
    return doc


def issues(doc, *, resolutions=(), listing=None):
    if kit.is_kit(doc):
        return kit.issues(doc, resolutions=resolutions, listing=listing)
    result = []

    def issue(path, code, message):
        result.append({"path": path, "code": code, "message": message})

    from .route_consistency import checks

    result.extend(item for item in checks(doc) if not item["resolved"])

    expected = list(range(1, doc.summary.days + 1))
    if not doc.days or [d.day for d in doc.days] != expected:
        issue("days", "DAYS_INCOMPLETE", "逐日行程必须完整、连续，并与线路天数一致")
    ids = [d.day_id for d in doc.days]
    if any(not x for x in ids) or len(set(ids)) != len(ids):
        issue("days", "DAY_ID_INVALID", "每天需有独立的内容标识")
    for i, day in enumerate(doc.days):
        path = f"days/{i}"
        if not day.title.strip() or not day.summary.strip():
            issue(path, "DAY_HEADING_MISSING", f"第 {day.day} 天需填写标题与简述")
        if MEASURE.search(day.title):
            issue(
                path + "/title",
                "TITLE_HAS_MEASURE",
                "标题只保留地点或主题，里程和车程请移到参考交通",
            )
        if not day.blocks or not all(
            b.paragraphs and all(p.strip() for p in b.paragraphs) for b in day.blocks
        ):
            issue(path + "/blocks", "PROGRAMME_MISSING", f"第 {day.day} 天需填写详细内容")
        if len({b.block_id for b in day.blocks}) != len(day.blocks):
            issue(path + "/blocks", "BLOCK_ID_INVALID", "内容块标识不能重复")
        if day.summary_basis_hash != day_basis(day):
            issue(
                path + "/summary", "SUMMARY_RECHECK", f"第 {day.day} 天详情已修改，请重新核对简述"
            )
        for key in ("breakfast", "lunch", "dinner"):
            if getattr(day.meals, key).included is None:
                issue(
                    path + "/meals/" + key,
                    "MEAL_UNKNOWN",
                    f"第 {day.day} 天{dict(breakfast='早餐', lunch='午餐', dinner='晚餐')[key]}尚待确认",
                )
        if day.overnight == "unknown" or (
            day.overnight == "hotel" and (not day.hotel or not day.hotel.name.strip())
        ):
            issue(path + "/hotel", "STAY_UNKNOWN", f"第 {day.day} 天住宿安排尚待确认")
    if not doc.inclusions or not doc.exclusions:
        issue("fees", "FEES_MISSING", "请核对并填写费用包含与费用不含")
    return result


def customer_projection(doc, *, title=None, tags=(), review_mode="human"):
    """No private fields pass through, including fields nested in a source parse."""
    if kit.is_kit(doc):
        return {
            **kit.customer_projection(doc, title=title, tags=tags),
            **(
                {"publication_notice": "测试自动发布 · 未人工审核，仅供开发测试"}
                if review_mode == "test_auto"
                else {}
            ),
        }
    doc = RouteDoc.model_validate(doc)
    return {
        "title": title or doc.name,
        "summary": doc.summary.model_dump(),
        "highlights": list(doc.cover.highlights),
        "tags": [
            t["label"] for t in tags if t.get("state") == "confirmed" and t.get("customer_visible")
        ],
        "days": [
            {
                "day": d.day,
                "title": d.title,
                "summary": d.summary,
                "places": list(d.places),
                "countries": list(d.countries),
                "cities": list(d.cities),
                "vehicle_type": d.vehicle_type,
                "travel_reference": d.travel_reference,
                "transport": d.transport,
                "distances_km": list(d.distances_km),
                "blocks": [
                    {
                        "type": b.type,
                        "title": b.title,
                        "paragraphs": list(b.paragraphs),
                        "visit_mode": b.visit_mode,
                        "ticket_status": b.ticket_status,
                    }
                    for b in d.blocks
                ],
                "text": d.text if not d.blocks else "",
                "sights": [
                    {k: getattr(s, k) for k in ("name", "kind", "duration", "ticket_included")}
                    for s in d.sights
                ],
                "meals": d.meals.model_dump(),
                "overnight": d.overnight,
                "hotel": {
                    k: getattr(d.hotel, k)
                    for k in (
                        "name",
                        "or_similar",
                        "grade",
                        "grade_basis",
                        "room_type",
                        "consecutive_nights",
                    )
                }
                if d.hotel
                else None,
                "flights": [
                    {
                        k: getattr(f, k)
                        for k in (
                            "flight_no",
                            "carrier",
                            "from_place",
                            "to_place",
                            "times",
                            "departure_local_time",
                            "arrival_local_time",
                            "arrival_day_offset",
                        )
                    }
                    for f in d.flights
                ],
            }
            for d in doc.days
        ],
        "inclusions": list(doc.inclusions),
        "exclusions": list(doc.exclusions),
        "shopping": [
            {k: getattr(s, k) for k in ("name", "day", "duration", "categories")}
            for s in doc.shopping
        ],
        "optional": [
            {"name": s.name, "price": s.price, "day": s.day, "money": _money(s.money)}
            for s in doc.optional
        ],
        "applicability": {
            k: doc.applicability.model_dump(mode="json")[k]
            for k in ("start", "end", "departure_cities", "version_label")
        },
        "formation": {
            k: getattr(doc.formation, k)
            for k in ("minimum_travelers", "failure_action", "booking_deadline")
        },
        "service_fee": _money(doc.service_fee),
        "single_room_supplement": _money(doc.single_room_supplement),
        "cancellation_tiers": [
            {
                "days_before_min": t.days_before_min,
                "days_before_max": t.days_before_max,
                "penalty_percent": str(t.penalty_percent)
                if t.penalty_percent is not None
                else None,
                "penalty_money": _money(t.penalty_money) if t.penalty_money else None,
            }
            for t in doc.cancellation_tiers
        ],
        "traveler_requirements": {
            **{
                key: {
                    k: getattr(getattr(doc.traveler_requirements, key), k)
                    for k in ("minimum_age", "maximum_age", "bed_policy", "conditions")
                }
                for key in ("child", "senior")
            },
            "pregnancy": doc.traveler_requirements.pregnancy.raw,
            "visa": {
                k: getattr(doc.traveler_requirements.visa, k)
                for k in ("type", "submission_deadline", "passport_validity")
            },
            "insurance": {
                k: getattr(doc.traveler_requirements.insurance, k)
                for k in ("included", "description")
            },
        },
        "meeting": {
            k: getattr(doc.meeting, k) for k in ("location", "time", "domestic_connection")
        },
        "shopping_total": doc.shopping_total,
        "policies": doc.policies.model_dump(),
        "notices": list(doc.notices),
    }


def _money(value):
    return {
        "amount": str(value.amount) if value.amount is not None else None,
        "currency": value.currency,
        "unit": value.unit,
    }
