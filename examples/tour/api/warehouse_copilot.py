"""One advisor copilot with a program-owned, bounded and always-answering turn pipeline."""

import asyncio
import re
import time
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from cloud_warehouse import (
    advisor_actions,
    advisor_flow,
    copilot_engine,
    copilot_facts,
    copilot_records,
    trip_brief,
)
from cloud_warehouse.changes import Conflict
from cloud_warehouse.integrations import SourceError, fingerprint
from cloud_warehouse.persistence import Forbidden, transaction
from commerce_common.streaming import AgentEvent
from shopping_agent import NotOffered

from .copilot_model import Decision, TypedModel

CLARIFY = {
    "destinations": "这次最想去哪里，或更偏向海边、城市还是自然风光？",
    "window": "大概哪段时间可以出发？",
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
                (component == "warehouse_routes" and pages[0].get("page_scope") == route_scope)
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
        return saved, version, current["lease_id"], message, visible, memory


def redact(value):
    if isinstance(value, str):
        return re.sub(r"(?<!\d)(?:\d{17}[\dXx]|1[3-9]\d{9})(?!\d)", "[个人信息已隐藏]", value)
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def question(brief):
    ready = trip_brief.readiness(brief)
    missing = ready["search"]["missing"] or (ready["quote"]["missing"] if brief.route_id else [])
    return CLARIFY.get(missing[0], "请核对需求卡中标记的待确认内容。") if missing else ""


class CopilotAgent:
    def __init__(self, backend, *, client=None):
        self.backend = backend
        self.models = TypedModel(client)

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
        brief, version, turn_id, message, visible, memory = await run_in_threadpool(
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
        extraction, decision = await asyncio.gather(
            self.models.call("extract", redact(current)),
            self.models.call("decide", redact(current)),
        )
        degraded = []
        if extraction and not extraction.changes and decision and decision.intent == "change":
            extraction = await self.models.call(
                "extract",
                redact(
                    {
                        **current,
                        "validation_feedback": "本轮意图是调整需求，但上次返回空 changes。重新逐项核对本轮原话与 requirements，补充未知字段也属于变化；不要从历史助手文字补事实。",
                    }
                ),
            )
            if extraction and not extraction.changes:
                degraded.append("已识别需求调整，但未提取到可核对的变化，请检查需求卡。")
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
        decision = decision or Decision(intent="unknown", confidence=0)
        if (
            extraction
            and any(q in message for q in extraction.questions if q)
            and decision.intent != "quote"
        ):
            decision = decision.model_copy(update={"intent": "ask"})
        valid = {p["product_id"] for p in visible}
        targets = [x for x in decision.target_ids if x in valid]
        if brief.route_id and not targets:
            targets = [brief.route_id]
        facts = []
        notice = []
        conclusion = "已保留本轮沟通。"
        pending = bool(degraded or (proposal and proposal["status"] == "pending"))
        if pending:
            conclusion = "发现需求变化，请先采纳或保留原需求。"
        elif decision.intent == "select" and targets and decision.confidence >= 0.85:
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
        elif decision.intent == "quote" and brief.departure_id:
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
        elif decision.intent in {"ask", "compare", "recommend"} and targets:
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
            trip_brief.readiness(brief)["search"]["ready"]
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
                facts.append(
                    copilot_facts.fact(
                        f"{p['title']}；{attrs.get('days', '待核实')}天；{attrs.get('depart_city', '待核实')}出发。",
                        "catalog",
                        {"product_id": p["product_id"], "version": attrs.get("version")},
                    )
                )
        elif decision.intent == "aftercare":
            conclusion = "可以登记线下收款、核对旅客材料并安排出发前待办。"
        else:
            conclusion = question(brief) or "可以继续查看行程、比较线路，或在确认后核价。"
        if extraction:
            remembered = [x for x in extraction.memory if x and x in message]
            if remembered:
                await run_in_threadpool(
                    copilot_facts.save_output,
                    self.backend.engine,
                    self.backend.actor,
                    identifier,
                    turn_id,
                    "memory",
                    {"text": "；".join(remembered), "source": "said"},
                    version,
                )
        yield AgentEvent(type="progress", data={"message": "正在整理可发给客人的回复…"})
        drafted = await self.models.call(
            "draft",
            redact(
                {
                    "message": message,
                    "facts": facts,
                    "next_question": question(brief),
                    "pending": pending,
                    "conclusion": conclusion,
                }
            ),
        )
        by_id = {f["fact_id"]: f for f in facts if f["reviewed"]}
        chosen = [by_id[x] for x in (drafted.fact_ids if drafted else []) if x in by_id]
        if drafted and drafted.opening == "pending":
            chosen = []
        # Customer prose only contains server facts chosen by ID, preserving their conditions.
        # Unvalidated free model prose is never a fallback for factual answers.
        if pending:
            customer = "收到您的调整，我会按更新后的需求重新核对方案。"
        elif chosen:
            customer = "我核对到以下信息：\n" + "\n".join(f["text"] for f in chosen)
            if notice:
                customer += "\n" + "\n".join(dict.fromkeys(notice))
        elif decision.intent in {"ask", "compare", "recommend"}:
            customer = "这个细节我还需要向供应商核实，确认适用条件后再回复您。"
        else:
            customer = question(brief) or "我已整理好当前方案，接下来为您核对具体团期和适用条件。"
        if drafted is None:
            degraded.append("起草服务暂不可用，本轮已使用依据模板。")
        asked = [q for q in extraction.questions if q in message] if extraction else []
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
                    "answer": customer,
                    "facts": chosen,
                    "status": "answered" if chosen else "pending",
                },
                version,
            )
        if decision.intent == "recommend" and chosen:
            await run_in_threadpool(
                copilot_facts.save_output,
                self.backend.engine,
                self.backend.actor,
                identifier,
                turn_id,
                "plan",
                {
                    "text": customer,
                    "facts": chosen,
                    "limitation": "适用团期、费用及未核实事项需在正式报价前确认。",
                },
                version,
            )
        payload = {
            "to_advisor": conclusion,
            "to_customer": customer,
            "claims": chosen,
            "brief_version": version,
            "degraded": degraded,
            "clarity": copilot_engine.clarity(brief),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
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
