"""Route-kit storage contract, immutable evidence, review gates and sales allowlist."""

from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from pydantic import BeforeValidator
from route_kit.models import RouteContent

from .changes import Conflict
from .integrations import fingerprint
from .product_facts import same_city
from .route_doc import RouteDoc


def is_kit(value):
    return isinstance(value, RouteContent) or (
        isinstance(value, dict) and value.get("schema") == "route-kit/1"
    )


def parse_content(value):
    if isinstance(value, (RouteContent, RouteDoc)):
        return value
    if isinstance(value, dict) and "schema" in value:
        return RouteContent.model_validate(value)
    return RouteDoc.model_validate(value)


Content = Annotated[RouteContent | RouteDoc, BeforeValidator(parse_content)]


def dump(value):
    return value.model_dump(mode="json", by_alias=True)


def day_count(value):
    doc = parse_content(value)
    return doc.days_count if is_kit(doc) else doc.summary.days


def prepare(value):
    doc = parse_content(value).model_copy(deep=True)
    if not is_kit(doc):
        return doc
    for day in doc.days:
        if not day.day_id:
            day.day_id = str(uuid5(NAMESPACE_URL, f"{doc.source.sha256}/day/{day.day}/{day.units}"))
        for i, node in enumerate(day.items):
            if not node.node_id:
                node.node_id = str(
                    uuid5(NAMESPACE_URL, f"{day.day_id}/{i}/{node.cite}/{node.name}")
                )
            for j, child in enumerate(node.children):
                if not child.node_id:
                    child.node_id = str(uuid5(NAMESPACE_URL, f"{node.node_id}/{j}/{child.cite}"))
    return doc


def guard(doc, original):
    if not is_kit(original) or any(
        getattr(doc, k) != getattr(original, k)
        for k in ("schema_", "source", "units", "quality", "code", "listed_name")
    ):
        raise Conflict("不可修改原文件、解析证据、问题记录或线路身份")


def basis(doc):
    return fingerprint(dump(doc))


def issues(doc, departure_days=(), resolutions=(), listing=None):
    doc = parse_content(doc)
    digest = basis(doc)
    acknowledged = {
        (r.get("code"), r.get("path"), r.get("basis_hash"))
        for r in resolutions
        if isinstance(r, dict) and str(r.get("note", "")).strip()
    }
    result = []

    def add(path, code, message, acknowledgeable=False):
        if acknowledgeable and (code, path, digest) in acknowledged:
            return
        key = (path, code)
        if any((r["path"], r["code"]) == key for r in result):
            return
        result.append(
            dict(
                path=path,
                code=code,
                message=message,
                acknowledgeable=acknowledgeable,
                basis_hash=digest,
            )
        )

    covered = [n for d in doc.days for n in range(d.day, (d.day_end or d.day) + 1)]
    if not covered or covered != list(range(1, doc.days_count + 1)):
        add("days", "DAYS_INCOMPLETE", "逐日序号必须连续、无重复，并与声明天数一致")
    ids = [d.day_id for d in doc.days]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        add("days", "DAY_ID_INVALID", "每天需要独立、稳定的标识")
    nodes = [n for d in doc.days for item in d.items for n in [item, *item.children]]
    ids = [n.node_id for n in nodes]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        add("days", "NODE_ID_INVALID", "行程节点标识缺失或重复")
    import re

    for i, day in enumerate(doc.days):
        if not day.title.strip() or not day.summary.strip() or not day.items:
            add(f"days/{i}", "DAY_CONTENT_MISSING", f"第 {day.day} 天需完整标题、简述和行程节点")
        if re.search(r"\d+\s*(?:km|公里|小时|分钟)", day.title, re.I):
            add(f"days/{i}/title", "TITLE_HAS_MEASURE", "标题只保留地点，里程与车程放入参考交通")
        for node in day.items:
            if not node.name.strip():
                add(f"days/{i}/items", "NODE_NAME_MISSING", "行程节点名称不能为空")
    if not doc.inclusions or not doc.exclusions:
        add("fees", "FEES_MISSING", "请核对费用包含与不含，明确填写原文约定")
    inc = {x.text.strip() for x in doc.inclusions}
    if inc.intersection(x.text.strip() for x in doc.exclusions):
        add("fees", "FEE_CONFLICT", "同一条款同时列为包含与不含，请核对")
    scope = doc.applicability
    if bool(scope.start) != bool(scope.end) or (scope.start and scope.end < scope.start):
        add("applicability", "APPLICABILITY_INVALID", "适用日期须完整且结束不早于开始")
    # With the route's listing at hand, a days conflict is settled by a warehouse decision or
    # by the content, never by a note; a departure that spans other days then needs only a note.
    days_decided = bool(listing and listing["days"]["origin"] == "warehouse_decision")
    off = {days for _, days in departure_days if days is not None and days != doc.days_count}
    if off:
        # A decision covers only departures spanning the overridden upstream value; a departure
        # with any other span really disagrees with the content and stays blocking.
        covered = days_decided and off == {listing["days"]["upstream"]}
        add(
            "days_count",
            "DEPARTURE_DURATION_MISMATCH",
            "适用团期的出发及返回日期与行程天数不一致"
            + ("；已按云仓核定天数处理，团期日期以上游为准" if covered else ""),
            covered,
        )
    expected = listing["days"]["effective"] if listing else doc.quality.days_expected
    if expected and expected != doc.days_count:
        add(
            "days_count",
            "DAYS_DIFFER_FROM_LISTING",
            f"行程是 {doc.days_count} 天，线路登记是 {expected} 天，请选择以哪个为准"
            if listing
            else "附件与线路登记天数不一致，请核对适用版本",
            not listing,
        )
    gateway = listing["gateway"]["effective"] if listing else None
    if gateway and doc.depart_city and not same_city(gateway, doc.depart_city):
        add(
            "depart_city",
            "GATEWAY_DIFFERS_FROM_LISTING",
            f"行程写的出发地是“{doc.depart_city}”，线路登记的出发口岸是“{gateway}”",
            True,
        )
    if doc.shopping_status == "none" and (
        doc.shopping or any(n.type == "shopping" for d in doc.days for n in d.items)
    ):
        add("shopping_status", "SHOPPING_CONFLICT", "已列有购物安排，不能同时标为无购物")
    if doc.shopping_status == "none":
        add(
            "shopping_status",
            "NO_SHOPPING_CONFIRM",
            "请确认原文明确约定无购物，不能以空列表代替",
            True,
        )
    for i, tier in enumerate(doc.cancellation_tiers):
        if (
            tier.days_before_min is not None
            and tier.days_before_max is not None
            and tier.days_before_min > tier.days_before_max
        ):
            add(f"cancellation_tiers/{i}", "CANCELLATION_RANGE", "退改区间无效")
        if tier.penalty_money and tier.penalty_percent is not None:
            add(f"cancellation_tiers/{i}", "CANCELLATION_CONFLICT", "退改金额与比例不能重复确定")
    for name in ("child", "senior"):
        rule = getattr(doc.traveler_requirements, name)
        if (
            rule.minimum_age is not None
            and rule.maximum_age is not None
            and rule.minimum_age > rule.maximum_age
        ):
            add(f"traveler_requirements/{name}", "AGE_RANGE", "年龄范围无效")

    def referenced(value):
        if isinstance(value, dict):
            return set(value.get("cite", [])) | set().union(
                *(
                    referenced(v)
                    for k, v in value.items()
                    if k not in {"cite", "quality", "units", "source"}
                )
            )
        if isinstance(value, list):
            return set().union(*(referenced(v) for v in value))
        return set()

    refs = referenced(dump(doc))
    if refs - {u["id"] for u in doc.units}:
        add("cite", "EVIDENCE_ID_INVALID", "引用了原文件中不存在的原文编号")
    for i, unit in enumerate(doc.quality.unmapped):
        if unit.get("risky") and unit.get("unit") not in refs:
            add(
                f"quality/unmapped/{i}",
                "RISKY_UNMAPPED",
                "风险原文未整理，须补入对应条款并保留引用",
            )
    informative = {
        "PDF_DAY_BOUNDARIES_ADJUSTED",
        "LAST_DAY_TRIMMED",
        "RETRIED",
        "PDF_PAGE_REORDERED",
    }
    for issue in doc.quality.issues:
        code = issue.get("code", "PARSE_REVIEW")
        if code in informative or code == "DAYS_DIFFER_FROM_LISTING":
            continue
        add(
            str(issue.get("path", "source")),
            code,
            {
                "AUTO_ATTACHED": "风险原文已原样挂入，请核对并确认保留或整理结果",
                "IMAGE_TEXT_TRANSCRIBED": "图片转写内容须对照原文件确认",
                "ATTRIBUTE_UNSUPPORTED": "部分属性缺少依据，请确认保留未说明或人工修正",
            }.get(code, "解析问题需核对原文及修订结果：" + code),
            True,
        )
    # Every changed draft requires a fresh content-bound human review, including lexical
    # conditions that cannot be certified by source coverage counters.
    add(
        "content",
        "CONTENT_FACT_REVIEW",
        "已逐项核对景点、餐宿、费用、限制与原文，确认改写未改变事实",
        True,
    )

    return result


def customer_projection(doc, *, title=None, tags=()):
    """Typed public shape. No generic removal of private keys from arbitrary JSON."""
    doc = parse_content(doc)

    def note(x):
        return {"text": x.text}

    def money(x):
        return {k: getattr(x, k) for k in ("amount", "currency", "unit")} if x else None

    def child(n):
        return {
            "node_id": n.node_id,
            "name": n.name,
            "local_name": n.local_name,
            "description": n.description,
            "includes": list(n.includes),
            **{
                k: getattr(n, k)
                for k in ("visit_mode", "ticket", "inclusion", "duration_text")
                if getattr(n, k) not in ("unknown", "")
            },
        }

    def node(n):
        value = {
            **child(n),
            "type": n.type,
            "part_of_day": n.part_of_day,
            "clock_text": n.clock_text,
            "highlight": n.highlight,
            "spot_label": n.spot_label,
            "price_text": n.price_text,
            "min_participants": n.min_participants,
            "booking_note": n.booking_note,
            "categories": list(n.categories),
            "shopping_kind": n.shopping_kind,
            "children": [child(c) for c in n.children],
        }
        if n.type == "recommend":
            value["inclusion"] = "recommended_not_included"
        if n.transport:
            value["transport"] = {
                k: getattr(n.transport, k)
                for k in (
                    "from_place",
                    "to_place",
                    "distance_text",
                    "duration_text",
                    "service_no",
                    "times_text",
                    "reference",
                    "departure_local_time",
                    "arrival_local_time",
                    "arrival_day_offset",
                )
            }
            if n.transport.mode != "unknown":
                value["transport"]["mode"] = n.transport.mode
        if n.alternative:
            value["alternative"] = {
                "condition": n.alternative.condition,
                "text": n.alternative.text,
            }
        if n.disclaimer:
            value["disclaimer"] = {"reason": n.disclaimer.reason, "text": n.disclaimer.text}
        if n.extra_cost_note:
            value["extra_cost_note"] = note(n.extra_cost_note)
        return value

    days = []
    for d in doc.days:
        meals = {}
        for k in ("breakfast", "lunch", "dinner"):
            m = getattr(d.meals, k)
            meals[k] = {"text": m.text, **({"status": m.status} if m.status != "unknown" else {})}
        stay = {
            k: getattr(d.stay, k)
            for k in (
                "names",
                "city",
                "grade_text",
                "or_similar",
                "check_in_text",
                "room_type",
                "consecutive_nights",
            )
        }
        if d.stay.kind != "unknown":
            stay["kind"] = d.stay.kind
        if d.stay.grade_basis != "unknown":
            stay["grade_basis"] = d.stay.grade_basis
        days.append(
            {
                "day_id": d.day_id,
                "day": d.day,
                "day_end": d.day_end,
                "title": d.title,
                "summary": d.summary,
                "cities": d.cities,
                "countries": d.countries,
                "day_kind": d.day_kind,
                "travel_text": d.travel_text,
                "items": [node(n) for n in d.items],
                "meals": meals,
                "stay": stay,
                "notes": [note(n) for n in d.notes],
                "services": {
                    k: getattr(d.services, k)
                    for k in ("coach", "guide", "note")
                    if getattr(d.services, k) != "unknown"
                }
                if d.services
                else None,
                "port_call": {
                    k: getattr(d.port_call, k)
                    for k in ("port", "arrive_text", "depart_text", "all_aboard_text")
                }
                if d.port_call
                else None,
            }
        )
    result = {
        "schema": "route-kit/1",
        "cover_asset_id": doc.source.cover_image or None,
        "title": title or doc.title,
        "subtitle": doc.subtitle,
        "days_count": doc.days_count,
        "nights": doc.nights,
        "countries": doc.countries,
        "depart_city": doc.depart_city,
        "tags": [
            t["label"] for t in tags if t.get("state") == "confirmed" and t.get("customer_visible")
        ],
        "cover_facts": [{**note(x), "category": x.category} for x in doc.cover_facts],
        "selling_points": [note(x) for x in doc.selling_points],
        "highlights": [{"title": x.title, "text": x.text} for x in doc.highlights],
        "days": days,
        "inclusions": [note(x) for x in doc.inclusions],
        "exclusions": [note(x) for x in doc.exclusions],
        "prices": [
            {
                k: getattr(p, k)
                for k in (
                    "category",
                    "audience",
                    "condition",
                    "label",
                    "amount",
                    "currency",
                    "basis",
                    "text",
                )
            }
            for p in doc.prices
        ],
        "price_notice": "附件参考价格，非实时报价；另付费用不与团费自动合计",
        "shopping": [
            {k: getattr(x, k) for k in ("name", "kind", "categories", "duration_text")}
            for x in doc.shopping
        ],
        "optional_items": [
            {k: getattr(x, k) for k in ("name", "price_text", "duration_text", "note")}
            for x in doc.optional_items
        ],
        "policies": {
            k: [note(x) for x in getattr(doc.policies, k)]
            for k in ("single_room", "child", "tips", "cancellation", "deposit")
        },
        "notices": [
            {"category": n.category, "title": n.title, "items": [note(x) for x in n.items]}
            for n in doc.notices
        ],
        "applicability": {
            k: getattr(doc.applicability, k)
            for k in ("start", "end", "departure_cities", "version_label")
        },
        "formation": {
            k: getattr(doc.formation, k)
            for k in ("minimum_travelers", "failure_action", "booking_deadline", "raw")
        },
        "meeting": {
            k: getattr(doc.meeting, k) for k in ("location", "time", "domestic_connection", "raw")
        },
        "traveler_requirements": {
            "child": {
                k: getattr(doc.traveler_requirements.child, k)
                for k in ("minimum_age", "maximum_age", "bed_policy", "conditions", "raw")
            },
            "senior": {
                k: getattr(doc.traveler_requirements.senior, k)
                for k in ("minimum_age", "maximum_age", "bed_policy", "conditions", "raw")
            },
            "visa": {
                k: getattr(doc.traveler_requirements.visa, k)
                for k in ("type", "submission_deadline", "passport_validity", "raw")
            },
            "pregnancy": {"raw": doc.traveler_requirements.pregnancy.raw},
            "insurance": {
                k: getattr(doc.traveler_requirements.insurance, k)
                for k in ("included", "description", "raw")
            },
        },
        "cancellation_tiers": [
            {
                "days_before_min": t.days_before_min,
                "days_before_max": t.days_before_max,
                "penalty_percent": t.penalty_percent,
                "penalty_money": money(t.penalty_money),
                "raw": t.raw,
            }
            for t in doc.cancellation_tiers
        ],
    }

    def known(value):
        if isinstance(value, dict):
            return {k: known(v) for k, v in value.items() if v != "unknown"}
        if isinstance(value, list):
            return [known(v) for v in value]
        return value

    return known(result)
