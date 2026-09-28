"""Compare routes against the customer's concerns, build versioned plans, share them.

Every comparison cell and every recommendation reason names the concern it answers and the
fact it rests on. A plan belongs to one need version; when the need changes it is marked
"调整中" and its customer page says so.
"""

import hashlib
import re
import secrets
from datetime import UTC, datetime

from . import db, memory, pricing, routes, store
from . import need as needs
from .facts import Route
from .need import Need

DEFAULT_TOPICS = ("行程节奏", "预算", "购物自费", "儿童")


def _concerns(conn, owner, deal, need: Need):
    mem = memory.summary(conn, owner, deal["id"])
    topics = [(c["topic"], c["text"], c["count"]) for c in mem["concerns"] if c["topic"] != "其他"]
    seen = {t for t, _, _ in topics}
    if need.get("budget") and "预算" not in seen:
        topics.append(("预算", needs.show("budget", need.get("budget")), 1))
    for key in need.get("preferences") or []:
        topic = {
            "slow_pace": "行程节奏",
            "no_shopping": "购物自费",
            "family": "儿童",
            "senior": "老人",
        }.get(key)
        if topic and topic not in {t for t, _, _ in topics}:
            topics.append((topic, needs.PREFERENCES[key], 1))
    for topic in DEFAULT_TOPICS:
        if len(topics) >= 4:
            break
        if topic not in {t for t, _, _ in topics}:
            topics.append((topic, topic, 0))
    return topics[:4]


def _cell(topic, route: Route, card, need: Need, price):
    """Whether one route answers one concern, with the fact that says so."""
    facts = route.facts()

    def first(pattern, sections=None):
        return next(
            (
                f
                for f in facts
                if (not sections or f["section"] in sections) and re.search(pattern, f["text"])
            ),
            None,
        )

    if topic == "行程节奏":
        stays = [d["stay"] for d in route.days if d["stay"]]
        long = first(r"车程[约]?\s*[5-9]\s*小时|[5-9]\s*小时车程")
        if not route.days:
            return "q", "行程未发布，节奏待核实", None
        changes = len(set(stays)) if stays else None
        if long:
            return "n", long["text"].split("：", 1)[-1][:40], long["fact_id"]
        if changes is not None and changes <= max(2, len(route.days) // 3):
            return "y", f"住 {changes} 个地方，换酒店少", None
        free = first(r"自由活动|休息|连住")
        if free:
            return "y", free["text"].split("：", 1)[-1][:40], free["fact_id"]
        return "q", "每天车程没写明", None
    if topic == "预算":
        budget = need.get("budget")
        if not price:
            return "q", "价格待询", None
        if budget and price["per_person"] > float(budget.per_person):
            return (
                "n",
                f"每人约 ¥{round(price['per_person']):,}，超 ¥{round(price['per_person'] - float(budget.per_person)):,}",
                None,
            )
        return "y", f"每人约 ¥{round(price['per_person']):,}", None
    if topic == "购物自费":
        shop = [f for f in facts if f["section"] == "购物说明"]
        if shop:
            return "n", shop[0]["text"].split("：", 1)[-1][:40], shop[0]["fact_id"]
        if route.days:
            return "y", "行程未列购物店", None
        return "q", "购物安排待核实", None
    if topic in ("儿童", "老人"):
        rule = first(
            "儿童" if topic == "儿童" else r"老人|岁以上|健康声明",
            sections=("儿童政策", "老人政策", "儿童政策", "注意事项"),
        )
        if rule:
            return (
                "q" if re.search(r"须|需|限|不", rule["text"]) else "y",
                rule["text"].split("：", 1)[-1][:40],
                rule["fact_id"],
            )
        return "q", f"{topic}政策没写明", None
    hit = first(re.escape(topic[:2]))
    if hit:
        return "y", hit["text"].split("：", 1)[-1][:40], hit["fact_id"]
    return "q", "资料没写明", None


async def compare(engine, owner, wh, deal_id, product_ids):
    if not 2 <= len(set(product_ids)) <= 3:
        raise ValueError("选两到三条线路比较")
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
        need = Need.model_validate(deal["need"])
        topics = _concerns(conn, owner, deal, need)
        latest = store.rows(
            conn, owner, db.searches, deal_id, order=db.searches.c.created_at.desc()
        )
        from .turns import prices_by_route

        prices = prices_by_route(conn, owner, deal)
    cards = {c["product_id"]: c for c in (latest[0]["body"]["cards"] if latest else [])}
    views = []
    for pid in dict.fromkeys(product_ids):
        doc = await routes.document(wh, pid)
        item = cards.get(pid) or {"product_id": pid, "title": ""}
        if not item.get("title"):
            detail = await wh.product(pid)
            item = routes.card(detail, routes.route_view(detail, doc), need, prices.get(pid))
        route = Route(pid, item["title"], (doc or {}).get("body"))
        views.append((item, route, prices.get(pid)))
    rows = []
    for topic, text, count in topics:
        cells = [
            dict(zip(("mark", "text", "fact_id"), _cell(topic, r, c, need, p), strict=True))
            for c, r, p in views
        ]
        rows.append({"topic": topic, "concern": text, "count": count, "cells": cells})
    priced = [(c, p) for c, _, p in views if p]
    delta = None
    if len(priced) == len(views) and len(views) >= 2:
        ordered = sorted(views, key=lambda v: v[2]["per_person"])
        low, high = ordered[0], ordered[-1]
        party = need.get("party")
        per = round(high[2]["per_person"] - low[2]["per_person"])
        extras = []
        if (high[0].get("days") or 0) > (low[0].get("days") or 0):
            extras.append(f"多 {high[0]['days'] - low[0]['days']} 天")
        for topic_row in rows:
            a = topic_row["cells"][views.index(high)]
            b = topic_row["cells"][views.index(low)]
            if a["mark"] == "y" and b["mark"] != "y":
                extras.append(f"{topic_row['topic']}：{a['text']}")
        delta = {
            "high": high[0]["title"],
            "per_person": per,
            "family": per * party.total if party and party.total else None,
            "extras": extras or ["已复核资料里看不出多了什么，需要问供应商"],
        }
    pros_cons = []
    for (c, r, _price), index in zip(views, range(len(views)), strict=True):
        good = [row["cells"][index]["text"] for row in rows if row["cells"][index]["mark"] == "y"]
        bad = [row["cells"][index]["text"] for row in rows if row["cells"][index]["mark"] == "n"]
        watch = [f["text"].split("：", 1)[-1] for f in r.watch()[:1]]
        pros_cons.append({"title": c["title"], "good": good[:3], "tell": (bad + watch)[:3]})
    scores = [
        sum(
            {"y": 2, "q": 1, "n": 0}[row["cells"][i]["mark"]] * (3 if n == 0 else 1)
            for n, row in enumerate(rows)
        )
        for i in range(len(views))
    ]
    best = max(range(len(views)), key=lambda i: scores[i])
    first = rows[0] if rows else None
    other = max((i for i in range(len(views)) if i != best), key=lambda i: scores[i], default=None)
    verdict = (
        f"“{first['concern']}”排第一，推荐 {views[best][0]['title']}：{first['cells'][best]['text'].rstrip('。')}。"
        if first
        else ""
    )
    if other is not None and len(rows) > 1:
        second = next(
            (
                row
                for row in rows[1:]
                if row["cells"][other]["mark"] == "y" and row["cells"][best]["mark"] != "y"
            ),
            None,
        )
        if second:
            verdict += f"如果更看重“{second['concern']}”，{views[other][0]['title']}更合适。"
    return {
        "routes": [
            {"product_id": c["product_id"], "title": c["title"], "days": c.get("days"), "price": p}
            for c, _, p in views
        ],
        "rows": rows,
        "delta": delta,
        "pros_cons": pros_cons,
        "verdict": verdict,
        "basis": "依据：两条线路已发布的行程、费用说明和本单报价",
    }


async def build_plan(engine, owner, wh, deal_id, product_ids):
    comparison = (
        await compare(engine, owner, wh, deal_id, product_ids) if len(product_ids) > 1 else None
    )
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        need = Need.model_validate(deal["need"])
        existing = store.rows(conn, owner, db.plans, deal_id, order=db.plans.c.version.desc())
        version = (existing[0]["version"] + 1) if existing else 1
        for old in existing:
            if old["status"] in ("draft", "sent"):
                store.change(conn, owner, db.plans, old["id"], status="void")
        quotes = store.rows(conn, owner, db.quotes, deal_id, where=[db.quotes.c.status == "active"])
    plan_routes = []
    for index, pid in enumerate(product_ids):
        doc = await routes.document(wh, pid)
        detail = await wh.product(pid)
        route = Route(pid, routes.display(detail.get("title", "")), (doc or {}).get("body"))
        reasons, tell = [], []
        if comparison:
            for row in comparison["rows"]:
                cell = row["cells"][index]
                if not cell["text"]:
                    continue
                if cell["mark"] == "y":
                    reasons.append(
                        {
                            "concern": row["topic"],
                            "text": cell["text"],
                            "source": _section(route, cell["fact_id"]),
                        }
                    )
                elif cell["mark"] == "n":
                    tell.append(
                        {
                            "concern": "说清",
                            "text": cell["text"],
                            "source": _section(route, cell["fact_id"]),
                        }
                    )
        watch = route.watch()
        if not tell and watch:
            tell.append(
                {
                    "concern": "说清",
                    "text": watch[0]["text"].split("：", 1)[-1],
                    "source": watch[0]["section"],
                }
            )
        if not tell:
            tell.append(
                {"concern": "说清", "text": "费用和退改条件以确认单为准", "source": "确认单"}
            )
        quote = next(
            (
                q
                for q in quotes
                if q["snapshot"].get("product_id") == pid
                and pricing.validity(q, {**deal, "departure": None}, need)[0]
            ),
            None,
        )
        money = pricing.summary(quote["snapshot"], need, quote["sales_total"]) if quote else None
        plan_routes.append(
            {
                "product_id": pid,
                "title": route.title,
                "days": detail.get("attributes", {}).get("days"),
                "reasons": reasons[:3],
                "tell": tell[:1],
                "price": money,
                "includes": _includes(route),
                "dates": [],
            }
        )
        try:
            deps = await routes.departures(wh, pid, need)
            plan_routes[-1]["dates"] = [
                f"{d['date'][5:].replace('-', '/')} 周{d['weekday']}"
                for d in deps
                if d["in_window"]
            ][:3]
        except Exception:  # noqa: BLE001 - dates are optional on a plan
            pass
    body = {
        "routes": plan_routes,
        "need": needs.spoken(need) or needs.summary(need),
        "comparison": comparison,
    }
    with engine.begin() as conn:
        row = store.add(
            conn,
            owner,
            db.plans,
            deal_id=deal_id,
            version=version,
            need_version=deal["need_version"],
            body=body,
            status="draft",
        )
    return _plan(row)


def _section(route, fact_id):
    if not fact_id:
        return "行程"
    fact = next((f for f in route.facts() if f["fact_id"] == fact_id), None)
    return fact["section"] if fact else "行程"


def _includes(route: Route):
    facts = route.facts()
    inc = [f["text"].split("：", 1)[-1] for f in facts if f["text"].startswith("费用包含")][:3]
    exc = [f["text"].split("：", 1)[-1] for f in facts if f["text"].startswith("费用不含")][:3]
    shop = [f["text"].split("：", 1)[-1] for f in facts if f["section"] == "购物说明"][:1]
    parts = []
    if inc:
        parts.append("含" + "、".join(inc))
    if exc:
        parts.append("不含" + "、".join(exc))
    if shop:
        parts.append(shop[0])
    return "；".join(parts)


def _plan(row):
    return {
        "id": str(row["id"]),
        "version": row["version"],
        "need_version": row["need_version"],
        "status": row["status"],
        "body": row["body"],
        "sent_at": row["sent_at"].isoformat() if row["sent_at"] else None,
        "views": row["views"],
        "signals": row["signals"],
    }


def plans(conn, owner, deal_id):
    return [
        _plan(r)
        for r in store.rows(conn, owner, db.plans, deal_id, order=db.plans.c.version.desc())
    ]


def share(engine, owner, deal_id, plan_id):
    token = secrets.token_urlsafe(24)
    with engine.begin() as conn:
        plan = store.one(conn, owner, db.plans, plan_id)
        if plan["deal_id"] != deal_id or plan["status"] == "void":
            raise store.Conflict("这版方案已作废，请按当前需求重新做方案")
        store.change(
            conn,
            owner,
            db.plans,
            plan_id,
            status="sent",
            token_hash=_hash(token),
            sent_at=datetime.now(UTC),
        )
    return token


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public(engine, token, signal=None, route=0):
    """The customer's page. No trade price, margin or supplier name ever leaves here."""
    with engine.begin() as conn:
        plan = (
            conn.execute(db.plans.select().where(db.plans.c.token_hash == _hash(token)))
            .mappings()
            .one_or_none()
        )
        if not plan:
            return None
        if plan["status"] == "void":
            return {"status": "void", "message": "方案调整中，请联系顾问"}
        deal = (
            conn.execute(db.deals.select().where(db.deals.c.id == plan["deal_id"])).mappings().one()
        )
        session = (
            conn.execute(
                db.sessions.select()
                .where(db.sessions.c.user_id == plan["user_id"])
                .order_by(db.sessions.c.created_at.desc())
            )
            .mappings()
            .first()
        )
        name_row = (
            conn.execute(
                db.memory.select().where(
                    db.memory.c.deal_id == plan["deal_id"], db.memory.c.kind == "salutation"
                )
            )
            .mappings()
            .first()
        )
        values = {"views": plan["views"] + 1, "last_view_at": datetime.now(UTC)}
        body = plan["body"]
        if signal in ("select", "question") and 0 <= route < len(body["routes"]):
            chosen = body["routes"][route]
            signals = [
                *plan["signals"],
                {
                    "signal": signal,
                    "route": chosen["title"],
                    "product_id": chosen["product_id"],
                    "at": datetime.now(UTC).isoformat(),
                },
            ]
            values["signals"] = signals
            conn.execute(
                db.deals.update()
                .where(db.deals.c.id == deal["id"])
                .values(
                    waiting_reply=True,
                    next_step=("客人选了 " if signal == "select" else "客人想问 ")
                    + chosen["title"],
                )
            )
        conn.execute(db.plans.update().where(db.plans.c.id == plan["id"]).values(**values))
    hello_name = name_row["text"] if name_row else ""
    return {
        "status": "sent",
        "advisor": (session or {}).get("advisor_name") or "您的顾问",
        "org": (session or {}).get("org_name") or "",
        "version": plan["version"],
        "hello": f"{hello_name + '，' if hello_name else ''}按您说的{body['need']}，我挑了这 {len(body['routes'])} 条：",
        "routes": [
            {
                "title": r["title"],
                "days": r["days"],
                "why": "；".join(x["text"] for x in r["reasons"]) or "",
                "tell": r["tell"][0]["text"] if r["tell"] else "",
                "dates": r["dates"],
                "per_person": r["price"]["per_person"] if r.get("price") else None,
                "total": r["price"]["sales_total"] or r["price"]["market_total"]
                if r.get("price")
                else None,
                "valid_until": r["price"]["valid_until"] if r.get("price") else None,
                "includes": r["includes"],
            }
            for r in body["routes"]
        ],
        "notice": "价格以顾问最终确认为准；选择方案只通知顾问，不构成预订",
    }


def void_for(conn, owner, deal_id, fields):
    """A need change that affects routes or prices voids sent plans."""
    if not set(fields) & (needs.SEARCH_FIELDS | needs.PRICE_FIELDS):
        return 0
    count = 0
    for plan in store.rows(
        conn, owner, db.plans, deal_id, where=[db.plans.c.status.in_(["draft", "sent"])]
    ):
        store.change(conn, owner, db.plans, plan["id"], status="void")
        count += 1
    return count
