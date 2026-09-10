# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_itinerary``: one version of the 定制方案 the advisor builds on a published 线路.
The model writes the days and names the baseline; everything else on the card is the server's
— the plan's own id, the version number, what this version did to the one before it, the
reference price read off the 团期's own record, the 计调's copy and the customer's link. The
plan itself lives in ``api/store.py`` and is reached only through ``TourBackend``, so the ids
behind a link never pass through the model.

``api/plans.py`` is what a version is made of and how one reads against the one before it;
this module is the tool over it: the gates the call must pass, the card the workbench draws,
and ``card_payload``, which the plan routes in ``api/main.py`` draw a stored version with.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from commerce_common.presentation import (
    EnrichmentContext,
    PresentationExtension,
    PresentationRefused,
)
from shopping_agent import Product, ShoppingSessionState

from .plans import (
    DayDiff,
    Plan,
    PlanDay,
    PlanVersion,
    ReferencePrice,
    handoff_text,
    mark_requests,
    summarize,
)
from .tour_backend import CURRENCY, departure_id_of, route_id_of

# What one plan may hold, which is what ``PlanVersion`` itself allows: a 线路 the ERP sells
# runs at most twenty days, and a plan is a change to one of those and not a new catalog.
MAX_DAYS = 20
MAX_TITLE = 80
MAX_DAY_LABEL = 80
MAX_DAY_NOTE = 300
MAX_TRAVEL_DATES = 60
MAX_PARTY = 20

# The order the flow runs in, stated to the model the way the route-first gate states its own:
# a plan is a change to a 线路 the advisor has in front of them, so the 线路 comes first.
NOT_PRESENTED = (
    "present_itinerary builds on a route the advisor has been shown: show it with "
    "present_products (or open it by id) first, then build the plan on its RT- id."
)
# The three things a call may name that this session cannot resolve to a plan of its own.
NO_SUCH_PLAN = (
    "There is no such plan in this deployment. Call present_itinerary without plan_id to "
    "start one, or check the PL- id."
)
PLAN_ROUTE_MISMATCH = (
    "That plan was built on {route_id}. A plan keeps its baseline route: pass that route_id "
    "to revise it, or leave plan_id out to start a plan on this one."
)
LINK_NEEDS_A_PLAN = (
    "erp_route_id links a plan the agency has already built the line for: pass it with the "
    "plan_id of that plan."
)
LINK_NOT_PRESENTED = (
    "erp_route_id is the line the agency built in the ERP: open it by id with "
    "get_product_details first, so the link is made against a route this session has read."
)
# A reference price is the 团期's own, so a 团期 that is not this route's is dropped rather
# than refused: the plan stands, and the model is told it went out with no reference price.
_DROPPED_DEPARTURE = "参考团期不在本次会话里，或者不是这条线路的团期，方案已按无参考价出："


class ItineraryDay(BaseModel):
    """One day as the model writes it. ``request`` is not the model's to send: ``plans.py``
    derives it from what the note says."""

    label: str = Field(max_length=MAX_DAY_LABEL)
    note: str = Field(max_length=MAX_DAY_NOTE)


class ItineraryPayload(BaseModel):
    """What the model sends: the days, the 线路 they are built on, and which plan they are a
    version of. ``base_version`` is the version the days were written against, the latest
    when it is left out; ``erp_route_id`` links the line the agency built and adds no
    version."""

    title: str = Field(max_length=MAX_TITLE)
    days: list[ItineraryDay] = Field(min_length=1, max_length=MAX_DAYS)
    route_id: str
    plan_id: str | None = None
    base_version: int | None = Field(default=None, ge=1)
    departure_id: str | None = None
    travel_dates: str | None = Field(default=None, max_length=MAX_TRAVEL_DATES)
    party: str | None = Field(default=None, max_length=MAX_PARTY)
    erp_route_id: str | None = None


class CardDay(BaseModel):
    """One day as the card draws it: what it says, whether only the 计调 can answer it, and
    how it stands against the version this one was made from. ``change`` is empty on a plan
    made straight off the baseline, where there is nothing yet to have changed from."""

    label: str
    note: str
    request: bool
    change: Literal["same", "changed", "added"] | None = None
    changed_fields: list[Literal["label", "note"]] = Field(default_factory=list)


class RemovedDay(BaseModel):
    """A day of the previous version this one dropped; ``index`` is where it sat, which is
    where the card draws it struck through."""

    index: int
    label: str
    note: str


class ItineraryCard(BaseModel):
    """What the UI renders. Every figure on it is the baseline 团期's, because the 定制
    difference is the 计调's to quote."""

    plan_id: str
    version: int
    parent_version: int | None = None
    title: str
    travel_dates: str | None = None
    party: str | None = None
    route: Product
    departure: Product | None = None
    days: list[CardDay]
    removed_days: list[RemovedDay]
    summary: str
    reference_price: ReferencePrice | None = None
    erp_route_id: str | None = None
    handoff_text: str
    share_url: str


# The JSON schema the model sees; the payload model above is what the executor enforces.
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": MAX_TITLE},
        "days": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_DAYS,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "maxLength": MAX_DAY_LABEL},
                    "note": {"type": "string", "maxLength": MAX_DAY_NOTE},
                },
                "required": ["label", "note"],
                "additionalProperties": False,
            },
        },
        "route_id": {"type": "string"},
        "plan_id": {"type": "string"},
        "base_version": {"type": "integer", "minimum": 1},
        "departure_id": {"type": "string"},
        "travel_dates": {"type": "string", "maxLength": MAX_TRAVEL_DATES},
        "party": {"type": "string", "maxLength": MAX_PARTY},
        "erp_route_id": {"type": "string"},
    },
    "required": ["title", "days", "route_id"],
    "additionalProperties": False,
}


def _plain(product: Product) -> Product:
    """A record read back from provenance may be the details one, carrying the 行程 text and
    every sibling 团期; the card wants the record alone."""
    return Product.model_validate(product.model_dump())


def _price(raw: str | None) -> float | None:
    """A price attribute as a number. 0 is 未发布 in this ERP and not free, so it reads as no
    figure at all, and so does an attribute the record does not carry."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value or None


def reference_price(departure: Product | None) -> ReferencePrice | None:
    """What the baseline 团期 was quoted at, off the record the advisor was shown. A plan
    opened on no 团期 has no reference price at all."""
    if departure is None:
        return None
    attributes = departure.attributes
    return ReferencePrice(
        tong_ye_adult=_price(attributes.get("adult_price")),
        market_adult=_price(attributes.get("market_adult_price")),
        quote_source=attributes.get("quote_source", ""),
        party_total=_price(attributes.get("party_quote_total")),
    )


def card_payload(
    plan: Plan,
    version: PlanVersion,
    diff: list[DayDiff],
    route: Product,
    departure: Product | None,
    backend: Any,
) -> dict[str, Any]:
    """One stored version as the card draws it. The version is what it was when it was sent —
    its days, its reference price and its own link — and ``diff`` is the reading it was stored
    with, so a card drawn again months later says what that version changed and not what the
    plan has become since."""
    marks = {day.index: day for day in diff if day.kind != "removed"}
    days = [
        CardDay(
            label=day.label,
            note=day.note,
            request=day.request,
            # A baseline version changed nothing, so its days carry no mark at all.
            change=None if version.parent_version is None else marks[index].kind,
            changed_fields=marks[index].fields if index in marks else [],
        )
        for index, day in enumerate(version.days)
    ]
    card = ItineraryCard(
        plan_id=plan.plan_id,
        version=version.version,
        parent_version=version.parent_version,
        title=version.title,
        travel_dates=version.travel_dates,
        party=version.party,
        route=route,
        departure=departure,
        days=days,
        removed_days=[
            RemovedDay(index=day.index, label=day.label, note=day.note)
            for day in diff
            if day.kind == "removed"
        ],
        summary=summarize(diff, parent_version=version.parent_version),
        reference_price=version.reference_price,
        erp_route_id=None if plan.erp_route_id is None else route_id_of(plan.erp_route_id),
        handoff_text=handoff_text(plan, version, diff),
        share_url=backend.plan_share_url(version.share_token),
    )
    return card.model_dump(exclude_none=True)


def stored_records(plan: Plan, backend: Any) -> tuple[Product, Product | None]:
    """The 线路 and 团期 records a stored version's card is drawn with. The boot snapshot is
    where they come from; a 线路 it does not carry — a 包团 line, a catalog that has moved on
    — is drawn from the name the plan itself stored, because the card names the baseline and
    reads nothing else off it."""
    route_id = route_id_of(plan.route_id)
    route = backend.product(route_id) or Product(
        product_id=route_id, title=plan.route_name, price=0.0, currency=CURRENCY
    )
    departure = (
        None if plan.departure_id is None else backend.product(departure_id_of(plan.departure_id))
    )
    return _plain(route), None if departure is None else _plain(departure)


def _baseline(payload: ItineraryPayload, context: EnrichmentContext) -> Product:
    """The 线路 the plan is built on. It has to be one the advisor has in front of them: a
    card of its own, or a 包团 or 定制 line the advisor opened by id, which was never a search
    result and so never a card."""
    seen = context.state.seen_products.get(payload.route_id)
    if seen is None:
        raise PresentationRefused(NOT_PRESENTED)
    shown = payload.route_id in context.backend.presented(context.session.session_id)
    if not shown and not seen.attributes.get("line_type"):
        raise PresentationRefused(NOT_PRESENTED)
    return _plain(seen)


def _departure(payload: ItineraryPayload, context: EnrichmentContext) -> Product | None:
    """The 团期 the reference price is read off. One this session has not seen, or one of
    another 线路, is dropped and named to the model: the plan is the advisor's work and does
    not fall over a price that was only ever a reference."""
    if not payload.departure_id:
        return None
    seen = context.state.seen_products.get(payload.departure_id)
    if seen is None or seen.variant_of != payload.route_id:
        context.notes.append(f"{_DROPPED_DEPARTURE}{payload.departure_id}。")
        return None
    return _plain(seen)


async def _linked(
    payload: ItineraryPayload, route: Product, departure: Product | None, context: EnrichmentContext
) -> dict[str, Any]:
    """The card again with the ERP's own 线路 on it, once the agency has built the custom line.
    It is the plan's and not a version's, so nothing is added: the latest version is drawn
    again, and the days in this call are not read."""
    if not payload.plan_id:
        raise PresentationRefused(LINK_NEEDS_A_PLAN)
    if payload.erp_route_id not in context.state.seen_products:
        raise PresentationRefused(LINK_NOT_PRESENTED)
    plan = await context.backend.link_plan_route(
        context.session, payload.plan_id, payload.erp_route_id
    )
    versions = context.backend.plan_versions(plan.plan_id)
    if not versions:
        raise PresentationRefused(NO_SUCH_PLAN)
    version, diff = versions[-1]
    return card_payload(plan, version, diff, route, departure, context.backend)


async def _enrich(payload: ItineraryPayload, context: EnrichmentContext) -> dict[str, Any]:
    route = _baseline(payload, context)
    departure = _departure(payload, context)
    if payload.erp_route_id:
        return await _linked(payload, route, departure, context)
    days = mark_requests([PlanDay(label=day.label, note=day.note) for day in payload.days])
    if payload.plan_id:
        plan = context.backend.plan_of(payload.plan_id)
        if plan is None:
            raise PresentationRefused(NO_SUCH_PLAN)
        if route_id_of(plan.route_id) != payload.route_id:
            raise PresentationRefused(
                PLAN_ROUTE_MISMATCH.format(route_id=route_id_of(plan.route_id))
            )
    else:
        plan = await context.backend.create_plan(context.session, route, departure)
    version, diff = await context.backend.add_plan_version(
        context.session,
        plan,
        days,
        title=payload.title,
        travel_dates=payload.travel_dates,
        party=payload.party,
        reference_price=reference_price(departure),
        base_version=payload.base_version,
    )
    return card_payload(plan, version, diff, route, departure, context.backend)


def _enrich_partial(data: dict[str, Any], state: ShoppingSessionState) -> dict[str, Any] | None:
    """The streamed prefix of a still-generating call: the head of the plan and the days
    written so far. Nothing here reaches the plan store — a version is numbered once, on the
    finished call, and that is where the link is minted too."""
    del state
    days = [
        {"label": day["label"], **({"note": day["note"]} if day.get("note") else {})}
        for day in data.get("days") or []
        if isinstance(day, dict) and day.get("label")
    ]
    if not days:
        return None
    payload: dict[str, Any] = {"title": str(data.get("title") or ""), "days": days}
    for key in ("travel_dates", "party"):
        if isinstance(value := data.get(key), str) and value:
            payload[key] = value
    return payload


def build_itinerary_extension() -> PresentationExtension:
    return PresentationExtension(
        name="present_itinerary",
        component="itinerary",
        description=(
            "Show the day-by-day itinerary of a custom plan (定制方案) for this customer, "
            "built on one 线路 the advisor has been shown. Pass route_id (RT-…) and one entry "
            "per day — label '第 N 天 · 起点—终点', note the day's programme in one or two "
            "sentences of your own drawn from the route's 第N天 specs, because every later "
            "version sends every day again; write 待计调确认 into a note for anything the "
            "customer wants that the route does not carry; never write a price. The first "
            "call creates the plan and is v1; to change it, call again with plan_id and the "
            "full list of days with only the asked days changed — the server numbers the "
            "version, marks what changed against the previous one, and mints the customer's "
            "link. Pass departure_id (DP-…) when the advisor opened a 团期, for the reference "
            "price; pass erp_route_id once the agency has built the custom line in the ERP to "
            "link it (no new version)."
        ),
        input_schema=_INPUT_SCHEMA,
        payload_model=ItineraryPayload,
        enrich=_enrich,
        enrich_partial=_enrich_partial,
    )
