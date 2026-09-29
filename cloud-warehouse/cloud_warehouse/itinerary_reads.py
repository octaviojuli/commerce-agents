"""Bounded, published-only itinerary sections shared by advisor tools and HTTP reads."""

import json

from . import documents, route_content
from .advisor import parse_id
from .integrations import canonical


def read(engine, actor, product_id, *, departure_id=None, section="overview", day=None, offset=0):
    if section not in {"overview", "day", "terms"} or offset < 0:
        raise ValueError("无效的行程读取范围")
    published = documents.current(
        engine,
        actor,
        parse_id(product_id, "WP-"),
        departure_id=parse_id(departure_id, "WD-") if departure_id else None,
        route_preview=departure_id is None,
    )
    if not published:
        return {
            "status": "unavailable",
            "message": "暂无可读取的已发布行程，请向商户核实线路内容。",
        }
    # current() already projects buyer reads. Supplier readers still require projection.
    raw = published["body"]
    content = (
        route_content.customer_projection(raw, review_mode=published.get("review_mode", "human"))
        if "quality" in raw
        else raw
    )
    kit = content.get("schema") == "route-kit/1"
    if section == "overview":
        rows = [
            {"day": d["day"], "title": d["title"], "summary": d.get("summary", "")}
            for d in content["days"]
        ]
        metadata = {
            "title": content["title"],
            "days_count": content.get("days_count")
            if kit
            else content.get("summary", {}).get("days"),
        }
    elif section == "day":
        d = next(
            (
                d
                for d in content["days"]
                if d["day"] == day or d["day"] <= (day or 0) <= (d.get("day_end") or d["day"])
            ),
            None,
        )
        if not d:
            return {"status": "unavailable", "message": "该日暂无已复核内容。"}
        rows = d.get("items", d.get("blocks", []))
        if not rows and d.get("text"):
            rows = [{"type": "programme", "text": d["text"]}]
        metadata = {k: v for k, v in d.items() if k not in {"items", "blocks", "text"}}
    else:
        rows = [
            {"section": k, "content": v}
            for k in (
                "inclusions",
                "exclusions",
                "shopping",
                "optional_items",
                "optional",
                "prices",
                "service_fee",
                "single_room_supplement",
                "applicability",
                "policies",
                "notices",
                "formation",
                "meeting",
                "traveler_requirements",
                "cancellation_tiers",
            )
            if (v := content.get(k))
        ]
        metadata = {"price_notice": "附件参考费用，非实时报价。"}
    if len(canonical(metadata)) > 18000:
        return {
            "status": "view_required",
            "message": "此节较长，请打开完整行程查看，不能据摘要推断未显示内容。",
        }
    page = []
    for row in rows[offset : offset + 8]:
        if len(canonical({"meta": metadata, "items": [*page, row]})) > 18000:
            break
        page.append(row)
    if not page and offset < len(rows):
        return {
            "status": "view_required",
            "message": "此节较长，请在完整行程详情中查看，不能据摘要推断未显示内容。",
        }
    return {
        "status": "published",
        "product_id": product_id,
        "publication_id": str(published["id"]),
        **(
            {"publication_notice": content["publication_notice"]}
            if content.get("publication_notice")
            else {}
        ),
        "section": section,
        "day": day,
        "metadata": json.loads(canonical(metadata)),
        "items": page,
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
    }
