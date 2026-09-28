"""Deterministic proposal adoption, downstream invalidation and version-bound confirmations."""

import re
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError
from sqlalchemy import text

from . import copilot_dependencies, travel_requirements, trip_brief
from . import copilot_records as records
from .changes import Conflict
from .integrations import fingerprint
from .persistence import transaction
from .trip_brief import FIELDS, QUOTE_FIELDS, ROUTE_FIELDS, Requirements, Source, TripBrief


class Adoption(records.Command):
    accept: bool
    fields: list[str] | None = Field(default=None, min_length=1, max_length=20)


class Confirmation(records.Command):
    evidence: str = Field(min_length=1, max_length=1000)
    confirmed_items: list[str] = Field(min_length=1, max_length=12)


CONFIRM_ITEMS = (
    "线路与团期",
    "出行人数与年龄",
    "房型与儿童占床",
    "费用包含与另付",
    "退改与材料要求",
)


def original_evidence(message, evidence):
    """Recover the original span when a model normalizes only punctuation/spacing."""
    if evidence in message:
        return evidence
    punctuation = str.maketrans("，。；：！？（）", ",.;:!?()")
    source = [(c.translate(punctuation), i) for i, c in enumerate(message) if not c.isspace()]
    target = "".join(c.translate(punctuation) for c in evidence if not c.isspace())
    offset = "".join(c for c, _ in source).find(target) if target else -1
    if offset < 0:
        # A model may join separate phrases ("别太累、最怕行程太赶"). Each part
        # must be in the message; the longest part is kept as the evidence.
        parts = [p for p in re.split(r"[、，,；;/…]+|\.{2,}", evidence) if len(p.strip()) >= 2]
        found = [original_evidence(message, p.strip()) for p in parts] if len(parts) > 1 else []
        return max(found, key=len) if found and all(found) else ""
    return message[source[offset][1] : source[offset + len(target) - 1][1] + 1]


def material(brief):
    body = brief.model_dump(mode="json")
    return {
        k: body[k]["value"] if k in QUOTE_FIELDS else body[k]
        for k in (*sorted(QUOTE_FIELDS), "route_id", "departure_id", "offer_id")
    }


def invalidate(conn, identifier, previous, updated):
    if fingerprint(material(previous)) == fingerprint(
        material(updated)
    ) and copilot_dependencies.same_quote(previous.quote, updated.quote):
        return
    conn.execute(
        text("""UPDATE quote_share SET revoked_at=now() WHERE revoked_at IS NULL AND (
      quote_id=CAST(:quote AS uuid) OR advisor_record_id IN
      (SELECT id FROM advisor_record WHERE deal_id=:deal AND kind='retail_quote'))"""),
        {"quote": previous.quote_id, "deal": identifier},
    )


def merged(brief, fields, version):
    values = brief.model_dump(mode="json")
    for name, field in fields.items():
        if name not in FIELDS:
            raise ValueError("未知需求字段")
        Requirements.model_validate({name: field})
        values[name] = field
    updated = TripBrief.model_validate(values)
    changed = {k for k in fields if getattr(updated, k).value != getattr(brief, k).value}
    if changed & QUOTE_FIELDS:
        updated.quote_fields_version = version + 1
        updated.share_token = None
        updated.offline_status = "none"
        # Historical notes and receipts remain in advisor_record.
    if changed & ROUTE_FIELDS:
        updated.route_id = updated.route_title = updated.departure_id = updated.departure_title = (
            None
        )
        updated.offer_id = None
    return updated


def propose(engine, actor, identifier, fields, message, turn_id):
    """The typed model proposes facts; it has no write authority over established values."""
    with transaction(engine, actor) as conn:
        conversation = trip_brief.conversations._load(conn, identifier, lock=True)
        if not conversation["busy"] or conversation["lease_id"] != turn_id:
            raise Conflict("本轮租约已失效")
        records.ensure(conn, actor, identifier)
        brief, version = trip_brief.load(conn, identifier)
        effective = {}
        rejected = {}
        for name, data in fields.items():
            try:
                if name not in FIELDS:
                    raise ValueError("未知需求字段")
                value = getattr(Requirements.model_validate({name: data}), name)
            except (ValueError, ValidationError):
                rejected[name] = "字段格式未通过校验"
                continue
            if getattr(brief, name).value == value.value:
                continue
            if value.source == Source.said:
                value.evidence = original_evidence(message, value.evidence)
                if not value.evidence or len(value.evidence) > 120 or value.evidence not in message:
                    rejected[name] = "缺少本轮原话依据"
                    continue
                if len(message) > 30 and len(value.evidence) >= 0.8 * len(message):
                    # Citing the whole message is not evidence for one field.
                    rejected[name] = "原话依据不具体"
                    continue
            elif value.source == Source.inferred:
                if not value.hint.strip():
                    rejected[name] = "推断缺少说明"
                    continue
            else:
                rejected[name] = "模型不能声明顾问已确认"
                continue
            if getattr(brief, name).value != value.value:
                effective[name] = value.model_dump(mode="json")
        started = conn.scalar(
            text("SELECT started_at FROM agent_turn WHERE id=:id"), {"id": turn_id}
        )
        effective = travel_requirements.normalize(
            effective, message, brief, started.astimezone(ZoneInfo("Asia/Shanghai")).date()
        )
        effective = {
            k: v
            for k, v in effective.items()
            if getattr(brief, k).value != getattr(Requirements.model_validate({k: v}), k).value
        }
        if not effective:
            return {
                "status": "unchanged",
                "rejected": rejected,
                "brief": trip_brief.envelope(brief, version),
            }
        # Only direct initial facts can be adopted automatically, before any route selection.
        automatic = not brief.route_id and all(
            getattr(brief, k).value is None
            and getattr(brief, k).source != Source.advisor
            and v["source"] in ("said", "inferred")
            for k, v in effective.items()
        )
        proposal = records.append(
            conn,
            actor,
            identifier,
            "proposal",
            {
                "fields": effective,
                "previous": {k: getattr(brief, k).model_dump(mode="json") for k in effective},
                "impact": impacts(brief, effective, conn=conn, identifier=identifier),
            },
            version,
            f"proposal:{turn_id}",
        )
        if automatic:
            updated = merged(brief, effective, version)
            saved = trip_brief.save(conn, actor, identifier, updated, version + 1)
            records.append(
                conn,
                actor,
                identifier,
                "adoption",
                {
                    "proposal_id": str(proposal["id"]),
                    "accepted": True,
                    "automatic_initial_facts": True,
                },
                version + 1,
                f"adopt:{turn_id}",
            )
            return {"status": "adopted", "rejected": rejected, "brief": saved}
        return {
            "status": "pending",
            "proposal_id": str(proposal["id"]),
            "fields": effective,
            "previous": {k: getattr(brief, k).model_dump(mode="json") for k in effective},
            "impact": impacts(brief, effective, conn=conn, identifier=identifier),
            "rejected": rejected,
            "brief": trip_brief.envelope(brief, version),
        }


def adopt(engine, actor, identifier, proposal_id, request):
    with transaction(engine, actor) as conn:
        # Identical retries return the committed receipt, even after the version moved.
        receipt_body = {"proposal_id": str(proposal_id), "accepted": request.accept}
        if request.fields is not None:
            receipt_body["fields"] = sorted(set(request.fields))
        previous = (
            conn.execute(
                text("SELECT * FROM advisor_record WHERE request_key=:key"),
                {"key": str(request.request_id)},
            )
            .mappings()
            .one_or_none()
        )
        if previous:
            if (
                previous["deal_id"] != identifier
                or previous["kind"] != "adoption"
                or previous["body"] != receipt_body
            ):
                raise Conflict("重复操作内容不一致")
            return dict(previous)
        brief, version = records.checked(conn, identifier, request.expected_version)
        proposal = records.record(conn, proposal_id, kind="proposal", deal_id=identifier)
        receipts = (
            conn.execute(
                text(
                    "SELECT body FROM advisor_record WHERE deal_id=:deal AND kind='adoption' AND body->>'proposal_id'=:proposal"
                ),
                {"deal": identifier, "proposal": str(proposal_id)},
            )
            .scalars()
            .all()
        )
        processed = {k for r in receipts for k in r.get("fields", proposal["body"]["fields"])}
        selected = set(request.fields or proposal["body"]["fields"])
        if not selected <= proposal["body"]["fields"].keys() or selected & processed:
            raise Conflict("变更项不存在或已经处理")
        remaining = {k: v for k, v in proposal["body"]["fields"].items() if k in selected}
        old = proposal["body"].get("previous", {})
        if proposal["brief_version"] != version and any(
            k not in old
            or getattr(brief, k).value != getattr(Requirements.model_validate({k: old[k]}), k).value
            for k in remaining
        ):
            raise Conflict("这些需求项已有更新，请重新核对")
        if request.accept:
            updated = merged(brief, remaining, version)
            trip_brief.save(conn, actor, identifier, updated, version + 1)
            version += 1
        return records.append(
            conn,
            actor,
            identifier,
            "adoption",
            receipt_body,
            version,
            str(request.request_id),
        )


def impacts(brief, fields, *, conn=None, identifier=None):
    items = []
    for key in fields:
        if key in ROUTE_FIELDS:
            items.append(
                {
                    "field": key,
                    "text": "重新核对候选范围；已选线路与团期需重选"
                    if brief.route_id
                    else "按新条件重新检索候选",
                }
            )
        elif key in QUOTE_FIELDS:
            items.append({"field": key, "text": "重新核对人数、年龄与房型计价；保留所选线路"})
        else:
            items.append({"field": key, "text": "只调整排序与推荐理由，保留所选线路和团期"})
    if fields.keys() & QUOTE_FIELDS and brief.quote_id:
        items.append(
            {
                "field": "quote",
                "text": "当前报价与分享链接将失效，需要重新询价",
                "quote_id": str(brief.quote_id),
            }
        )
    if conn is not None:
        from uuid import UUID

        from . import advisor_actions, advisor_matching

        events = conn.execute(
            text(
                "SELECT events FROM agent_turn WHERE conversation_id=:id AND status='complete' ORDER BY started_at DESC LIMIT 30"
            ),
            {"id": identifier},
        ).scalars()
        candidates = {}
        for turn in events:
            for event in turn:
                data = event.get("data", {})
                if data.get("component") == "warehouse_routes":
                    for p in data.get("payload", {}).get("items", []):
                        candidates[p["product_id"]] = p["title"]
        if brief.route_id:
            candidates[brief.route_id] = brief.route_title
        candidates = dict(list(candidates.items())[:30])
        for field in fields.keys() & ROUTE_FIELDS:
            changed = merged(brief, {field: fields[field]}, 0)
            query, filters = advisor_actions.search_parameters(changed)
            statement, params = advisor_matching.query_sql(
                query, filters.attributes, None, count=True
            )
            sql = str(statement).replace(
                "SELECT count(*) FROM scored", "SELECT id FROM scored WHERE id=ANY(:ids)"
            )
            params["ids"] = [UUID(p.removeprefix("WP-")) for p in candidates]
            matching = set(conn.execute(text(sql), params).scalars()) if candidates else set()
            for product, title in candidates.items():
                if UUID(product.removeprefix("WP-")) not in matching:
                    items.append(
                        {
                            "field": field,
                            "product_id": product,
                            "text": f"{title}：不再满足调整后的范围或在售日期，需要重新找线",
                        }
                    )
        if "depart_city" in fields:
            city = fields["depart_city"].get("value")
            for product, title in candidates.items():
                gateway = conn.scalar(
                    text("SELECT gateway FROM product_listing WHERE id=:id"),
                    {"id": UUID(product.removeprefix("WP-"))},
                )
                if city and gateway and city != gateway:
                    items.append(
                        {
                            "field": "depart_city",
                            "product_id": product,
                            "text": f"{title}：{gateway}出发，与新的{city}出发要求冲突",
                        }
                    )
        if fields.keys() & QUOTE_FIELDS:
            links = (
                conn.execute(
                    text(
                        "SELECT id FROM quote_share WHERE revoked_at IS NULL AND advisor_record_id IN (SELECT id FROM advisor_record WHERE deal_id=:id AND kind='retail_quote')"
                    ),
                    {"id": identifier},
                )
                .scalars()
                .all()
            )
            if links:
                items.append(
                    {
                        "field": "share",
                        "text": f"本单 {len(links)} 个现有报价分享链接将失效",
                        "share_ids": [str(x) for x in links],
                    }
                )
        if fields.keys() & {"children", "child_ages", "adults", "seniors", "rooms"}:
            items.append(
                {
                    "field": "party",
                    "text": "需重新确认每位儿童年龄、是否占床，以及房间总数与各房型数量；已有房型不会按人数自动改写",
                }
            )
    return items


def pending_fields(conn, identifier):
    brief, _ = trip_brief.load(conn, identifier)
    proposals = conn.execute(
        text(
            "SELECT id,body FROM advisor_record WHERE deal_id=:id AND kind='proposal' ORDER BY created_at DESC"
        ),
        {"id": identifier},
    ).mappings()
    receipts = (
        conn.execute(
            text("SELECT body FROM advisor_record WHERE deal_id=:id AND kind='adoption'"),
            {"id": identifier},
        )
        .scalars()
        .all()
    )
    result = {}
    for p in proposals:
        processed = {
            k
            for r in receipts
            if r.get("proposal_id") == str(p["id"])
            for k in r.get("fields", p["body"]["fields"])
        }
        remaining = [
            k
            for k in p["body"]["fields"]
            if k not in processed
            and (
                k not in p["body"].get("previous", {})
                or getattr(brief, k).model_dump(mode="json")["value"]
                == p["body"]["previous"][k]["value"]
            )
        ]
        if remaining:
            result[str(p["id"])] = remaining
    return result


def revert(engine, actor, identifier, state_id, request):
    with transaction(engine, actor) as conn:
        brief, version = records.checked(conn, identifier, request.expected_version)
        old = records.record(conn, state_id, kind="state", deal_id=identifier)
        updated = merged(brief, {k: old["body"][k] for k in FIELDS}, version)
        # Reverting requirements never resurrects old selections, quotes or shares.
        updated.route_id = updated.route_title = updated.departure_id = updated.departure_title = (
            None
        )
        updated.offer_id = None
        updated.quote_fields_version = version + 1
        return trip_brief.save(conn, actor, identifier, updated, version + 1)


def confirm(engine, actor, identifier, request):
    from . import quotes

    with transaction(engine, actor) as conn:
        brief, version = records.checked(conn, identifier, request.expected_version)
        if brief.quote_id:
            quote = quotes._read(conn, brief.quote_id)
            if quote is None or quotes._display(conn, actor, quote)["quote_expired"]:
                raise Conflict("报价已到期或适用条件变化，请重新询价")
        if not brief.offer_id or not trip_brief.readiness(brief)["quote"]["ready"]:
            raise Conflict("请先选定团期方案并补齐人数、年龄和房型")
        if set(request.confirmed_items) != set(CONFIRM_ITEMS):
            raise Conflict("请逐项核对确认单，并记录客人确认原话")
        return records.append(
            conn,
            actor,
            identifier,
            "confirmation",
            {
                "items": list(CONFIRM_ITEMS),
                "evidence": request.evidence,
                "material_hash": fingerprint(material(brief)),
                "dependencies": copilot_dependencies.stamp(brief),
            },
            version,
            str(request.request_id),
        )


def confirmation_reason(record, brief, version):
    dependencies = record["body"].get("dependencies")
    if dependencies is not None:
        return copilot_dependencies.changed(dependencies, brief)
    return (
        "需求已变化，请重新核对确认单"
        if record["brief_version"] != version
        or record["body"].get("material_hash") != fingerprint(material(brief))
        else ""
    )


def current_confirmation(conn, identifier, brief, version):
    rows = conn.execute(
        text(
            "SELECT * FROM advisor_record WHERE deal_id=:id AND kind='confirmation' ORDER BY created_at DESC,id DESC"
        ),
        {"id": identifier},
    ).mappings()
    return next((row for row in rows if not confirmation_reason(row, brief, version)), None)


def clarity(brief):
    checks = (
        ("window", 25),
        ("destinations", 25),
        ("adults", 15),
        ("days", 10),
        ("depart_city", 10),
        ("budget", 10),
        ("preferences", 5),
    )
    return sum(
        weight
        for key, weight in checks
        if (
            any(
                getattr(brief, k).value
                for k in ("destinations", "destination_regions", "destination_examples", "themes")
            )
            if key == "destinations"
            else getattr(brief, key).value is not None
        )
    )
