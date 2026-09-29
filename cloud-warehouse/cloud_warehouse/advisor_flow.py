"""Require a human choice before a model advances from routes to departures or quotes."""

import re

from sqlalchemy import text

from . import conversations
from .changes import Conflict
from .persistence import transaction

_BLOCK = re.compile(
    r"先不|暂不|不要|不选|不看|别查|比较|对比|区别|重新找|换个目的地|只(?:看|要|推荐).*线路"
)
_DATES = re.compile(r"团期|发团|出发日期|哪天|哪几天|几号")
_PRICE = re.compile(r"询价|报价|多少钱|价格|费用|方案")
_ORDINAL = re.compile(r"第\s*(\d{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:条|个|团|期)")


def _number(value):
    if value.isdigit():
        return int(value)
    numbers = dict(zip("一二三四五六七八九两", [1, 2, 3, 4, 5, 6, 7, 8, 9, 2], strict=True))
    if "十" in value:
        left, right = value.split("十", 1)
        return numbers.get(left, 1) * 10 + numbers.get(right, 0)
    return numbers.get(value, 0)


def choice(message, items):
    """Resolve only a unique, explicit reference in the human's latest message.

    Ordinals refer to the completed displayed page chain, never this turn's search.
    Ambiguous/negative choices fail closed and the route buttons remain available.
    """
    if _BLOCK.search(message):
        return None
    found = set()
    for match in _ORDINAL.finditer(message):
        index = _number(match[1]) - 1
        if not 0 <= index < len(items):
            return None
        found.add(items[index]["product_id"])
    for item in items:
        attrs = item.get("attributes") or {}
        aliases = [item.get("product_id"), item.get("title"), attrs.get("code")]
        aliases.extend((item.get("option_values") or {}).values())
        if attrs.get("depart_date"):
            aliases.append(attrs["depart_date"])
        if any(
            isinstance(value, str) and len(value) >= 3 and value in message for value in aliases
        ):
            found.add(item["product_id"])
    # A short, unique name such as "选 ACME 轻奢那条" is also a choice; a
    # destination by itself or a number of travelers is never a selection.
    named = re.search(
        r"(?:选(?:择)?|看看|看|要)\s*[“\"]?(.{2,40}?)[”\"]?(?:那条|这条|的团期|的报价)", message
    )
    if named:
        found.update(
            item["product_id"] for item in items if named[1].strip() in item.get("title", "")
        )
    return next(iter(found)) if len(found) == 1 else None


def require_choice(engine, actor, conversation_id, action, target, brief):
    """Use the active server lease and persisted, previously displayed cards.

    Direct HTTP buttons are already explicit user actions and do not call this
    model-only guard. No model-supplied claimed intent or evidence is accepted.
    """
    if action not in {"departures", "offers", "quote"}:
        return
    route = action == "departures"
    component = "warehouse_routes" if route else "warehouse_departures"
    refusal = (
        "顾问尚未选定这条线路。只展示匹配线路，等待点击“查看团期”或明确选择一条；不要提前查团期、追问报价资料。"
        if route
        else "顾问尚未选定这一个团期并要求报价。停留在当前步骤，等待选择，不要自动询价或展示方案。"
    )
    with transaction(engine, actor) as conn:
        current = conversations._load(conn, conversation_id)
        turn = (
            conn.execute(
                text("SELECT message,started_at FROM agent_turn WHERE id=:id AND status='running'"),
                {"id": current["lease_id"]},
            )
            .mappings()
            .one_or_none()
            if current["busy"]
            else None
        )
        if turn is None:
            raise Conflict("顾问操作必须来自当前有效会话轮次")
        message = turn["message"]
        if _BLOCK.search(message):
            raise ValueError(refusal)
        intent = _DATES.search(message) if route else _PRICE.search(message)
        selection = re.search(
            r"选(?:择|定)?|就要|第\s*[\d一二三四五六七八九十两]+\s*(?:条|个)|(?:这|那)条"
            if route
            else r"第\s*[\d一二三四五六七八九十两]+\s*(?:期|团|个团期)",
            message,
        )
        if not route and re.search(r"第\s*[\d一二三四五六七八九十两]+\s*条", message):
            raise ValueError(refusal)
        if not intent and not selection:
            raise ValueError(refusal)
        # Questions about itinerary content are not requests to advance stages.
        if not intent and re.search(r"介绍|行程|亮点|特色|详情|怎么样|如何", message):
            raise ValueError(refusal)
        pages = (
            conn.execute(
                text("""SELECT event.value->'data'->'payload' FROM agent_turn t
                CROSS JOIN LATERAL jsonb_array_elements(t.events) WITH ORDINALITY event(value,n)
                WHERE t.conversation_id=:conversation AND t.status='complete'
                  AND t.completed_at<=:started AND event.value->>'type'='ui'
                  AND event.value->'data'->>'component'=:component
                ORDER BY t.completed_at DESC,t.id DESC,event.n DESC LIMIT 100"""),
                {
                    "conversation": conversation_id,
                    "started": turn["started_at"],
                    "component": component,
                },
            )
            .scalars()
            .all()
        )
        items = displayed_items(pages)
        selected = choice(message, items)
        if selected == target:
            return
        # Once chosen, "再查这条的团期" or "重新询价" can use that choice.
        # A conflicting/ambiguous reference must not silently fall back to it.
        previous = brief["body"].get("route_id" if route else "departure_id")
        continuation = re.fullmatch(
            r"(?:请|帮我)?(?:再|重新)?(?:查|查询|查看|看|看看|刷新)(?:(?:这|当前|已选)条(?:线路)?的)?团期[。！!？?]?"
            if route
            else r"(?:请|帮我)?(?:再|重新)?(?:询价|报价|查价)[。！!？?]?",
            message.strip(),
        )
        if continuation and selected is None and previous == target:
            return
        raise ValueError(refusal)


def displayed_items(pages):
    """Reconstruct server-persisted append pages; a new search resets ordinal order."""
    if not pages:
        return []
    latest = pages[0]
    chain = [latest]
    after = latest.get("after")
    for previous in pages[1:]:
        if not after:
            break
        if (
            previous.get("page_scope") != latest.get("page_scope")
            or previous.get("next_cursor") != after
        ):
            # Never splice unrelated searches or infer a missing page.
            return []
        chain.insert(0, previous)
        after = previous.get("after")
    if after:
        return []
    result, seen = [], set()
    for page in chain:
        for item in page.get("items", []):
            key = item.get("product_id")
            if key and key not in seen:
                seen.add(key)
                result.append(item)
    return result
