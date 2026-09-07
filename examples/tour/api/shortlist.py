# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_shortlist``: the 团期 shortlist the advisor hands the customer. The model
picks the departures and writes the title; every 团期 is joined from what this session has
seen, with the 线路 it belongs to beside it, and the share link is minted on the backend.
The link's inputs never pass through the model, so the ids the customer's page may act on
are the server's own."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from commerce_common.presentation import (
    EnrichmentContext,
    PresentationExtension,
    PresentationRefused,
)
from shopping_agent import Product, ShoppingSessionState

# What the advisor can read out to a customer in one go; more than this is a search result.
MAX_DEPARTURES = 5

_DROPPED_NOTE = "以下团期不在本次会话的结果里，已从分享清单中去掉："
_NOTHING_LEFT = (
    "分享清单里没有一个团期来自本次会话的结果。先搜索线路、打开团期，再用返回的 DP- 编号发清单。"
)


class ShortlistPayload(BaseModel):
    """What the model sends: the ids and the words around them."""

    title: str = Field(max_length=80)
    departure_ids: list[str] = Field(min_length=1, max_length=MAX_DEPARTURES)
    note: str | None = Field(default=None, max_length=280)


class ShortlistItem(BaseModel):
    """One line of the card: the 团期 and the 线路 it is a departure of."""

    departure: Product
    route: Product


class ShortlistCard(BaseModel):
    """What the UI renders. ``share_url`` is the advisor's link for the customer; a still
    streaming call has no link yet."""

    title: str
    note: str | None = None
    items: list[ShortlistItem]
    share_url: str | None = None


# The JSON schema the model sees; the payload model above is what the executor enforces.
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "departure_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_DEPARTURES,
        },
        "note": {"type": "string", "maxLength": 280},
    },
    "required": ["title", "departure_ids"],
    "additionalProperties": False,
}


def _plain(product: Product) -> Product:
    """A record read back from provenance may be the details one, carrying the 行程 text
    and every sibling 团期; the card wants the record alone."""
    return Product.model_validate(product.model_dump())


def _seen_route(departure: Product, state: ShoppingSessionState) -> Product | None:
    route_id = departure.variant_of
    return state.seen_products.get(route_id) if route_id else None


async def _route_of(departure: Product, context: EnrichmentContext) -> Product | None:
    """The 线路 the 团期 belongs to. A departure id the advisor pasted resolves without its
    route ever being searched, so the route is fetched when provenance has no copy of it."""
    seen = _seen_route(departure, context.state)
    if seen is not None:
        return seen
    if not departure.variant_of:
        return None
    details = await context.backend.get_product_details(context.session, departure.variant_of)
    return _plain(details) if details is not None else None


async def _enrich(payload: ShortlistPayload, context: EnrichmentContext) -> dict[str, Any]:
    items: list[ShortlistItem] = []
    kept: list[str] = []
    dropped: list[str] = []
    for departure_id in payload.departure_ids:
        departure = context.state.seen_products.get(departure_id)
        route = await _route_of(departure, context) if departure is not None else None
        if departure is None or route is None:
            dropped.append(departure_id)
            continue
        items.append(ShortlistItem(departure=_plain(departure), route=route))
        kept.append(departure_id)
    if dropped:
        context.notes.append(f"{_DROPPED_NOTE}{'、'.join(dropped)}。")
    if not items:
        raise PresentationRefused(_NOTHING_LEFT)
    # Server-side: the customer's page acts on the ids this call kept, not on anything the
    # model wrote.
    share_url = await context.backend.create_share_link(
        context.session.session_id, context.session.user_id, kept
    )
    card = ShortlistCard(title=payload.title, note=payload.note, items=items, share_url=share_url)
    return card.model_dump(exclude_none=True)


def _enrich_partial(data: dict[str, Any], state: ShoppingSessionState) -> dict[str, Any] | None:
    """The streamed prefix of a still-generating call, under the same provenance rule and
    without a link: a share link is minted once, on the finished call."""
    items: list[ShortlistItem] = []
    for departure_id in data.get("departure_ids") or []:
        if not isinstance(departure_id, str):
            continue
        departure = state.seen_products.get(departure_id)
        route = _seen_route(departure, state) if departure is not None else None
        if departure is None or route is None:
            continue
        items.append(ShortlistItem(departure=_plain(departure), route=_plain(route)))
    if not items:
        return None
    note = data.get("note")
    card = ShortlistCard(
        title=str(data.get("title") or ""),
        note=note if isinstance(note, str) and note else None,
        items=items,
    )
    return card.model_dump(exclude_none=True)


def build_shortlist_extension() -> PresentationExtension:
    return PresentationExtension(
        name="present_shortlist",
        component="shortlist",
        description=(
            "Show a shortlist of dated departures for the customer to choose between, "
            "each with the route it departs from. Use when the advisor wants a set of "
            "departures to send to the customer; pass departure ids (DP-…) from this "
            "session's results — the UI fills in the titles, the quotes, and the share "
            "link the advisor sends."
        ),
        input_schema=_INPUT_SCHEMA,
        payload_model=ShortlistPayload,
        enrich=_enrich,
        enrich_partial=_enrich_partial,
    )
