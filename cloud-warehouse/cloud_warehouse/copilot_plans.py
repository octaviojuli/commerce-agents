"""Evidence-bound route comparisons and revocable customer plan projections."""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import text

from . import copilot_dependencies, copilot_facts, copilot_inquiries, copilot_reply, quotes
from . import copilot_records as records
from .changes import Conflict
from .persistence import Forbidden, Principal, transaction

TOPICS = {
    "节奏与孩子": "慢|累|孩子|儿童|亲子|连住|车程|自由活动|休息|散步|节奏",
    "购物与另付": "购物|自费|费用|另付|不含",
    "住宿与房间": "酒店|住宿|房|相邻|床",
    "餐食": "餐|早餐|午餐|晚餐",
    "退改与材料": "退|取消|护照|签证|材料",
}


class Build(records.Command):
    product_ids: list[str] = Field(min_length=1, max_length=3)


def valid_price(conn, actor, brief, row):
    body = row["body"]
    if body.get("dependencies", {}).get("requirements") != copilot_dependencies.requirements(brief):
        return False
    if datetime.fromisoformat(body["expires_at"]) <= datetime.now(UTC):
        return False
    snapshot = quotes._read(conn, UUID(body["quote_id"]))
    return bool(snapshot and not quotes._display(conn, actor, snapshot)["quote_expired"])


LIMITING = r"不含|自费|不能保证|购物|不予|以.{0,20}为准|需要提前|须提前"


def clip(text_value, pattern):
    """The sentences of a fact that match a topic, keeping the day or section label."""
    label, _, body = text_value.partition("：")
    if not body:
        label, body = "", text_value
    kept = [
        s.strip() for s in re.split(r"(?<=[。；！？])", body) if s.strip() and re.search(pattern, s)
    ]
    if not kept:
        return ""
    return (label + "：" if label else "") + "".join(kept).rstrip("；")


def compare(engine, actor, identifier, products):
    if not 1 <= len(set(products)) <= 3:
        raise ValueError("请选择一至三条不同线路")
    with transaction(engine, actor) as conn:
        brief, version = records.trip_brief.load(conn, identifier)
        memory = " ".join(
            conn.execute(
                text(
                    "SELECT body->>'text' FROM advisor_record WHERE deal_id=:id AND kind='memory' ORDER BY created_at DESC LIMIT 20"
                ),
                {"id": identifier},
            )
            .scalars()
            .all()
        )
        concerns = [p.label for p in brief.preferences.value or []]
        topics = [
            k for k, pattern in TOPICS.items() if re.search(pattern, memory + " ".join(concerns))
        ] or ["节奏与孩子", "住宿与房间", "购物与另付"]
        prices = {}
        retail_rows = conn.execute(
            text(
                "SELECT * FROM advisor_record WHERE deal_id=:id AND kind='retail_quote' ORDER BY created_at DESC LIMIT 30"
            ),
            {"id": identifier},
        ).mappings()
        for row in retail_rows:
            try:
                product = row["body"].get("dependencies", {}).get("route_id")
                if product not in prices and valid_price(conn, actor, brief, row):
                    prices[product] = {**row["body"], "record_id": str(row["id"])}
            except (Conflict, Forbidden):
                continue
    routes, all_facts = [], []
    for product in dict.fromkeys(products):
        read = copilot_facts.read(
            engine, actor, product, brief.departure_id if product == brief.route_id else None
        )
        facts = [f for f in read["facts"] if f["reviewed"]]
        all_facts += facts
        with transaction(engine, actor) as conn:
            source = copilot_inquiries.source(conn, UUID(product.removeprefix("WP-")))
        dimensions = []
        for topic in topics:
            matches = [
                (f, clip(f["text"], TOPICS[topic]))
                for f in facts
                if f["kind"] != "catalog" and clip(f["text"], TOPICS[topic])
            ][:2]
            dimensions.append(
                {
                    "concern": topic,
                    "text": "；".join(dict.fromkeys(text for _, text in matches))
                    or "暂无已复核依据，需向商户核实",
                    "fact_ids": [f["fact_id"] for f, _ in matches],
                    "known": bool(matches),
                }
            )
        # A drawback is the limiting sentence itself, not the day it appears in.
        drawbacks = list(
            dict.fromkeys(
                clip(f["text"], LIMITING)
                for f in facts
                if f["kind"] != "catalog"
                and not f["text"].startswith("费用包含")
                and clip(f["text"], LIMITING)
            )
        )[:3] or [read["notice"] or "房间、费用和退改的适用条件仍需在确认单逐项核对"]
        price = prices.get(product)
        total = brief.adults.value or 0
        total += (brief.children.value or 0) + (brief.seniors.value or 0)
        routes.append(
            {
                "product_id": product,
                "title": copilot_reply.display_name(source["name"]),
                "days": source["days"],
                "depart_city": source["gateway"],
                "dimensions": dimensions,
                "drawbacks": drawbacks,
                "sales_total": price["sales_total"] if price else None,
                "per_person": str((Decimal(price["sales_total"]) / total).quantize(Decimal(".01")))
                if price and total
                else None,
                "currency": price["currency"] if price else "CNY",
                "price_record_id": price["record_id"] if price else None,
                "departure_date": price["departure_date"] if price else None,
                "valid_until": price["expires_at"] if price else None,
                "profit": price["profit"] if price else None,
                "margin": str(
                    (Decimal(price["profit"]) / Decimal(price["sales_total"]) * 100).quantize(
                        Decimal(".1")
                    )
                )
                if price and Decimal(price["sales_total"])
                else None,
            }
        )
    price_answer = "目前缺少同一人数、房型和日期口径的有效销售报价，无法判断贵的方案多了什么；以下先比较已复核安排。"
    priced = [r for r in routes if r["sales_total"] is not None]
    if len(priced) >= 2 and len({(r["currency"], r["departure_date"]) for r in priced}) == 1:
        ordered = sorted(priced, key=lambda r: Decimal(r["sales_total"]))
        low, high = ordered[0], ordered[-1]
        delta = Decimal(high["sales_total"]) - Decimal(low["sales_total"])
        differences = [
            f"{d['concern']}：{d['text']}"
            for d, other in zip(high["dimensions"], low["dimensions"], strict=True)
            if d["known"] and d["text"] != other["text"]
        ]
        price_answer = (
            f"{high['title']}比{low['title']}全家多 {high['currency']} {delta:.2f}。已复核差异："
            + (
                "；".join(differences[:2])
                if differences
                else "暂无足以解释差价的额外安排依据，需向商户核实。"
            )
        )
    best = max(routes, key=lambda r: sum(d["known"] for d in r["dimensions"]))
    known = [d["concern"] for d in best["dimensions"] if d["known"]]
    recommendation = (
        f"若优先核对{'、'.join(known)}，可先看{best['title']}的已复核安排；需同时接受：{best['drawbacks'][0]}。其他未知条件仍需核实。"
        if known
        else "这些方案对客人顾虑的依据仍不完整；先核实节奏、房间和费用，再决定取舍。"
    )
    return {
        "routes": routes,
        "facts": all_facts,
        "brief_version": version,
        "price_answer": price_answer,
        "recommendation": recommendation,
        "limitation": "所有方案都需核对所列缺点与适用条件；选择方案只通知顾问，不构成预订。",
    }


def build(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
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
                or previous["kind"] != "plan"
                or [r["product_id"] for r in previous["body"]["routes"]]
                != list(dict.fromkeys(request.product_ids))
            ):
                raise Conflict("请求编号已用于其他方案")
            return dict(previous)
    comparison = compare(engine, actor, identifier, request.product_ids)
    with transaction(engine, actor) as conn:
        _, version = records.checked(conn, identifier, request.expected_version)
        if comparison["brief_version"] != version:
            raise Conflict("需求已变化，请重新比较")
        comparison["plan_version"] = conn.scalar(
            text("SELECT count(*)+1 FROM advisor_record WHERE deal_id=:id AND kind='plan'"),
            {"id": identifier},
        )
        comparison["text"] = " / ".join(r["title"] for r in comparison["routes"])
        return records.append(
            conn, actor, identifier, "plan", comparison, version, str(request.request_id)
        )


def current(conn, identifier, plan_id):
    row = records.record(conn, plan_id, kind="plan", deal_id=identifier)
    brief, version = records.trip_brief.load(conn, identifier)
    if row["brief_version"] != version or not row["body"].get("routes"):
        raise Conflict("方案已更新，请联系顾问")
    for route in row["body"]["routes"]:
        if route.get("price_record_id"):
            price = records.record(
                conn, UUID(route["price_record_id"]), kind="retail_quote", deal_id=identifier
            )
            actor = Principal(row["user_id"], row["organization_id"])
            if not valid_price(conn, actor, brief, price):
                raise Conflict("方案报价已到期，请联系顾问重新核价")
    for fact in row["body"].get("facts", []):
        scope = fact["scope"]
        if not scope.get("product_id") or "product_version" not in scope:
            continue
        fresh = copilot_inquiries.scope(
            conn,
            UUID(scope["product_id"].removeprefix("WP-")),
            UUID(scope["departure_id"].removeprefix("WD-")) if scope.get("departure_id") else None,
        )
        if any(scope.get(k) != v for k, v in fresh.items()):
            raise Conflict("方案内容已更新，请联系顾问")
    return row


def public_projection(body):
    return {
        "version": body["plan_version"],
        "price_answer": body["price_answer"],
        "recommendation": body["recommendation"],
        "limitation": body["limitation"],
        "routes": [
            {
                k: v
                for k, v in r.items()
                if k
                in {
                    "title",
                    "days",
                    "depart_city",
                    "dimensions",
                    "drawbacks",
                    "sales_total",
                    "per_person",
                    "currency",
                }
            }
            for r in body["routes"]
        ],
    }


def share(engine, actor, identifier, plan_id, request):
    with transaction(engine, actor) as conn:
        records.checked(conn, identifier, request.expected_version)
        current(conn, identifier, plan_id)
        prior = (
            conn.execute(
                text("SELECT id,plan_id FROM advisor_plan_share WHERE request_id=:id"),
                {"id": request.request_id},
            )
            .mappings()
            .one_or_none()
        )
        if prior:
            if prior["plan_id"] != plan_id:
                raise Conflict("请求编号已用于其他方案")
            return {"id": str(prior["id"]), "token": None}
        token, share_id = secrets.token_urlsafe(32), uuid4()
        conn.execute(
            text(
                "INSERT INTO advisor_plan_share(id,organization_id,user_id,plan_id,request_id,token_hash,expires_at) VALUES(:id,:org,:user,:plan,:request,:hash,:expires)"
            ),
            {
                "id": share_id,
                "org": actor.organization_id,
                "user": actor.user_id,
                "plan": plan_id,
                "request": request.request_id,
                "hash": hashlib.sha256(token.encode()).hexdigest(),
                "expires": datetime.now(UTC) + timedelta(hours=24),
            },
        )
        return {"id": str(share_id), "token": token}


def public_read(engine, authentication, token, *, signal=None, route=0):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with authentication.connect() as conn:
        capability = (
            conn.execute(text("SELECT * FROM warehouse_plan_share_lookup(:hash)"), {"hash": digest})
            .mappings()
            .one_or_none()
        )
    if not capability:
        return None
    actor = Principal(capability["user_id"], capability["organization_id"])
    try:
        with transaction(engine, actor) as conn:
            saved = (
                conn.execute(
                    text(
                        "SELECT * FROM advisor_plan_share WHERE id=:id AND token_hash=:hash AND revoked_at IS NULL AND expires_at>now()"
                    ),
                    {"id": capability["id"], "hash": digest},
                )
                .mappings()
                .one_or_none()
            )
            if not saved:
                return None
            row = records.record(conn, saved["plan_id"], kind="plan")
            records.deal(conn, row["deal_id"], lock=bool(signal))
            current(conn, row["deal_id"], row["id"])
            if signal:
                if signal not in {"select", "question"} or not 0 <= route < len(
                    row["body"]["routes"]
                ):
                    raise ValueError("请选择方案中的线路")
                selected = row["body"]["routes"][route]
                records.append(
                    conn,
                    actor,
                    row["deal_id"],
                    "task",
                    {
                        "text": ("客人倾向选择：" if signal == "select" else "客人想问问：")
                        + selected["title"],
                        "category": "customer_signal",
                        "plan_id": str(row["id"]),
                        "product_id": selected["product_id"],
                        "signal": signal,
                        "confirmed": False,
                    },
                    row["brief_version"],
                    f"plan-signal:{saved['id']}:{signal}:{route}",
                )
            return public_projection(row["body"])
    except (Forbidden, Conflict):
        return None
