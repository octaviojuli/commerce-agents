# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_focus``: the 聚焦卡, one narrowing question over a request the catalog answers
with too many 线路 to shortlist. The model writes the question and names the dimension; the
card's chips are the groups the backend counted on the last search (``TourBackend.overview``),
each one a filter the workbench sends straight back — so the choices the advisor is offered
are the catalog's and never the model's. Up to three of the results may stand on the card as a
foothold when the match is not huge; above twelve the card is the question alone.

The card is the step before a shortlist on a broad request and refused otherwise: with no
overview standing the last search fits a shortlist, and once the advisor has narrowed the
conversation shows cards rather than asking again."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from commerce_common.presentation import (
    EnrichmentContext,
    PresentationExtension,
    PresentationRefused,
)
from shopping_agent import Product

MAX_ANCHORS = 3
# The advisor's foothold: above this many matches, the card carries no results at all.
ANCHORS_UP_TO = 12
DIMENSIONS = ("线路系", "出发城市", "天数", "起价")

NOT_BROAD = (
    "present_focus is for a search that matched more 线路 than it could show. The last search "
    "fits a shortlist: present its results with present_products instead."
)
ALREADY_NARROWED = (
    "The advisor has already narrowed once; do not ask again. Present what the last search "
    "returned with present_products, and offer any further narrowing as chips beside it."
)
_ANCHORS_DROPPED = "匹配超过 12 条时聚焦卡不带线路，已去掉："
_UNSEEN_DROPPED = "以下编号不在本次会话的结果里，已从聚焦卡上去掉："


class FocusPayload(BaseModel):
    """What the model sends: the question, the dimension it asks about, and the results it
    wants to stand beside the question."""

    question: str = Field(min_length=1, max_length=80)
    dimension: Literal["线路系", "出发城市", "天数", "起价"] | None = None
    picks: list[str] = Field(default_factory=list, max_length=MAX_ANCHORS)


class FocusValue(BaseModel):
    """One chip: the group's value, how many 线路 it holds, and the words the workbench sends
    when it is chosen; a group with no filter behind it has no ``ask``."""

    value: str
    count: int
    ask: str | None = None


class FocusGroup(BaseModel):
    label: str
    filter: str
    values: list[FocusValue]


class FocusCard(BaseModel):
    """What the UI renders. ``dimension`` names the group whose values are the chips; the
    rest are shown as counts."""

    question: str
    total: int
    shown: int
    dimension: str
    groups: list[FocusGroup]
    anchors: list[Product]


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 80},
        "dimension": {"type": "string", "enum": list(DIMENSIONS)},
        "picks": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ANCHORS},
    },
    "required": ["question"],
    "additionalProperties": False,
}


def _groups(overview: Any) -> list[FocusGroup]:
    groups: list[FocusGroup] = []
    for label, counts in overview.groups.items():
        key = overview.FILTERS.get(label, "")
        values = [
            FocusValue(value=value, count=count, ask=f"只看{label}：{value}" if key else None)
            for value, count in counts
        ]
        if values:
            groups.append(FocusGroup(label=label, filter=key, values=values))
    return groups


def _dimension(chosen: str | None, groups: list[FocusGroup]) -> str:
    """The model's dimension when its group has more than one value, else the first group in
    ``DIMENSIONS`` order that splits the set at all."""
    by_label = {group.label: group for group in groups}
    if chosen and len(by_label.get(chosen, FocusGroup(label="", filter="", values=[])).values) > 1:
        return chosen
    for label in DIMENSIONS:
        if len(by_label.get(label, FocusGroup(label="", filter="", values=[])).values) > 1:
            return label
    return chosen or DIMENSIONS[0]


def _plain(product: Product) -> Product:
    return Product.model_validate(product.model_dump())


async def _enrich(payload: FocusPayload, context: EnrichmentContext) -> dict[str, Any]:
    read = getattr(context.backend, "overview", None)
    overview = read(context.session.session_id) if read is not None else None
    if overview is None:
        raise PresentationRefused(NOT_BROAD)
    if overview.answered:
        raise PresentationRefused(ALREADY_NARROWED)
    anchors: list[Product] = []
    unseen: list[str] = []
    for product_id in payload.picks:
        product = context.state.seen_products.get(product_id)
        if product is None:
            unseen.append(product_id)
        else:
            anchors.append(_plain(product))
    if unseen:
        context.notes.append(f"{_UNSEEN_DROPPED}{'、'.join(unseen)}。")
    if anchors and overview.total > ANCHORS_UP_TO:
        context.notes.append(f"{_ANCHORS_DROPPED}{'、'.join(p.product_id for p in anchors)}。")
        anchors = []
    groups = _groups(overview)
    card = FocusCard(
        question=payload.question,
        total=overview.total,
        shown=overview.shown,
        dimension=_dimension(payload.dimension, groups),
        groups=groups,
        anchors=anchors,
    )
    # The card is the question the conversation asked: the next search is the answer, and
    # the results it shows are presented — so are the footholds this card carries.
    context.backend.note_focused(context.session.session_id)
    if anchors:
        context.backend.note_presented(
            context.session.session_id, [product.product_id for product in anchors]
        )
    return card.model_dump(exclude_none=True)


def build_focus_extension() -> PresentationExtension:
    return PresentationExtension(
        name="present_focus",
        component="focus",
        description=(
            "Ask the advisor one narrowing question over a search that matched more 线路 than "
            "a shortlist can show (the search result carries a 目录概览 block). Write the "
            "question and name the dimension it asks about (线路系, 出发城市, 天数, 起价); the "
            "card fills in the groups and their counts as chips the advisor taps, which come "
            "back as 只看<维度>：<值>. Between 7 and 12 matches, up to three result ids may "
            "stand on the card as picks; above 12, none. Not for a search that fits, and not "
            "twice in a row."
        ),
        input_schema=_INPUT_SCHEMA,
        payload_model=FocusPayload,
        enrich=_enrich,
    )
