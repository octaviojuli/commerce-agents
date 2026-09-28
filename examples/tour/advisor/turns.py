"""One customer message in, one set of cards and a checked WeChat draft out.

The model reads the message, answers questions from facts and writes the draft. The program
saves facts, opens proposals, runs searches, picks the one question worth asking, works out
the consequences of a change and checks every draft sentence before the advisor sees it.
"""

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from cloud_warehouse import destinations

from . import closing, db, grounding, memory, pricing, routes, store
from . import need as needs
from . import suppliers as supplier_notes
from .facts import Route
from .interpret import changes as read_changes
from .model import ModelUnavailable
from .need import Need
from .store import Owner

TZ = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger("advisor.turns")
RESEARCH = re.compile(
    r"重新找|再找找|重新搜|重新检索|换[一]?条|其他线路|别的线路|有哪些.{0,4}线|看看.{0,6}线路|推荐几条|找线"
    r"|有没有合适|有(?:什么|啥)合适|找找|看看.{0,4}合适"
)
NO_RESEARCH = re.compile(r"不(?:用|要|需要|必)(?:再)?重新找")
RECOMMEND = re.compile(r"你推荐吧|你来推荐|帮我推荐|没想好|随便|都行")
ASK_ORDER = (
    "window",
    "destinations",
    "adults",
    "children",
    "child_ages",
    "child_beds",
    "senior_ages",
    "rooms",
)
CHOICE = {
    "window": "大概什么时候？寒假、春节，还是五一？",
    "destinations": "想去海边放松，还是去欧洲看看？",
}


def today():
    return datetime.now(TZ).date()


def known_fields(need: Need) -> set:
    known = set()
    party = need.get("party")
    if party and party.adults is not None:
        known.add("party")
        if party.children is not None and all(c.age is not None for c in party.children):
            known.add("child_ages")
    if party and party.children and all(c.bed is not None for c in party.children):
        known.add("child_beds")
    rooms = need.get("rooms")
    if rooms and rooms.total:
        known.add("rooms")
    for field in ("window", "days", "depart_city", "destinations", "budget"):
        if need.get(field):
            known.add(field)
    return known


def chosen_facts(deal) -> list:
    """The route and departure this deal has settled on, so a reply names them, not the window."""
    out = []
    route, departure = deal.get("route"), deal.get("departure")
    if route:
        out.append(
            {"fact_id": "deal:route", "text": f"已选线路：{route['title']}", "section": "已选"}
        )
    if departure and departure.get("date"):
        start = date.fromisoformat(departure["date"])
        text = f"已选团期：{start.month}月{start.day}日出发"
        if departure.get("return_date"):
            end = date.fromisoformat(departure["return_date"])
            text += f"，{end.month}月{end.day}日回"
        out.append({"fact_id": "deal:departure", "text": text, "section": "已选"})
    return out


def requirement_facts(need: Need) -> list:
    """The saved need as citable facts, each phrased the way a reply would say it."""
    out = []

    def add(key, *texts):
        for n, text in enumerate(texts):
            if text:
                out.append({"fact_id": f"need:{key}:{n}", "text": text, "section": "需求"})

    for field in needs.FIELDS:
        value = need.get(field)
        if value in (None, []):
            continue
        add(field, f"{needs.LABELS[field]}：{needs.show(field, value)}")
        v = getattr(need, field)
        if v.evidence:
            out.append({"fact_id": f"said:{field}", "text": v.evidence, "section": "客人原话"})
    window, budget, days = need.get("window"), need.get("budget"), need.get("days")
    if window:
        add(
            "window_spoken",
            f"{window.start.month}月{window.start.day}日至{window.end.month}月{window.end.day}日出发",
            f"{window.start.month}/{window.start.day}–{window.end.month}/{window.end.day}出发",
        )
    if budget:
        amount = int(budget.per_person)
        add("budget_spoken", f"每人预算{amount}元以内", f"预算每人{amount}元以内")
    if days:
        add("days_spoken", f"{days.min}至{days.max}天", f"{days.min}–{days.max}天")
    party = need.get("party")
    if party and party.adults is not None:
        parts = [f"{party.adults}位成人"]
        if party.children:
            ages = "、".join(f"{c.age}岁" for c in party.children if c.age is not None)
            parts.append(f"{len(party.children)}位儿童" + (f"（{ages}）" if ages else ""))
        if party.seniors:
            ages = "、".join(f"{s.age}岁" for s in party.seniors if s.age is not None)
            parts.append(f"{len(party.seniors)}位长辈" + (f"（{ages}）" if ages else ""))
        add("party_spoken", "、".join(parts))
    rooms = need.get("rooms")
    if rooms and rooms.total:
        add(
            "rooms_spoken",
            "、".join(
                f"{label}{n}间"
                for label, n in (
                    ("大床房", rooms.doubles),
                    ("双床房", rooms.twins),
                    ("单间", rooms.singles),
                )
                if n
            ),
        )
    beds = needs.beds_text(need.get("party"))
    if beds:
        add("beds", beds, beds.replace(" ", ""))
    return out


def ask_next(need: Need, asked_recently=()) -> tuple[str, str]:
    """The one thing worth asking now: what blocks search first, then what blocks a quote."""
    gates = needs.gates(need)
    missing = gates["search"]["missing"] or gates["quote"]["missing"]
    for key in ASK_ORDER:
        if key in missing and key not in asked_recently:
            party = need.get("party")
            if key == "child_beds" and party and party.children:
                unknown = [c for c in party.children if c.bed is None]
                who = "、".join(f"{c.age} 岁" for c in unknown if c.age is not None) or "孩子"
                return key, f"{who}的孩子要占床吗？" if len(
                    unknown
                ) == 1 else "两个孩子都要占床吗？"
            if key == "rooms" and party and party.seniors:
                return key, "长辈是想单住一间，还是和家人住一起？"
            return key, needs.MISSING_TEXT[key] + "？"
    return "", ""


def stage(deal, need: Need) -> int:
    """0 需求 · 1 选线 · 2 定团 · 3 报价 · 4 成交 · 5 出行."""
    if deal["status"] == "won":
        departure = (deal.get("departure") or {}).get("date")
        if departure and date.fromisoformat(departure) <= today():
            return 5
        return 4
    if deal.get("departure"):
        return 2
    if deal.get("route") or needs.gates(need)["search"]["ready"]:
        return 1
    return 0


class Turns:
    """Runs a turn. ``engine`` is the advisor database; ``wh`` the advisor's warehouse view."""

    def __init__(self, engine, model):
        self.engine = engine
        self.model = model

    async def run(self, owner: Owner, wh, deal_id, text: str, kind="customer"):
        with self.engine.begin() as conn:
            deal = store.deal(conn, owner, deal_id, lock=True)
            seq = store.next_seq(conn, owner, deal_id)
            store.add_turn(conn, owner, deal_id, seq, kind, text)
            context = self._context(conn, owner, deal)
        need = Need.model_validate(deal["need"])
        result = {"seq": seq, "kind": kind, "text": text, "cards": [], "chips": [], "notes": []}
        try:
            understanding = await self.model.call(
                "understand", self._understand_input(deal, need, context, text)
            )
        except ModelUnavailable:
            understanding = None
            result["degraded"] = "本轮没能读懂，原话已保存，可以重试或手动改需求单。"
        tags = self._tags(understanding, text)
        result["tags"] = tags
        _, _, rejected = read_changes(need, understanding, text, today(), turn=seq)
        if rejected:
            LOG.info(
                json.dumps(
                    {
                        "event": "advisor_reading_rejected",
                        "fields": rejected,
                        "changes": [
                            c.model_dump(mode="json")
                            for c in (understanding.changes if understanding else [])
                            if c.field in rejected
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            result["notes"].append(
                "没读准："
                + "、".join(needs.LABELS.get(f, f) for f in rejected)
                + "，可在需求单里改"
            )
        with self.engine.begin() as conn:
            deal = store.deal(conn, owner, deal_id, lock=True)
            need = Need.model_validate(deal["need"])
            # Decided again on the need as it is now: an edit saved while the model was reading
            # turns a fill into a proposal instead of being overwritten.
            fills, proposals, _ = read_changes(need, understanding, text, today(), turn=seq)
            if fills:
                for field, item in fills.items():
                    need = needs.set_field(
                        need,
                        field,
                        item["new"],
                        item["source"],
                        item["evidence"],
                        item["hint"],
                        seq,
                    )
                store.save_need(conn, owner, deal, need, list(fills), "读到新信息", seq)
                result["cards"].append(
                    {"type": "read", "items": list(fills.values()), "version": deal["need_version"]}
                )
            if proposals:
                impact = self._impact(conn, owner, deal, need, proposals)
                row = store.add(
                    conn,
                    owner,
                    db.proposals,
                    deal_id=deal_id,
                    base_version=deal["need_version"],
                    turn=seq,
                    items=[{**p, "status": "pending"} for p in proposals],
                    impact=impact,
                )
                result["cards"].append(
                    {
                        "type": "change",
                        "proposal_id": str(row["id"]),
                        "from": deal["need_version"],
                        "items": row["items"],
                        "impact": impact,
                    }
                )
            if understanding:
                memory.remember(conn, owner, deal_id, understanding, text, seq)
                if understanding.salutation:
                    memory.salutation(conn, owner, deal, understanding.salutation)
        with self.engine.connect() as conn:
            context = self._context(conn, owner, deal)
        if understanding and understanding.selection.route and not proposals:
            # A route chosen this turn is settled before any fact or price is gathered, so the
            # reply is written for the deal as it stands after the choice.
            chosen = routes_named(understanding.selection.route, context["visible"])
            if chosen and chosen["product_id"] != (deal.get("route") or {}).get("product_id"):
                with self.engine.begin() as conn:
                    closing.settle_route(
                        conn,
                        owner,
                        deal_id,
                        chosen["product_id"],
                        chosen["title"],
                        chosen.get("supplier"),
                    )
                    deal = store.deal(conn, owner, deal_id)
                    context = self._context(conn, owner, deal)
                result["cards"].append(
                    {"type": "chosen", "title": chosen["title"], "product_id": chosen["product_id"]}
                )
        gates = needs.gates(need)
        result["gates"] = gates
        result["clarity"] = needs.clarity(need)
        facts = requirement_facts(need) + chosen_facts(deal)
        conflicts = []
        draft_extra = {}
        # --- what the program does this turn, in priority order
        research = ("research" in tags or RESEARCH.search(text)) and not NO_RESEARCH.search(text)
        if not gates["search"]["ready"]:
            asked = context["recent_asks"]
            result["cards"].append(
                {
                    "type": "clarity",
                    "clarity": result["clarity"],
                    "known": [needs.show(f, need.get(f)) for f in needs.FIELDS if need.get(f)],
                    "unknown": [needs.MISSING_TEXT[k] for k in gates["search"]["missing"]]
                    + ["几个人", "玩几天", "预算"],
                }
            )
            if RECOMMEND.search(text) or len(asked) >= 2:
                directions = await explore(wh)
                result["cards"].append({"type": "directions", "items": directions})
                draft_extra["directions"] = [
                    f"{d['name']}：{d['days']}，{d['count']} 条在售，例：{d['example']}"
                    for d in directions
                ]
                facts += [
                    {"fact_id": f"dir:{i}", "text": line, "section": "方向"}
                    for i, line in enumerate(draft_extra["directions"])
                ]
                draft_extra["ask_next"] = ""
            else:
                missing = [k for k in ("window", "destinations") if k in gates["search"]["missing"]]
                draft_extra["ask_next"] = " ".join(CHOICE[k] for k in missing)
        elif research and not proposals:
            found = await self.search(owner, wh, deal_id)
            result["cards"].append({"type": "routes", "search_id": found["id"], **found["body"]})
            # This turn's facts and privacy checks must see suppliers found this turn too.
            context["visible"] = list(
                {
                    c["product_id"]: c for c in [*context["visible"], *found["body"]["cards"]]
                }.values()
            )
            for c in found["body"]["cards"]:
                line = f"{c['title']}，{c['days'] or '天数待核实'}天，{c['depart_city'] or '出发地待核实'}出发"
                facts.append(
                    {"fact_id": f"route:{c['product_id']}", "text": line, "section": "目录"}
                )
                if c["alternative"] or (
                    need.get("depart_city") and need.get("depart_city") not in c["depart_city"]
                ):
                    conflicts.append(c["title"])
            draft_extra["routes"] = [
                f"{c['title']}（{c['days']}天，{c['depart_city']}出发）"
                for c in found["body"]["cards"][:3]
            ]
        # price facts come only from a price that still holds
        quote, reason = context["quote"], ""
        if quote:
            ok, reason = pricing.validity(quote, deal, need)
            if ok:
                s = pricing.summary(quote["snapshot"], need, quote["sales_total"])
                total = s["sales_total"] or s["market_total"]
                facts.append(
                    {
                        "fact_id": "price:total",
                        "text": f"全家合计{int(float(total))}元",
                        "section": "报价",
                    }
                )
                adjusted = (
                    s["sales_total"] != s["market_total"] and quote["sales_total"] is not None
                )
                # A changed sales price is quoted as a total; list prices per person would not add up.
                for line in [] if adjusted else pricing.lines(quote["snapshot"]):
                    if line["unit"]:
                        facts.append(
                            {
                                "fact_id": f"price:{line['label']}",
                                "text": f"{line['label']}每位{int(float(line['unit']))}元",
                                "section": "报价",
                            }
                        )
                units = {
                    line["label"]: float(line["unit"])
                    for line in ([] if adjusted else pricing.lines(quote["snapshot"]))
                    if line["unit"]
                }
                if "儿童占床" in units and "儿童不占床" in units:
                    facts.append(
                        {
                            "fact_id": "price:bed",
                            "text": f"儿童不占床比占床每位便宜{int(units['儿童占床'] - units['儿童不占床'])}元",
                            "section": "报价",
                        }
                    )
            else:
                result["notes"].append("价格没有进入回复：" + reason)
        # questions are answered from the route they are about
        questions = understanding.questions if understanding else []
        price_facts = [f for f in facts if f.get("section") == "报价"]
        answered, used = (
            (await self.answer(owner, wh, deal, need, context, questions, price_facts, reason))
            if questions
            else ([], [])
        )
        if answered:
            result["cards"].append({"type": "answers", "items": answered})
            facts += used
            draft_extra["answers"] = [
                {"q": a["question"], "a": a["answer"], "kind": a["kind"]} for a in answered
            ]
            with self.engine.begin() as conn:
                memory.book(conn, owner, deal_id, answered, seq)
        if understanding and "confirm" in tags and context.get("confirmation"):
            confirmed = understanding.confirms and not understanding.disputes
            try:
                with self.engine.begin() as conn:
                    locked = store.deal(conn, owner, deal_id, lock=True)
                    sheet = store.one(
                        conn,
                        owner,
                        db.confirmations,
                        context["confirmation"]["id"],
                        deal_id=deal_id,
                    )
                    status = closing.confirm_sheet(conn, owner, locked, sheet, text, confirmed)[
                        "status"
                    ]
            except store.Conflict as error:
                status = "stale"
                result["notes"].append(str(error))
            result["cards"].append(
                {"type": "confirm_reply", "status": status, "disputes": understanding.disputes}
            )
        # the one question to ask
        if "ask_next" not in draft_extra:
            # With a change waiting, ask about the need as it would be once adopted.
            ask_need = need
            for p in proposals:
                ask_need = needs.set_field(ask_need, p["field"], p["new"], p["source"])
            key, question = ask_next(ask_need, [] if proposals else context["recent_asks"])
            draft_extra["ask_next"] = question
            result["asked"] = key
        result["draft"] = await self.draft(
            owner,
            deal,
            need,
            context,
            text,
            understanding,
            facts,
            conflicts,
            draft_extra,
            bool(proposals),
        )
        result["chips"] = chips(deal, need, gates, bool(proposals), result)
        result["to_advisor"] = result["draft"].pop("to_advisor", "") or summary_line(result)
        result["may_ask"] = result["draft"].pop("may_ask", [])
        with self.engine.begin() as conn:
            store.set_turn_result(conn, owner, deal_id, seq, result)
            store.update_deal(
                conn,
                owner,
                deal_id,
                waiting_reply=kind == "customer",
                last_customer_at=datetime.now(UTC)
                if kind == "customer"
                else deal.get("last_customer_at"),
                next_step=result["chips"][0]["label"] if result["chips"] else "",
            )
            if understanding:
                conn.execute(
                    db.turns.update()
                    .where(
                        owner.where(db.turns), db.turns.c.deal_id == deal_id, db.turns.c.seq == seq
                    )
                    .values(understanding=understanding.model_dump(mode="json"))
                )
        return result

    # ------------------------------------------------------------ pieces

    def _context(self, conn, owner, deal):
        deal_id = deal["id"]
        latest = store.rows(
            conn, owner, db.searches, deal_id, order=db.searches.c.created_at.desc()
        )
        visible = latest[0]["body"]["cards"] if latest else []
        plans = store.rows(conn, owner, db.plans, deal_id, order=db.plans.c.version.desc())
        if plans:
            visible += [
                r
                for r in plans[0]["body"].get("routes", [])
                if r["product_id"] not in {v["product_id"] for v in visible}
            ]
        quotes = store.rows(
            conn,
            owner,
            db.quotes,
            deal_id,
            order=db.quotes.c.created_at.desc(),
            where=[db.quotes.c.status == "active"],
        )
        confirms = store.rows(
            conn, owner, db.confirmations, deal_id, order=db.confirmations.c.created_at.desc()
        )
        recent = store.turns(conn, owner, deal_id, limit=3)
        asks = [
            t["result"].get("asked")
            for t in recent[:-1]
            if t.get("result") and t["result"].get("asked")
        ]
        return {
            "visible": visible,
            "supplier_notes": supplier_notes.notes(conn, owner),
            "search": latest[0] if latest else None,
            "plan": plans[0] if plans else None,
            "quote": quotes[0] if quotes else None,
            "confirmation": confirms[0] if confirms else None,
            "memory": memory.summary(conn, owner, deal_id),
            "recent_asks": asks,
        }

    def _understand_input(self, deal, need, context, text):
        return {
            "today": today().isoformat(),
            "saved": {f: needs.show(f, need.get(f)) for f in needs.FIELDS if need.get(f)},
            "saved_party": need.get("party").model_dump(mode="json") if need.get("party") else None,
            "visible_routes": [v["title"] for v in context["visible"]][:8],
            "chosen_route": (deal.get("route") or {}).get("title"),
            "confirmation_open": bool(
                context.get("confirmation") and context["confirmation"]["status"] == "open"
            ),
            "message": text,
        }

    def _tags(self, understanding, text):
        tags = list(understanding.kinds) if understanding else []
        if RESEARCH.search(text) and not NO_RESEARCH.search(text) and "research" not in tags:
            tags.append("research")
        return tags

    def _impact(self, conn, owner, deal, need, proposals):
        """What adopting these changes would do, computed from the deal's current state."""
        fields = {p["field"] for p in proposals}
        proposed = need
        for p in proposals:
            proposed = needs.set_field(proposed, p["field"], p["new"], p["source"])
        out = []
        effects = needs.consequences(fields)
        if "research" in effects:
            latest = store.rows(
                conn, owner, db.searches, deal["id"], order=db.searches.c.created_at.desc()
            )
            cards = latest[0]["body"]["cards"] if latest else []
            misfit = [c["title"] for c in cards if not fits(c, proposed)]
            if misfit:
                out.append(
                    {
                        "level": "bad",
                        "title": f"候选里 {len(misfit)} 条线路要重新看",
                        "text": "、".join(misfit[:3]),
                    }
                )
            departure = deal.get("departure")
            window = proposed.get("window")
            if (
                departure
                and window
                and not window.start <= date.fromisoformat(departure["date"]) <= window.end
            ):
                out.append(
                    {
                        "level": "bad",
                        "title": f"已选团期 {departure['date']} 不在新时间内",
                        "text": "需要重新选团期",
                    }
                )
            out.append(
                {
                    "level": "warn",
                    "title": "按新需求重新找线",
                    "text": "旧方案标“调整中”，客人那边的方案页同步",
                }
            )
        if "rerank" in effects:
            budget = proposed.get("budget")
            latest = store.rows(
                conn, owner, db.searches, deal["id"], order=db.searches.c.created_at.desc()
            )
            over = [
                c["title"]
                for c in (latest[0]["body"]["cards"] if latest else [])
                if budget and c.get("price") and c["price"]["per_person"] > float(budget.per_person)
            ]
            out.append(
                {
                    "level": "warn" if over else "ok",
                    "title": "只调整排序和推荐理由",
                    "text": ("超出新预算：" + "、".join(over)) if over else "不会筛掉线路",
                }
            )
        if "reprice" in effects:
            quotes = store.rows(
                conn, owner, db.quotes, deal["id"], where=[db.quotes.c.status == "active"]
            )
            if quotes:
                out.append(
                    {
                        "level": "bad",
                        "title": "现在的报价会作废",
                        "text": "人数或房间变了，要重新核价；已发给客人的报价页显示“方案调整中”",
                    }
                )
            party_old, party_new = need.get("party"), proposed.get("party")
            if party_new and (not party_old or party_new.total != party_old.total):
                out.append(
                    {
                        "level": "warn",
                        "title": "房间要重新定",
                        "text": f"人数变为 {needs.party_text(party_new)}",
                    }
                )
            if party_new and party_new.seniors:
                out.append(
                    {
                        "level": "warn",
                        "title": "年龄规则",
                        "text": "有长辈同行，选线时核对线路的老人参团条件",
                    }
                )
        kept = [needs.LABELS[f] for f in needs.FIELDS if f not in fields and need.get(f)]
        if kept:
            out.append({"level": "ok", "title": "保留", "text": "、".join(kept)})
        return out

    async def search(self, owner, wh, deal_id, suppliers=()):
        with self.engine.connect() as conn:
            deal = store.deal(conn, owner, deal_id)
            prices = prices_by_route(conn, owner, deal)
            notes = supplier_notes.notes(conn, owner)
        need = Need.model_validate(deal["need"])
        body = await routes.search(
            wh, need, prices=prices, cache={}, suppliers=suppliers, notes=notes
        )
        body["version"] = deal["need_version"]
        with self.engine.begin() as conn:
            row = store.add(
                conn,
                owner,
                db.searches,
                deal_id=deal_id,
                need_version=deal["need_version"],
                body=body,
            )
        return {"id": str(row["id"]), "body": body}

    async def answer(self, owner, wh, deal, need, context, questions, extra=(), price_gap=""):
        by_route = {}
        for index, q in enumerate(questions):
            target = routes_named(q.route, context["visible"]) if q.route else None
            if (
                not target
                and not deal.get("route")
                and len(context["visible"]) != 1
                and not q.route
            ):
                # A general question ("有啥推荐") is the stage's to answer, not a route's.
                continue
            if not target and deal.get("route"):
                target = deal["route"]
            if not target and len(context["visible"]) == 1:
                target = context["visible"][0]
            key = target["product_id"] if target else ""
            by_route.setdefault(key, {"route": target, "items": []})["items"].append((index, q))
        departure = (deal.get("departure") or {}).get("departure_id")
        docs = {}
        for key in by_route:
            if key:
                docs[key] = await routes.document(
                    wh,
                    key,
                    departure if (deal.get("route") or {}).get("product_id") == key else None,
                )
        answered, used = [], []
        for key, group in by_route.items():
            route = (
                Route(
                    key, (group["route"] or {}).get("title", ""), (docs.get(key) or {}).get("body")
                )
                if key
                else None
            )
            facts = route.facts() if route else []
            facts = [f for f in facts if f["reviewed"]] or facts
            # This deal's current price answers price questions about the chosen route.
            if key and key == (deal.get("route") or {}).get("product_id"):
                facts = [*extra, *facts]
            payload = {
                "questions": [
                    {"index": i, "text": q.text, "topic": q.topic, "day": q.day}
                    for i, q in group["items"]
                ],
                "route": route.title if route else "",
                "facts": [
                    {"fact_id": f["fact_id"], "text": f["text"]}
                    for f in relevant(facts, [q for _, q in group["items"]], 120)
                ],
            }
            try:
                result = await self.model.call("answer", payload) if facts else None
            except ModelUnavailable:
                result = None
            by_index = {a.index: a for a in (result.answers if result else [])}
            ids = {f["fact_id"]: f for f in facts}
            for index, q in group["items"]:
                a = by_index.get(index)
                item = {
                    "question": q.text,
                    "topic": q.topic,
                    "product_id": key or None,
                    "route": route.title if route else "",
                    "answer": "资料没写明，需要向供应商确认。",
                    "kind": "unknown",
                    "facts": [],
                }
                if a and a.kind in ("fact", "advice") and a.fact_ids:
                    cited = [ids[i] for i in a.fact_ids if i in ids]
                    checked = grounding.check(a.answer, cited)
                    if cited and checked["text"] and not checked["removed"]:
                        item.update(
                            answer=a.answer,
                            kind=a.kind,
                            facts=[
                                {
                                    "fact_id": f["fact_id"],
                                    "section": f["section"],
                                    "text": f["text"],
                                    "reviewed": f["reviewed"],
                                }
                                for f in cited
                            ],
                        )
                        used += cited
                if not facts and not key:
                    item["answer"] = "还没有选定线路，先确定问的是哪一条。"
                if (
                    item["kind"] == "unknown"
                    and price_gap
                    and (q.topic in PRICE_TOPICS or PRICE_WORDS.search(q.text))
                    and key == (deal.get("route") or {}).get("product_id")
                ):
                    # A price exists but may not be quoted yet; say why, not "资料没写明".
                    item["answer"] = f"已核价，但还不能报给客人：{price_gap}。"
                answered.append(item)
        answered.sort(key=lambda a: [q.text for q in questions].index(a["question"]))
        return answered, used

    async def draft(
        self, owner, deal, need, context, text, understanding, facts, conflicts, extra, pending
    ):
        mem = context["memory"]
        payload = {
            "message": text,
            "salutation": mem.get("salutation", ""),
            "known": [
                f"{needs.LABELS[f]}：{needs.show(f, need.get(f))}"
                for f in needs.FIELDS
                if need.get(f)
            ],
            "concerns": [c["text"] for c in mem.get("concerns", [])][:4],
            "ask_next": extra.get("ask_next", ""),
            "answers": extra.get("answers", []),
            "routes": extra.get("routes", []),
            "searched": "routes" in extra,
            "directions": extra.get("directions", []),
            "change_pending": pending,
            "conflicts": conflicts,
            "facts": [
                f["text"] for f in facts if f.get("section") in ("已选", "报价", "目录", "方向")
            ][:20],
        }
        try:
            d = await self.model.call("draft", payload)
        except ModelUnavailable:
            d = None
        known = known_fields(need)
        forbidden = supplier_names(context, deal)
        if d is None:
            text_out = fallback(extra, forbidden=forbidden)
            return {
                "text": text_out,
                "removed": [],
                "claims": [],
                "simplified": True,
                "to_advisor": "",
                "may_ask": [],
            }
        checked = grounding.check(
            d.to_customer,
            facts,
            # The saved need is the customer's words too: its numbers may be restated.
            said=[text, needs.summary(need)],
            known=known,
            # Supplier names are the advisor's to know, never the customer's to read.
            forbidden=forbidden,
            conflicts=conflicts,
            max_questions=3 if not needs.gates(need)["search"]["ready"] else 2,
        )
        body = salute(checked["text"], mem.get("salutation", ""))
        simplified = bool(checked["removed"])
        if len(body) < 8:
            body, simplified = fallback(extra, forbidden=forbidden), True
        open_questions = [a for a in extra.get("answers", []) if a["kind"] == "unknown"]
        if open_questions and not grounding.VERIFY.search(body):
            # The reply must still say which questions are being checked, in the program's words.
            body += "\n" + pending_line(open_questions)
        may_ask = [
            q
            for q in d.may_ask
            if not grounding.INTERNAL.search(q) and not grounding.PRIVATE.search(q)
        ][:3]
        return {
            "text": body,
            "removed": checked["removed"],
            "reasons": checked["reasons"],
            "claims": checked["claims"],
            "simplified": simplified,
            "sentences": checked["sentences"],
            "to_advisor": d.to_advisor,
            "may_ask": may_ask,
        }


def supplier_names(context, deal) -> list:
    names = {(v.get("supplier") or {}).get("name", "") for v in context["visible"]}
    names |= {x["supplier"]["name"] for v in context["visible"] for x in v.get("also", [])}
    names.add((deal.get("route") or {}).get("supplier", ""))
    names |= {n["name"] for n in context.get("supplier_notes", {}).values()}
    return sorted(n for n in names if n and n != "供应商未标注")


def pending_line(answers):
    asked = "、".join(re.sub(r"^.{0,16}?那条", "", a["q"]).rstrip("？?") for a in answers[:4])
    return f"您问的{asked}，资料里没写明，我去跟供应商确认后回您。"


def fallback(extra, *, forbidden=()):
    parts = []
    for a in extra.get("answers", []):
        parts.append(a["a"] if a["kind"] != "unknown" else f"“{a['q']}”我去跟供应商确认一下。")
    if extra.get("routes"):
        parts.append("先给您挑了几条：" + "；".join(extra["routes"]) + "。")
    if extra.get("ask_next"):
        parts.append(extra["ask_next"])
    # A source title or repeated question can carry a supplier name even without a model.
    parts = [part for part in parts if not any(name in part for name in forbidden)]
    return "\n".join(parts) or "收到，我整理一下马上回复您。"


def salute(text, name):
    if not text:
        return text
    text = re.sub(r"\[客人称呼\][，,\s]*", (name + "，") if name else "", text)
    return re.sub(r"您[，,\s]*您好", "您好", text)


def summary_line(result):
    for card in result["cards"]:
        if card["type"] == "change":
            return f"客人改了 {len(card['items'])} 项，变更单已备好。"
        if card["type"] == "routes":
            return f"按需求找到 {len(card['cards'])} 条。"
    return "已整理这一轮。"


def fits(card, need: Need) -> bool:
    days, city = need.get("days"), need.get("depart_city")
    if city and city not in (card.get("depart_city") or ""):
        return False
    if days and card.get("days") and not days.min <= card["days"] <= days.max:
        return False
    budget = need.get("budget")
    return not (
        budget
        and card.get("price")
        and card["price"]["per_person"] > float(budget.per_person) * 1.2
    )


PRICE_TOPICS = {"费用", "价格", "报价"}
PRICE_WORDS = re.compile(r"价格|多少钱|报价|团费|费用")
# What a question's topic is usually written as in a document.
TOPIC_WORDS = {
    "购物": "购物|奥特莱斯|奥莱|免税",
    "酒店": "酒店|住宿|星",
    "餐食": "餐|早|午|晚",
    "儿童": "儿童|小孩|占床|岁",
    "老人": "老人|长者|岁以上|健康",
    "自费": "自费|另付|自愿",
    "签证": "签证",
    "节奏": "车程|小时|自由活动",
}


def relevant(facts: list, questions: list, limit: int) -> list:
    """The facts the model sees, in document order: those that share words with a question first."""
    if len(facts) <= limit:
        return facts
    words = set()
    pattern = "|".join(
        TOPIC_WORDS[t] for t in TOPIC_WORDS if any(t in (q.topic or "") + q.text for q in questions)
    )
    for q in questions:
        words |= grounding.bigrams(q.text)
    scored = sorted(
        range(len(facts)),
        key=lambda i: (
            -(
                len(words & grounding.bigrams(facts[i]["text"]))
                + (5 if pattern and re.search(pattern, facts[i]["text"]) else 0)
            )
        ),
    )
    keep = set(scored[:limit])
    return [f for i, f in enumerate(facts) if i in keep]


def routes_named(name: str, visible: list):
    """The visible route a message names by a distinctive part of its title."""
    if not name:
        return None
    text = re.sub(r"\s", "", name)
    scores = {}
    for v in visible:
        title = re.sub(r"\s|ACME", "", v.get("title", ""))
        best = max(
            (
                n
                for n in range(2, len(title) + 1)
                for i in range(len(title) - n + 1)
                if title[i : i + n] in text
            ),
            default=0,
        )
        scores[v["product_id"]] = (best, v)
    if not scores:
        return None
    ranked = sorted(scores.values(), key=lambda s: -s[0])
    if ranked[0][0] >= 2 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][1]
    return None


def prices_by_route(conn, owner, deal):
    need = Need.model_validate(deal["need"])
    out = {}
    for q in store.rows(conn, owner, db.quotes, deal["id"], where=[db.quotes.c.status == "active"]):
        ok, _ = pricing.validity(q, {**deal, "departure": None}, need)
        product = q["snapshot"].get("product_id")
        if ok and product and product not in out:
            s = pricing.summary(q["snapshot"], need, q["sales_total"])
            if s["per_person"]:
                out[product] = {
                    "per_person": s["per_person"],
                    "total": s["sales_total"] or s["market_total"],
                    "date": q["snapshot"].get("departure_date"),
                }
    return out


def chips(deal, need, gates, pending, result):
    """Next steps, only ever from what the program allows now."""
    types = {c["type"] for c in result["cards"]}
    v = deal["need_version"]
    if pending:
        return [{"label": "去确认变更单", "action": "review_change", "primary": True}]
    if not gates["search"]["ready"]:
        return [
            {"label": "发两道选择题", "action": "copy_draft", "primary": True},
            {"label": "给 3 个方向", "action": "directions"},
            {"label": "手动改需求", "action": "edit_need"},
        ]
    out = []
    found = next((c for c in result["cards"] if c["type"] == "routes"), None)
    if found and found.get("earlier") and deal.get("route"):
        # An earlier search does not steer a deal that has settled on a route.
        found = None
    shown = (found or {}).get("cards", [])
    if found and len(shown) >= 2:
        out.append({"label": "比较前两条", "action": "compare", "primary": True})
        out.append({"label": "做方案", "action": "plan"})
    elif found and shown:
        out.append({"label": "做方案", "action": "plan", "primary": True})
    elif found:
        # Nothing fits: the next move is loosening the condition that removed the most.
        relax = found.get("relax") or []
        out.append(
            {
                "label": relax[0]["text"] if relax else "改需求再找",
                "action": "edit_need",
                "primary": True,
            }
        )
    elif not deal.get("route"):
        out.append({"label": f"按 v{v} 找线", "action": "search", "primary": True})
    if deal.get("route") and not deal.get("departure"):
        out.append({"label": "看团期", "action": "dates", "primary": not out})
    if deal.get("departure"):
        out.append({"label": "去确认单", "action": "confirm", "primary": not out})
    if "answers" in types:
        out.append({"label": "看问答簿", "action": "qa"})
    if not gates["quote"]["ready"] and gates["search"]["ready"]:
        out.append({"label": "补全房型", "action": "edit_need"})
    return out[:3]


async def explore(wh, limit=3):
    """Directions from routes that are really on sale, for a customer who will not say more."""
    items = (await wh.products("", start=today(), end=today() + timedelta(days=180), limit=60)).get(
        "items", []
    )
    groups = {}
    for item in items:
        codes = destinations.country_codes(item.get("title", ""), cities=True)
        region = next((r for r, cs in destinations.REGIONS.items() if codes & set(cs)), None)
        label = region or next(
            (destinations.COUNTRIES[c][0] for c in sorted(codes) if c in destinations.COUNTRIES),
            None,
        )
        if not label:
            continue
        groups.setdefault(label, []).append(item)
    out = []
    for label, group in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]:
        days = sorted(
            {
                int(i["attributes"]["days"])
                for i in group
                if str(i.get("attributes", {}).get("days", "")).isdigit()
            }
        )
        span = (
            (f"{days[0]} 天" if len(days) == 1 else f"{days[0]}–{days[-1]} 天")
            if days
            else "天数不一"
        )
        out.append(
            {
                "region": label in destinations.REGIONS,
                "start": today().isoformat(),
                "end": (today() + timedelta(days=180)).isoformat(),
                "name": label + ("经典" if label in destinations.REGIONS else ""),
                "label": label,
                "days": span,
                "count": len(group),
                "example": routes.display(group[0]["title"]),
            }
        )
    return out
