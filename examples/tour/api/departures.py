# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_departures``: the 团期卡, which dates one 线路 runs and what the advisor may do
with each.

The 线路 is the document's (``api/catalog.py``); its dates are not. Which 团期 exist, whether
they have made up their numbers, how many seats are left and what this 同行 customer pays are
the ERP's and are read live, so this card is the one place the two halves meet: the line as
the agency wrote it, the dates as the ERP sells them today.

Every 团期 the card shows enters the session's provenance as it is rendered, so the advisor
can go straight from a date on the card to a 占位 order on it."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from commerce_common.presentation import (
    EnrichmentContext,
    PresentationExtension,
    PresentationRefused,
)
from shopping_agent import Product

UNSEEN = (
    "{route_id} is not a 线路 this session has shown. Present the 线路 cards first and let "
    "the advisor name one; a line's 团期 open after that."
)
NO_ROUTE = (
    "{route_id} is not a 线路 this catalog carries, so it has no 团期. Search again and "
    "present what the catalog does hold."
)
NO_DEPARTURES = (
    "{title} 在 {window} 没有团期。Say so and offer the nearest window instead: call "
    "present_departures again with a wider depart_from/depart_to."
)
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class DeparturesPayload(BaseModel):
    """What the model sends: the 线路, and the window the advisor asked about where they gave
    one. With no window the card covers the dates the conversation is working in."""

    route_id: str = Field(min_length=1, max_length=40)
    depart_from: str | None = None
    depart_to: str | None = None


class DepartureRow(BaseModel):
    """One date on the card. ``price_adult`` is the 同业价 per adult, or null while the ERP
    has quoted none — a 0 in this ERP is 未发布 and not free."""

    departure: Product
    date: str
    weekday: str
    status: str
    price_adult: float | None = None


class DeparturesCard(BaseModel):
    route: Product
    window: dict[str, str]
    items: list[DepartureRow]


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "route_id": {"type": "string", "maxLength": 40},
        "depart_from": {"type": "string", "description": "ISO date, e.g. 2026-10-01"},
        "depart_to": {"type": "string", "description": "ISO date"},
    },
    "required": ["route_id"],
    "additionalProperties": False,
}


def _date_of(raw: str | None) -> date | None:
    """A date the model wrote; anything that is not an ISO date is no date at all, and the
    card then covers the window the conversation is working in."""
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _plain(product: Product) -> Product:
    return Product.model_validate(product.model_dump())


async def _enrich(payload: DeparturesPayload, context: EnrichmentContext) -> dict[str, Any]:
    route_id = payload.route_id.strip()
    if route_id not in context.state.seen_products:
        raise PresentationRefused(UNSEEN.format(route_id=route_id))
    view = await context.backend.departures_view(
        context.session, route_id, _date_of(payload.depart_from), _date_of(payload.depart_to)
    )
    if view is None:
        raise PresentationRefused(NO_ROUTE.format(route_id=route_id))
    window = {"from": view.window[0].isoformat(), "to": view.window[1].isoformat()}
    if not view.items:
        raise PresentationRefused(
            NO_DEPARTURES.format(title=view.route.title, window=f"{window['from']}–{window['to']}")
        )
    card = DeparturesCard(
        route=_plain(view.route),
        window=window,
        items=[
            DepartureRow(
                departure=_plain(item.departure),
                date=item.date.isoformat(),
                weekday=_WEEKDAYS[item.date.weekday()],
                status=item.status,
                price_adult=item.price_adult,
            )
            for item in view.items
        ],
    )
    # The dates on the card are records the advisor can act on: they enter provenance so a
    # 占位 order may be written on one, and count as presented so the 分享清单 may carry them.
    shown = [item.departure for item in view.items]
    context.state.remember_products([view.route, *shown])
    context.backend.note_presented(
        context.session.session_id, [product.product_id for product in (view.route, *shown)]
    )
    return card.model_dump()


def build_departures_extension() -> PresentationExtension:
    return PresentationExtension(
        name="present_departures",
        component="departures",
        description=(
            "Show which dates one 线路 runs: every 团期 in the window with its 成团 state, its "
            "seats and the 同业价 this customer is quoted. Use when the advisor asks a line's "
            "团期 — which dates it runs, whether a date has a group, what is left on it — and "
            "only after that line has been presented; pass its route id (RT-…) and, when the "
            "advisor named dates, depart_from and depart_to as ISO dates. Not before a line "
            "is chosen: 线路 cards come first."
        ),
        input_schema=_INPUT_SCHEMA,
        payload_model=DeparturesPayload,
        enrich=_enrich,
    )
