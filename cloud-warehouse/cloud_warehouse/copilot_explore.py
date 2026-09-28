"""Exploration directions aggregated only from the advisor's live, saleable catalog."""

import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import text

from . import copilot_records, destinations, trip_brief
from .changes import Conflict
from .integrations import fingerprint
from .persistence import transaction


def direction_label(name):
    """A region, else the country visited, else a title theme; None when there is none."""
    codes = destinations.country_codes(name, cities=True)
    region = next(
        (region for region, countries in destinations.REGIONS.items() if codes & set(countries)),
        None,
    )
    if region:
        return region
    countries = sorted(destinations.COUNTRIES[c][0] for c in codes if c in destinations.COUNTRIES)
    if countries:
        return countries[0]
    return next(
        (
            theme
            for theme, pattern in (
                ("海边放松", "海岛|沙滩|海滨|海湾"),
                ("自然风光", "雪山|山水|峡谷|森林|观鲸"),
                ("城市漫游", "城市|古城|都市|博物馆"),
            )
            if re.search(pattern, name)
        ),
        None,
    )


def _directions(conn):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rows = (
        conn.execute(
            text("""SELECT p.id,p.effective_name AS name,p.days,d.depart_date,
      price.schedule#>>'{market,adult}' AS adult_price,price.schedule->>'currency' AS currency
      FROM product_listing p JOIN departure d ON d.product_id=p.id
      LEFT JOIN LATERAL (
        SELECT cp.schedule FROM offer o
        CROSS JOIN LATERAL warehouse_applicable_prices(o.id,warehouse_org_id()) cp
        WHERE o.departure_id=d.id AND o.active AND cp.valid_from<=now() AND cp.valid_until>now()
          AND (warehouse_sales_version()=0 OR cp.layer='standard')
        ORDER BY CASE cp.layer WHEN 'contract' THEN 0 WHEN 'grade' THEN 1 ELSE 2 END,cp.id LIMIT 1
      ) price ON true
      WHERE p.status='published' AND d.status='published' AND NOT d.sales_paused
      AND (d.local_booking_deadline IS NULL OR d.local_booking_deadline>now())
      AND d.depart_date BETWEEN :today AND :end ORDER BY d.depart_date,p.id LIMIT 2000"""),
            {"today": today, "end": today + timedelta(days=180)},
        )
        .mappings()
        .all()
    )
    groups = defaultdict(list)
    for row in rows:
        label = direction_label(row["name"])
        if label:
            # A route with no place or theme is not offered as a made-up direction.
            groups[label].append(row)
    # When the available catalog is concentrated in one region, real title
    # themes can supply additional choices. Never fabricate three directions.
    if len(groups) < 3:
        for label, pattern in (
            ("小镇慢游", "小镇|慢游|休闲"),
            ("自然风光", "自然|山湖|雪山|峡谷|观鲸"),
            ("城市漫游", "城市|古城|博物馆"),
            ("海边放松", "海边|海岛|沙滩|海滨"),
        ):
            group = [r for r in rows if re.search(pattern, r["name"])]
            if group and label not in groups:
                groups[label] = group
            if len(groups) >= 3:
                break
    result = []
    for label, group in sorted(
        groups.items(), key=lambda item: (-len({r["id"] for r in item[1]}), item[0])
    )[:3]:
        known_days = [r["days"] for r in group if r["days"]]
        priced = [r for r in group if r["adult_price"] is not None]
        currencies = {r["currency"] for r in priced}
        price_range = None
        if priced and len(currencies) == 1:
            amounts = [Decimal(r["adult_price"]) for r in priced]
            price_range = {
                "min": str(min(amounts)),
                "max": str(max(amounts)),
                "currency": priced[0]["currency"],
            }
        scope = {
            "label": label,
            "routes": sorted({str(r["id"]) for r in group}),
            "start": str(min(r["depart_date"] for r in group)),
            "end": str(max(r["depart_date"] for r in group)),
        }
        result.append(
            {
                **scope,
                "id": fingerprint(scope),
                "name": label + ("经典" if label in destinations.REGIONS else ""),
                "count": len(scope["routes"]),
                "days_min": min(known_days) if known_days else None,
                "days_max": max(known_days) if known_days else None,
                "price_range": price_range,
                "price_note": "已审核成人基础客人价，未计房差与另付；部分团期价格可能未知"
                if price_range
                else "目录未提供可比较的客人价，选团期后核价",
            }
        )
    return result


def directions(engine, actor):
    with transaction(engine, actor) as conn:
        return {"items": _directions(conn), "notice": "按未来半年在售团期聚合；价格未知时不估算。"}


def choose(engine, actor, identifier, direction_id, request):
    with transaction(engine, actor) as conn:
        brief, version = copilot_records.checked(conn, identifier, request.expected_version)
        item = next((x for x in _directions(conn) if x["id"] == direction_id), None)
        if not item:
            raise Conflict("方向的在售线路已变化，请重新看看当前方向")
        if brief.route_id:
            raise Conflict("已选线路，请先在需求单中调整方向")
        key = "destination_regions" if item["label"] in destinations.REGIONS else "themes"
        fields = {
            key: {
                "value": [item["label"]],
                "source": "explore",
                "hint": "顾问选定的探索方向，只作排序参考",
            }
        }
        if not brief.window.value:
            fields["window"] = {
                "value": {"start": item["start"], "end": item["end"]},
                "source": "explore",
                "hint": "当前方向的在售日期范围，客人出发窗口待确认",
            }
        body = brief.model_dump(mode="json")
        body.update(fields)
        updated = trip_brief.TripBrief.model_validate(body)
        return trip_brief.save(conn, actor, identifier, updated, version + 1)
