"""Semantic dependencies for advisor artifacts, independent of button events."""

from .integrations import fingerprint
from .trip_brief import QUOTE_FIELDS


def requirements(brief):
    body = brief.model_dump(mode="json")
    return {name: body[name]["value"] for name in sorted(QUOTE_FIELDS)}


def quote_terms(quote):
    if not quote:
        return None
    return {
        key: quote.get(key)
        for key in (
            "offer_id",
            "departure_date",
            "party",
            "currency",
            "complete",
            "market_total",
            "settlement_total",
            "market_lines",
            "settlement_lines",
            "missing_items",
            "versions",
            "business_timezone",
        )
    }


def stamp(brief):
    return {
        "requirements": requirements(brief),
        "route_id": brief.route_id,
        "departure_id": brief.departure_id,
        "offer_id": str(brief.offer_id) if brief.offer_id else None,
        "quote_terms": quote_terms(brief.quote),
    }


def changed(saved, brief):
    current = stamp(brief)
    if saved.get("requirements") != current["requirements"]:
        return "人数、房型或出行条件已变化"
    if any(saved.get(k) != current[k] for k in ("route_id", "departure_id", "offer_id")):
        return "所选线路、团期或方案已变化"
    if saved.get("quote_terms") is not None and saved["quote_terms"] != current["quote_terms"]:
        return "价格或报价适用条件已变化"
    return ""


def same_quote(left, right):
    return fingerprint(quote_terms(left)) == fingerprint(quote_terms(right))
