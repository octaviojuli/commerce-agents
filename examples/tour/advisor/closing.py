"""Adopting changes, choosing a departure, the confirmation sheet, the formal quote,
the sale and what happens before departure.

A formal quote can only come from a confirmation sheet the customer confirmed for the
current need version and departure. Extra payments are listed, never added to the total.
"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from . import db, pricing, routes, selling, store
from . import need as needs
from .facts import Route
from .need import Need

NOTE_FLOW = {
    "fee": "进报价单“另付”，确认单里列出",
    "special": "随确认单交给供应商",
    "advisor": "出发前写进行前提醒",
    "question": "同步进问答簿",
}


# ---------------------------------------------------------------- changes


def adopt(engine, owner, deal_id, proposal_id, fields=None, keep=False):
    """Adopt (or keep the old value of) some or all items of a proposal."""
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        proposal = store.one(conn, owner, db.proposals, proposal_id, deal_id=deal_id)
        if proposal["deal_id"] != deal_id:
            raise store.NotFound("变更单不存在")
        items = [dict(i) for i in proposal["items"]]
        chosen = [
            i
            for i in items
            if i["status"] == "pending" and (fields is None or i["field"] in fields)
        ]
        if not chosen:
            raise store.Conflict("这些变更已经处理过")
        need = Need.model_validate(deal["need"])
        adopted = []
        for item in chosen:
            if keep:
                item["status"] = "kept"
                continue
            current = need.get(item["field"])
            if needs._json(current) != item["old"]:
                item["status"] = "stale"
                continue
            need = needs.set_field(
                need,
                item["field"],
                item["new"],
                item["source"],
                item["evidence"],
                item["hint"],
                item.get("turn"),
            )
            item["status"] = "adopted"
            adopted.append(item["field"])
        for item in items:
            match = next((c for c in chosen if c["field"] == item["field"]), None)
            if match:
                item["status"] = match["status"]
        store.change(conn, owner, db.proposals, proposal_id, items=items)
        effects = []
        if adopted:
            version = store.save_need(
                conn,
                owner,
                deal,
                need,
                adopted,
                "采纳：" + "、".join(needs.LABELS[f] for f in adopted),
                proposal["turn"],
            )
            effects = consequences(conn, owner, deal, need, adopted)
            return {"version": version, "adopted": adopted, "effects": effects}
        return {"version": deal["need_version"], "adopted": [], "effects": effects}


def consequences(conn, owner, deal, need, fields):
    """Carry out what a changed need means for the selected departure, prices and plans."""
    effects = []
    fields = set(fields)
    if fields & needs.PRICE_FIELDS or fields & {"window", "days", "depart_city", "destinations"}:
        for q in store.rows(
            conn, owner, db.quotes, deal["id"], where=[db.quotes.c.status == "active"]
        ):
            if pricing.validity(q, deal, need)[0] is False or fields & needs.PRICE_FIELDS:
                store.change(
                    conn,
                    owner,
                    db.quotes,
                    q["id"],
                    status="void",
                    void_reason="需求已变，需要重新核价",
                )
                effects.append("报价已作废，需要重新核价")
                break
        for c in store.rows(
            conn, owner, db.confirmations, deal["id"], where=[db.confirmations.c.status != "void"]
        ):
            store.change(conn, owner, db.confirmations, c["id"], status="void")
            effects.append("确认单需要重新确认")
            break
    window = need.get("window")
    departure = deal.get("departure")
    if (
        departure
        and window
        and "window" in fields
        and not window.start <= date.fromisoformat(departure["date"]) <= window.end
    ):
        store.update_deal(conn, owner, deal["id"], departure=None)
        effects.append(f"已选团期 {departure['date']} 不在新时间内，已取消选择")
    if fields & needs.SEARCH_FIELDS and deal.get("route"):
        effects.append("按新需求重新找线")
    voided = selling.void_for(conn, owner, deal["id"], fields)
    if voided:
        effects.append("已发的方案标为“调整中”")
    return effects


def choose_direction(engine, owner, deal_id, label, region, start, end):
    """The customer picked a direction: it ranks the search, it is not something they said."""
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        need = Need.model_validate(deal["need"])
        places = {
            "must": [] if region else [label],
            "examples": [],
            "regions": [label] if region else [],
            "exclude": [],
        }
        need = needs.set_field(
            need, "destinations", places, "explore", "", "客人从方向里选的，只影响排序"
        )
        fields = ["destinations"]
        if not need.get("window"):
            need = needs.set_field(
                need,
                "window",
                {"start": start, "end": end, "label": "在售日期"},
                "explore",
                "",
                "方向的在售日期，客人出发时间待问",
            )
            fields.append("window")
        version = store.save_need(conn, owner, deal, need, fields, "客人选了方向：" + label)
        return {"version": version}


def edit_need(engine, owner, deal_id, field, value):
    """The advisor's own edit; it is never overwritten by the model."""
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        need = Need.model_validate(deal["need"])
        need = needs.set_field(need, field, value, "advisor", "", "")
        version = store.save_need(
            conn, owner, deal, need, [field], "顾问修改：" + needs.LABELS[field]
        )
        effects = consequences(conn, owner, deal, need, [field])
        return {"version": version, "effects": effects}


# ---------------------------------------------------------------- route and dates


def void_settled(conn, owner, deal_id, reason):
    """A new route or departure ends every confirmation and price made for the old one."""
    for row in store.rows(
        conn, owner, db.confirmations, deal_id, where=[db.confirmations.c.status != "void"]
    ):
        store.change(conn, owner, db.confirmations, row["id"], status="void")
    for row in store.rows(conn, owner, db.quotes, deal_id, where=[db.quotes.c.status == "active"]):
        store.change(conn, owner, db.quotes, row["id"], status="void", void_reason=reason)


def choose_route(engine, owner, deal_id, product_id, title):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        if (deal.get("route") or {}).get("product_id") != product_id:
            void_settled(conn, owner, deal_id, "线路已换，需要重新核价")
        store.update_deal(
            conn,
            owner,
            deal_id,
            route={"product_id": product_id, "title": routes.display(title)},
            departure=None,
            next_step="看团期",
        )
        return {
            "route": {"product_id": product_id, "title": routes.display(title)},
            "previous": deal.get("route"),
        }


async def dates(engine, owner, wh, deal_id, compare_ids=()):
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
        quotes = store.rows(conn, owner, db.quotes, deal_id, where=[db.quotes.c.status == "active"])
    need = Need.model_validate(deal["need"])
    route = deal.get("route")
    if not route:
        raise store.Conflict("先选定一条线路")
    items = await routes.departures(wh, route["product_id"], need)
    doc = await routes.document(wh, route["product_id"])
    facts = Route(route["product_id"], route["title"], (doc or {}).get("body")).facts()
    formation = next((f for f in facts if f["section"] == "成团规则"), None)
    for item in items:
        quote = next(
            (
                q
                for q in quotes
                if q["departure_id"] == item["departure_id"]
                and pricing.validity(q, {**deal, "departure": item}, need)[0]
            ),
            None,
        )
        item["price"] = (
            pricing.summary(quote["snapshot"], need, quote["sales_total"]) if quote else None
        )
    chosen = [i for i in items if i["departure_id"] in compare_ids] or [
        i for i in items if i["in_window"]
    ][:2]
    return {
        "route": route,
        "items": items,
        "compare": chosen[:2],
        "formation": formation,
        "window": needs.show("window", need.get("window")) if need.get("window") else "",
    }


async def price_departure(engine, owner, wh, deal_id, departure_id, offer_id=None, kind="check"):
    """Ask the warehouse for a price for this deal's party on one departure."""
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
    need = Need.model_validate(deal["need"])
    if not offer_id:
        offers = (await wh.offers(departure_id)).get("items", [])
        active = [o for o in offers if o.get("active", True)]
        if len(active) > 1:
            raise store.OfferChoice(
                [
                    {
                        "offer_id": o.get("offer_id") or o.get("id"),
                        "name": o.get("name") or o.get("code") or "套餐",
                        "text": o.get("service_description") or "",
                    }
                    for o in active
                ]
            )
        if active:
            offer_id = active[0].get("offer_id") or active[0].get("id")
    snapshot = await pricing.check(wh, need, departure_id, offer_id)
    route = deal.get("route") or {}
    snapshot = {
        **snapshot,
        "terms": pricing.terms(need),
        "product_id": route.get("product_id") or snapshot.get("product_id"),
    }
    snapshot["hints"] = await price_hints(wh, snapshot, departure_id)
    valid = snapshot.get("quote_valid_until")
    with engine.begin() as conn:
        row = store.add(
            conn,
            owner,
            db.quotes,
            deal_id=deal_id,
            need_version=deal["need_version"],
            departure_id=departure_id,
            offer_id=str(offer_id) if offer_id else None,
            warehouse_quote_id=str(snapshot.get("quote_id") or ""),
            snapshot=snapshot,
            kind=kind,
            settlement_total=Decimal(snapshot["settlement_total"])
            if snapshot.get("settlement_total")
            else None,
            currency=snapshot.get("currency", "CNY"),
            valid_until=datetime.fromisoformat(valid) if valid else None,
        )
    return quote_view(row, deal, need)


HINTS = {
    "儿童": r"儿童|占床",
    "单房差": r"单房差|单人间",
    "费用": r"费用不含",
}


async def price_hints(wh, snapshot, departure_id):
    """What the route's document says about each gap in the price, for the advisor to check."""
    gaps = pricing.missing(snapshot)
    if not gaps or not snapshot.get("product_id"):
        return []
    doc = await routes.document(wh, snapshot["product_id"], departure_id)
    facts = Route(snapshot["product_id"], "", (doc or {}).get("body")).facts()
    wanted = [
        topic
        for topic, gap in (("儿童", "儿童"), ("单房差", "单房差"), ("费用", "费用"))
        if any(gap in g for g in gaps)
    ]
    out = []
    for topic in wanted:
        hits = [
            f
            for f in facts
            if re.search(HINTS[topic], f["text"]) and not re.search(r"之外的任何费用", f["text"])
        ]
        # The policy section first, then the shortest unit: the document repeats itself.
        hits.sort(key=lambda f: (f["section"] not in ("儿童政策", "单房差"), len(f["text"])))
        out += [
            {"topic": topic, "text": f["text"], "fact_id": f["fact_id"], "section": f["section"]}
            for f in hits[: 3 if topic == "费用" else 1]
        ]
    return out


def choose_departure(engine, owner, deal_id, departure):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        old = deal.get("departure") or {}
        if old and (
            old.get("departure_id") != departure.get("departure_id")
            or (old.get("offer_id") or None) != (departure.get("offer_id") or None)
        ):
            # Only the confirmations go: a price checked on the new departure may already exist.
            for row in store.rows(
                conn, owner, db.confirmations, deal_id, where=[db.confirmations.c.status != "void"]
            ):
                store.change(conn, owner, db.confirmations, row["id"], status="void")
        store.update_deal(conn, owner, deal_id, departure=departure, next_step="发确认单")
    return departure


def sendable(conn, owner, row, deal, need):
    """A quote the customer may be sent, or sold on: formal, current, and on a confirmed sheet."""
    ok, reason = pricing.validity(row, deal, need)
    if not ok:
        raise store.Conflict(reason)
    if row["kind"] != "formal":
        raise store.Conflict("先从确认单生成正式报价")
    if row["departure_id"] != (deal.get("departure") or {}).get("departure_id"):
        raise store.Conflict("团期已换，需要重新出正式报价")
    sheets = store.rows(
        conn, owner, db.confirmations, deal["id"], where=[db.confirmations.c.status == "confirmed"]
    )
    if not any(confirmation_view(c, deal)["current"] for c in sheets):
        raise store.Conflict("确认单已失效，客人需要重新确认")


# ---------------------------------------------------------------- confirmation


def confirmation_items(conn, owner, deal, need: Need):
    party, rooms = need.get("party"), need.get("rooms")
    route, departure = deal.get("route") or {}, deal.get("departure") or {}
    fees = store.rows(conn, owner, db.notes, deal["id"], where=[db.notes.c.category == "fee"])
    special = store.rows(
        conn, owner, db.notes, deal["id"], where=[db.notes.c.category == "special"]
    )
    people = needs.party_text(party) if party else "人数未定"
    beds = needs.beds_text(party)
    items = [
        {
            "key": "route",
            "label": "线路",
            "text": route.get("title", "未选线路"),
            "ok": bool(route),
        },
        {
            "key": "departure",
            "label": "团期",
            "text": f"{departure.get('date', '')} → {departure.get('return_date', '')}".strip(" →")
            or "未选团期",
            "ok": bool(departure),
        },
        {
            "key": "people",
            "label": "人员",
            "text": people + (f"（{beds}）" if beds else ""),
            "ok": bool(party and needs.gates(need)["quote"]["ready"]),
        },
        {
            "key": "rooms",
            "label": "房间",
            "text": needs.show("rooms", rooms) if rooms else "未定",
            "ok": bool(rooms and rooms.total),
        },
        {
            "key": "extras",
            "label": "另付",
            "text": " · ".join(f"{n['text']}" for n in fees) or "按线路费用说明",
            "ok": True,
        },
        {
            "key": "special",
            "label": "特殊需求",
            "text": " · ".join(n["text"] for n in special) or "无",
            "ok": True,
        },
    ]
    if party and party.seniors:
        items.append(
            {"key": "sign", "label": "要签的", "text": "长辈健康声明 · 旅游合同", "ok": True}
        )
    else:
        items.append({"key": "sign", "label": "要签的", "text": "旅游合同", "ok": True})
    return items


def open_confirmation(engine, owner, deal_id):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        need = Need.model_validate(deal["need"])
        if not deal.get("departure"):
            raise store.Conflict("先选定团期")
        items = confirmation_items(conn, owner, deal, need)
        for old in store.rows(
            conn, owner, db.confirmations, deal_id, where=[db.confirmations.c.status == "open"]
        ):
            store.change(conn, owner, db.confirmations, old["id"], status="void")
        row = store.add(
            conn,
            owner,
            db.confirmations,
            deal_id=deal_id,
            need_version=deal["need_version"],
            departure_id=deal["departure"]["departure_id"],
            items=items,
            status="open",
        )
        return confirmation_view(row, deal)


def confirmation_view(row, deal):
    items = row["items"]
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "need_version": row["need_version"],
        "items": items,
        "missing": [i["label"] for i in items if not i["ok"]],
        "evidence": row["evidence"],
        "current": row["need_version"] == deal["need_version"]
        and row["departure_id"] == (deal.get("departure") or {}).get("departure_id"),
        "draft": "跟您核对一下："
        + "；".join(f"{i['label']}：{i['text']}" for i in items if i["key"] not in ("sign",))
        + "。没问题您回个“确认”，我马上出正式报价～",
    }


def record_confirmation(engine, owner, deal_id, confirmation_id, evidence, confirmed=True):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        row = store.one(conn, owner, db.confirmations, confirmation_id, deal_id=deal_id)
        view = confirmation_view(row, deal)
        if not view["current"]:
            raise store.Conflict("需求或团期变了，确认单要重新生成")
        if confirmed and view["missing"]:
            raise store.Conflict("还差：" + "、".join(view["missing"]))
        row = store.change(
            conn,
            owner,
            db.confirmations,
            confirmation_id,
            status="confirmed" if confirmed else "open",
            evidence=evidence[:1000],
        )
        if confirmed:
            store.update_deal(conn, owner, deal_id, next_step="出正式报价")
        return confirmation_view(row, deal)


# ---------------------------------------------------------------- formal quote


async def formal_quote(engine, owner, wh, deal_id):
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
        confirmations = store.rows(
            conn, owner, db.confirmations, deal_id, order=db.confirmations.c.created_at.desc()
        )
    current = next((c for c in confirmations if confirmation_view(c, deal)["current"]), None)
    if not current or current["status"] != "confirmed":
        raise store.Conflict("正式报价只能从客人确认过的确认单生成")
    departure = deal["departure"]
    view = await price_departure(
        engine,
        owner,
        wh,
        deal_id,
        departure["departure_id"],
        departure.get("offer_id"),
        kind="formal",
    )
    with engine.begin() as conn:
        fees = store.rows(conn, owner, db.notes, deal_id, where=[db.notes.c.category == "fee"])
        extras = [
            {
                "text": n["text"],
                "amount": str(n["amount"]) if n["amount"] is not None else None,
                "currency": n["currency"],
                "quantity": n["quantity"],
            }
            for n in fees
        ]
        store.change(conn, owner, db.quotes, view["id"], extras=extras)
    view["extras"] = extras
    return view


def set_sales_total(engine, owner, deal_id, quote_id, total):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id)
        row = store.one(conn, owner, db.quotes, quote_id, deal_id=deal_id)
        total = Decimal(str(total))
        if total <= 0:
            raise ValueError("销售价必须大于 0")
        row = store.change(conn, owner, db.quotes, quote_id, sales_total=total)
        return quote_view(row, deal, Need.model_validate(deal["need"]))


def quote_view(row, deal, need):
    ok, reason = pricing.validity(row, deal, need)
    summary = pricing.summary(row["snapshot"], need, row["sales_total"])
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "status": row["status"],
        "valid": ok,
        "reason": reason,
        "departure_id": row["departure_id"],
        "date": row["snapshot"].get("departure_date"),
        "lines": pricing.lines(row["snapshot"], sales_total=row["sales_total"]),
        "extras": row["extras"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        **summary,
    }


def send_quote(engine, owner, deal_id, quote_id):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id)
        row = store.one(conn, owner, db.quotes, quote_id, deal_id=deal_id)
        need = Need.model_validate(deal["need"])
        sendable(conn, owner, row, deal, need)
        s = pricing.summary(row["snapshot"], need, row["sales_total"])
        total = s["sales_total"] or s["market_total"]
        lines = "；".join(
            f"{line['label']} ¥{line['unit']} × {line['quantity']}"
            for line in pricing.lines(row["snapshot"], sales_total=row["sales_total"])
            if line["unit"]
        )
        extra = "、".join(e["text"] for e in row["extras"])
        text = (
            f"正式报价：{deal['route']['title']}，{row['snapshot'].get('departure_date')} 出发。{lines}。团费合计 ¥{total}"
            + (f"；另付：{extra}（不含在合计里）" if extra else "")
            + "。"
        )
        return {"text": text}


# ---------------------------------------------------------------- sale and after


def record_sale(engine, owner, deal_id, quote_id, deposit=None, received_on=None):
    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        row = store.one(conn, owner, db.quotes, quote_id, deal_id=deal_id)
        sold = store.rows(conn, owner, db.ledger, deal_id, where=[db.ledger.c.kind == "sale"])
        if sold:
            # A retried or repeated click finds the sale it already made.
            if sold[0]["op_key"] == sale_key(quote_id):
                return after(engine, owner, deal_id, conn=conn)
            raise store.Conflict("这单已登记成交；金额变动请走调整")
        need = Need.model_validate(deal["need"])
        sendable(conn, owner, row, deal, need)
        s = pricing.summary(row["snapshot"], need, row["sales_total"])
        total = Decimal(s["sales_total"] or s["market_total"])
        store.add(
            conn,
            owner,
            db.ledger,
            deal_id=deal_id,
            kind="sale",
            amount=total,
            currency=s["currency"],
            note="线下成交",
            op_key=sale_key(quote_id),
            occurred_on=received_on or date.today(),
        )
        if s["settlement_total"]:
            store.add(
                conn,
                owner,
                db.ledger,
                deal_id=deal_id,
                kind="payable",
                amount=Decimal(s["settlement_total"]),
                currency=s["currency"],
                note="应付供应商",
                occurred_on=received_on or date.today(),
            )
        if deposit:
            store.add(
                conn,
                owner,
                db.ledger,
                deal_id=deal_id,
                kind="receipt",
                amount=Decimal(str(deposit)),
                currency=s["currency"],
                note="定金",
                occurred_on=received_on or date.today(),
            )
        store.update_deal(
            conn, owner, deal_id, status="won", waiting_reply=False, next_step="收证件和尾款"
        )
        depart = date.fromisoformat(deal["departure"]["date"])
        back = date.fromisoformat(deal["departure"].get("return_date") or deal["departure"]["date"])
        remain = total - Decimal(str(deposit or 0))
        tasks = [
            (
                "balance",
                f"尾款 ¥{remain:,.0f} · 出发前 30 天前收齐",
                depart - timedelta(days=30),
                remain,
            ),
            ("documents", "收齐护照和签证材料", depart - timedelta(days=21), None),
            ("notice", "出团通知到了核对后转发客人", depart - timedelta(days=3), None),
            ("brief", "发行前提醒：天气、穿衣、换汇、插头", depart - timedelta(days=2), None),
            ("followup", "回来后回访，记下下次想去哪", back + timedelta(days=2), None),
        ]
        for kind, text, due, amount in tasks:
            store.add(
                conn,
                owner,
                db.tasks,
                deal_id=deal_id,
                kind=kind,
                text=text,
                due_at=datetime.combine(due, datetime.min.time(), UTC),
                amount=amount,
            )
        for note in store.rows(
            conn, owner, db.notes, deal_id, where=[db.notes.c.category == "advisor"]
        ):
            store.add(
                conn,
                owner,
                db.tasks,
                deal_id=deal_id,
                kind="brief_note",
                text="行前提醒：" + note["text"],
                due_at=datetime.combine(depart - timedelta(days=2), datetime.min.time(), UTC),
            )
    return after(engine, owner, deal_id)


def sale_key(quote_id):
    return f"sale:{quote_id}"


def add_receipt(engine, owner, deal_id, amount, note="收款", received_on=None, key=None):
    """One receipt per client key: a retried request returns the ledger as it is."""
    with engine.begin() as conn:
        store.deal(conn, owner, deal_id, lock=True)
        if key and store.rows(conn, owner, db.ledger, deal_id, where=[db.ledger.c.op_key == key]):
            return after(engine, owner, deal_id, conn=conn)
        store.add(
            conn,
            owner,
            db.ledger,
            deal_id=deal_id,
            kind="receipt",
            amount=Decimal(str(amount)),
            note=note,
            occurred_on=received_on or date.today(),
            op_key=key,
        )
    return after(engine, owner, deal_id)


def after(engine, owner, deal_id, conn=None):
    if conn is None:
        with engine.connect() as fresh:
            return after(engine, owner, deal_id, conn=fresh)
    deal = store.deal(conn, owner, deal_id)
    entries = store.rows(conn, owner, db.ledger, deal_id, order=db.ledger.c.occurred_on)
    tasks = store.rows(conn, owner, db.tasks, deal_id, order=db.tasks.c.due_at)
    total = sum((e["amount"] for e in entries if e["kind"] == "sale"), Decimal(0))
    received = sum((e["amount"] for e in entries if e["kind"] == "receipt"), Decimal(0))
    payable = sum((e["amount"] for e in entries if e["kind"] == "payable"), Decimal(0))
    profit = total - payable if payable else None
    departure = deal.get("departure") or {}
    days_left = (
        (date.fromisoformat(departure["date"]) - date.today()).days
        if departure.get("date")
        else None
    )
    return {
        "departure": departure,
        "days_left": days_left,
        "money": {
            "receivable": str(total),
            "received": str(received),
            "outstanding": str(total - received),
            "payable": str(payable),
            "profit": str(profit) if profit is not None else None,
            "margin": str((profit / total * 100).quantize(Decimal("0.1")))
            if profit is not None and total
            else None,
        },
        "tasks": [
            {
                "id": str(t["id"]),
                "kind": t["kind"],
                "text": t["text"],
                "due": t["due_at"].date().isoformat() if t["due_at"] else None,
                "status": t["status"],
            }
            for t in tasks
        ],
        "ledger": [
            {
                "kind": e["kind"],
                "amount": str(e["amount"]),
                "note": e["note"],
                "on": e["occurred_on"].isoformat(),
            }
            for e in entries
        ],
    }


def finish_task(engine, owner, task_id):
    with engine.begin() as conn:
        store.change(conn, owner, db.tasks, task_id, status="done")


# ---------------------------------------------------------------- notes


def add_note(
    engine,
    owner,
    deal_id,
    product_id,
    day,
    node,
    category,
    text,
    amount=None,
    currency="CNY",
    quantity=1,
):
    if category not in NOTE_FLOW:
        raise ValueError("备注类型只能是 费用、特殊需求、顾问备注、客人问题")
    with engine.begin() as conn:
        store.deal(conn, owner, deal_id)
        row = store.add(
            conn,
            owner,
            db.notes,
            deal_id=deal_id,
            product_id=product_id,
            day=day,
            node=node[:200],
            category=category,
            text=text[:500],
            amount=Decimal(str(amount)) if amount not in (None, "") else None,
            currency=currency,
            quantity=quantity,
        )
        if category == "question":
            from . import memory

            memory.book(
                conn,
                owner,
                deal_id,
                [
                    {
                        "question": text,
                        "topic": "",
                        "product_id": product_id,
                        "answer": "",
                        "kind": "unknown",
                        "facts": [],
                    }
                ],
                None,
            )
    return note_view(row)


def note_view(row):
    return {
        "id": str(row["id"]),
        "product_id": row["product_id"],
        "day": row["day"],
        "node": row["node"],
        "category": row["category"],
        "text": row["text"],
        "amount": str(row["amount"]) if row["amount"] is not None else None,
        "currency": row["currency"],
        "quantity": row["quantity"],
        "flow": NOTE_FLOW[row["category"]],
    }
