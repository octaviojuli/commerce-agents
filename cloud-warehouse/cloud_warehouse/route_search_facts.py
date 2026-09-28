"""Published itinerary facts for the versioned search projection, never prices or stock."""

VERSION = 1


def derive(body):
    if not body:
        return {
            "shopping": "unknown",
            "meals_included": None,
            "hotel_grades": [],
            "selling_points": [],
            "optional_count": None,
        }
    kit = body.get("schema") == "route-kit/1"
    days = body.get("days", [])
    nodes = [n for d in days for n in d.get("items" if kit else "blocks", [])]
    shopping = bool(body.get("shopping")) or any(n.get("type") == "shopping" for n in nodes)
    if not kit:
        shopping |= any(s.get("kind") == "购物" for d in days for s in d.get("sights", []))
    state = (
        "conflict"
        if shopping
        else "ok"
        if not kit or body.get("shopping_status") == "none"
        else "unknown"
    )
    meals = [m for d in days for m in d.get("meals", {}).values() if isinstance(m, dict)]
    grades = [
        d.get("stay" if kit else "hotel", {}).get("grade_text" if kit else "grade", "")
        for d in days
        if d.get("stay" if kit else "hotel")
    ]
    if kit:
        grades += [
            x.get("text", "") for x in body.get("cover_facts", []) if x.get("category") == "hotel"
        ]
    else:
        grades += [body.get("cover", {}).get("hotel_standard", "")]
    return {
        "shopping": state,
        "meals_included": sum(
            m.get("status") == "included" if kit else m.get("included") is True for m in meals
        ),
        "meals_unknown": sum(
            m.get("status", "unknown") == "unknown" if kit else m.get("included") is None
            for m in meals
        ),
        "hotel_grades": list(dict.fromkeys(x for x in grades if x)),
        "selling_points": [x.get("text", "") for x in body.get("selling_points", [])]
        if kit
        else body.get("cover", {}).get("highlights", []),
        "optional_count": len(body.get("optional_items" if kit else "optional", []))
        + sum(n.get("type") in {"optional", "package"} for n in nodes),
    }
