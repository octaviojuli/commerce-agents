"""One advisor copilot with a program-owned, bounded and always-answering turn pipeline."""

import re
import time
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from cloud_warehouse import (
    advisor_actions,
    advisor_flow,
    copilot_engine,
    copilot_explore,
    copilot_facts,
    copilot_memory,
    copilot_plans,
    copilot_policy,
    copilot_records,
    copilot_reply,
    trip_brief,
)
from cloud_warehouse.changes import Conflict
from cloud_warehouse.integrations import SourceError, fingerprint
from cloud_warehouse.persistence import Forbidden, transaction
from commerce_common.streaming import AgentEvent
from shopping_agent import NotOffered, SearchFilters

from .copilot_model import Decision, TypedModel

CLARIFY = {
    "destinations": "这次更想海边放松、逛逛城市，还是亲近自然？",
    "window": "您倾向近期出发，还是等假期再走？",
    "adults": "这次有几位成人同行？",
    "children": "有几位儿童同行？没有的话请说明。",
    "child_ages": "儿童分别多大？",
    "rooms": "希望安排几间房，双人房、双床房还是单人房？",
    "rooms.child_bed": "儿童需要单独占床吗？",
}


def load_context(backend, identifier):
    with transaction(backend.engine, backend.actor) as conn:
        current = trip_brief.conversations._load(conn, identifier)
        copilot_records.ensure(conn, backend.actor, identifier)
        saved, version = trip_brief.load(conn, identifier)
        message = conn.scalar(
            text("SELECT message FROM agent_turn WHERE id=:id AND status='running'"),
            {"id": current["lease_id"]},
        )
        if not message:
            raise Conflict("缺少有效会话轮次")
        visible = []
        query, filters = advisor_actions.search_parameters(saved)
        route_scope = fingerprint({"query": query, "filters": filters.attributes})
        for component in ("warehouse_routes", "warehouse_departures"):
            pages = (
                conn.execute(
                    text("""SELECT e->'data'->'payload' FROM agent_turn t,
              LATERAL jsonb_array_elements(t.events) e WHERE t.conversation_id=:id AND t.status='complete'
              AND e->'data'->>'component'=:component ORDER BY t.completed_at DESC,t.id DESC LIMIT 100"""),
                    {"id": identifier, "component": component},
                )
                .scalars()
                .all()
            )
            if pages and (
                (
                    component == "warehouse_routes"
                    and (pages[0].get("page_scope") == route_scope or pages[0].get("named_lookup"))
                )
                or (
                    component == "warehouse_departures"
                    and pages[0].get("product_id") == saved.route_id
                )
            ):
                visible.extend(advisor_flow.displayed_items(pages))
        memory = (
            conn.execute(
                text("""SELECT r.body FROM advisor_record r JOIN advisor_deal d ON d.id=r.deal_id
          WHERE r.kind='memory' AND (r.deal_id=:id OR d.customer_id=(SELECT customer_id FROM advisor_deal WHERE id=:id))
          ORDER BY r.created_at DESC LIMIT 8"""),
                {"id": identifier},
            )
            .scalars()
            .all()
        )
        customer = (
            conn.execute(
                text(
                    "SELECT c.body->>'name' AS name, jsonb_path_query_array(c.body, '$.travelers[*].name') AS names FROM advisor_deal d JOIN advisor_customer c ON c.id=d.customer_id WHERE d.id=:id"
                ),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        meta = {
            "salutation": customer["name"] if customer else "",
            "names": ([customer["name"]] + list(customer["names"] or [])) if customer else [],
            "confirmed": copilot_engine.current_confirmation(conn, identifier, saved, version)
            is not None,
            "sold": bool(
                conn.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM advisor_record WHERE deal_id=:id AND kind='sale')"
                    ),
                    {"id": identifier},
                )
            ),
            "pending": bool(copilot_engine.pending_fields(conn, identifier)),
            "clarifications": conn.scalar(
                text(
                    "SELECT count(*) FROM (SELECT events FROM agent_turn WHERE conversation_id=:id AND status='complete' ORDER BY completed_at DESC LIMIT 2) t WHERE jsonb_path_exists(events, '$[*].data.payload ? (@.clarification == true)')"
                ),
                {"id": identifier},
            ),
        }
        return saved, version, current["lease_id"], message, visible, memory, meta


def redact(value, names=()):
    if isinstance(value, str):
        for name in sorted(set(names), key=len, reverse=True):
            if name:
                value = value.replace(name, "[客人称呼]")
        value = re.sub(r"[\u4e00-\u9fff]{1,3}(?:先生|女士|小姐|太太)", "[客人称呼]", value)
        return re.sub(r"(?<!\d)(?:\d{17}[\dXx]|1[3-9]\d{9})(?!\d)", "[个人信息已隐藏]", value)
    if isinstance(value, list):
        return [redact(x, names) for x in value]
    if isinstance(value, dict):
        return {k: redact(v, names) for k, v in value.items()}
    return value


def question(brief):
    ready = trip_brief.readiness(brief)
    missing = ready["search"]["missing"] or (ready["quote"]["missing"] if brief.route_id else [])
    return (
        "\n".join(
            CLARIFY.get(k, "请核对需求卡中标记的待确认内容。")
            for k in list(dict.fromkeys(missing))[:2]
        )
        if missing
        else ""
    )


LABELS_ZH = {
    "destinations": "目的地",
    "destination_regions": "目的地区域",
    "destination_examples": "举例目的地",
    "excluded_destinations": "不去的目的地",
    "window": "出发时间",
    "days": "天数",
    "depart_city": "出发地",
    "party_total": "总人数",
    "adults": "成人",
    "children": "儿童",
    "seniors": "长者",
    "child_ages": "儿童年龄",
    "rooms": "房型",
    "preferences": "偏好",
    "budget": "预算",
    "themes": "主题",
}
FIELD_LABELS = {
    "party": "人数",
    "child_ages": "儿童年龄",
    "window": "出发时间",
    "days": "天数",
    "depart_city": "出发地",
    "destinations": "目的地",
    "budget": "预算",
    "rooms": "房型与占床",
    "preferences": "偏好",
}
LINE_LABELS = {
    "adult": "成人",
    "child": "儿童",
    "senior": "长者",
    "child.occupied": "儿童占床",
    "child.unoccupied": "儿童不占床",
    "single_room": "单房差",
}
CONCERN = re.compile(r"怕|担心|在意|顾虑|别太|不要|不想|希望|最好|讨厌|受不了|晕车|第一次")


# Actions that make no commitment and give way to answering a question.
YIELDING = {"ask_clarify", "search_routes", "list_departures", "note_memory", "compare_dates"}


def named_routes(message, visible):
    """Routes the message names by a distinctive part of their title, e.g. "慢游小镇"."""
    text_value = re.sub(r"\s", "", message)
    scores = {}
    for product in visible:
        if not product["product_id"].startswith("WP-"):
            continue
        title = re.sub(r"\s|ACME", "", copilot_reply.display_name(product["title"]))
        best = max(
            (
                n
                for n in range(3, len(title) + 1)
                for i in range(len(title) - n + 1)
                if title[i : i + n] in text_value
            ),
            default=0,
        )
        scores[product["product_id"]] = best
    if not scores:
        return []
    # A part shared by every route ("德法意瑞") names none of them.
    shared = min(scores.values()) if len(scores) > 1 else 0
    named = [pid for pid, score in scores.items() if score >= 3 and score > shared]
    return named[:3]


def _num(value):
    amount = Decimal(str(value))
    return str(amount.quantize(Decimal(1))) if amount == amount.to_integral() else str(amount)


def _day(value):
    return f"{value.month}月{value.day}日"


def requirement_facts(brief, scope):
    """The saved requirements as citable facts, and which ones are on file."""
    facts, known = [], set()

    def add(field, text_value):
        known.add(field)
        facts.append(copilot_facts.fact(text_value, "requirement", {**scope, "field": field}))

    adults, children, seniors = brief.adults.value, brief.children.value, brief.seniors.value
    if adults is not None or brief.party_total.value:
        parts = []
        if adults:
            parts.append(f"{adults}位成人")
        if children:
            ages = brief.child_ages.value or []
            parts.append(
                f"{children}位儿童" + (f"（{'、'.join(f'{a}岁' for a in ages)}）" if ages else "")
            )
        if seniors:
            parts.append(f"{seniors}位长者")
        add("party", "、".join(parts) or f"共{brief.party_total.value}人")
    if brief.child_ages.value and children and len(brief.child_ages.value) == children:
        known.add("child_ages")
    if brief.window.value:
        add("window", f"{_day(brief.window.value.start)}至{_day(brief.window.value.end)}之间出发")
    if brief.days.value:
        days = brief.days.value
        add("days", f"{days.min}天" if days.min == days.max else f"{days.min}至{days.max}天")
    if brief.depart_city.value:
        add("depart_city", f"{brief.depart_city.value}出发")
    places = brief.destinations.value or brief.destination_regions.value
    if places:
        add("destinations", "目的地：" + "、".join(places))
    if brief.budget.value:
        add("budget", f"预算每人{_num(brief.budget.value.max_per_person)}元以内")
    rooms = brief.rooms.value
    if rooms and rooms.doubles + rooms.twins + rooms.singles:
        kinds = [
            f"{label}{n}间"
            for label, n in (
                ("双人房", rooms.doubles),
                ("双床房", rooms.twins),
                ("单人房", rooms.singles),
            )
            if n
        ]
        beds = (
            "、".join(
                f"{b.age}岁{'占床' if b.bed else '不占床'}"
                for b in rooms.child_beds
                if b.age is not None
            )
            if rooms.child_beds
            else ""
        )
        add("rooms", "、".join(kinds) + (f"；{beds}" if beds else ""))
    for name in trip_brief.FIELDS:
        saved = getattr(brief, name)
        if saved.value is not None and saved.source == trip_brief.Source.said and saved.evidence:
            # The customer's own phrase may be restated as said.
            facts.append(
                copilot_facts.fact(saved.evidence, "requirement", {**scope, "field": "said"})
            )
    for preference in brief.preferences.value or []:
        known.add("preferences")
        facts.append(
            copilot_facts.fact(
                "您希望" + preference.label, "requirement", {**scope, "field": "concern"}
            )
        )
    if children:
        facts.append(
            copilot_facts.fact("这次有孩子同行", "requirement", {**scope, "field": "concern"})
        )
    missing = trip_brief.readiness(brief)["quote"]["missing"]
    return facts, known, [FIELD_LABELS.get(k.split(".")[0], k) for k in missing]


def offer_facts(brief, scope):
    """The chosen departure and its current customer price; never the settlement side."""
    facts = []
    if brief.route_id and brief.route_title:
        name = copilot_reply.display_name(brief.route_title)
        date = (brief.quote or {}).get("departure_date") or ""
        facts.append(
            copilot_facts.fact(
                name + (f"，{date}出发" if date else ""), "catalog", {**scope, "field": "route"}
            )
        )
    quote = brief.quote or {}
    valid = quote.get("quote_valid_until")
    if (
        quote.get("complete")
        and quote.get("market_total")
        and valid
        and datetime.fromisoformat(valid) > datetime.now(ZoneInfo("UTC"))
    ):
        currency = quote.get("currency", "CNY")
        currency = "元" if currency == "CNY" else " " + currency
        facts.append(
            copilot_facts.fact(
                f"全家合计{_num(quote['market_total'])}{currency}",
                "price",
                {**scope, "quote_id": quote.get("quote_id")},
            )
        )
        units = {
            line.get("code"): Decimal(line["unit_amount"])
            for line in quote.get("market_lines", [])
            if line.get("unit_amount")
        }
        if "child.occupied" in units and "child.unoccupied" in units:
            facts.append(
                copilot_facts.fact(
                    f"儿童不占床比占床每位便宜{_num(units['child.occupied'] - units['child.unoccupied'])}{currency}",
                    "price",
                    {**scope, "quote_id": quote.get("quote_id")},
                )
            )
        for line in quote.get("market_lines", []):
            label = LINE_LABELS.get(line.get("code"))
            if label and line.get("unit_amount"):
                facts.append(
                    copilot_facts.fact(
                        f"{label}每位{_num(line['unit_amount'])}{currency}",
                        "price",
                        {**scope, "quote_id": quote.get("quote_id")},
                    )
                )
    return facts


def grounded_memory(items, message):
    """Keep concerns in the customer's own words: model items are mapped to a clause."""
    clauses = [c.strip() for c in re.split(r"[，,。！？!?；;]", message) if len(c.strip()) >= 3]
    kept = [c for c in clauses if CONCERN.search(c)]
    for item in items:
        if item in message:
            kept.append(item)
            continue
        words = copilot_reply.bigrams(item)
        best = max(clauses, key=lambda c: len(words & copilot_reply.bigrams(c)), default="")
        if (
            best
            and words
            and len(words & copilot_reply.bigrams(best)) >= 0.5 * len(copilot_reply.bigrams(best))
        ):
            kept.append(best)
    kept = list(dict.fromkeys(kept))
    return [k for k in kept if not any(k != other and k in other for other in kept)][:3]


def salute(text_value, salutation):
    """Put the saved salutation back; never "您您好"."""
    name = salutation or ""
    text_value = re.sub(
        r"\[客人称呼\]\s*[，,]?\s*(?=您好)", (name + "，") if name else "", text_value
    )
    text_value = text_value.replace("[客人称呼]", name or "您")
    return re.sub(r"您[，,\s]*您好", "您好", text_value)


class CopilotAgent:
    def __init__(self, backend, *, client=None):
        self.backend = backend
        self.models = TypedModel(client)

    def set_metrics(self, metrics):
        self.models.metrics = metrics

    async def aclose(self):
        await self.models.aclose()

    async def stream_turn(self, messages, context, state):
        try:
            async for event in self._stream_turn(messages, context, state):
                yield event
        except (ValueError, Conflict, NotOffered, SourceError):
            # A failed domain read must not end with an empty assistant bubble.
            # Database/authorization failures remain errors and cannot claim a save.
            reply = "本次查询暂未完成。已保留需求，请核对需求卡或重新查询；价格和行程细节仍待核实。"
            messages.append({"role": "assistant", "content": reply})
            yield AgentEvent.ui(
                "copilot_reply",
                {
                    "to_advisor": reply,
                    "to_customer": "这个细节我还需要进一步核实，确认后再回复您。",
                    "claims": [],
                    "degraded": ["查询未完成"],
                },
            )
            yield AgentEvent.text_delta(reply)
            yield AgentEvent(type="turn_complete", data={"stop_reason": "end_turn"})

    async def _stream_turn(self, messages, context, state):
        started = time.monotonic()
        identifier = UUID(context.session_id)
        brief, version, turn_id, message, visible, memory, meta = await run_in_threadpool(
            load_context, self.backend, identifier
        )
        yield AgentEvent(type="progress", data={"message": "正在理解本轮需求，核对当前版本…"})
        current = {
            "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "message": message,
            "selected_route": brief.route_id,
            "selected_departure": brief.departure_id,
            "has_quote": brief.quote_id is not None,
            "requirements": {
                k: getattr(brief, k).model_dump(mode="json") for k in trip_brief.FIELDS
            },
            "visible": [
                {
                    "id": p["product_id"],
                    "title": p["title"],
                    "date": p.get("attributes", {}).get("depart_date"),
                }
                for p in visible
            ],
            "memory": memory,
            "recent_messages": messages[-12:],
        }
        extraction = await self.models.call("extract", redact(current, meta["names"]))
        decision = None
        degraded = []
        unread = []
        proposal = None
        if extraction:
            fields = {
                c.field: c.model_dump(mode="json", exclude={"field"}) for c in extraction.changes
            }
            total = fields.get("party_total")
            if total and total["source"] == "inferred" and brief.party_total.value is None:
                adults = fields.get("adults", {}).get("value", brief.adults.value)
                children = fields.get("children", {}).get("value", brief.children.value)
                seniors = fields.get("seniors", {}).get("value", brief.seniors.value) or 0
                if (
                    isinstance(adults, int)
                    and isinstance(children, int)
                    and total["value"] == adults + children + seniors
                ):
                    # A redundant inferred sum must not block explicitly stated
                    # initial facts. Party pricing already computes this sum.
                    fields.pop("party_total")
            try:
                proposal = await run_in_threadpool(
                    copilot_engine.propose,
                    self.backend.engine,
                    self.backend.actor,
                    identifier,
                    fields,
                    message,
                    turn_id,
                )
                brief = trip_brief.TripBrief.model_validate(proposal["brief"]["body"])
                version = proposal["brief"]["version"]
                if proposal.get("rejected"):
                    # One unreadable field is a note for the advisor, not a failed turn.
                    unread.extend(proposal["rejected"])
                if proposal["status"] == "pending":
                    yield AgentEvent.ui("copilot_proposal", {**proposal, "brief_version": version})
            except ValueError:
                retry = await self.models.call(
                    "extract",
                    redact(
                        {
                            **current,
                            "validation_feedback": "上次变更未通过字段或原话证据校验。仅返回与当前值不同的字段；evidence 必须逐字复制本轮原文，不能把中文数字换成阿拉伯数字；rooms 必须使用 doubles/twins/singles/child_bed/raw。",
                        }
                    ),
                )
                try:
                    if retry is None:
                        raise ValueError("extraction unavailable")
                    proposal = await run_in_threadpool(
                        copilot_engine.propose,
                        self.backend.engine,
                        self.backend.actor,
                        identifier,
                        {
                            c.field: c.model_dump(mode="json", exclude={"field"})
                            for c in retry.changes
                        },
                        message,
                        turn_id,
                    )
                    brief = trip_brief.TripBrief.model_validate(proposal["brief"]["body"])
                    version = proposal["brief"]["version"]
                    if proposal["status"] == "pending":
                        yield AgentEvent.ui(
                            "copilot_proposal", {**proposal, "brief_version": version}
                        )
                except ValueError:
                    degraded.append("本轮需求解析未通过校验，请在需求卡核对后修改。")
        else:
            degraded.append("本轮自动理解暂不可用，已保留原话，可在需求卡补充。")
        if extraction and extraction.rejected_fields:
            unread.extend(extraction.rejected_fields)
        policy = copilot_policy.derive(
            brief,
            visible=visible,
            pending=meta["pending"] or bool(proposal and proposal["status"] == "pending"),
            confirmed=meta["confirmed"],
            sold=meta["sold"],
        )
        decision = await self.models.call(
            "decide",
            redact(
                {
                    **current,
                    "requirements": {
                        k: getattr(brief, k).model_dump(mode="json") for k in trip_brief.FIELDS
                    },
                    **policy,
                },
                meta["names"],
            ),
        )
        if extraction and not extraction.changes and decision and decision.intent == "change":
            retry = await self.models.call(
                "extract",
                redact(
                    {
                        **current,
                        "validation_feedback": "本轮意图为调整，changes 却为空。逐项检查本轮年龄、人数、房型和日期与已保存字段的差异。",
                    },
                    meta["names"],
                ),
            )
            if retry and retry.changes:
                extraction = retry
                proposal = await run_in_threadpool(
                    copilot_engine.propose,
                    self.backend.engine,
                    self.backend.actor,
                    identifier,
                    {c.field: c.model_dump(mode="json", exclude={"field"}) for c in retry.changes},
                    message,
                    turn_id,
                )
                brief = trip_brief.TripBrief.model_validate(proposal["brief"]["body"])
                version = proposal["brief"]["version"]
                if proposal["status"] == "pending":
                    yield AgentEvent.ui("copilot_proposal", {**proposal, "brief_version": version})
                policy = copilot_policy.derive(
                    brief,
                    visible=visible,
                    pending=proposal["status"] == "pending",
                    confirmed=meta["confirmed"],
                    sold=meta["sold"],
                )
            else:
                degraded.append("本轮理解不完整：提到了调整，但没有提取到可核对的变化，可重试。")
        if decision is None:
            degraded.append("本轮判断未完成，可重试。")
        decision = decision or Decision(intent="unknown", confidence=0)
        action = (
            decision.next_action
            if decision.next_action in policy["allowed_actions"]
            else policy["allowed_actions"][0]
        )
        if "present_directions" in policy["allowed_actions"] and (
            meta["clarifications"] >= 2
            or re.search(r"你推荐吧|你来推荐|帮我推荐|没想好.*推荐", message)
        ):
            action = "present_directions"
        valid = {p["product_id"] for p in visible}
        targets = [x for x in decision.target_ids if x in valid] or named_routes(message, visible)
        if brief.route_id and not targets:
            targets = [brief.route_id]
        facts = []
        forbidden = [p.get("attributes", {}).get("source_name", "") for p in visible]
        notice = []
        conclusion = "已保留本轮沟通。"
        pending = meta["pending"] or bool(proposal and proposal["status"] == "pending")
        asking = decision.intent in {"ask", "compare"} or bool(re.search(r"[？?]|吗", message))
        if targets and asking and not pending and action in YIELDING:
            # A question about a named route is answered from its facts.
            wanted = "compare_routes" if len(targets) > 1 else "answer_question"
            if wanted in policy["allowed_actions"]:
                action = wanted
            elif "route_facts" in policy["allowed_actions"]:
                action = "route_facts"
        if pending:
            conclusion = "发现需求变化，请先采纳或保留原需求。"
        elif degraded:
            conclusion = "本轮理解不完整，可重试；已保存的需求保持有效。"
        elif action == "present_directions":
            result = await run_in_threadpool(
                copilot_explore.directions, self.backend.engine, self.backend.actor
            )
            yield AgentEvent.ui("copilot_directions", {**result, "brief_version": version})
            for item in result["items"]:
                facts.append(
                    copilot_facts.fact(
                        f"{item['name']}，"
                        + (
                            f"{item['days_min']}天"
                            if item["days_min"] == item["days_max"]
                            else f"{item['days_min']}至{item['days_max']}天"
                        )
                        + f"，{item['count']}条线路在售"
                        + (
                            f"，客人价{item['price_range']}"
                            if item.get("price_range")
                            else "，价格选团期后核实"
                        ),
                        "direction",
                        {"direction_id": item["id"]},
                    )
                )
            conclusion = (
                "根据当前在售团期整理了可探索的方向，选一个再细化。"
                if result["items"]
                else "当前没有可用的在售方向，先保留偏好，调整出发窗口后再查。"
            )
        elif action == "lookup_route" and decision.lookup_name and decision.lookup_name in message:
            page = await run_in_threadpool(
                self.backend.catalog_page,
                context,
                query=decision.lookup_name,
                filters=SearchFilters(),
                limit=3,
            )
            state.remember_products(page["items"])
            products = [p.model_dump(mode="json") for p in page["items"]]
            yield AgentEvent.ui(
                "warehouse_routes",
                {**page, "items": products, "brief_version": version, "named_lookup": True},
            )
            forbidden += [p["attributes"].get("source_name", "") for p in products]
            for product in products:
                facts.append(
                    copilot_facts.fact(
                        copilot_reply.display_name(product["title"]),
                        "catalog",
                        {"product_id": product["product_id"]},
                    )
                )
            conclusion = (
                "已按名称找到线路，可直接查看行程和团期。"
                if products
                else "未找到同名的可见线路，请核对名称中的关键词。"
            )
        elif (
            decision.intent == "select"
            and action in {"list_departures", "build_confirmation", "compare_dates"}
            and targets
            and decision.confidence >= 0.85
        ):
            chosen = targets[0]
            command = "departures" if chosen.startswith("WP-") else "offers"
            yield AgentEvent.ui(
                "copilot_choice",
                {
                    "product_id": chosen,
                    "action": command,
                    "brief_version": version,
                    "title": next(
                        (p["title"] for p in visible if p["product_id"] == chosen),
                        brief.route_title or "当前线路",
                    ),
                },
            )
            conclusion = "已识别客人的选择，请点击卡片确认后继续。"
        elif action in {"quote", "recheck_price"} and brief.departure_id:
            if trip_brief.readiness(brief)["quote"]["ready"]:
                yield AgentEvent.ui(
                    "copilot_choice",
                    {
                        "product_id": brief.departure_id,
                        "offer_id": str(brief.offer_id) if brief.offer_id else None,
                        "action": "quote",
                        "brief_version": version,
                        "title": "重新核对统一结算价",
                    },
                )
                conclusion = "人数与房型已齐，请点击核价；正式销售报价还需核对确认单。"
            else:
                conclusion = "可以继续核价，先补齐这一项。"
        elif (
            action in {"answer_question", "route_facts", "compare_routes", "build_plan"} and targets
        ):
            yield AgentEvent(
                type="progress", data={"message": "正在读取当前线路内容与商户核实回复…"}
            )
            for target in targets[:3]:
                route = target if target.startswith("WP-") else brief.route_id
                if not route:
                    continue
                try:
                    result = await run_in_threadpool(
                        copilot_facts.read,
                        self.backend.engine,
                        self.backend.actor,
                        route,
                        brief.departure_id if route == brief.route_id else None,
                        day=decision.day,
                    )
                    facts.extend(result["facts"])
                    if result["notice"]:
                        notice.append(result["notice"])
                except (ValueError, Forbidden, Conflict):
                    notice.append("当前线路内容不可用，请重新查询或向商户核实。")
            facts += await run_in_threadpool(
                copilot_facts.adopted, self.backend.engine, self.backend.actor, identifier, version
            )
            conclusion = "已核对可用依据；未确认的安排可以发给商户核实。"
            yield AgentEvent.ui(
                "copilot_facts",
                {
                    "facts": facts,
                    "notice": list(dict.fromkeys(notice)),
                    "brief_version": version,
                    "comparison": decision.intent == "compare",
                },
            )
        elif (
            action == "search_routes"
            and trip_brief.readiness(brief)["search"]["ready"]
            and not brief.route_id
            and decision.intent not in {"ask", "chitchat", "aftercare"}
        ):
            result = await advisor_actions.perform(
                self.backend,
                context,
                state,
                advisor_actions.Action(
                    action="search_routes",
                    expected_version=version,
                    limit=3,
                    request_id=uuid5(NAMESPACE_URL, f"copilot:{turn_id}:search"),
                ),
            )
            yield AgentEvent.ui(
                result["component"], {**result["payload"], "brief_version": version}
            )
            conclusion = result["payload"]["summary"]
            for p in result["payload"]["items"]:
                attrs = p.get("attributes", {})
                forbidden.append(attrs.get("source_name", ""))
                facts.append(
                    copilot_facts.fact(
                        f"{copilot_reply.display_name(p['title'])}，{attrs.get('days', '待核实')}天，{attrs.get('depart_city', '待核实')}出发。",
                        "catalog",
                        {"product_id": p["product_id"], "version": attrs.get("version")},
                    )
                )
                facts[-1]["conflict"] = bool(
                    brief.depart_city.value and attrs.get("depart_city") != brief.depart_city.value
                )
            policy = copilot_policy.derive(
                brief,
                visible=result["payload"]["items"],
                confirmed=meta["confirmed"],
                sold=meta["sold"],
            )
        elif action in {"collect_documents", "predeparture_task"}:
            conclusion = "可以登记线下收款、核对旅客材料并安排出发前待办。"
        else:
            conclusion = question(brief) or "可以继续查看行程、比较线路，或在确认后核价。"
        remembered = grounded_memory(extraction.memory if extraction else [], message)
        if remembered:
            await run_in_threadpool(
                copilot_facts.save_output,
                self.backend.engine,
                self.backend.actor,
                identifier,
                turn_id,
                "memory",
                {"text": "；".join(remembered), "source": "said", "source_turn": str(turn_id)},
                version,
            )
        yield AgentEvent(type="progress", data={"message": "正在整理可发给客人的回复…"})
        if not brief.route_id and not any(f["kind"] == "catalog" for f in facts):
            # Routes already on screen can be named without searching again.
            for p in [p for p in visible if p["product_id"].startswith("WP-")][:3]:
                attrs = p.get("attributes", {})
                forbidden.append(attrs.get("source_name", ""))
                facts.append(
                    copilot_facts.fact(
                        f"{copilot_reply.display_name(p['title'])}，{attrs.get('days', '待核实')}天，{attrs.get('depart_city', '待核实')}出发。",
                        "catalog",
                        {"product_id": p["product_id"], "version": attrs.get("version")},
                    )
                )
                facts[-1]["conflict"] = bool(
                    brief.depart_city.value and attrs.get("depart_city") != brief.depart_city.value
                )
        routes = [f for f in facts if f["kind"] == "catalog"]
        if len(routes) > 1:
            facts.append(copilot_facts.fact(f"{len(routes)}条线路", "catalog", {"routes": True}))
        scope = {"deal_id": str(identifier), "version": version}
        saved_facts, known, still_missing = requirement_facts(brief, scope)
        # The customer's words this turn may be restated back to them.
        saved_facts.append(
            copilot_facts.fact(
                redact(message, meta["names"]), "requirement", {**scope, "field": "said"}
            )
        )
        facts.extend(saved_facts + offer_facts(brief, scope))
        # Priorities are questions to ask, not questions already asked by the customer.
        next_question = question(brief)
        drafted = await self.models.call(
            "draft",
            redact(
                {
                    "message": message,
                    "facts": facts,
                    "next_question": next_question,
                    "clarify_candidates": [
                        candidate
                        for candidate in (extraction.clarify_candidates if extraction else [])
                        if any(
                            term in candidate and term in next_question
                            for term in (
                                "时间",
                                "出发",
                                "海边",
                                "方向",
                                "目的地",
                                "几位",
                                "占床",
                                "年龄",
                            )
                        )
                    ][:2],
                    "known_requirements": [f["text"] for f in saved_facts],
                    "still_missing": still_missing,
                    "memory": memory + [{"text": x} for x in remembered],
                    "salutation": meta["salutation"],
                    "pending": pending,
                    "degraded": bool(degraded),
                    "conclusion": conclusion,
                    "allowed_actions": policy["allowed_actions"],
                    "next_action": action,
                },
                meta["names"],
            ),
        )
        if degraded:
            fallback = "收到，我再核对一下这次沟通的细节，稍后继续和您确认。"
        elif pending:
            fallback = "收到您的想法，我先核对这些变化对方案的影响，再和您确认。"
        elif policy["stage"] == "explore":
            fallback = next_question or "这次更偏向海边放松，还是城市和自然风光？"
        elif decision.intent == "ask":
            fallback = "这个具体安排还缺少明确依据，我核实适用条件后再回复您。"
        else:
            fallback = next_question or "我们可以先看看这些备选，再一起确认出发时间和具体安排。"
        validated = copilot_reply.validate(
            drafted,
            facts,
            policy["allowed_actions"],
            fallback=fallback,
            conclusion=conclusion,
            forbidden=forbidden,
            metrics=self.models.metrics,
            max_questions=2 if policy["stage"] == "explore" and action == "ask_clarify" else None,
            preserve_requirements=action == "search_routes" and not pending and not degraded,
            known=known,
        )
        customer = salute(validated["to_customer"], meta["salutation"])
        chosen = validated["claims"]
        if degraded:
            customer = fallback
            validated["simplified"] = True
        else:
            conclusion = validated["to_advisor"]
        if drafted is None:
            degraded.append("起草服务暂不可用，本轮已使用依据模板。")
        asked = [q for q in extraction.customer_questions if q in message] if extraction else []
        if not asked and decision.intent == "ask":
            asked = [message]
        if asked:
            await run_in_threadpool(
                copilot_facts.save_output,
                self.backend.engine,
                self.backend.actor,
                identifier,
                turn_id,
                "qa",
                {
                    "questions": asked,
                    "topic": copilot_memory.topic("；".join(asked)),
                    "product_id": targets[0] if targets else "",
                    "answer": customer,
                    "facts": chosen,
                    "status": "answered" if chosen else "pending",
                },
                version,
            )
        if action in {"compare_routes", "build_plan"} and targets:
            comparison = await run_in_threadpool(
                copilot_plans.compare, self.backend.engine, self.backend.actor, identifier, targets
            )
            yield AgentEvent.ui("copilot_comparison", comparison)
        if action == "build_plan" and targets:
            with_version = {**comparison, "text": customer, "plan_version": version}
            await run_in_threadpool(
                copilot_facts.save_output,
                self.backend.engine,
                self.backend.actor,
                identifier,
                turn_id,
                "plan",
                with_version,
                version,
            )
        if unread:
            fields = list(dict.fromkeys(unread))
            conclusion += (
                "（未能记录："
                + "、".join(LABELS_ZH.get(k, k) for k in fields)
                + "，请在需求卡核对）"
            )
        payload = {
            **validated,
            "to_advisor": conclusion,
            "to_customer": customer,
            "claims": chosen,
            "brief_version": version,
            "degraded": degraded,
            "clarity": copilot_engine.clarity(brief),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            **policy,
            "next_action": action,
            "confidence": decision.confidence_by_question,
            "clarification": bool(next_question and policy["stage"] == "explore"),
        }
        yield AgentEvent.ui("copilot_reply", payload)
        final = conclusion + ("\n" + "\n".join(degraded) if degraded else "")
        messages.append({"role": "assistant", "content": final})
        # Business state and immutable records own long-term memory, not a growing model transcript.
        if len(messages) > 24:
            messages[:] = messages[-24:]
        yield AgentEvent.text_delta(final)
        yield AgentEvent(
            type="turn_complete",
            data={"stop_reason": "end_turn", "elapsed_ms": payload["elapsed_ms"]},
        )
