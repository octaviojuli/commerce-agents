"""Current, allowlisted fact references and versioned plan drafts for advisor conversations."""

from uuid import UUID

from sqlalchemy import text

from . import copilot_inquiries, copilot_records, copilot_reply, documents, route_content
from .advisor import parse_id
from .integrations import fingerprint
from .persistence import transaction


def fact(text_value, kind, scope, *, node_id=None, reviewed=True):
    return {
        "fact_id": fingerprint({"scope": scope, "node": node_id, "text": text_value}),
        "text": text_value,
        "kind": kind,
        "scope": scope,
        "node_id": node_id,
        "reviewed": reviewed,
    }


def read(engine, actor, product_id, departure_id=None, *, day=None):
    product = parse_id(product_id, "WP-")
    departure = parse_id(departure_id, "WD-") if departure_id else None
    with transaction(engine, actor) as conn:
        source = copilot_inquiries.source(conn, product)
        scope = {
            "product_id": product_id,
            "departure_id": departure_id,
            **copilot_inquiries.scope(conn, product, departure),
        }
        facts = [
            fact(
                f"{copilot_reply.display_name(source['name'])}，{source['days']}天，{source['gateway'] or '出发地待确认'}",
                "catalog",
                scope,
            )
        ]
        row = documents.current_in_transaction(
            conn, product, departure_id=departure, sales=departure is not None
        )
        if not row:
            return {"facts": facts, "notice": "暂无适用的已发布行程，具体安排待商户核实。"}
        human = row.get("review_mode", "human") == "human"
        content = route_content.customer_projection(
            row["body"], review_mode=row.get("review_mode", "human")
        )
        notice = "" if human else "测试自动发布 · 未人工审核，具体安排待商户核实。"
        if departure is None:
            notice += " 尚未选定团期，当前内容仅作线路参考。"
        for key, label in (
            ("inclusions", "费用包含"),
            ("exclusions", "费用不含"),
            ("shopping", "购物安排"),
            ("optional_items", "自费安排"),
            ("optional", "自费安排"),
            ("policies", "政策"),
            ("notices", "注意事项"),
            ("traveler_requirements", "材料要求"),
            ("cancellation_tiers", "退改条件"),
            ("single_room_supplement", "单房差"),
            ("service_fee", "服务费"),
        ):
            value = content.get(key)
            if value:
                values = value if isinstance(value, list) else [value]
                for item in values[:12]:
                    rendered = copilot_reply.plain(item).strip("：:；; ")
                    # A label with no content ("政策：") is not a fact.
                    if rendered and len(rendered) <= 1800:
                        facts.append(
                            fact(f"{label}：{rendered}", "itinerary", scope, reviewed=human)
                        )
        for item in content.get("days", []):
            if day is not None and item.get("day") != day:
                continue
            label = f"第 {item['day']} 天"
            if (item.get("title") or "").strip():
                facts.append(
                    fact(
                        f"{label}：{item['title'].strip()}",
                        "itinerary",
                        scope,
                        node_id=item.get("day_id"),
                        reviewed=human,
                    )
                )
            for node in item.get("items", item.get("blocks", []))[:20]:
                details = {
                    k: v
                    for k, v in node.items()
                    if k
                    in {
                        "name",
                        "title",
                        "text",
                        "description",
                        "paragraphs",
                        "duration",
                        "fee",
                        "conditions",
                        "included",
                        "type",
                    }
                    and v
                }
                rendered = copilot_reply.plain(details)
                if details and len(rendered) <= 1800:
                    facts.append(
                        fact(
                            f"{label}：{rendered}",
                            "itinerary",
                            scope,
                            node_id=node.get("node_id", node.get("block_id")),
                            reviewed=human,
                        )
                    )
            if (
                not item.get("items")
                and not item.get("blocks")
                and item.get("text")
                and len(item["text"]) <= 1800
            ):
                facts.append(fact(f"{label}：{item['text']}", "itinerary", scope, reviewed=human))
        return {"facts": facts[:100], "notice": notice.strip()}


def adopted(engine, actor, identifier, version):
    with transaction(engine, actor) as conn:
        brief, _ = copilot_records.trip_brief.load(conn, identifier)
        result = []
        for row in conn.execute(
            text(
                "SELECT body,brief_version FROM advisor_record WHERE deal_id=:id AND kind='inquiry_adoption' ORDER BY created_at DESC LIMIT 12"
            ),
            {"id": identifier, "version": version},
        ).mappings():
            body = row["body"]
            context = body["context"]
            if "requirements" not in context and row["brief_version"] != version:
                continue
            if copilot_inquiries.dependency_reason(
                conn, brief, {"context": context, "product_id": UUID(body["product_id"])}
            ):
                continue
            try:
                current = copilot_inquiries.scope(
                    conn,
                    UUID(body["product_id"]),
                    UUID(context["departure_id"]) if context.get("departure_id") else None,
                )
            except (ValueError, PermissionError):
                continue
            if all(current.get(k) == v for k, v in context["source_versions"].items()):
                result.append(
                    fact(
                        "本单商户回复：" + body["answer"],
                        "supplier_reply",
                        {
                            "reply_id": body["reply_id"],
                            "deal_id": str(identifier),
                            "product_id": "WP-" + body["product_id"],
                        },
                    )
                )
        return result


def save_output(engine, actor, identifier, turn_id, kind, body, version):
    with transaction(engine, actor) as conn:
        conversation = copilot_records.conversations._load(conn, identifier, lock=True)
        if conversation["lease_id"] != turn_id or not conversation["busy"]:
            from .changes import Conflict

            raise Conflict("本轮租约已失效")
        copilot_records.ensure(conn, actor, identifier)
        if kind == "plan":
            body = {
                **body,
                "plan_version": conn.scalar(
                    text("SELECT count(*)+1 FROM advisor_record WHERE deal_id=:id AND kind='plan'"),
                    {"id": identifier},
                ),
            }
        return copilot_records.append(
            conn, actor, identifier, kind, body, version, f"{kind}:{turn_id}"
        )
