"""Route search with a visible funnel, route quick-look and departure comparison.

Must-have conditions (place, window, departure city, days) filter; nice-to-have ones
(budget, preferences, themes, examples) only rank and explain. When few routes remain,
the funnel says which condition removed them and what relaxing it would add.
"""

import asyncio
import re
from datetime import date, timedelta

from cloud_warehouse import destinations

from . import need as needs
from .facts import Route
from .suppliers import view as suppliers_view
from .warehouse import WarehouseError

CODE = re.compile(
    r"^\s*(?:W[PDO]-[a-f\d-]{8,}|[A-Z]{1,5}\d{1,5}(?:-[A-Z]{1,3}(?=[-－_]))?)\s*[-－_：:]*\s*"
)


def display(title: str) -> str:
    return CODE.sub("", title or "").strip()


def _days(item):
    """The day count the customer sees: the title's "N天" first, else the source field."""
    title = re.search(r"(\d{1,2})\s*天", item.get("title") or "")
    if title:
        return int(title.group(1))
    try:
        return int(item.get("attributes", {}).get("days") or 0) or None
    except ValueError:
        return None


def listed_days(item):
    """The effective warehouse display count, which may differ from the title."""
    try:
        return int(item.get("attributes", {}).get("days") or 0) or None
    except ValueError:
        return None


def day_check(attrs) -> dict:
    """Display calendar span separately from itinerary days; neither determines ownership."""

    def number(key):
        try:
            return int(attrs.get(key) or 0) or None
        except ValueError:
            return None

    return {
        "calendar_days": number("calendar_days"),
        "route_days": number("route_days"),
        "days_differ": bool(
            number("calendar_days")
            and number("route_days")
            and number("calendar_days") != number("route_days")
        ),
    }


def unpublished_reason(product, departures=()) -> str:
    """Report publication state and visible content differences, never infer a group mismatch gate."""
    title = re.search(r"(\d{1,2})\s*天", product.get("title") or "")
    named, listed = (int(title.group(1)) if title else None), listed_days(product)
    if named and listed and named != listed:
        return f"这条线路尚未发布行程；线路名写 {named} 天，线路登记 {listed} 天，请供应商核对内容"
    return "这条线路尚未发布行程"


def _city(item):
    return (item.get("attributes", {}).get("depart_city") or "").strip()


def query_for(need) -> str:
    places = need.get("destinations")
    words = []
    if places:
        words = places.must or places.regions or places.examples
    if not words:
        words = need.get("themes") or []
    return " ".join(words)


async def document(wh, product_id, departure_id=None, cache=None):
    key = (product_id, departure_id)
    if cache is not None and key in cache:
        return cache[key]
    try:
        doc = await wh.document(product_id, departure_id)
    except WarehouseError:
        doc = None
    if cache is not None:
        cache[key] = doc
    return doc


def route_view(item, doc) -> Route:
    body = (doc or {}).get("body") or {}
    return Route(item["product_id"], display(item.get("title", "")), body)


def supplier_of(item) -> tuple[str, str]:
    """The supplier's id and the short name advisors know it by."""
    a = item.get("attributes", {})
    return a.get("supplier_id", ""), a.get("supplier_name", "")


def _similar(a, b) -> bool:
    """Two routes a customer would weigh against each other: same countries, days within one."""
    ca = destinations.country_codes(a.get("title", ""), cities=True)
    cb = destinations.country_codes(b.get("title", ""), cities=True)
    da, db_ = _days(a), _days(b)
    return bool(ca) and ca == cb and (da is None or db_ is None or abs(da - db_) <= 1)


async def search(wh, need, *, prices=None, cache=None, limit=5, suppliers=(), notes=None):
    """Search the warehouse catalog for this need and explain the result.

    ``suppliers`` keeps only those suppliers' routes; ``notes`` are the advisor's own
    supplier notes, shown on each card.
    """
    prices, notes = prices or {}, notes or {}
    places = need.get("destinations")
    window, days, city = need.get("window"), need.get("days"), need.get("depart_city")
    budget = need.get("budget")
    query = query_for(need)
    everything = (await wh.products(query, limit=60))["items"] if query else []
    if places and places.exclude:
        everything = [
            i for i in everything if not any(x in i.get("title", "") for x in places.exclude)
        ]
    steps = []
    label = query.replace(" ", "·") or "全部线路"
    at_city = [i for i in everything if not city or city in _city(i)]
    steps.append({"label": label + (f" · {city}出发" if city else ""), "count": len(at_city)})
    in_window_ids = set()
    if window:
        dated = (
            (await wh.products(query, start=window.start, end=window.end, limit=60))["items"]
            if query
            else []
        )
        in_window_ids = {i["product_id"] for i in dated}
        in_window = [i for i in at_city if i["product_id"] in in_window_ids]
        steps.append(
            {
                "label": f"{window.label or needs.show('window', window)} 有团",
                "count": len(in_window),
            }
        )
    else:
        in_window = at_city
    if days:
        fits = [i for i in in_window if _days(i) is None or days.min <= _days(i) <= days.max]
        steps.append({"label": f"{needs.show('days', days)}", "count": len(fits)})
    else:
        fits = in_window
    # Which suppliers sell what fits, before any supplier filter narrows it.
    facet = {}
    for i in fits:
        sid, name = supplier_of(i)
        facet.setdefault(sid, {**suppliers_view(sid, name, notes), "count": 0})["count"] += 1
    fitting = fits
    selected_suppliers = set(suppliers)

    def supplier_matches(item):
        return not selected_suppliers or supplier_of(item)[0] in selected_suppliers

    if suppliers:
        fits = [i for i in fits if supplier_matches(i)]
        names = "、".join(facet[s]["name"] for s in suppliers if s in facet) or "所选供应商"
        steps.append({"label": f"只看 {names}", "count": len(fits)})
    alternatives = []
    if not fits and city:
        # Nothing from the stated city: other cities are shown only as alternatives.
        others = [
            i
            for i in everything
            if supplier_matches(i) and (i["product_id"] in in_window_ids or not window)
        ]
        alternatives = [
            i for i in others if not days or _days(i) is None or days.min <= _days(i) <= days.max
        ][:3]
    views = {}
    pool = (fits or alternatives)[: max(limit + 3, 8)]
    docs = await asyncio.gather(*(document(wh, i["product_id"], cache=cache) for i in pool))
    for item, doc in zip(pool, docs, strict=True):
        views[item["product_id"]] = route_view(item, doc)
    cards = [
        card(
            item,
            views[item["product_id"]],
            need,
            prices.get(item["product_id"]),
            alternative=item in alternatives,
        )
        for item in pool
    ]
    by_id = {i["product_id"]: i for i in pool}
    for c in cards:
        item = by_id[c["product_id"]]
        c["supplier"] = suppliers_view(*supplier_of(item), notes)
        # The same kind of route from other suppliers, so the advisor can choose whose to sell.
        c["also"] = [
            {
                "product_id": o["product_id"],
                "title": display(o.get("title", "")),
                "supplier": suppliers_view(*supplier_of(o), notes),
            }
            for o in fitting
            if supplier_of(o)[0] != supplier_of(item)[0] and _similar(o, item)
        ][:3]
    cards.sort(key=lambda c: -c["score"])
    within = [
        c
        for c in cards
        if c["price"] and budget and c["price"]["per_person"] <= float(budget.per_person)
    ]
    if budget:
        priced = [c for c in cards if c["price"]]
        steps.append(
            {
                "label": "预算内",
                "count": len(within) if priced else None,
                "note": "" if priced else "询价后才能判断",
                "over": [
                    {
                        "title": c["title"],
                        "by": round(c["price"]["per_person"] - float(budget.per_person)),
                    }
                    for c in priced
                    if c["price"]["per_person"] > float(budget.per_person)
                ][:2],
            }
        )
    relax = []
    if len(fits) < 3:
        if days:
            wider = [
                i
                for i in in_window
                if supplier_matches(i)
                and _days(i)
                and not days.min <= _days(i) <= days.max
                and abs(_days(i) - (days.min + days.max) / 2) <= 3
            ]
            if wider:
                relax.append(
                    {
                        "field": "days",
                        "text": f"天数放宽 ±2 天能多出 {len(wider)} 条",
                        "count": len(wider),
                    }
                )
        if city:
            elsewhere = [
                i
                for i in everything
                if supplier_matches(i)
                and city not in _city(i)
                and (not window or i["product_id"] in in_window_ids)
            ]
            if elsewhere:
                relax.append(
                    {
                        "field": "depart_city",
                        "text": f"不限出发地能多出 {len(elsewhere)} 条",
                        "count": len(elsewhere),
                    }
                )
        if window and query:
            wide = await wh.products(
                query,
                start=window.start - timedelta(days=10),
                end=window.end + timedelta(days=10),
                limit=60,
            )
            extra = [
                i
                for i in wide["items"]
                if supplier_matches(i)
                and i["product_id"] not in in_window_ids
                and (not city or city in _city(i))
            ]
            if extra:
                relax.append(
                    {
                        "field": "window",
                        "text": f"出发时间前后放宽 10 天能多出 {len(extra)} 条",
                        "count": len(extra),
                    }
                )
    must = [
        x
        for x in [
            label if query else "",
            needs.show("window", window) + " 出发" if window else "",
            city,
            needs.show("days", days) if days else "",
        ]
        if x
    ]
    nice = []
    if budget:
        nice.append(needs.show("budget", budget))
    for p in need.get("preferences") or []:
        nice.append(needs.PREFERENCES.get(p, p))
    nice += need.get("themes") or []
    if places and places.examples:
        nice += places.examples
    return {
        "query": query,
        "must": must,
        "nice": nice,
        "steps": steps,
        "cards": cards[:limit],
        "suppliers": sorted(facet.values(), key=lambda f: -f["count"]),
        "supplier_filter": list(suppliers),
        "alternatives_only": bool(alternatives and not fits),
        "relax": relax,
        "explain": explain(steps, relax, len(fits)),
    }


def explain(steps, relax, count):
    if count >= 3 or not steps:
        return ""
    worst = min(
        (
            (a, b)
            for a, b in zip(steps, steps[1:], strict=False)
            if a["count"] and b["count"] is not None and b["count"] < a["count"]
        ),
        key=lambda pair: (pair[1]["count"] or 0) - (pair[0]["count"] or 0),
        default=None,
    )
    text = (
        f"卡在“{worst[1]['label']}”这一步：{worst[0]['count']} 条里只剩 {worst[1]['count']} 条。"
        if worst
        else ""
    )
    if relax:
        text += relax[0]["text"] + "。"
    return text


def card(item, route: Route, need, price=None, *, alternative=False):
    """One candidate as shown in the search result and in the conversation."""
    grid = {g["label"]: g["value"] for g in route.grid()}
    yes, no, unknown = [], [], []
    score = 60
    city = need.get("depart_city")
    if city:
        if city in _city(item):
            yes.append(f"{city}出发")
            score += 5
        else:
            no.append(f"{_city(item) or '出发地未写明'}出发")
            score -= 30
    days = need.get("days")
    d = _days(item)
    if days and d:
        if days.min <= d <= days.max:
            yes.append(f"{d} 天")
            score += 5
        else:
            no.append(f"{d} 天")
            score -= 10
    prefs = need.get("preferences") or []
    shopping = grid.get("购物", "")
    if "no_shopping" in prefs:
        if "购物村" in shopping:
            unknown.append(shopping)
        elif "未列购物店" in shopping:
            yes.append("未列购物店")
            score += 10
        elif "店" in shopping:
            no.append(shopping)
            score -= 10
        else:
            unknown.append("购物未写明")
    if "slow_pace" in prefs:
        stays = [x["stay"] for x in route.days if x["stay"]]
        moves = len({s for s in stays})
        if route.days and stays:
            if moves <= max(2, len(route.days) // 3):
                yes.append("换酒店少")
                score += 10
            else:
                no.append(f"换酒店 {moves} 次")
                score -= 5
        else:
            unknown.append("节奏待看行程")
    places = need.get("destinations")
    title = display(item.get("title", ""))
    for example in places.examples if places else []:
        if example in title or any(example in x["title"] for x in route.days):
            yes.append(example)
            score += 5
    for theme in need.get("themes") or []:
        if theme in title:
            yes.append(theme)
            score += 5
    budget = need.get("budget")
    per_person = price["per_person"] if price else None
    if budget and per_person is not None:
        if per_person <= float(budget.per_person):
            yes.append("预算内")
            score += 10
        else:
            no.append(f"超 ¥{round(per_person - float(budget.per_person)):,}")
            score -= 10
    elif budget:
        unknown.append("价格待询")
    if not route.days:
        unknown.append("暂无已发布行程")
        score -= 5
    if not route.reviewed:
        unknown.append("行程未人工审核")
    return {
        "product_id": item["product_id"],
        "title": title,
        "days": d,
        "depart_city": _city(item),
        "three": {
            "hotel": grid.get("酒店", "未写明"),
            "meals": grid.get("含餐", "未写明"),
            "shopping": grid.get("购物", "未写明"),
        },
        "yes": yes,
        "no": no,
        "unknown": unknown,
        "score": max(0, min(100, score)),
        "price": price,
        "alternative": alternative,
        "notice": route.notice,
    }


async def departures(wh, product_id, need, *, around_days=7):
    """Departures near the need's window, the calendar of screen 15."""
    window = need.get("window")
    start = end = None
    if window:
        start, end = (
            window.start - timedelta(days=around_days),
            window.end + timedelta(days=around_days),
        )
    page = await wh.departures(product_id, start=start, end=end, limit=25)
    out = []
    for item in page.get("items", []):
        a = item.get("attributes", {})
        try:
            depart = date.fromisoformat(a.get("depart_date", ""))
        except ValueError:
            continue
        back = a.get("return_date") or ""
        out.append(
            {
                "departure_id": item["product_id"],
                # The catalog's default offer is not the advisor's choice; `closing.dates` fills it.
                "offer_id": None,
                "date": depart.isoformat(),
                "weekday": "一二三四五六日"[depart.weekday()],
                "return_date": back,
                "return_weekday": "一二三四五六日"[date.fromisoformat(back).weekday()]
                if back
                else "",
                "availability": a.get("availability", ""),
                "sales_status": a.get("sales_status_label", ""),
                "in_window": bool(window and window.start <= depart <= window.end),
                "code": item.get("option_values", {}).get("团期", ""),
                "leave_days": _workdays(depart, date.fromisoformat(back)) if back else None,
                **day_check(a),
            }
        )
    return out


def _workdays(start: date, end: date) -> int:
    days, current = 0, start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


async def overview(wh, target, need, *, around_days=10):
    """Live dates and published content are independent reads; neither selects a route."""
    pid, title = target["product_id"], target["title"]
    window = need.get("window")

    async def dated(start, end):
        items, after = [], None
        for _ in range(4):
            page = await wh.departures(pid, start=start, end=end, limit=50, after=after)
            for item in page.get("items", []):
                attrs = item.get("attributes", {})
                try:
                    day = date.fromisoformat(attrs.get("depart_date", ""))
                except ValueError:
                    continue
                if (start and day < start) or (end and day > end):
                    continue
                items.append(
                    {
                        "date": day.isoformat(),
                        "departure_id": item["product_id"],
                        "availability": attrs.get("availability", ""),
                        "in_window": bool(window and window.start <= day <= window.end),
                        **day_check(attrs),
                    }
                )
            after = page.get("next_cursor")
            if not after:
                break
        unique = {d["departure_id"]: d for d in items}
        return sorted(unique.values(), key=lambda d: (d["date"], d["departure_id"])), bool(after)

    async def date_read():
        try:
            exact, partial = await dated(
                window.start if window else None, window.end if window else None
            )
            nearby = []
            if window and not exact:
                nearby, more = await dated(
                    window.start - timedelta(days=around_days),
                    window.end + timedelta(days=around_days),
                )
                partial = partial or more
            items = exact or nearby
            status = (
                "available" if exact else "nearby" if nearby else "partial" if partial else "none"
            )
            return {
                "status": status,
                "items": items[:8],
                "partial": partial or len(items) > 8,
                "requested": needs.show("window", window) if window else "在售日期",
                "asked": asked_label(window),
                "around_days": around_days,
            }
        except WarehouseError as error:
            if error.status in (401, 403):
                raise
            return {
                "status": "error",
                "items": [],
                "partial": False,
                "message": "团期查询失败，请重试；不能据此判断是否有团",
            }

    async def doc_read():
        try:
            doc = await wh.document(pid)
        except WarehouseError as error:
            if error.status in (401, 403):
                raise
            return {
                "status": "error",
                "summary": [],
                "notice": "",
                "message": "行程查询失败，请重试",
            }
        route = Route(pid, title, (doc or {}).get("body"))
        return {
            "status": "published" if doc else "unpublished",
            "notice": route.notice,
            "summary": [f"D{d['day']} {d['title']}" for d in route.days[:4]],
            "message": "" if doc else "这条线路尚未发布行程",
        }

    dates, itinerary = await asyncio.gather(date_read(), doc_read())
    if itinerary["status"] == "unpublished":
        try:
            product = await wh.product(pid)
        except WarehouseError as error:
            if error.status in (401, 403):
                raise
            product = {"title": title}
        itinerary["message"] = unpublished_reason(product or {"title": title}, dates["items"])
    supplier = target.get("supplier")
    if isinstance(supplier, str):
        supplier = {"id": target.get("supplier_id", ""), "name": supplier}
    return {
        "product_id": pid,
        "title": title,
        "supplier": supplier,
        "dates": dates,
        "itinerary": itinerary,
    }


async def nearby_candidates(wh, need, *, suppliers=()):
    """Keep the direction, days and origin while inspecting adjacent departure dates."""
    window = need.get("window")
    if not window:
        return []
    widened = needs.set_field(
        need,
        "window",
        {
            "start": (window.start - timedelta(days=10)).isoformat(),
            "end": (window.end + timedelta(days=10)).isoformat(),
        },
        "explore",
    )
    body = await search(wh, widened, limit=3, suppliers=suppliers)
    candidates = [c for c in body["cards"] if not c["alternative"]]
    return await asyncio.gather(*(overview(wh, c, need) for c in candidates))


WEEKDAYS = "一二三四五六日"


def day_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}月{d.day}日（周{WEEKDAYS[d.weekday()]}）"


def asked_label(window) -> str:
    """The asked dates as a reply says them: 10月30日, or 10月20日至11月9日."""
    if not window:
        return "近期"

    def cn(d):
        return f"{d.month}月{d.day}日"

    if window.start == window.end:
        return cn(window.start)
    return f"{cn(window.start)}至{cn(window.end)}"


def date_line(item) -> str | None:
    """A route's departures in the words a reply uses; None when the read failed."""
    dates = item["dates"]
    if dates["status"] == "error":
        return None
    asked = dates.get("asked") or "所问时间"
    days = "、".join(dict.fromkeys(day_label(d["date"]) for d in dates["items"][:4]))
    title = display(item["title"])
    if dates["status"] == "available":
        return f"{title}：{asked}有团，出发日期{days}"
    if dates["status"] == "nearby":
        return f"{title}：{asked}没有团，前后最近的出发日期是{days}"
    if dates["status"] == "none":
        return f"{title}：{asked}前后{dates.get('around_days', 10)}天都没有团"
    return None


def reply_lines(item) -> list:
    """What a customer reply may say from one route read: its dates, a failed query, and the
    published itinerary's outline. Publication status stays with the advisor."""
    title = display(item["title"])
    lines = []
    if item["dates"]["status"] == "error":
        lines.append(f"{title}：团期这次没有查询成功（查询失败），暂时不能判断有没有团，稍后重新查")
    else:
        line = date_line(item)
        if line:
            lines.append(line)
    itinerary = item["itinerary"]
    if itinerary["status"] == "published" and itinerary["summary"]:
        lines.append(f"{title}：行程概要 " + "；".join(itinerary["summary"]))
    return lines


def overview_text(item):
    dates, itinerary = item["dates"], item["itinerary"]
    lines = [item["title"]]
    if dates["status"] == "error":
        lines.append(dates["message"])
    elif dates["items"]:
        label = (
            "所问日期范围内有团"
            if dates["status"] == "available"
            else "所问日期范围内未查到团，邻近可选日期"
        )
        lines.append(label + "：" + "、".join(dict.fromkeys(d["date"] for d in dates["items"])))
        if dates["partial"]:
            lines.append("这里只展示部分查询结果")
    else:
        lines.append(
            "团期查询结果不完整，请缩小日期范围"
            if dates["partial"]
            else "所问日期及前后10天内未查到团期"
        )
    if itinerary["message"]:
        lines.append(itinerary["message"])
    elif itinerary["summary"]:
        lines.append("行程概要：" + "；".join(itinerary["summary"]))
    if itinerary["notice"]:
        lines.append(itinerary["notice"])
    return "。".join(lines) + "。"
