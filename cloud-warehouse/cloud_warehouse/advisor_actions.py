"""One authority path for advisor tools and direct, durable workbench actions."""

import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter
from starlette.concurrency import run_in_threadpool

from shopping_agent import Product, SearchFilters

from . import advisor_stages, quote_shares, trip_brief
from .changes import Conflict
from .integrations import fingerprint
from .persistence import transaction


class Action(trip_brief.Model):
    action: Literal[
        "search_routes",
        "departures",
        "offers",
        "quote",
        "share_quote",
        "customer_confirmed",
        "offline_hold_recorded",
    ]
    expected_version: int = Field(ge=0)
    request_id: UUID = Field(default_factory=uuid4)
    product_id: str | None = None
    offer_id: UUID | None = None
    after: str | None = Field(default=None, max_length=1000)
    query: str | None = Field(default=None, max_length=200)
    filters: dict[str, str] = Field(default_factory=dict, max_length=10)
    limit: int = Field(default=12, ge=1, le=25)
    note: str = Field(default="", max_length=2000)


def search_parameters(brief, query=None, overrides=None):
    attrs = {"ranked": "true"}
    if brief.window.value:
        attrs.update(
            depart_from=str(brief.window.value.start), depart_to=str(brief.window.value.end)
        )
    if brief.days.value:
        attrs.update(days_min=str(brief.days.value.min), days_max=str(brief.days.value.max))
    if brief.depart_city.value:
        attrs["depart_city"] = brief.depart_city.value
    if any(p.key == "no_shopping" for p in brief.preferences.value or []):
        attrs["no_shopping"] = "true"
    if brief.destination_examples.value:
        attrs["destination_examples"] = json.dumps(
            brief.destination_examples.value, ensure_ascii=False
        )
    if brief.excluded_destinations.value:
        attrs["excluded_destinations"] = json.dumps(
            brief.excluded_destinations.value, ensure_ascii=False
        )
    attrs.update(overrides or {})
    return query if query is not None else " ".join(brief.destinations.value or []), SearchFilters(
        attributes=attrs
    )


def route_summary(page, brief):
    """Use only saved requirements and observed page facts for the search summary."""
    if not page["items"]:
        return "当前条件下暂未找到候选线路。是否调整出发时间或目的地范围后再查？"
    count = len(page["items"])
    more = "，还有更多可查看" if page.get("next_cursor") else ""
    date_note = "年份为推断，待确认；" if brief.window.source == trip_brief.Source.inferred else ""
    exclusion = "排除要求也需按行程核实。" if brief.excluded_destinations.value else ""
    return f"本页列出 {count} 条候选线路{more}。{date_note}具体行程覆盖与能否报名仍需核实。{exclusion}请先选一条查看团期。"


def snapshot(backend, session):
    return trip_brief.get(backend.engine, backend.actor, UUID(session.session_id))


def _record(backend, session, version, updates):
    identifier = UUID(session.session_id)
    with transaction(backend.engine, backend.actor) as conn:
        brief, current = trip_brief.load(conn, identifier, lock=True)
        if current != version:
            raise Conflict("需求单已变化，请重新读取后操作")
        for key, value in updates.items():
            setattr(brief, key, value)
        return trip_brief.save(conn, backend.actor, identifier, brief, current + 1)


async def perform(backend, session, state, command: Action):
    saved = await run_in_threadpool(snapshot, backend, session)
    brief = trip_brief.TripBrief.model_validate(saved["body"])
    if saved["version"] != command.expected_version:
        raise Conflict("需求单已变化，请刷新后再操作")
    if command.action == "quote":
        party = trip_brief.to_party(brief)  # Missing fields are returned before stage refusal.
    if command.action not in advisor_stages.derive(brief)["allowed_actions"]:
        raise ValueError("当前阶段不允许该操作，请先补充需求或完成前一步")
    product = state.seen_products.get(command.product_id) if command.product_id else None
    if command.action in ("departures", "offers", "quote") and product is None:
        raise ValueError("请先读取当前可见线路、团期或方案")
    updates = {}
    if command.action == "search_routes":
        query, filters = search_parameters(brief, command.query, command.filters)
        page = await run_in_threadpool(
            backend.catalog_page,
            session,
            query=query,
            filters=filters,
            limit=command.limit,
            after=command.after,
        )
        state.remember_products(page["items"])
        result = {
            **page,
            "query": query,
            "filters": filters.attributes,
            "after": command.after,
            "page_scope": fingerprint({"query": query, "filters": filters.attributes}),
            "summary": route_summary(page, brief),
            "items": [p.model_dump(mode="json") for p in page["items"]],
        }
        component, label = "warehouse_routes", "检索线路"
    elif command.action == "departures":
        window = brief.window.value
        page = await run_in_threadpool(
            backend.departures_page,
            session,
            command.product_id,
            start=window.start,
            end=window.end,
            party_total=brief.party_total.value,
            include_out_of_window=True,
            after=command.after,
            limit=command.limit,
        )
        state.remember_products(page["items"])
        result = {
            **page,
            "product_id": command.product_id,
            "after": command.after,
            "page_scope": fingerprint(
                {
                    "route": command.product_id,
                    "window": window.model_dump(mode="json"),
                    "party_total": brief.party_total.value,
                }
            ),
            "items": [p.model_dump(mode="json") for p in page["items"]],
        }
        updates["route_id"] = command.product_id
        updates["route_title"] = product.title
        if brief.route_id != command.product_id:
            updates.update(
                departure_id=None,
                offer_id=None,
                quote_id=None,
                quote=None,
                share_token=None,
                offline_status="none",
                offline_hold_note="",
            )
        component, label = "warehouse_departures", "看团期：" + product.title
    elif command.action == "offers":
        if product.variant_of != brief.route_id:
            raise ValueError("请先选择该团期所属线路")
        page = await run_in_threadpool(
            backend.offers_page,
            session,
            command.product_id,
            after=UUID(command.after) if command.after else None,
            limit=command.limit,
        )
        for row in page["items"]:
            state.seen_products["WO-" + str(row["id"])] = Product(
                product_id="WO-" + str(row["id"]),
                title=row["name"],
                price=None,
                attributes={"departure_id": command.product_id},
            )
        result = {
            "departure_id": command.product_id,
            "next_cursor": page["next_cursor"],
            "items": [
                {
                    "offer_id": str(row["id"]),
                    "name": row["name"],
                    "service_description": row["service_description"],
                    "shared_inventory": True,
                }
                for row in page["items"]
            ],
        }
        updates["departure_id"] = command.product_id
        updates["departure_title"] = product.title
        if brief.departure_id != command.product_id:
            updates.update(
                offer_id=None,
                quote_id=None,
                quote=None,
                share_token=None,
                offline_status="none",
                offline_hold_note="",
            )
        component, label = "warehouse_offers", "查看方案：" + product.title
    elif command.action == "quote":
        if product.variant_of != brief.route_id:
            raise ValueError("请先选择该团期所属线路")
        if command.offer_id:
            offer = state.seen_products.get("WO-" + str(command.offer_id))
            if not offer or offer.attributes.get("departure_id") != command.product_id:
                raise ValueError("请先读取该团期的方案后再选择")
        result = await backend.quote_departure(
            session,
            command.product_id,
            party,
            offer_id=command.offer_id,
            idempotency_key="action:" + str(command.request_id),
        )
        result.update(
            departure_id=command.product_id,
            rooms=brief.rooms.value.model_dump(mode="json"),
            inferred=trip_brief.readiness(brief)["quote"]["inferred"],
            quote_brief_version=saved["version"],
        )
        # A single offer may be selected by the quote service without an offers
        # list. Retain the validated selection for a later explicit re-quote.
        state.seen_products["WO-" + str(result["offer_id"])] = Product(
            product_id="WO-" + str(result["offer_id"]),
            title=result.get("offer_name") or "报价方案",
            price=None,
            attributes={"departure_id": command.product_id},
        )
        updates.update(
            departure_id=command.product_id,
            departure_title=product.title,
            offer_id=UUID(result["offer_id"]),
            quote_id=UUID(result["quote_id"]),
            quote_brief_version=saved["version"],
            quote=result,
            share_token=None,
            offline_status="none",
            offline_hold_note="",
        )
        component, label = "warehouse_quote", "询价：" + product.title
    elif command.action == "share_quote":
        result = await run_in_threadpool(
            quote_shares.create,
            backend.engine,
            backend.actor,
            brief.quote_id,
            quote_shares.Create(request_id=command.request_id),
        )
        # This slot is an owner-scoped share receipt, not the reusable bearer token.
        # The capability is only returned to the requesting browser, never persisted in turns.
        updates["share_token"] = str(result["id"])
        component, label = "warehouse_share", "生成对客报价链接"
    else:
        updates.update(
            offline_status=command.action, offline_hold_note=command.note or brief.offline_hold_note
        )
        result = {
            "status": command.action,
            "note": updates["offline_hold_note"],
            "reservation_created": False,
        }
        component, label = (
            "warehouse_offline",
            "客人已确认" if command.action == "customer_confirmed" else "记录线下占位备注",
        )
    current = (
        await run_in_threadpool(_record, backend, session, saved["version"], updates)
        if updates
        else saved
    )
    return {
        "component": component,
        "payload": TypeAdapter(dict).dump_python(
            {**result, "brief_version": current["version"]}, mode="json"
        ),
        "brief": current,
        "label": "▸ " + label,
    }
