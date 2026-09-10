# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The ERP seam behind the tour advisor, in the B2B 旅行社 ERP's own vocabulary: 线路
(route families), their dated 团期 (departures), the 同行 customers an order is written
for, and the order itself. ``ErpClient`` is the nine calls the backend needs; the records
are frozen dataclasses of plain types, so a real ERP maps onto them without importing
anything from this example. Every error message is advisor-facing Chinese, because the
executor relays it into the conversation unchanged.

Three shapes of the ERP show through the seam and cannot be hidden. Departures are listed by
route *name*, because the ERP's period list has no ``routeId`` filter, and the caller keeps
the rows whose ``route_id`` matches — which is why a client that reads a whole window at once
says so through ``WindowReader``, and why a search asks for the window instead of for every
route in it. An order is the only write there is: the ERP has no cancel, release, or amend
call, so nothing here can take an order back. And a department (``company_id``) rides on every
departure, because the account reads across all the departments it is authorised for while a
quote and an order are written in one of them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

# The ERP's own order statuses; a fresh order is 预留 (0) or, over the seats left, 候补 (5).
ORDER_STATUS = {0: "预留", 1: "占位", 2: "确认", 3: "取消", 4: "审批中", 5: "候补"}


class ErpError(Exception):
    """A failed ERP call; the message is safe to show the advisor as written."""


class ErpRefused(ErpError):
    """The ERP refused the call on its own rules (HTTP 400); the message is the ERP's."""


class ErpAuth(ErpError):
    """Login failed, a token a fresh login did not cure, or a department the account
    cannot see (HTTP 401 and 403)."""


class ErpNotFound(ErpError):
    """No such record, or none this salesperson can see (HTTP 404)."""


class ErpThrottled(ErpError):
    """Too many attempts; the ERP locks a mobile after ten failed logins (HTTP 429)."""


class ErpUnavailable(ErpError):
    """The ERP could not be reached, answered 5xx, or answered something unreadable."""


@dataclass(frozen=True)
class PriceInfo:
    """One price line: the three per-person prices and the 单房差, in ``currency`` (元)."""

    adult: float
    child: float
    elder: float
    single_room_diff: float
    currency: str = "CNY"


@dataclass(frozen=True)
class RouteQuery:
    """One route search. ``route_name`` is the fuzzy text the ERP matches on; the backend
    maps the advisor's destination onto it, because the ERP carries no destination field.
    The date window filters the routes to those with a departure inside it."""

    route_name: str = ""
    route_code: str = ""
    depart_from: date | None = None
    depart_to: date | None = None
    page_size: int = 50


@dataclass(frozen=True)
class RouteRecord:
    """A route family. ``from_price`` is the ERP's own 起价 and may be 0 when the catalog
    does not carry one; ``tags`` and ``features`` are free text the ERP's editors wrote.
    ``itinerary_tags`` is the ERP's own auto-extraction of the itinerary attachment — some
    fifty tags naming the destination, the departure city, the hotel standard, 购物, what the
    price includes and every attraction on the way — written in whatever words the extractor
    found and carrying its mistakes, so it is evidence and not a specification.
    ``price_tags`` is the budget band the ERP puts on the route's 团期 (预算约9999—1万元).
    ``sale_type`` is the ERP's ``saleType``, the field asked of it for who may sell the line;
    ``""`` until the ERP carries it, and ``api/private_lines.py`` reads the name meanwhile."""

    route_id: int
    route_code: str
    route_name: str
    days: int
    depart_city: str
    company_name: str
    from_price: float
    tags: tuple[str, ...]
    itinerary_tags: tuple[str, ...]
    price_tags: tuple[str, ...]
    features: tuple[str, ...]
    image_url: str | None
    attachment_name: str | None
    attachment_url: str | None
    sale_type: str = ""


@dataclass(frozen=True)
class ItineraryDay:
    """One day of a 线路's baseline 行程. ``text`` is the day's programme as one paragraph;
    ``hotel`` is the night's 住宿 and ``meals`` the day's 用餐, each ``None`` where the source
    states none. The day is the 线路's own and not a 团期's: a dated departure runs it as
    written, and what a party actually gets is the 计调's to confirm."""

    day_no: int
    title: str
    text: str
    hotel: str | None
    meals: str | None


@dataclass(frozen=True)
class Itinerary:
    """A 线路's day-by-day 行程 and where it was read. ``source`` is ``erp`` for the ERP's own
    structured days and ``attachment`` for days parsed out of the 行程附件 the catalog links,
    and ``source_ref`` is what identifies that reading — the ERP's ``version``, or the
    attachment's ETag — so a host that caches one knows whether it is still the same."""

    route_id: int
    source: Literal["erp", "attachment"]
    source_ref: str | None
    days: tuple[ItineraryDay, ...]


@dataclass(frozen=True)
class DepartureRecord:
    """One dated departure. ``confirm_count`` against ``min_group_size`` is what makes a
    团期 成团 or 待成团, and ``available_seats`` is what a party has to fit into.
    ``reserve_hours`` is the ERP's own hold window on a new 预留 order. ``company_id`` is the
    department the 团期 belongs to, and both writes on it are made in that department. The
    last four fields come only from the departure detail call: the list call does not carry
    them, so ``price`` is ``None`` on a listed row and the 市场价 on a fetched one."""

    period_id: int
    period_code: str
    route_id: int
    route_name: str
    depart_date: date
    return_date: date
    days: int
    plan_guests: int
    min_group_size: int
    confirm_count: int
    available_seats: int
    reserve_hours: int
    depart_city: str
    company_id: int
    reserve_count: int | None = None
    placeholder_count: int | None = None
    waitlist_count: int | None = None
    price: PriceInfo | None = None


@dataclass(frozen=True)
class CustomerRecord:
    """A customer of the agency. ``customer_type`` 1 is 同行, the only kind this deployment
    books for."""

    customer_id: int
    name: str
    code: str
    customer_type: int


@dataclass(frozen=True)
class Quote:
    """What one customer pays for one departure: the 同业价, which is the advisor's
    settlement price and what an order is booked at, not the 市场价 the departure lists.
    ``price_type`` is the ERP's own label (同行价, 直客价); ``is_external`` marks an order
    placed outside the agency's own book."""

    price: PriceInfo
    price_type: str
    is_external: bool


@dataclass(frozen=True)
class OrderRequest:
    """The order to write. ``company_id`` is the departure's own department: the write is
    made in it, and a client that holds one token per department switches to it first.
    ``store_name`` is the shop the 同行 customer books through, which the ERP requires for a
    同行 order; ``contact_name`` and ``contact_mobile`` are the advisor's own, because the
    advisor is the ERP salesperson."""

    period_id: int
    customer_id: int
    company_id: int
    adults: int
    children: int
    elders: int
    rooms: int
    single_room_diff_count: int
    contact_name: str
    contact_mobile: str
    store_name: str = ""
    remark: str = ""


@dataclass(frozen=True)
class OrderResult:
    """What the ERP answers a written order with. ``status`` is an ``ORDER_STATUS`` key: a
    party larger than the seats left comes back as 候补 (5) with ``is_waitlist``, not as a
    refusal."""

    order_id: int
    needs_approval: bool
    approval_id: int
    status: int
    is_waitlist: bool


@dataclass(frozen=True)
class OrderRecord:
    """An order as the ERP holds it. The list call carries the money, the status and the
    route; the detail call adds the party, the contact, the dates and the 预留 expiry, so
    those fields keep their defaults on a record built from a list row."""

    order_id: int
    order_no: str
    period_id: int
    period_code: str
    route_name: str
    customer_id: int
    customer_name: str
    total_amount: float
    received_amount: float
    unreceived_amount: float
    status: int
    status_text: str
    created_at: datetime
    adults: int = 0
    children: int = 0
    elders: int = 0
    contact_name: str = ""
    contact_mobile: str = ""
    depart_date: date | None = None
    return_date: date | None = None
    reserve_expires_at: datetime | None = None


class ErpClient(Protocol):
    """What the tour backend needs from a 旅行社 ERP. Reads run as the logged-in salesperson
    and span every department that account is authorised for; the two writes are made in one
    department, the departure's own ``company_id``, and a client bound to another department
    is refused. No call takes an advisor id, because the credentials already name one."""

    async def search_routes(self, q: RouteQuery) -> list[RouteRecord]:
        """Route families matching the query's fuzzy name and date window."""
        ...

    async def list_departures(
        self,
        route_id: int,
        route_name: str,
        depart_from: date | None,
        depart_to: date | None,
    ) -> list[DepartureRecord]:
        """One route's departures inside the window, by date. The ERP's period list
        filters on the route's *name*, so the rows come back by ``route_name`` and are kept
        by ``route_id``: both arguments name the same route."""
        ...

    async def get_itinerary(self, route_id: int) -> Itinerary | None:
        """The ERP's own structured itinerary for a 线路, ``None`` where the ERP has none. The
        agency's API does not carry this endpoint yet (``docs/erp-contract.md`` asks for it),
        so a client over it answers ``None`` for every 线路; reading the 行程附件 instead is the
        host's own work (``api/itinerary_source.py``) and not this seam's."""
        ...

    async def get_departure(self, period_id: int) -> DepartureRecord | None:
        """One departure with its seat counts, its department, and its 市场价 — the price the
        customer-facing share page shows — or ``None`` when there is no such id, or none this
        salesperson can see."""
        ...

    async def search_customers(self, keyword: str) -> list[CustomerRecord]:
        """Customers whose name or code matches, best match first."""
        ...

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> Quote:
        """The 同业价 this customer pays for this departure: the advisor's settlement price,
        and what an order is booked at, where the departure's own ``price`` is the 市场价.
        ``company_id`` is the departure's department, which this write-side call is made in:
        a client bound to another department is refused."""
        ...

    async def create_order(self, req: OrderRequest) -> OrderResult:
        """Write the order, in the department ``req.company_id`` names. This is the only
        write the ERP offers: there is no cancel, release, or amend call, so an order that
        lands stands until the advisor changes it inside the ERP. There is no idempotency key
        either, so a caller that times out lists orders before deciding anything, and never
        retries blindly."""
        ...

    async def list_orders(self, statuses: tuple[int, ...] = ()) -> list[OrderRecord]:
        """The salesperson's orders in the department they are logged in to, newest first,
        filtered to ``ORDER_STATUS`` keys when any are named. An order written in another
        department is not listed."""
        ...

    async def get_order(self, order_id: int) -> OrderRecord | None:
        """One order with its party, contact and 预留 expiry, or ``None`` when there is no
        such id."""
        ...


@runtime_checkable
class WindowReader(Protocol):
    """A client that can read a whole date window's 团期 in one call. The ERP's period list
    filters on the route *name* and carries no ``routeId``, so learning many 线路's departures
    otherwise costs a call per 线路 — a fan-out a search over a live catalog cannot afford. A
    client that offers this says so by having the method; ``TourBackend`` asks for the window
    once per search and reads each route's departures out of it, and falls back to
    ``list_departures`` per route for a client that does not, where a call costs nothing."""

    async def list_window(
        self, depart_from: date | None, depart_to: date | None
    ) -> list[DepartureRecord]:
        """Every departure inside the window, across every 线路 and every department this
        salesperson reads, however many pages the ERP holds them in."""
        ...
