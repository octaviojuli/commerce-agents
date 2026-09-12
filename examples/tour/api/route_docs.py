# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ``RouteDoc`` files the runtime reads: every document the parser wrote, the reviewed ones
ahead of them.

``scripts/parse_attachments.py`` writes one parsed document per 线路 under the state
directory's ``route-docs/``; the agency's product staff move the ones they have checked into
``route-docs/published/`` with ``quality.reviewed_by`` set, and the round they are checking is
in ``route-docs/selected/``. This store reads all three at boot — a published document winning
over a selected one and a selected one over the draft beside them — and says of each whether it
is reviewed. The drafts are in because a line the agency has not reviewed yet still sells: the
线路 selling this month are mostly drafts, and a search that reads only the reviewed documents
answers with the lines that do not.

Two lines whose parsed 逐日行程 is identical word for word are one product sold under two
names — a second departure city, an 加班 line, a second airline — and the draft of such a line
inherits the reviewed document of the line it copies, under its own identity and with
``twin_of`` naming it.

The backend prefers a document to the attachment parse it would otherwise make
(``_read_itinerary``), and reads what the tags only guess at — 购物店, the hotel standard, the
meals — off it. A document is trusted as far as its state: a reviewed one is the agency's word,
a draft is the parser's, and the card says which."""

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
    """The documents by 线路 id. ``load`` reads the three directories and maps the twins;
    ``get`` answers one, ``docs`` the catalog."""

    def __init__(self) -> None:
        self._docs: dict[int, RouteDoc] = {}

    @classmethod
    def load(cls, root: Path | None) -> RouteDocStore:
        """Every readable document under ``root`` itself, ``root/selected`` and
        ``root/published``, a published one winning over a selected one and a selected one over
        the draft beside them, with the twins mapped onto the reviewed document they copy. A
        file that is not a ``RouteDoc`` is logged and skipped, and a missing directory holds
        nothing."""
        store = cls()
        if root is None:
            return store
        drafts = _read_dir(root)
        selected = _read_dir(root / SELECTED)
        published = _read_dir(root / PUBLISHED)
        store._docs = {**drafts, **selected, **published}
        twins = _twins(store._docs, drafts, selected)
        store._docs.update(twins)
        if twins:
            log.info("tour: %d 线路 read the reviewed document of the line they copy", len(twins))
        return store

    def get(self, route_id: int) -> RouteDoc | None:
        return self._docs.get(route_id)

    def docs(self) -> list[RouteDoc]:
        """Every document the store holds, by 线路 id: the catalog the search runs over."""
        return [self._docs[route_id] for route_id in sorted(self._docs)]

    def twins(self) -> dict[int, int]:
        """The lines reading another line's reviewed document, each to the 线路 id it copies."""
        return {doc.route_id: doc.twin_of for doc in self.docs() if doc.twin_of is not None}

    def __len__(self) -> int:
        return len(self._docs)

    def reviewed(self, route_id: int) -> bool:
        doc = self._docs.get(route_id)
        return bool(doc and doc.quality.reviewed_by)


def _read_dir(directory: Path) -> dict[int, RouteDoc]:
    """One directory's documents by 线路 id, the index and the selection list skipped."""
    found: dict[int, RouteDoc] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        if path.name in ("selected.json", "index.json"):
            continue
        try:
            doc = RouteDoc.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as error:
            log.warning("tour: route doc %s not read: %s", path.name, type(error).__name__)
            continue
        found[doc.route_id] = doc
    return found


def _itinerary_key(doc: RouteDoc) -> tuple[str, ...]:
    """The 逐日行程 as the parser read it, which is what makes two lines one product: the
    programme of every day, word for word. A document with no day text keys nothing."""
    days = tuple(day.text for day in doc.days)
    return days if any(days) else ()


def _twins(
    docs: dict[int, RouteDoc], drafts: dict[int, RouteDoc], selected: dict[int, RouteDoc]
) -> dict[int, RouteDoc]:
    """The unreviewed lines whose parse is a reviewed line's parse, each as that reviewed
    document under its own identity.

    A reviewed line is keyed by its *draft* — ``selected/{id}.json`` or the draft beside it —
    because that is the same parser's reading as the twin's, where the reviewed document has a
    person's corrections in it. Where several reviewed lines share one 行程 the lowest 线路 id
    is the one inherited; they are the same product too."""
    reviewed: dict[tuple[str, ...], RouteDoc] = {}
    for route_id in sorted(docs):
        doc = docs[route_id]
        if not is_reviewed(doc) or doc.twin_of is not None:
            continue
        key = _itinerary_key(selected.get(route_id) or drafts.get(route_id) or doc)
        if key:
            reviewed.setdefault(key, doc)
    twins: dict[int, RouteDoc] = {}
    for route_id in sorted(drafts):
        if is_reviewed(docs[route_id]):
            continue
        doc = reviewed.get(_itinerary_key(drafts[route_id]))
        if doc is not None:
            twins[route_id] = _as_twin(drafts[route_id], doc)
    return twins


def _as_twin(draft: RouteDoc, reviewed: RouteDoc) -> RouteDoc:
    """The reviewed document read under the twin's own identity: its 线路 id, code, name,
    department and 出发城市, and the attachment its own parse was read from, over the reviewed
    line's 行程 and the review that signed it."""
    return reviewed.model_copy(
        deep=True,
        update={
            "route_id": draft.route_id,
            "route_code": draft.route_code,
            "name": draft.name,
            "department": draft.department,
            "summary": reviewed.summary.model_copy(
                deep=True, update={"depart_city": draft.summary.depart_city}
            ),
            "source": draft.source.model_copy(deep=True),
            "twin_of": reviewed.route_id,
        },
    )


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
