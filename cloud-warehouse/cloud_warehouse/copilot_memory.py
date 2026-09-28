"""Owner-scoped concern counts, question themes and explicitly sent statements."""

import re
from collections import defaultdict
from uuid import UUID

from pydantic import Field
from sqlalchemy import text

from . import copilot_dependencies, copilot_inquiries
from . import copilot_records as records
from .changes import Conflict

TOPICS = {
    "节奏与体力": r"慢|累|车程|赶|休息|节奏",
    "儿童安排": r"孩子|儿童|亲子|宝宝|占床",
    "购物与费用": r"购物|费用|预算|自费|另付|贵|价",
    "房间与住宿": r"房|酒店|住|相邻|连住",
    "餐食": r"餐|吃|口味",
    "退改与材料": r"退|取消|签证|护照|证件",
}


def topic(value):
    return next((name for name, pattern in TOPICS.items() if re.search(pattern, value)), "其他细节")


def evidence_reason(conn, body):
    for fact in body.get("facts", []):
        scope = fact.get("scope", {})
        if scope.get("product_id") and "product_version" in scope:
            try:
                current = copilot_inquiries.scope(
                    conn,
                    UUID(scope["product_id"].removeprefix("WP-")),
                    UUID(scope["departure_id"].removeprefix("WD-"))
                    if scope.get("departure_id")
                    else None,
                )
                if any(scope.get(k) != v for k, v in current.items()):
                    return "行程版本或适用条件已更新，原表述需要重新核对"
            except (ValueError, PermissionError, Conflict):
                return "线路依据不再可用"
    return ""


def summary(conn, identifier):
    brief, version = records.trip_brief.load(conn, identifier)
    rows = (
        conn.execute(
            text(
                "SELECT * FROM advisor_record WHERE deal_id=:id AND kind=ANY(:kinds) ORDER BY created_at,id"
            ),
            {"id": identifier, "kinds": ["memory", "qa", "note", "state"]},
        )
        .mappings()
        .all()
    )
    concerns, questions, sent, timeline, avoid, habits = {}, defaultdict(list), [], [], [], []
    previous = {}
    for row in rows:
        body = row["body"]
        if row["kind"] == "state":
            changed = [
                k
                for k, v in body.items()
                if isinstance(v, dict)
                and "value" in v
                and v.get("value") != previous.get(k, {}).get("value")
            ]
            timeline.append(
                {"version": row["brief_version"], "fields": changed, "record_id": str(row["id"])}
            )
            previous = body
        if row["kind"] == "memory":
            value = body.get("text", "")
            name = topic(value)
            group = concerns.setdefault(name, {"topic": name, "count": 0, "sources": []})
            group["count"] += 1
            group["sources"].append(
                {
                    "text": value,
                    "turn_id": body.get("source_turn"),
                    "record_id": str(row["id"]),
                    "version": row["brief_version"],
                }
            )
            if re.search(r"不要|不去|避开|不能|不想", value):
                avoid.append(value)
            if re.search(r"微信|电话|晚上|白天|简短|语音|文字", value):
                habits.append(value)
        if row["kind"] == "qa" and body.get("questions"):
            name = body.get("topic") or topic("；".join(body["questions"]))
            route = body.get("product_id", "")
            questions[(route, name)].append(row)
        if row["kind"] == "note" and body.get("category") == "sent":
            reason = evidence_reason(conn, body)
            if body.get("dependencies"):
                reason = reason or copilot_dependencies.changed(body["dependencies"], brief)
            sent.append(
                {
                    "id": str(row["id"]),
                    "text": body.get("text", ""),
                    "correction": reason,
                    "correction_draft": "之前和您沟通过的安排有更新，我正在逐项核对，确认适用情况后发您更新说明。"
                    if reason
                    else "",
                }
            )
    qa = []
    for (route, name), entries in questions.items():
        latest = entries[-1]
        reason = evidence_reason(conn, latest["body"])
        if latest["brief_version"] != version:
            reason = reason or "需求已变化，原回答需要核对适用条件"
        qa.append(
            {
                "product_id": route,
                "topic": name,
                "count": len(entries),
                "critical": len(entries) >= 3,
                "questions": list(
                    dict.fromkeys(q for r in entries for q in r["body"]["questions"])
                ),
                "answer": reason or latest["body"].get("answer", ""),
                "status": "pending" if reason else latest["body"].get("status", "pending"),
                "stale_reason": reason,
                "record_ids": [str(r["id"]) for r in entries],
            }
        )
    return {
        "concerns": list(concerns.values()),
        "avoid": list(dict.fromkeys(avoid)),
        "habits": list(dict.fromkeys(habits)),
        "sent": sent,
        "qa": qa,
        "timeline": timeline,
    }


class Sent(records.Command):
    turn_id: UUID


def mark_sent(engine, actor, identifier, request):
    from .persistence import transaction

    with transaction(engine, actor) as conn:
        brief, version = records.checked(conn, identifier, request.expected_version)
        events = conn.execute(
            text(
                "SELECT events FROM agent_turn WHERE id=:turn AND conversation_id=:id AND status='complete'"
            ),
            {"turn": request.turn_id, "id": identifier},
        ).scalar_one_or_none()
        replies = [
            e["data"]["payload"]
            for e in events or []
            if e.get("type") == "ui" and e.get("data", {}).get("component") == "copilot_reply"
        ]
        if not replies or replies[-1].get("brief_version") != version:
            raise Conflict("这份草稿已过期，请重新整理后发送")
        reply = replies[-1]
        reason = evidence_reason(conn, {"facts": reply.get("claims", [])})
        if reason:
            raise Conflict(reason)
        return records.append(
            conn,
            actor,
            identifier,
            "note",
            {
                "category": "sent",
                "source_turn": str(request.turn_id),
                "text": reply["to_customer"],
                "facts": reply.get("claims", []),
                "dependencies": copilot_dependencies.stamp(brief),
            },
            version,
            "sent:" + str(request.turn_id),
        )


class Explain(records.Command):
    topic: str = Field(max_length=100)
    product_id: str = Field(default="", max_length=60)


def explain(engine, actor, identifier, request):
    from .persistence import transaction

    with transaction(engine, actor) as conn:
        records.checked(conn, identifier, request.expected_version)
        data = next(
            (
                q
                for q in summary(conn, identifier)["qa"]
                if q["topic"] == request.topic and q["product_id"] == request.product_id
            ),
            None,
        )
        if not data:
            raise ValueError("暂无该主题的问答记录")
        return {
            "text": "关于"
            + data["topic"]
            + "，您关心的是：\n"
            + "\n".join(data["questions"][:5])
            + "\n最近核对的回复：\n"
            + data["answer"]
            + "\n发送前请再核对当前团期适用条件。",
            "needs_review": True,
        }
