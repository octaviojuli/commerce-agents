"""Deterministic proposal adoption, downstream invalidation and version-bound confirmations."""

from pydantic import Field
from sqlalchemy import text

from . import copilot_dependencies, trip_brief
from . import copilot_records as records
from .changes import Conflict
from .integrations import fingerprint
from .persistence import transaction
from .trip_brief import FIELDS, QUOTE_FIELDS, ROUTE_FIELDS, Requirements, Source, TripBrief


class Adoption(records.Command):
    accept: bool


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
        return ""
    return message[source[offset][1] : source[offset + len(target) - 1][1] + 1]


def material(brief):
    body = brief.model_dump(mode="json")
    return {k: body[k] for k in (*FIELDS, "route_id", "departure_id", "offer_id")}


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
        for name, data in fields.items():
            if name not in FIELDS:
                raise ValueError("未知需求字段")
            value = getattr(Requirements.model_validate({name: data}), name)
            if getattr(brief, name).value == value.value:
                continue
            if value.source == Source.said:
                value.evidence = original_evidence(message, value.evidence)
                if not value.evidence or len(value.evidence) > 120 or value.evidence not in message:
                    raise ValueError("需求依据必须是本轮原话的短片段")
            elif value.source == Source.inferred:
                if not value.hint.strip():
                    raise ValueError("推断需要说明依据")
            else:
                raise ValueError("模型不能声明顾问已经确认")
            if getattr(brief, name).value != value.value:
                effective[name] = value.model_dump(mode="json")
        if not effective:
            return {"status": "unchanged", "brief": trip_brief.envelope(brief, version)}
        # Only direct initial facts can be adopted automatically, before any route selection.
        automatic = not brief.route_id and all(
            getattr(brief, k).value is None
            and getattr(brief, k).source != Source.advisor
            and v["source"] == "said"
            for k, v in effective.items()
        )
        proposal = records.append(
            conn,
            actor,
            identifier,
            "proposal",
            {"fields": effective},
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
            return {"status": "adopted", "brief": saved}
        return {
            "status": "pending",
            "proposal_id": str(proposal["id"]),
            "fields": effective,
            "impact": "采纳后重新核对候选、确认单和报价；历史记录保留。",
            "brief": trip_brief.envelope(brief, version),
        }


def adopt(engine, actor, identifier, proposal_id, request):
    with transaction(engine, actor) as conn:
        # Identical retries return the committed receipt, even after the version moved.
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
                or previous["body"] != {"proposal_id": str(proposal_id), "accepted": request.accept}
            ):
                raise Conflict("重复操作内容不一致")
            return dict(previous)
        brief, version = records.checked(conn, identifier, request.expected_version)
        proposal = records.record(conn, proposal_id, kind="proposal", deal_id=identifier)
        if proposal["brief_version"] != version:
            raise Conflict("变更单基于旧需求，请重新理解本次变化")
        if conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM advisor_record WHERE deal_id=:deal AND kind='adoption' AND body->>'proposal_id'=:proposal)"
            ),
            {"deal": identifier, "proposal": str(proposal_id)},
        ):
            raise Conflict("变更单已经处理")
        if request.accept:
            updated = merged(brief, proposal["body"]["fields"], version)
            trip_brief.save(conn, actor, identifier, updated, version + 1)
            version += 1
        return records.append(
            conn,
            actor,
            identifier,
            "adoption",
            {"proposal_id": str(proposal_id), "accepted": request.accept},
            version,
            str(request.request_id),
        )


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
    return sum(weight for key, weight in checks if getattr(brief, key).value is not None)
