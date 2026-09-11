# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ``RouteDoc`` files the runtime reads: the reviewed documents first, the first review
round's draft pick behind them.

``scripts/parse_attachments.py`` writes the parsed documents under the state directory's
``route-docs/``; the agency's product staff move the ones they have checked into
``route-docs/published/`` with ``quality.reviewed_by`` set, and the first round's pick is in
``route-docs/selected/``. This store reads both at boot, a published document winning over a
selected one for the same 线路, and says of each whether it is reviewed. The backend prefers a
document to the attachment parse it would otherwise make (``_read_itinerary``), and reads
what the tags only guess at — 购物店, the hotel standard, the meals — off it.

A document is trusted as far as its state: a reviewed one is the agency's word, a draft is the
parser's, and the card says which."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from .erp_client import Itinerary, ItineraryDay
from .route_doc import Day, RouteDoc

log = logging.getLogger(__name__)

PUBLISHED = "published"
SELECTED = "selected"
DOC_SOURCE_REF = "route-doc"


class RouteDocStore:
    """The documents by 线路 id. ``load`` reads the two directories; ``get`` answers one."""

    def __init__(self) -> None:
        self._docs: dict[int, RouteDoc] = {}

    @classmethod
    def load(cls, root: Path | None) -> RouteDocStore:
        """Every readable document under ``root/published`` and ``root/selected``; a file that
        is not a ``RouteDoc`` is logged and skipped, and a missing directory holds nothing."""
        store = cls()
        if root is None:
            return store
        for folder in (SELECTED, PUBLISHED):
            directory = root / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                if path.name in ("selected.json", "index.json"):
                    continue
                try:
                    doc = RouteDoc.model_validate_json(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, ValidationError) as error:
                    log.warning("tour: route doc %s not read: %s", path.name, type(error).__name__)
                    continue
                store._docs[doc.route_id] = doc
        return store

    def get(self, route_id: int) -> RouteDoc | None:
        return self._docs.get(route_id)

    def __len__(self) -> int:
        return len(self._docs)

    def reviewed(self, route_id: int) -> bool:
        doc = self._docs.get(route_id)
        return bool(doc and doc.quality.reviewed_by)


def is_reviewed(doc: RouteDoc) -> bool:
    return bool(doc.quality.reviewed_by)


def itinerary_of(doc: RouteDoc) -> Itinerary:
    """The document's days as the ``Itinerary`` the details record and the 定制方案 read:
    the title, the programme whole, the hotel names and the three meals as one line."""
    days = tuple(
        ItineraryDay(
            day_no=day.day,
            title=day.title,
            text=day.text,
            hotel=_hotel_text(day),
            meals=_meals_text(day),
        )
        for day in doc.days
    )
    return Itinerary(doc.route_id, "attachment", DOC_SOURCE_REF, days)


def _hotel_text(day: Day) -> str | None:
    if day.hotel is not None:
        return day.hotel.name + ("或同级" if day.hotel.or_similar else "")
    return {"flight": "飞机上", "home": None}.get(day.overnight)


def _meals_text(day: Day) -> str | None:
    parts = []
    for label, meal in (
        ("早", day.meals.breakfast),
        ("中", day.meals.lunch),
        ("晚", day.meals.dinner),
    ):
        if meal.included is None:
            continue
        parts.append(f"{label}：{meal.text or ('含' if meal.included else '不含')}")
    return " ".join(parts) or None


def shopping_stops(doc: RouteDoc) -> int:
    """How many 购物店 the document names, from the terms and from the days."""
    return len(doc.shopping)


def meals_included(doc: RouteDoc) -> tuple[int, int]:
    """Meals the price includes over meals the document states."""
    included = stated = 0
    for day in doc.days:
        for meal in (day.meals.breakfast, day.meals.lunch, day.meals.dinner):
            if meal.included is None:
                continue
            stated += 1
            included += int(meal.included)
    return included, stated


def hotel_grade(doc: RouteDoc) -> str:
    """The standard the document states: the cover's 酒店标准 first, else the grade any night
    carries; ``""`` where neither says."""
    for text in (doc.cover.hotel_standard, *(d.hotel.grade for d in doc.days if d.hotel)):
        for digit, grade in (
            ("5", "五钻"),
            ("五", "五钻"),
            ("4", "四钻"),
            ("四", "四钻"),
            ("3", "三钻"),
            ("三", "三钻"),
        ):
            if f"{digit}钻" in text or f"{digit}星" in text:
                return grade
    return ""


def flights_text(doc: RouteDoc) -> str:
    return "；".join(
        f"{f.flight_no} {f.from_place}-{f.to_place} {f.times}".strip() for f in doc.transport
    )
