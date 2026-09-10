# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour example's ``ErpClient`` over the ERP-shaped fixtures in ``data/``: 线路 ranked
against the advisor's text, their 团期 inside a date window, the 同行 customers an order is
written for, and an in-memory order book. Departure dates move forward by whole weeks from
``dates_anchored_to`` and each ``periodCode`` is rebuilt from the shifted date, so a demo
booted any week still has departures ahead of today while ``periodId`` never moves. The rules
here are the ERP's own and no more: a party over the seats left becomes a 候补 rather than a
refusal, a quote and an order are refused unless they name the 团期's own department, an order
is the only write, and nothing expires — the 30-minute countdown the advisor sees belongs to
the backend above this one."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from demo_common.storefront_fixtures import anchored_shift, example_data_dir, load_json

from . import erp_client as erp

DATA_DIR = example_data_dir(__file__)
FIRST_ORDER_ID = 70001
PRICE_TYPE = "同行价"

# ``keyword_score`` finds nothing in a Chinese field, so ranking matches character 2-grams.
_SEARCH_WEIGHTS = {"routeName": 3.0, "tags": 2.0, "features": 1.5}
_SYNONYMS = {
    "纯玩": ("零购物",),
    "亲子": ("儿童", "家庭"),
    "小车": ("商务车",),
    "老人": ("轻松", "低强度", "老人友好"),
}
_SEPARATORS = re.compile(r"[\s,，、。·:：;；/\\_-]+")
_CONTACT_NAME = re.compile(r"[一-龥]{2,50}")
_MOBILE = re.compile(r"1\d{10}")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _flatten(text: str) -> str:
    return _SEPARATORS.sub("", text.lower())


def _text_score(query: str, route: dict[str, Any]) -> float:
    """Each character 2-gram of the query (and the whole query) scores the weight of the
    best field it, or one of its synonyms, appears in."""
    flat = {
        "routeName": _flatten(route["routeName"]),
        "tags": _flatten(" ".join(route.get("tags", ()))),
        "features": _flatten(" ".join(route.get("features", ()))),
    }
    text = _flatten(query)
    units = {text[i : i + 2] for i in range(len(text) - 1)} | ({text} if text else set())
    score = 0.0
    for unit in units:
        candidates = (unit, *_SYNONYMS.get(unit, ()))
        score += max(
            (w for name, w in _SEARCH_WEIGHTS.items() if any(c in flat[name] for c in candidates)),
            default=0.0,
        )
    return score


def _period_code(code: str, depart_date: date) -> str:
    """``XJ-<routeCode>-<YYYYMMDD>-<serial>``, rebuilt after the shift so a code matches its
    date; a code the fixture wrote in another shape is left alone."""
    parts = code.split("-")
    if len(parts) != 4:
        return code
    return "-".join((parts[0], parts[1], depart_date.strftime("%Y%m%d"), parts[3]))


def _price(row: dict[str, Any]) -> erp.PriceInfo:
    return erp.PriceInfo(
        adult=float(row["adultPrice"]),
        child=float(row["childPrice"]),
        elder=float(row["elderPrice"]),
        single_room_diff=float(row["singleRoomDiff"]),
        currency=str(row.get("currency") or "CNY"),
    )


def _route_record(row: dict[str, Any]) -> erp.RouteRecord:
    return erp.RouteRecord(
        route_id=row["routeId"],
        route_code=row["routeCode"],
        route_name=row["routeName"],
        days=row["days"],
        depart_city=row["departCityName"],
        company_name=row["companyName"],
        from_price=float(row["fromPrice"]),
        tags=tuple(row.get("tags") or ()),
        itinerary_tags=tuple(row.get("itineraryTags") or ()),
        price_tags=tuple(row.get("periodPriceTags") or ()),
        features=tuple(row.get("features") or ()),
        image_url=row.get("firstImageUrl"),
        attachment_name=row.get("routeAttachmentName"),
        attachment_url=row.get("routeAttachmentUrl"),
        sale_type=str(row.get("saleType") or ""),
    )


def _departure_record(row: dict[str, Any], *, detail: bool) -> erp.DepartureRecord:
    """Only the detail call carries the price and the 占位 counts, as the ERP's two calls do."""
    return erp.DepartureRecord(
        period_id=row["periodId"],
        period_code=row["periodCode"],
        route_id=row["routeId"],
        route_name=row["routeName"],
        depart_date=row["departDate"],
        return_date=row["returnDate"],
        days=row["days"],
        plan_guests=row["planGuests"],
        min_group_size=row["minGroupSize"],
        confirm_count=row["confirmCount"],
        available_seats=row["availableSeats"],
        reserve_hours=row["reserveHours"],
        depart_city=row["departCityName"],
        company_id=row["companyId"],
        reserve_count=row["reserveCount"] if detail else None,
        placeholder_count=0 if detail else None,
        waitlist_count=row["waitlistCount"] if detail else None,
        price=_price(row["priceInfo"]) if detail else None,
    )


class MockErpClient:
    """An ``ErpClient`` backed by ``data/routes.json``, ``data/departures.json`` and
    ``data/customers.json``, with the orders it writes kept in memory."""

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        today: date | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or _utcnow
        self.today = today or self._now().date()
        raw = load_json(data_dir, "departures.json")
        shift = anchored_shift(raw, self.today)
        routes = load_json(data_dir, "routes.json")
        self.store_name: str = routes.get("store_name", "")
        self._routes: dict[int, dict[str, Any]] = {
            row["routeId"]: dict(row) for row in routes["routes"]
        }
        self._departures: dict[int, dict[str, Any]] = {}
        for entry in raw["departures"]:
            row = dict(entry)
            row["departDate"] = date.fromisoformat(row["departDate"]) + shift
            row["returnDate"] = date.fromisoformat(row["returnDate"]) + shift
            row["periodCode"] = _period_code(row["periodCode"], row["departDate"])
            row.setdefault("reserveCount", 0)
            row.setdefault("waitlistCount", 0)
            self._departures[row["periodId"]] = row
        self._customers: dict[int, dict[str, Any]] = {
            row["customerId"]: dict(row)
            for row in load_json(data_dir, "customers.json")["customers"]
        }
        self._orders: dict[int, erp.OrderRecord] = {}

    async def search_routes(self, q: erp.RouteQuery) -> list[erp.RouteRecord]:
        """Routes with at least one departure inside the window, best text match first,
        then cheapest. The ERP matches on the name; nothing else is filtered here."""
        ranked: list[tuple[float, float, int]] = []
        for route_id, route in self._routes.items():
            if q.route_code and q.route_code not in route["routeCode"]:
                continue
            score = _text_score(q.route_name, route)
            if q.route_name and score <= 0:
                continue
            if not self._window(route_id, q.depart_from, q.depart_to):
                continue
            ranked.append((-score, float(route["fromPrice"]), route_id))
        ranked.sort()
        size = q.page_size if q.page_size > 0 else 50
        return [_route_record(self._routes[route_id]) for _s, _p, route_id in ranked[:size]]

    async def list_departures(
        self,
        route_id: int,
        route_name: str,
        depart_from: date | None,
        depart_to: date | None,
    ) -> list[erp.DepartureRecord]:
        """The route's departures inside the window, by date. ``route_name`` is what the
        ERP's period list filters on; here the id decides, as it does after that filter."""
        del route_name
        rows = self._window(route_id, depart_from, depart_to)
        rows.sort(key=lambda row: row["departDate"])
        return [_departure_record(row, detail=False) for row in rows]

    async def get_departure(self, period_id: int) -> erp.DepartureRecord | None:
        row = self._departures.get(period_id)
        return None if row is None else _departure_record(row, detail=True)

    async def search_customers(self, keyword: str) -> list[erp.CustomerRecord]:
        hit = [r for r in self._customers.values() if keyword in r["companyName"] + r["csCode"]]
        return [
            erp.CustomerRecord(r["customerId"], r["companyName"], r["csCode"], r["companyType"])
            for r in hit
        ]

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> erp.Quote:
        """The 同业价 for this customer, which is the 市场价 the departure carries here: the
        fixtures hold one price per 团期. The department is checked as the real ERP checks it,
        because a quote is a write-side call and answers only inside the 团期's own."""
        row = self._require_departure(period_id)
        self._require_customer(customer_id)
        self._require_company(row, company_id)
        return erp.Quote(price=_price(row["priceInfo"]), price_type=PRICE_TYPE, is_external=False)

    async def create_order(self, req: erp.OrderRequest) -> erp.OrderResult:
        """The ERP's own checks, then the seats. A party larger than what is left is written
        as a 候补 order and takes no seats; anything else is a 预留 that takes them."""
        row = self._require_departure(req.period_id)
        customer = self._require_customer(req.customer_id)
        self._require_company(row, req.company_id)
        party = req.adults + req.children + req.elders
        if party < 1:
            raise erp.ErpRefused("下单人数至少 1 人，请填写成人、儿童或老人人数。")
        if not _CONTACT_NAME.fullmatch(req.contact_name):
            raise erp.ErpRefused("联系人姓名需为 2-50 个汉字。")
        if not _MOBILE.fullmatch(req.contact_mobile):
            raise erp.ErpRefused("联系人手机号格式不正确，请填写 11 位手机号。")
        waitlist = party > row["availableSeats"]
        if not waitlist:
            row["availableSeats"] -= party
            row["reserveCount"] += party
        else:
            row["waitlistCount"] += party
        status = 5 if waitlist else 0
        order_id = FIRST_ORDER_ID + len(self._orders)
        price = _price(row["priceInfo"])
        total = (
            req.adults * price.adult
            + req.children * price.child
            + req.elders * price.elder
            + req.single_room_diff_count * price.single_room_diff
        )
        now = self._now()
        expires = None if waitlist else now + timedelta(hours=row["reserveHours"])
        self._orders[order_id] = erp.OrderRecord(
            order_id=order_id,
            order_no=f"ORD{order_id:06d}",
            period_id=row["periodId"],
            period_code=row["periodCode"],
            route_name=row["routeName"],
            customer_id=customer["customerId"],
            customer_name=customer["companyName"],
            total_amount=total,
            received_amount=0.0,
            unreceived_amount=total,
            status=status,
            status_text=erp.ORDER_STATUS[status],
            created_at=now,
            adults=req.adults,
            children=req.children,
            elders=req.elders,
            contact_name=req.contact_name,
            contact_mobile=req.contact_mobile,
            depart_date=row["departDate"],
            return_date=row["returnDate"],
            reserve_expires_at=expires,
        )
        return erp.OrderResult(
            order_id=order_id,
            needs_approval=False,
            approval_id=0,
            status=status,
            is_waitlist=waitlist,
        )

    async def list_orders(self, statuses: tuple[int, ...] = ()) -> list[erp.OrderRecord]:
        rows = [o for o in self._orders.values() if not statuses or o.status in statuses]
        return sorted(rows, key=lambda order: order.order_id, reverse=True)

    async def get_order(self, order_id: int) -> erp.OrderRecord | None:
        return self._orders.get(order_id)

    def _window(
        self, route_id: int, depart_from: date | None, depart_to: date | None
    ) -> list[dict[str, Any]]:
        """The route's departures inside the query's date window."""
        return [
            row
            for row in self._departures.values()
            if row["routeId"] == route_id
            and (depart_from is None or row["departDate"] >= depart_from)
            and (depart_to is None or row["departDate"] <= depart_to)
        ]

    def _require_departure(self, period_id: int) -> dict[str, Any]:
        row = self._departures.get(period_id)
        if row is None:
            raise erp.ErpNotFound(f"找不到该团期：{period_id}")
        return row

    def _require_customer(self, customer_id: int) -> dict[str, Any]:
        row = self._customers.get(customer_id)
        if row is None:
            raise erp.ErpNotFound(f"找不到该客户：{customer_id}")
        return row

    def _require_company(self, row: dict[str, Any], company_id: int) -> None:
        """A write made in the wrong department. The real ERP answers a bare 404 to it; the
        refusal here names what is wrong, since nothing else in the fixtures can."""
        if company_id != row["companyId"]:
            raise erp.ErpRefused(f"部门不匹配：该团期属于部门 {row['companyId']}。")
