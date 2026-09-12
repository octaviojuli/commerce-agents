# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_route_days``: one 线路's 逐日行程, whole.

The advisor has a line in front of them and the customer asks what they actually do on day
six. The answer is the agency's own 线路文档 (``route_docs.py``) — the day's title, the places
it passes through, the sights and whether their tickets are included, the three meals, the
night's hotel, the flights — rendered as the card the advisor reads off, with the flights out
and back, what the price includes and what it does not, the 购物店, the 自费项目 and the terms
beside it.

Nothing here is the model's: the model names a 线路 it has already shown, and the day-by-day
is joined from the document on the server. A line the agency has no document for has no card,
because the tags and the name are not an itinerary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from commerce_common.presentation import (
    EnrichmentContext,
    PresentationExtension,
    PresentationRefused,
)

from .route_doc import Day, Flight, RouteDoc

UNSEEN = (
    "{route_id} is not a 线路 this session has shown. Search first and present the 线路 cards; "
    "call present_route_days for a line the advisor has seen and asked the 行程 of."
)
NO_DOCUMENT = (
    "{route_id} has no 线路文档 in the catalog, so there is no 逐日行程 to show. Tell the "
    "advisor the line's 行程 has to come from the 行程附件 and offer it with "
    "present_attachments."
)


class FlightOut(BaseModel):
    day: int
    flight_no: str
    carrier: str
    from_place: str
    to_place: str
    times: str


class HotelOut(BaseModel):
    name: str
    grade: str
    or_similar: bool


class MealOut(BaseModel):
    text: str
    included: bool | None = None


class MealsOut(BaseModel):
    breakfast: MealOut
    lunch: MealOut
    dinner: MealOut


class SightOut(BaseModel):
    name: str
    kind: str
    duration: str
    ticket_included: bool | None = None


class DayOut(BaseModel):
    """One day as the advisor reads it out. ``text`` is the programme as the attachment
    wrote it; everything beside it is that paragraph read into fields. ``transport`` is the
    day's 车程 / 交通 note, ``""`` where the attachment writes none."""

    day: int
    title: str
    places: list[str]
    transport: str
    overnight: str
    hotel: HotelOut | None = None
    meals: MealsOut
    flights: list[FlightOut]
    sights: list[SightOut]
    text: str


class ShoppingOut(BaseModel):
    name: str
    day: int | None = None
    duration: str = ""


class OptionalOut(BaseModel):
    name: str
    price: str = ""
    day: int | None = None


class PoliciesOut(BaseModel):
    single_room: str = ""
    child: str = ""
    visa: str = ""
    cancellation: str = ""
    deposit: str = ""


class RouteDaysCard(BaseModel):
    """What the UI renders: the line's own heading, the flights out and back, every day, and
    the terms under them. ``day_count`` is the length the document states and ``days`` are
    the days it carries — an attachment that lists fewer days than it claims is a document
    the agency's product staff have yet to finish, and the card shows both."""

    route_id: str
    title: str
    route_code: str
    department: str
    day_count: int
    nights: int | None = None
    depart_city: str
    countries: list[str]
    reviewed: bool
    reviewed_by: str
    # The 线路's own cover photo from the ERP catalog row, where it carries one.
    image_url: str | None = None
    highlights: list[str]
    airline: str
    hotel_standard: str
    meal_standard: str
    outbound: list[FlightOut]
    inbound: list[FlightOut]
    days: list[DayOut]
    inclusions: list[str]
    exclusions: list[str]
    shopping: list[ShoppingOut]
    optional: list[OptionalOut]
    policies: PoliciesOut
    attachment_name: str


class RouteDaysPayload(BaseModel):
    """What the model sends: the 线路 whose 行程 the advisor asked for."""

    route_id: str = Field(min_length=1, max_length=40)


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"route_id": {"type": "string", "maxLength": 40}},
    "required": ["route_id"],
    "additionalProperties": False,
}


def _flight(flight: Flight) -> FlightOut:
    return FlightOut(
        day=flight.day,
        flight_no=flight.flight_no,
        carrier=flight.carrier,
        from_place=flight.from_place,
        to_place=flight.to_place,
        times=flight.times,
    )


def _flights(doc: RouteDoc) -> tuple[list[FlightOut], list[FlightOut]]:
    """The flights out and the flights back: the first day of the document that carries any,
    and the last one that does. A trip whose only flights are on one day has no return
    segment of its own, so the inbound list is empty rather than the outbound repeated."""
    flown = [day for day in doc.days if day.flights]
    if not flown:
        return [], []
    outbound = [_flight(flight) for flight in flown[0].flights]
    if flown[-1].day == flown[0].day:
        return outbound, []
    return outbound, [_flight(flight) for flight in flown[-1].flights]


def _day(day: Day) -> DayOut:
    return DayOut(
        day=day.day,
        title=day.title,
        places=list(day.places),
        transport=day.transport,
        overnight=day.overnight,
        hotel=(
            HotelOut(name=day.hotel.name, grade=day.hotel.grade, or_similar=day.hotel.or_similar)
            if day.hotel is not None
            else None
        ),
        meals=MealsOut(
            breakfast=MealOut(text=day.meals.breakfast.text, included=day.meals.breakfast.included),
            lunch=MealOut(text=day.meals.lunch.text, included=day.meals.lunch.included),
            dinner=MealOut(text=day.meals.dinner.text, included=day.meals.dinner.included),
        ),
        flights=[_flight(flight) for flight in day.flights],
        sights=[
            SightOut(
                name=sight.name,
                kind=sight.kind,
                duration=sight.duration,
                ticket_included=sight.ticket_included,
            )
            for sight in day.sights
        ],
        text=day.text,
    )


async def _enrich(payload: RouteDaysPayload, context: EnrichmentContext) -> dict[str, Any]:
    route_id = payload.route_id.strip()
    if route_id not in context.state.seen_products:
        raise PresentationRefused(UNSEEN.format(route_id=route_id))
    facts = context.backend.route_facts(route_id)
    if facts is None:
        raise PresentationRefused(NO_DOCUMENT.format(route_id=route_id))
    doc = facts.doc
    outbound, inbound = _flights(doc)
    card = RouteDaysCard(
        route_id=route_id,
        title=doc.name,
        route_code=doc.route_code,
        department=doc.department,
        day_count=facts.days,
        nights=facts.nights,
        depart_city=facts.depart_city,
        countries=list(facts.countries),
        reviewed=facts.reviewed,
        reviewed_by=facts.reviewed_by,
        image_url=facts.image_url,
        highlights=list(doc.cover.highlights),
        airline=doc.cover.airline,
        hotel_standard=doc.cover.hotel_standard,
        meal_standard=doc.cover.meal_standard,
        outbound=outbound,
        inbound=inbound,
        days=[_day(day) for day in doc.days],
        inclusions=list(doc.inclusions),
        exclusions=list(doc.exclusions),
        shopping=[
            ShoppingOut(name=stop.name, day=stop.day, duration=stop.duration)
            for stop in doc.shopping
        ],
        optional=[
            OptionalOut(name=item.name, price=item.price, day=item.day) for item in doc.optional
        ],
        policies=PoliciesOut(
            single_room=doc.policies.single_room,
            child=doc.policies.child,
            visa=doc.policies.visa,
            cancellation=doc.policies.cancellation,
            deposit=doc.policies.deposit,
        ),
        attachment_name=facts.attachment_name,
    )
    return card.model_dump()


def build_route_days_extension() -> PresentationExtension:
    return PresentationExtension(
        name="present_route_days",
        component="route_days",
        description=(
            "Show one 线路's 逐日行程 (its 详细行程): every day with its places, sights, meals, "
            "hotel and flights, and the 费用包含, 购物店, 自费项目 and terms under them. Use "
            "when the advisor asks what a line actually does day by day; pass the route id "
            "(RT-…) of a line presented earlier in this session. The card carries the whole "
            "itinerary, so do not retype the days in the reply — say what stands out and what "
            "the customer asked about. Not for dates or seats: that is present_departures."
        ),
        input_schema=_INPUT_SCHEMA,
        payload_model=RouteDaysPayload,
        enrich=_enrich,
    )
