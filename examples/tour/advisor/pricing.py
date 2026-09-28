"""Warehouse price checks, the formal quote, and the one rule for whether a price still holds.

Children whose beds differ are priced by asking the warehouse once per bed arrangement and
composing the child lines, so the rule holds on any warehouse version. A price is valid only
for the party, rooms and departure it was asked for, and only until the warehouse says.
"""

from datetime import UTC, datetime
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
}


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
        "rooms": {
            "total": rooms.total,
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


def validity(row, deal, need) -> tuple[bool, str]:
    """The single rule every screen and every draft uses for a stored price."""
    if row is None:
        return False, "还没有核价"
    if row["status"] != "active":
        return False, row.get("void_reason") or "报价已作废"
    departure = (deal.get("departure") or {}).get("departure_id")
    if departure and row["departure_id"] != departure:
        return False, "团期已换，需要重新核价"
    if row["snapshot"].get("terms") != terms(need):
        return False, "人数或房间已变，需要重新核价"
    until = row.get("valid_until")
    if until and until <= datetime.now(UTC):
        return False, "报价已过有效期，需要重新核价"
    if not row["snapshot"].get("complete"):
        return False, "价格不完整：" + "、".join(
            row["snapshot"].get("missing_items", [])[:3]
        ) or "价格不完整"
    return True, ""


def lines(snapshot, side="market"):
    out = []
    for line in snapshot.get(side + "_lines", []):
        if line.get("total") in (None, "0.00", "0") and str(line.get("code", "")).startswith(
            "room."
        ):
            continue
        out.append(
            {
                "label": line.get("label") or LINE_LABELS.get(line.get("code"), line.get("code")),
                "quantity": line.get("quantity"),
                "unit": line.get("unit_amount"),
                "total": line.get("total"),
            }
        )
    return out


def per_person(snapshot, need) -> float | None:
    total = snapshot.get("market_total")
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
        "per_person": per_person(snapshot, need),
        "currency": snapshot.get("currency", "CNY"),
        "complete": bool(snapshot.get("complete")),
        "missing": snapshot.get("missing_items", []),
        "valid_until": snapshot.get("quote_valid_until"),
        "fresh_until": snapshot.get("fresh_until"),
    }
