# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour example's ``StorefrontBackend`` over the 旅行社 ERP: a route (线路) is a product
family, its dated departures (团期) are that family's variants, and a cart line is a live
seat hold. A tour price holds only for one party on one date, so the window and party the
advisor last searched are kept per session and restated in every record's attributes. When
the stated window or filters leave the advisor with nothing to quote, the search relaxes
them a step at a time and each relaxed record says in Chinese what it does not meet, since
the advisor reads that back to the customer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from commerce_common.streaming import ToolOutcome
from demo_common.storefront_fixtures import (
    example_data_dir,
    find_order,
    find_product,
    load_json,
    load_orders,
    load_policies,
    load_users,
    newest_orders,
    orders_for,
    preferences_of,
    search_help,
)
from shopping_agent import (
    Cart,
    CartItem,
    FulfillmentOption,
    NotOffered,
    Order,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    Unavailable,
    UserPreferences,
)
from shopping_agent.executor import ShoppingToolExecutor

from .erp_client import (
    MAX_ROUTE_RESULTS,
    DepartureRecord,
    ErpClient,
    ErpDeadlinePassed,
    ErpError,
    ErpHoldLimit,
    ErpHoldNotFound,
    ErpSoldOut,
    HoldRecord,
    RouteQuery,
    RouteRecord,
)

DATA_DIR = example_data_dir(__file__)
STORE_NAME = "ACME 旅行社"
CURRENCY = "CNY"
CATEGORY = "tour"
ROUTE_PREFIX = "RT-"
DEPARTURE_PREFIX = "DP-"

# The window a search covers when the advisor states none, and the padding a route's
# details add on each side so the neighbouring 团期 are quotable without a second search.
DEFAULT_WINDOW_DAYS = 60
DETAIL_PAD_DAYS = 7
# One fenced details result holds every variant, so a long-running route is trimmed to the
# departures nearest the window the advisor is working in (docs/backends.md, step 4).
MAX_VARIANTS = 24
DEFAULT_ADULTS = 2
DEFAULT_CHILDREN = 0
MAX_LABELS = 4
# Fewer than two quotable routes is not a shortlist, so the search relaxes and says so.
MIN_RESULTS = 2
RELAX_WINDOW_DAYS = 7
RELAX_DAYS_SPAN = 2

_STATUS_TEXT = {"confirmed": "已成团", "pending": "待成团", "closed": "已截止"}


def _number(value: float) -> str:
    """A price or an hour count as the advisor says it: ``5980``, ``4.5``."""
    return f"{value:g}"


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _party_label(adults: int, children: int) -> str:
    return f"{adults}大{children}小"


def _int_or_none(raw: str | None) -> int | None:
    """A filter value the model wrote; anything that is not a whole number is no filter."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _int_of(raw: str | None, default: int) -> int:
    parsed = _int_or_none(raw)
    return default if parsed is None else parsed


def _date_of(raw: str | None, default: date) -> date:
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _ages_of(raw: str | None) -> list[int]:
    """``"5|9"`` as the ERP wants it; a part that is not a whole number is dropped."""
    parts = (str(raw or "")).split("|")
    return [age for age in (_int_or_none(part) for part in parts) if age is not None]


def _labels(record: RouteRecord) -> list[str]:
    """The display tags the advisor scans: the route's own 适合标签, plus the 纯玩 claim when
    no fit tag already makes it."""
    labels = list(record.fit_tags)
    if record.shopping_stops == 0 and not any("纯玩" in label for label in labels):
        labels.append("纯玩无购物")
    return labels[:MAX_LABELS]


def _route_attributes(record: RouteRecord, match: str, mismatch: str | None) -> dict[str, str]:
    return {
        "destination": record.destination,
        "region": record.region,
        "departure_city": record.departure_city,
        "days": str(record.days),
        "nights": str(record.nights),
        "hotel_level": record.hotel_level,
        "vehicle": record.vehicle,
        "group_size_max": str(record.group_size_max),
        "includes_transport": "yes" if record.includes_transport else "no",
        "shopping_stops": str(record.shopping_stops),
        "optional_paid_items": str(record.optional_paid_items),
        "child_min_age": str(record.child_min_age),
        "child_policy": record.child_policy,
        "intensity": str(record.intensity),
        **(
            {"max_drive_hours_per_day": _number(record.max_drive_hours_per_day)}
            if record.max_drive_hours_per_day is not None
            else {}
        ),
        "highlights": "|".join(record.highlights),
        "fit_tags": "|".join(record.fit_tags),
        "match": match,
        **({"mismatch": mismatch} if mismatch else {}),
    }


def _specs(record: RouteRecord) -> dict[str, str]:
    """The 行程规格 the advisor reads out; display only, so the keys are Chinese."""
    specs = {
        "行程天数": f"{record.days} 天 {record.nights} 晚",
        "住宿标准": record.hotel_level,
        "车型": record.vehicle,
        "成团人数": f"最多 {record.group_size_max} 人",
    }
    if record.meals is not None:
        specs["含餐"] = record.meals
    specs["购物店"] = f"{record.shopping_stops} 个"
    specs["自费项目"] = f"{record.optional_paid_items} 项"
    specs["儿童政策"] = record.child_policy
    if record.meeting_point is not None:
        specs["集合地点"] = record.meeting_point
    return specs


def _variant_title(row: DepartureRecord, record: RouteRecord | None) -> str:
    return f"{record.route_name if record else row.route_id} {_md(row.depart_date)} 出发"


def _variant_attributes(row: DepartureRecord, adults: int, children: int) -> dict[str, str]:
    return {
        "depart_date": row.depart_date.isoformat(),
        "return_date": row.return_date.isoformat(),
        "seats_left": str(row.seats_left),
        "seats_total": str(row.seats_total),
        "group_status": row.group_status,
        "booking_deadline": row.booking_deadline.isoformat(),
        "adult_price": _number(row.adult_price),
        "child_price": _number(row.child_price),
        **(
            {"child_bed_price": _number(row.child_bed_price)}
            if row.child_bed_price is not None
            else {}
        ),
        "single_supplement": _number(row.single_supplement),
        "party_quote_total": _number(row.party_quote_total),
        "quote_party": _party_label(adults, children),
        "hold_ttl_minutes": str(row.hold_ttl_minutes),
    }


def _variant_summary(row: DepartureRecord, adults: int, children: int) -> str:
    status = _STATUS_TEXT.get(row.group_status, row.group_status)
    party = _party_label(adults, children)
    return (
        f"余位 {row.seats_left}/{row.seats_total}，{status}，"
        f"{party}合计 {_number(row.party_quote_total)} 元"
    )


def _sold_out_detail(product_id: str, sold_out: ErpSoldOut) -> str:
    """``Unavailable`` leaves the fence, so it carries ids and nothing else."""
    siblings = ", ".join(sold_out.sibling_departure_ids)
    return f"{product_id}; in stock: {siblings}" if siblings else product_id


# -- relaxation: how a search widens when the stated filters leave nothing to quote ------


def _widen_window(q: RouteQuery) -> RouteQuery:
    if q.depart_from is None or q.depart_to is None:
        return q
    return replace(
        q,
        depart_from=q.depart_from - timedelta(days=RELAX_WINDOW_DAYS),
        depart_to=q.depart_to + timedelta(days=RELAX_WINDOW_DAYS),
    )


def _widen_days(q: RouteQuery) -> RouteQuery:
    return replace(
        q,
        days_min=None if q.days_min is None else q.days_min - RELAX_DAYS_SPAN,
        days_max=None if q.days_max is None else q.days_max + RELAX_DAYS_SPAN,
    )


def _drop_preferences(q: RouteQuery) -> RouteQuery:
    return replace(q, hotel_level=None, no_shopping=False)


def _distance(day: date, start: date, end: date) -> int:
    if start <= day <= end:
        return 0
    return min(abs((day - start).days), abs((day - end).days))


def _date_note(stated: RouteQuery, dates: list[date]) -> str | None:
    """Nothing departs inside the window the advisor stated; name the nearest one that does."""
    start, end = stated.depart_from, stated.depart_to
    if any(start <= day <= end for day in dates):
        return None
    span = f"{_md(start)}–{_md(end)}"
    nearest = min(dates, key=lambda day: (_distance(day, start, end), day), default=None)
    return f"无 {span} 团期，最近为 {_md(nearest)}" if nearest else f"无 {span} 团期"


def _days_note(record: RouteRecord, stated: RouteQuery) -> str | None:
    low = record.days if stated.days_min is None else stated.days_min
    high = record.days if stated.days_max is None else stated.days_max
    if low <= record.days <= high:
        return None
    return f"天数 {record.days} 天，超出要求的 {low}–{high} 天"


def _preference_notes(record: RouteRecord, stated: RouteQuery) -> list[str]:
    notes = []
    if stated.hotel_level and stated.hotel_level != record.hotel_level:
        notes.append(f"酒店为{record.hotel_level}，要求{stated.hotel_level}")
    if stated.no_shopping and record.shopping_stops:
        notes.append(f"含 {record.shopping_stops} 个购物店")
    return notes


def _mismatch(record: RouteRecord, stated: RouteQuery, dates: list[date]) -> str:
    """What the advisor reads back: every condition they stated that this record fails, in
    the order they stated them. A route admitted by one relaxation usually misses more than
    that step alone relaxed, and a note that named only that step would understate it."""
    notes = [note for note in (_date_note(stated, dates), _days_note(record, stated)) if note]
    return "；".join(notes + _preference_notes(record, stated)) or "与所提条件略有出入"


# Each step keeps the one before it, so a route found late is measured against everything
# the advisor stated, whichever step first found it.
_RELAXATIONS = (
    (_widen_window, "adjacent_date"),
    (_widen_days, "similar_route"),
    (_drop_preferences, "similar_route"),
)


@dataclass
class SearchContext:
    """The window and party the advisor last searched. Every later quote is made for it:
    ``get_product_details`` prices the departures for this party, and ``add_to_cart`` holds
    seats for it."""

    depart_from: date
    depart_to: date
    adults: int
    children: int
    child_ages: list[int]


class TourToolExecutor(ShoppingToolExecutor):
    """The ERP refuses a hold with a rule the advisor can act on (报名截止, 占位上限, 占位已
    过期); relay it so the model says what stands in the way instead of reporting an outage.
    ``ErpUnavailable`` is an outage and falls through to the base default."""

    erp_rule_text = "Nothing changed: {detail}. Tell the advisor and offer what fits."

    def domain_error(self, error: Exception) -> ToolOutcome | None:
        if isinstance(error, ErpDeadlinePassed | ErpHoldLimit | ErpHoldNotFound):
            return ToolOutcome.error(
                self.erp_rule_text.format(detail=self._sanitize(str(error), 200))
            )
        return super().domain_error(error)


class TourBackend(StorefrontBackend):
    """The advisor is the customer here: they search on behalf of the customer in front of
    them, and the cart is the seat holds their conversation owns."""

    def __init__(
        self, erp: ErpClient, data_dir: Path = DATA_DIR, today: date | None = None
    ) -> None:
        """``today`` is for a host that runs on its own clock; the default is the real date."""
        self.erp = erp
        self.today: date = today or datetime.now(UTC).date()
        self.store_name: str = load_json(data_dir, "routes.json").get("store_name", STORE_NAME)
        self._users = load_users(data_dir)
        self._orders = load_orders(data_dir)
        self._policies = load_policies(data_dir)
        self._contexts: dict[str, SearchContext] = {}
        # What the ERP has already returned this process: a route is looked up by id long
        # after the search that found it, and a cart line names a departure by id alone.
        self._routes: dict[str, RouteRecord] = {}
        self._departures: dict[str, DepartureRecord] = {}
        # The listing snapshot the host's catalog routes read; `load_listings` fills both.
        self.products: dict[str, ProductDetails] = {}
        self._variants: dict[str, ProductDetails] = {}
        # The holds the last cart read saw, per session, for the sync cart payload.
        self._hold_snapshots: dict[str, list[HoldRecord]] = {}

    # -- the searched window and party ------------------------------------------------

    def _default_context(self) -> SearchContext:
        """What an id pasted into a fresh conversation is quoted for. The party is stated
        back as ``quote_party`` so the advisor sees it is an assumption, not their party."""
        return SearchContext(
            depart_from=self.today,
            depart_to=self.today + timedelta(days=DEFAULT_WINDOW_DAYS),
            adults=DEFAULT_ADULTS,
            children=DEFAULT_CHILDREN,
            child_ages=[],
        )

    def _context(self, session: ShoppingSessionContext) -> SearchContext:
        return self._contexts.get(session.session_id) or self._default_context()

    def _read_context(self, attributes: dict[str, str]) -> SearchContext:
        """The advisor's stated window and party. The model writes these by hand, so a value
        that does not parse falls back to the default rather than failing the search."""
        depart_from = _date_of(attributes.get("depart_from"), self.today)
        depart_to = _date_of(
            attributes.get("depart_to"), self.today + timedelta(days=DEFAULT_WINDOW_DAYS)
        )
        # Stated backwards, the two dates are still the span the advisor means.
        if depart_to < depart_from:
            depart_from, depart_to = depart_to, depart_from
        return SearchContext(
            depart_from=depart_from,
            depart_to=depart_to,
            adults=max(1, _int_of(attributes.get("adults"), DEFAULT_ADULTS)),
            children=max(0, _int_of(attributes.get("children"), DEFAULT_CHILDREN)),
            child_ages=_ages_of(attributes.get("child_ages")),
        )

    def _route_query(
        self,
        query: str,
        attributes: dict[str, str],
        filters: SearchFilters | None,
        context: SearchContext,
        limit: int,
    ) -> RouteQuery:
        """The ERP query the advisor stated. ``destination`` is both the hard filter and the
        scored text, so the free-text query stands in when no destination attribute came."""
        return RouteQuery(
            destination=(attributes.get("destination") or query or "").strip(),
            depart_from=context.depart_from,
            depart_to=context.depart_to,
            days_min=_int_or_none(attributes.get("days_min")),
            days_max=_int_or_none(attributes.get("days_max")),
            adults=context.adults,
            children=context.children,
            child_ages=tuple(context.child_ages),
            departure_city=attributes.get("departure_city") or None,
            no_shopping=attributes.get("no_shopping", "").strip().lower() == "yes",
            hotel_level=attributes.get("hotel_level") or None,
            max_adult_price=filters.max_price if filters is not None else None,
            limit=max(1, min(limit, MAX_ROUTE_RESULTS)),
        )

    def _listing_window(self, start: date, end: date) -> tuple[date, date]:
        """A widened or padded window can reach into the past; nothing already gone is quotable."""
        start = max(self.today, start)
        return start, max(start, end)

    # -- catalog -----------------------------------------------------------------------

    async def _search(self, q: RouteQuery, filters: SearchFilters | None) -> list[RouteRecord]:
        """The ERP's ranked routes, minus any whose 起价 falls under a stated floor: the ERP
        filters on the ceiling, the floor is the advisor asking to be shown the better tier."""
        records = await self.erp.search_routes(q)
        self._routes.update({record.route_id: record for record in records})
        floor = filters.min_price if filters is not None else None
        if floor is None:
            return records
        return [record for record in records if record.min_adult_price_in_window >= floor]

    async def _list(
        self, route_id: str, window: tuple[date, date], context: SearchContext
    ) -> list[DepartureRecord]:
        rows = await self.erp.list_departures(
            route_id, window[0], window[1], context.adults, context.children, context.child_ages
        )
        self._departures.update({row.departure_id: row for row in rows})
        return rows

    async def _route(self, route_id: str) -> RouteRecord | None:
        """The route record, from the search that returned it or, for an id this process has
        not seen, from one broad ERP search. That search runs on the default window and two
        adults rather than the session's, because a route the advisor names has to resolve
        whatever they last searched for: an empty ``destination`` is not a filter, so it
        brings back every route selling seats in the next couple of months. It asks about no
        children either: an age rule is a reason to warn the advisor about the route they
        named, not a reason to fail to find it."""
        if route_id in self._routes:
            return self._routes[route_id]
        default = self._default_context()
        await self._search(
            RouteQuery(
                destination="",
                depart_from=default.depart_from,
                depart_to=default.depart_to,
                adults=default.adults,
                limit=MAX_ROUTE_RESULTS,
            ),
            None,
        )
        return self._routes.get(route_id)

    def _family(
        self,
        record: RouteRecord,
        rows: list[DepartureRecord],
        match: str,
        mismatch: str | None,
    ) -> Product:
        """One route as a family: its 起价 in the searched window, and the departure dates
        that window holds as the one option a variant chooses."""
        return Product(
            product_id=record.route_id,
            title=record.route_name,
            brand=record.supplier_name or STORE_NAME,
            price=record.min_adult_price_in_window,
            currency=CURRENCY,
            image_url=record.image_url,
            category=CATEGORY,
            labels=_labels(record),
            attributes=_route_attributes(record, match, mismatch),
            in_stock=record.has_seats_in_window,
            short_description=record.summary,
            options={"depart_date": [row.depart_date.isoformat() for row in rows]},
        )

    def _variant(
        self, row: DepartureRecord, record: RouteRecord | None, context: SearchContext
    ) -> Product:
        """One departure as a variant, quoted for the party in ``context``: its price, its
        seats, and whether this party can still take them."""
        party = context.adults + context.children
        bookable = row.group_status != "closed" and row.booking_deadline >= self.today
        return Product(
            product_id=row.departure_id,
            title=_variant_title(row, record),
            brand=(record.supplier_name if record else None) or STORE_NAME,
            price=row.adult_price,
            currency=CURRENCY,
            image_url=record.image_url if record else None,
            category=CATEGORY,
            attributes=_variant_attributes(row, context.adults, context.children),
            in_stock=bookable and row.seats_left >= party,
            short_description=_variant_summary(row, context.adults, context.children),
            option_values={"depart_date": row.depart_date.isoformat()},
            variant_of=row.route_id,
        )

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        attributes = dict(filters.attributes) if filters is not None else {}
        context = self._read_context(attributes)
        self._contexts[session.session_id] = context
        stated = self._route_query(query, attributes, filters, context, limit)
        window = self._listing_window(stated.depart_from, stated.depart_to)
        found = [
            self._family(record, await self._list(record.route_id, window, context), "exact", None)
            for record in await self._search(stated, filters)
        ]
        relaxed = stated
        for widen, match in _RELAXATIONS:
            if len(found) >= MIN_RESULTS:
                break
            relaxed = widen(relaxed)
            window = self._listing_window(relaxed.depart_from, relaxed.depart_to)
            seen = {product.product_id for product in found}
            for record in await self._search(relaxed, filters):
                if record.route_id in seen:
                    continue
                rows = await self._list(record.route_id, window, context)
                dates = [row.depart_date for row in rows]
                found.append(self._family(record, rows, match, _mismatch(record, stated, dates)))
        return found[:limit]

    async def _route_details(self, route_id: str, context: SearchContext) -> ProductDetails | None:
        record = await self._route(route_id)
        if record is None:
            return None
        window = self._listing_window(
            context.depart_from - timedelta(days=DETAIL_PAD_DAYS),
            context.depart_to + timedelta(days=DETAIL_PAD_DAYS),
        )
        rows = await self._list(record.route_id, window, context)
        if not rows:
            # The searched window holds no 团期 of the route the advisor named. Quote the
            # default window instead, once: the dates say the route runs elsewhere, which
            # the advisor can act on, and a family with no variants does not.
            default = self._default_context()
            window = self._listing_window(default.depart_from, default.depart_to)
            rows = await self._list(record.route_id, window, context)
        if len(rows) > MAX_VARIANTS:
            middle = window[0] + (window[1] - window[0]) / 2
            nearest = sorted(rows, key=lambda row: abs((row.depart_date - middle).days))
            rows = sorted(nearest[:MAX_VARIANTS], key=lambda row: row.depart_date)
        family = self._family(record, rows, "exact", None)
        details = ProductDetails(
            **family.model_dump(),
            long_description=record.itinerary_brief[:1200],
            specs=_specs(record),
        )
        details.variants = [self._variant(row, record, context) for row in rows]
        return details

    async def _departure_details(
        self, departure_id: str, context: SearchContext
    ) -> ProductDetails | None:
        row = await self.erp.get_departure(
            departure_id, context.adults, context.children, context.child_ages
        )
        if row is None:
            return None
        self._departures[row.departure_id] = row
        record = await self._route(row.route_id)
        variant = self._variant(row, record, context)
        return ProductDetails(
            **variant.model_dump(),
            long_description=record.itinerary_brief[:1200] if record else None,
            specs=_specs(record) if record else {},
        )

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        context = self._context(session)
        if product_id.startswith(ROUTE_PREFIX):
            return await self._route_details(product_id, context)
        if product_id.startswith(DEPARTURE_PREFIX):
            return await self._departure_details(product_id, context)
        return None

    # -- cart: a view of the conversation's live holds -----------------------------------

    async def _holds(self, session: ShoppingSessionContext) -> list[HoldRecord]:
        holds = await self.erp.list_holds(
            advisor_id=session.user_id, session_key=session.session_id
        )
        self._hold_snapshots[session.session_id] = holds
        return holds

    async def _hold_for(
        self, session: ShoppingSessionContext, departure_id: str
    ) -> HoldRecord | None:
        holds = await self._holds(session)
        return next((hold for hold in holds if hold.departure_id == departure_id), None)

    def _party(self, session: ShoppingSessionContext, quantity: int) -> tuple[int, int]:
        """The quantity the model asked for is heads; the split into 成人 and 儿童 comes from
        the searched party, because the ERP prices and seats them differently. A hold is for
        at least one head and at least one 成人, whatever the model asked for: an unaccompanied
        child is not a party the ERP will seat."""
        quantity = max(1, quantity)
        context = self._contexts.get(session.session_id)
        if context is None:
            return quantity, 0
        children = min(context.children, quantity - 1)
        return quantity - children, children

    async def _line(self, hold: HoldRecord, context: SearchContext) -> CartItem:
        row = self._departures.get(hold.departure_id)
        if row is None:
            row = await self.erp.get_departure(
                hold.departure_id, hold.adults, hold.children, context.child_ages
            )
            if row is not None:
                self._departures[hold.departure_id] = row
        quantity = max(1, hold.adults + hold.children)
        record = self._routes.get(row.route_id) if row is not None else None
        return CartItem(
            product_id=hold.departure_id,
            title=_variant_title(row, record) if row is not None else hold.departure_id,
            # The ERP quotes the party as a whole; a cart line is per head, so the quote is
            # split evenly and the line total comes back to the ERP's figure.
            price=round(hold.total_price / quantity, 2),
            quantity=quantity,
            option_values=({"depart_date": row.depart_date.isoformat()} if row is not None else {}),
            variant_of=row.route_id if row is not None else None,
        )

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        context = self._context(session)
        return Cart(
            items=[await self._line(hold, context) for hold in await self._holds(session)],
            currency=CURRENCY,
        )

    async def _hold(
        self, session: ShoppingSessionContext, departure_id: str, quantity: int
    ) -> None:
        adults, children = self._party(session, quantity)
        try:
            await self.erp.create_hold(
                departure_id,
                adults,
                children,
                advisor_id=session.user_id,
                session_key=session.session_id,
            )
        except ErpSoldOut as sold_out:
            raise Unavailable(_sold_out_detail(departure_id, sold_out)) from sold_out

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        if not product_id.startswith(DEPARTURE_PREFIX):
            # The executor's gate already holds an add of a family; this is the second layer,
            # and it also catches an id that is neither a route nor a departure.
            raise Unavailable(product_id)
        await self._hold(session, product_id, quantity)
        return await self.get_cart(session)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        """A hold is taken for one party, so a changed party is a new hold: the seats go back
        first and the ERP decides whether it can take the larger one. A refused change leaves
        the advisor the hold they had, not an empty cart."""
        hold = await self._hold_for(session, product_id)
        if hold is not None:
            await self.erp.release_hold(hold.hold_id, session.user_id)
            try:
                await self._hold(session, product_id, quantity)
            except (Unavailable, ErpError):
                # The seats were just freed, so the party that already fitted still fits.
                await self.erp.create_hold(
                    product_id,
                    hold.adults,
                    hold.children,
                    advisor_id=session.user_id,
                    session_key=session.session_id,
                )
                raise
        return await self.get_cart(session)

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        hold = await self._hold_for(session, product_id)
        if hold is not None:
            await self.erp.release_hold(hold.hold_id, session.user_id)
        return await self.get_cart(session)

    # -- advisor, orders, help content, fulfillment ---------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return preferences_of(self._users, session.user_id)

    async def get_account_context(self, session: ShoppingSessionContext) -> dict[str, Any] | None:
        profile = preferences_of(self._users, session.user_id)
        # The profile states the 门店 and then what it sells; the store is the first clause.
        store = profile.preferences.get("门店", STORE_NAME).split("，")[0]
        return {
            "advisor": session.user_id,
            "store": store,
            "active_holds": len(await self._holds(session)),
        }

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        return orders_for(self._orders, session.user_id, limit)

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        return find_order(self._orders, session.user_id, order_id)

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        del session
        return search_help(self._policies, query)

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        """Nothing ships: the customer joins the group at its 集合地点, which the route's
        specs carry. The tool is switched off in the config; this is the second layer."""
        del session, product_ids
        raise NotOffered("Delivery, pickup, and shipping for a tour booking")

    # -- the demo host's view: a listing snapshot, per-session cleanup, live holds --------

    async def load_listings(self) -> None:
        """Fill ``products`` with one record per route, its ``variants`` the departures in
        the default window. This is a boot snapshot for the host's catalog routes and the
        demo's listing pages; the agent's own tools call the ERP on every turn, so seats,
        prices, and party quotes in the conversation are live and these are not."""
        context = self._default_context()
        window = self._listing_window(context.depart_from, context.depart_to)
        records = await self._search(
            RouteQuery(
                destination="",
                depart_from=window[0],
                depart_to=window[1],
                adults=context.adults,
                limit=MAX_ROUTE_RESULTS,
            ),
            None,
        )
        listings: dict[str, ProductDetails] = {}
        variants: dict[str, ProductDetails] = {}
        for record in records:
            details = await self._route_details(record.route_id, context)
            if details is None:
                continue
            listings[details.product_id] = details
            for variant in details.variants:
                variants[variant.product_id] = ProductDetails(**variant.model_dump())
        self.products, self._variants = listings, variants

    def product(self, product_id: str) -> ProductDetails | None:
        """A loaded route or one of its departures by id, from the snapshot ``load_listings``
        took; the conversation's own reads go to the ERP instead."""
        return find_product(self.products, self._variants, product_id)

    def reset_session(self, session_id: str) -> None:
        """Forget what the conversation accumulated: the window and party it searched, and
        the holds its last cart read saw. The holds themselves stay with the ERP until their
        TTL runs out, because releasing one is an async write and this call is not."""
        self._contexts.pop(session_id, None)
        self._hold_snapshots.pop(session_id, None)

    def recent_orders(self, limit: int = 6) -> list[Order]:
        return newest_orders(self._orders, limit)

    def holds_snapshot(self, session_id: str) -> list[HoldRecord]:
        """The holds this session's last cart read returned. The host builds its cart extras
        synchronously, so they read this rather than the ERP."""
        return list(self._hold_snapshots.get(session_id, ()))
