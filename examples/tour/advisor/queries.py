"""Owner-scoped exploratory conditions and a viewed route, separate from the saved need.

Only explicit adoption creates a need version. Search, detail and departure reads reuse
the same conditions; a concurrent need edit expires them instead of silently rebasing.
"""

import re
from datetime import date, timedelta
from uuid import uuid4

from . import need as needs
from . import store
from .grounding import chinese_numbers

DATES = re.compile(
    r"团期|哪天.{0,6}(?:出发|发团)|(?:有|查|看).{0,8}团(?:吗|么|？|\?)|出发日期|发团日期"
)
VIEW = re.compile(r"(?:看|了解|介绍).{0,12}(?:线路|这条|行程)|这条.{0,5}(?:如何|怎样|怎么样)")
SELECT = re.compile(
    r"(?:就选|选定|确定选|决定选|就要|就这条|选这个|选这条|选第[一二三123]条|我要选)"
)
NOT_SELECT = re.compile(
    r"不选|别选|不要选|没选|还没|先不|暂不|考虑|如果|是否|能否|选.{0,12}[吗么？?]"
)
RELAX = re.compile(r"(?:前后)?放宽\s*(\d{1,2})\s*天")
DATE = re.compile(r"(?:(20\d{2})[年/-])?(\d{1,2})[月/-](\d{1,2})(?:日|号)?")


def context(deal):
    value = deal.get("query_context") or {}
    return value if value.get("base_version") == deal["need_version"] else {}


def effective(deal):
    need = needs.Need.model_validate(deal["need"])
    for field, item in context(deal).get("fields", {}).items():
        need = needs.set_field(need, field, item["new"], "explore", "", "顾问试探查询，未记入需求")
    return need


def view(deal):
    value = context(deal)
    fields = value.get("fields", {})
    if not fields:
        return None
    return {
        "id": value["id"],
        "base_version": value["base_version"],
        "conditions": [f"{needs.LABELS[f]}：{needs.show(f, i['new'])}" for f, i in fields.items()],
        "notice": "仅用于本次查询，未记入客人需求",
    }


def matches_search(deal, row):
    return row["need_version"] == deal["need_version"] and (
        (row["body"].get("query_context") or {}).get("id") == (view(deal) or {}).get("id")
    )


def save(conn, owner, deal, items):
    value = dict(context(deal))
    fields = dict(value.get("fields", {}))
    original = needs.Need.model_validate(deal["need"])
    for item in items:
        field = item["field"]
        if item["new"] == needs._json(original.get(field)):
            fields.pop(field, None)
        else:
            fields[field] = {"new": item["new"]}
    value.update(id=str(uuid4()), base_version=deal["need_version"], fields=fields)
    value.pop("viewed", None)
    store.update_deal(conn, owner, deal["id"], query_context=value)
    deal["query_context"] = value


def focus(conn, owner, deal, route):
    value = {**context(deal), "base_version": deal["need_version"], "viewed": route}
    store.update_deal(conn, owner, deal["id"], query_context=value)
    deal["query_context"] = value


def relaxed(need, field, amount=10):
    value = need.get(field)
    if field == "window" and value:
        new = {
            "start": (value.start - timedelta(days=amount)).isoformat(),
            "end": (value.end + timedelta(days=amount)).isoformat(),
            "label": "临时出发范围",
        }
    elif field == "days" and value:
        new = {"min": max(1, value.min - amount), "max": min(60, value.max + amount)}
    elif field == "depart_city":
        new = None
    else:
        raise store.Conflict("当前没有这一项可放宽的条件")
    return {"field": field, "new": new}


def resolve(engine, owner, deal_id, query_id, *, adopt=False):
    from .closing import consequences, require_unsold

    with engine.begin() as conn:
        deal = store.deal(conn, owner, deal_id, lock=True)
        value = context(deal)
        if not query_id or value.get("id") != query_id or not value.get("fields"):
            raise store.Conflict("查询条件已变化，请刷新后再操作")
        effects = []
        if adopt:
            require_unsold(conn, owner, deal)
            need = needs.Need.model_validate(deal["need"])
            for field, item in value["fields"].items():
                adopted = item["new"]
                if field == "window" and adopted and adopted.get("label") == "临时出发范围":
                    adopted = {**adopted, "label": ""}
                need = needs.set_field(need, field, adopted, "advisor", "", "顾问明确采纳查询条件")
            store.save_need(
                conn, owner, deal, need, list(value["fields"]), "顾问将查询条件记入需求"
            )
            effects = consequences(conn, owner, deal, need, value["fields"])
            if set(value["fields"]) & needs.SEARCH_FIELDS and deal.get("route"):
                from .closing import void_settled

                void_settled(conn, owner, deal_id, "查询条件已记入需求，需要重新选线核价")
                store.update_deal(conn, owner, deal_id, route=None, departure=None)
        store.update_deal(conn, owner, deal_id, query_context={})
        return {"version": deal["need_version"], "effects": effects}


def explicit_selection(text):
    return bool(SELECT.search(text) and not NOT_SELECT.search(text))


def dated_need(need, text, today):
    """A date mentioned in a question scopes this read, never the saved requirement."""
    match = DATE.search(text)
    if not match:
        return need
    window = need.get("window")
    year = int(match[1]) if match[1] else (window.start.year if window else today.year)
    try:
        value = date(year, int(match[2]), int(match[3]))
    except ValueError:
        return need
    return needs.set_field(
        need, "window", {"start": value.isoformat(), "end": value.isoformat()}, "explore"
    )


def action(text, understanding=None, named=None):
    if re.search(r"不(?:用|要|必|需要)(?:再)?(?:重新找|找线|查团期|选)", text):
        return "auto"
    if explicit_selection(text):
        return "select"
    if DATES.search(text):
        return "departures"
    if RELAX.search(chinese_numbers(text)) or re.search(
        r"找线|重新找|推荐.*线路|推荐几条|帮我推荐|重新搜|有哪些.{0,4}线路", text
    ):
        return "search"
    if VIEW.search(text) or (
        named
        and not re.search(
            r"[？?]|怎么|是否|能否|多少|吃饭|餐食|酒店|购物|自费|签证|退改|费用|航班", text
        )
    ):
        return "view"
    # A model may request reads, never promote a route mention to a write.
    proposed = getattr(understanding, "action", "auto")
    return proposed if proposed in {"search", "departures", "view", "quote"} else "auto"
