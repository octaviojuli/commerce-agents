# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ERP contract behind the tour advisor: route families (线路), their dated
departures (团期), and the timed seat holds (占位) an advisor takes while a customer
decides. ``ErpClient`` is the six calls a real 旅行社 ERP has to answer; the records are
plain dataclasses so a backend can map its own system onto them without importing
anything from this example. Every error message is advisor-facing Chinese, because the
executor relays it into the conversation unchanged."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

DEFAULT_ROUTE_RESULTS = 8
MAX_ROUTE_RESULTS = 20


class ErpError(Exception):
    """A refusal from the ERP; the message is safe to show the advisor as written."""


class ErpSoldOut(ErpError):
    """Fewer seats left than the party needs. ``sibling_departure_ids`` names the nearby
    departures of the same route that can still take the party, nearest date first."""

    def __init__(self, message: str, sibling_departure_ids: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.sibling_departure_ids: list[str] = list(sibling_departure_ids)


class ErpDeadlinePassed(ErpError):
    """The departure's booking deadline has passed, or the group is closed."""


class ErpHoldLimit(ErpError):
    """One session already holds as many departures as the ERP allows."""


class ErpHoldNotFound(ErpError):
    """No such hold: it never existed, it expired, or it belongs to another advisor."""


class ErpUnavailable(ErpError):
    """The ERP could not be reached in time."""


@dataclass(frozen=True)
class RouteQuery:
    """One route search. ``destination`` is the only free-text field; everything else is
    a filter the advisor stated. ``child_ages`` is a tuple so the query stays immutable."""

    destination: str = ""
    depart_from: date | None = None
    depart_to: date | None = None
    days_min: int | None = None
    days_max: int | None = None
    adults: int = 1
    children: int = 0
    child_ages: tuple[int, ...] = ()
    departure_city: str | None = None
    no_shopping: bool = False
    hotel_level: str | None = None
    max_adult_price: float | None = None
    limit: int = DEFAULT_ROUTE_RESULTS


@dataclass(frozen=True)
class RouteRecord:
    """A route family. ``min_adult_price_in_window`` and ``has_seats_in_window`` are
    computed per query from the departures inside the query's date window."""

    route_id: str
    route_name: str
    destination: str
    region: str
    departure_city: str
    days: int
    nights: int
    hotel_level: str
    vehicle: str
    group_size_max: int
    includes_transport: bool
    shopping_stops: int
    optional_paid_items: int
    child_min_age: int
    child_policy: str
    intensity: int
    summary: str
    itinerary_brief: str
    min_adult_price_in_window: float
    has_seats_in_window: bool
    highlights: tuple[str, ...] = ()
    fit_tags: tuple[str, ...] = ()
    supplier_name: str | None = None
    max_drive_hours_per_day: float | None = None
    meals: str | None = None
    meeting_point: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class DepartureRecord:
    """One dated departure of a route. ``party_quote_total`` is computed per request from
    the party the caller asked about, never stored."""

    departure_id: str
    route_id: str
    depart_date: date
    return_date: date
    seats_total: int
    seats_left: int
    group_status: str
    booking_deadline: date
    adult_price: float
    child_price: float
    single_supplement: float
    hold_ttl_minutes: int
    party_quote_total: float
    child_bed_price: float | None = None


@dataclass(frozen=True)
class HoldRecord:
    """A live seat hold. ``total_price`` is the party quote the hold was taken at."""

    hold_id: str
    departure_id: str
    adults: int
    children: int
    expires_at: datetime
    total_price: float


class ErpClient(Protocol):
    """What the tour backend needs from a 旅行社 ERP. A hold belongs to the advisor who
    took it: ``release_hold`` checks the advisor alone. The hold allowance and the
    no-duplicate rule count within one conversation (``session_key``), and ``list_holds``
    filters on both the advisor and the conversation. ``list_departures`` and
    ``get_departure`` take ``child_ages`` because a real ERP prices a party by them: each
    child falls in an age band and is either 占床 or 不占床. The ages travel with every
    quote request for that reason, and ``MockErpClient`` leaves them unread, because it
    prices every child at the departure's flat 儿童价."""

    async def search_routes(self, q: RouteQuery) -> list[RouteRecord]:
        """Routes matching every stated filter, best match first."""
        ...

    async def list_departures(
        self,
        route_id: str,
        depart_from: date,
        depart_to: date,
        adults: int,
        children: int,
        child_ages: list[int],
    ) -> list[DepartureRecord]:
        """One route's departures inside the window, by date, closed groups included."""
        ...

    async def get_departure(
        self, departure_id: str, adults: int, children: int, child_ages: list[int]
    ) -> DepartureRecord | None:
        """One departure quoted for this party, or ``None`` when there is no such id."""
        ...

    async def create_hold(
        self, departure_id: str, adults: int, children: int, advisor_id: str, session_key: str
    ) -> HoldRecord:
        """Take the party's seats off the departure until the hold expires."""
        ...

    async def release_hold(self, hold_id: str, advisor_id: str) -> None:
        """Give the held seats back."""
        ...

    async def list_holds(self, advisor_id: str, session_key: str) -> list[HoldRecord]:
        """The advisor's live holds in this conversation."""
        ...
