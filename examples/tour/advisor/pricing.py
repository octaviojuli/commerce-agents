"""Warehouse price checks, the formal quote, and the one rule for whether a price still holds.

Children whose beds differ are priced by asking the warehouse once per bed arrangement and
composing the child lines, so the rule holds on any warehouse version. A price is valid only
for the party, rooms and departure it was asked for, and only until the warehouse says.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from . import need as needs

LINE_LABELS = {
    "adult": "成人",
    "child": "儿童",
    "senior": "老人",
    "single_room": "单房差",
    "room.doubles": "大床房调整",
    "room.twins": "双床房调整",
    "room.singles": "单间调整",
    "child.occupied": "儿童占床价",
    "child.unoccupied": "儿童不占床价",
}


MISSING_LABELS = {
    "fees_not_fully_confirmed": "费用未全部确认",
    "child_seat_policy_unknown": "儿童占床规则未写明",
    "child_age_policy_unknown": "儿童年龄规则未写明",
    "child_ages_required": "缺儿童年龄",
    "child_age_outside_price_rule": "儿童年龄超出价格规则",
    "room_type_not_confirmed": "房型未确认",
    "single_room_unpriced": "单房差未报价",
    "SOURCE_PRICE_TIMEOUT": "供应商价格超时",
    "SOURCE_PRICE_UNAVAILABLE": "供应商暂未给价",
    "INVALID_SOURCE_PRICE": "供应商价格不可用",
}


def missing_text(code: str) -> str:
    if code in MISSING_LABELS:
        return MISSING_LABELS[code]
    parts = code.split(".")
    if parts[0] in ("market", "settlement") and len(parts) > 2 and parts[1] == "charge":
        return f"附加费未报价（{parts[2]}）"
    if parts[0] in ("market", "settlement") and len(parts) > 1:
        return (
            ("同行价" if parts[0] == "settlement" else "门市价")
            + "缺"
            + LINE_LABELS.get(".".join(parts[1:]), parts[-1])
        )
    return "供应商价格待确认"


def unpriced_single(line) -> bool:
    """A single-room line at zero means the source left it blank, not that it is free."""
    return line.get("code") == "single_room" and line.get("unit_amount") in ("0.00", "0", 0)


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def terms(need) -> dict:
    """The part of the need a price depends on; stored with every quote."""
    party, rooms = need.get("party"), need.get("rooms")
    return {
        "party": party.model_dump(mode="json") if party else None,
        "rooms": rooms.model_dump(mode="json") if rooms else None,
    }


def warehouse_party(need, bed=None) -> dict:
    party, rooms = need.get("party"), need.get("rooms")
    children = party.children or []
    beds = {c.bed for c in children}
    uniform = bed if bed is not None else (beds.pop() if len(beds) == 1 else None)
    room_type = (
        "双人标准间"
        if rooms.doubles and not rooms.twins and not rooms.singles
        else "双床标准间"
        if rooms.twins and not rooms.doubles and not rooms.singles
        else "单人间"
        if rooms.singles and not rooms.doubles and not rooms.twins
        else "混合房型待供应商确认"
    )
    return {
        "adults": party.adults,
        "children": len(children),
        "seniors": len(party.seniors),
        "single_rooms": rooms.singles,
        "child_ages": [c.age for c in children],
        "room_type": room_type,
        # Only the fields every warehouse version accepts; ``total`` is the sum and newer ones derive it.
        "rooms": {
            "doubles": rooms.doubles,
            "twins": rooms.twins,
            "singles": rooms.singles,
            "child_bed": uniform if children else None,
            "raw": rooms.note,
        },
    }


async def check(wh, need, departure_id, offer_id=None):
    """Ask the warehouse for today's price for this party; compose per-bed child lines."""
    gate = needs.gates(need)["quote"]
    if not gate["ready"]:
        raise ValueError(
            "报价前还缺：" + "、".join(needs.MISSING_TEXT.get(k, k) for k in gate["missing"])
        )
    party = need.get("party")
    beds = {c.bed for c in party.children or []}
    if len(beds) > 1:
        occupied = await wh.quote(
            departure_id, warehouse_party(need, True), offer_id, key=str(uuid4())
        )
        unoccupied = await wh.quote(
            departure_id, warehouse_party(need, False), offer_id, key=str(uuid4())
        )
        snapshot = compose(occupied, unoccupied, party)
    else:
        snapshot = await wh.quote(departure_id, warehouse_party(need), offer_id, key=str(uuid4()))
        snapshot = {**snapshot, "parts": [snapshot.get("quote_id")]}
    return snapshot


def _child(line):
    return str(line.get("code", "")).startswith("child")


def compose(occupied, unoccupied, party):
    """Replace every child line with one line per bed arrangement, from the matching quote."""
    counts = {
        True: sum(1 for c in party.children if c.bed),
        False: sum(1 for c in party.children if c.bed is False),
    }
    out = dict(occupied)
    for side in ("market", "settlement"):
        lines = [dict(line) for line in occupied.get(side + "_lines", []) if not _child(line)]
        total = Decimal(0)
        complete = True
        for line in lines:
            if line.get("total") is None:
                complete = False
            else:
                total += Decimal(line["total"])
        for bed, source in ((True, occupied), (False, unoccupied)):
            if not counts[bed]:
                continue
            child = next((line for line in source.get(side + "_lines", []) if _child(line)), None)
            unit = child and child.get("unit_amount")
            lines.append(
                {
                    "code": "child.occupied" if bed else "child.unoccupied",
                    "label": "儿童占床" if bed else "儿童不占床",
                    "quantity": counts[bed],
                    "unit_amount": unit,
                    "total": str(money(Decimal(unit) * counts[bed])) if unit is not None else None,
                }
            )
            if unit is None:
                complete = False
            else:
                total += Decimal(unit) * counts[bed]
        out[side + "_lines"] = lines
        out[side + "_total"] = str(money(total)) if complete else None
    out["complete"] = bool(
        occupied.get("complete")
        and unoccupied.get("complete")
        and out.get("market_total")
        and out.get("settlement_total")
    )
    out["missing_items"] = sorted(
        set(occupied.get("missing_items", [])) | set(unoccupied.get("missing_items", []))
    )
    out["parts"] = [occupied.get("quote_id"), unoccupied.get("quote_id")]
    until = [
        x for x in (occupied.get("quote_valid_until"), unoccupied.get("quote_valid_until")) if x
    ]
    out["quote_valid_until"] = min(until) if until else None
    fresh = [x for x in (occupied.get("fresh_until"), unoccupied.get("fresh_until")) if x]
    out["fresh_until"] = min(fresh) if fresh else None
    return out


# Departures this many days either side of the window still fit it, as the date list shows them.
WINDOW_SLACK = 7


def validity(row, deal, need) -> tuple[bool, str]:
    """The single rule every screen and every draft uses for a stored price."""
    if row is None:
        return False, "还没有核价"
    if row["status"] != "active":
        return False, row.get("void_reason") or "报价已作废"
    route = (deal.get("route") or {}).get("product_id")
    if not route or row["snapshot"].get("product_id") != route:
        return False, "线路已换，需要重新核价"
    chosen = deal.get("departure") or {}
    if chosen.get("departure_id") and row["departure_id"] != chosen["departure_id"]:
        return False, "团期已换，需要重新核价"
    if chosen.get("offer_id") and row.get("offer_id") and row["offer_id"] != chosen["offer_id"]:
        return False, "套餐已换，需要重新核价"
    window, day = need.get("window"), row["snapshot"].get("departure_date")
    if window and day:
        start = date.fromisoformat(str(day)[:10])
        if (
            not window.start - timedelta(days=WINDOW_SLACK)
            <= start
            <= window.end + timedelta(days=WINDOW_SLACK)
        ):
            return False, "团期不在当前出行时间内，需要重新核价"
    if row["snapshot"].get("terms") != terms(need):
        return False, "人数或房间已变，需要重新核价"
    until = row.get("valid_until")
    if until and until <= datetime.now(UTC):
        return False, "报价已过有效期，需要重新核价"
    gaps = missing(row["snapshot"])
    if gaps:
        return False, "价格不完整：" + "、".join(gaps[:3])
    return True, ""


def missing(snapshot) -> list[str]:
    """What keeps this price from being final, in the advisor's words, once each."""
    codes = list(snapshot.get("missing_items", []))
    if any(unpriced_single(x) for x in snapshot.get("market_lines", [])):
        codes.append("single_room_unpriced")
    if not codes and not snapshot.get("complete"):
        codes.append("fees_not_fully_confirmed")
    return list(dict.fromkeys(missing_text(c) for c in codes))


def lines(snapshot, side="market", sales_total=None):
    """The breakdown the customer sees; a sales price unlike the list price adds one adjustment."""
    out = []
    for line in snapshot.get(side + "_lines", []):
        if line.get("total") in (None, "0.00", "0") and str(line.get("code", "")).startswith(
            "room."
        ):
            continue
        blank = unpriced_single(line)
        out.append(
            {
                "label": line.get("label") or LINE_LABELS.get(line.get("code"), line.get("code")),
                "quantity": line.get("quantity"),
                "unit": None if blank else line.get("unit_amount"),
                "total": None if blank else line.get("total"),
            }
        )
    market = snapshot.get("market_total")
    if side == "market" and sales_total is not None and market:
        diff = money(sales_total) - money(market)
        if diff:
            out.append(
                {
                    "label": "价格调整" if diff > 0 else "优惠",
                    "quantity": 1,
                    "unit": str(diff),
                    "total": str(diff),
                }
            )
    return out


def per_person(snapshot, need, sales_total=None) -> float | None:
    total = sales_total if sales_total is not None else snapshot.get("market_total")
    party = need.get("party")
    if not total or not party or not party.total:
        return None
    return float(Decimal(total) / party.total)


def summary(snapshot, need, sales_total=None):
    """Totals for the formal quote card: customer, trade and margin."""
    market = snapshot.get("market_total")
    settlement = snapshot.get("settlement_total")
    sales = (
        Decimal(str(sales_total))
        if sales_total is not None
        else (Decimal(market) if market else None)
    )
    profit = margin = None
    if sales is not None and settlement:
        profit = money(sales - Decimal(settlement))
        margin = str((profit / sales * 100).quantize(Decimal("0.1"))) if sales else None
    return {
        "market_total": market,
        "settlement_total": settlement,
        "sales_total": str(money(sales)) if sales is not None else None,
        "profit": str(profit) if profit is not None else None,
        "margin": margin,
        "per_person": per_person(snapshot, need, sales),
        "currency": snapshot.get("currency", "CNY"),
        "complete": bool(snapshot.get("complete")) and not missing(snapshot),
        "missing": missing(snapshot),
        "hints": snapshot.get("hints", []),
        "valid_until": snapshot.get("quote_valid_until"),
        "fresh_until": snapshot.get("fresh_until"),
    }
