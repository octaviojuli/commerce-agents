# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The tour example's ``ErpClient`` over the fixtures in ``data/``: routes ranked against
the advisor's stated destination, departures quoted for the party in front of them, and
in-memory seat holds that expire on the injected clock. Departure dates move forward by
whole weeks from ``dates_anchored_to`` and their ids are rebuilt from the shifted date, so
a demo booted any week still has departures ahead of today. Expiry is swept lazily at the
top of every public call, as the entertainment example sweeps its ticket holds."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from demo_common.storefront_fixtures import anchored_shift, example_data_dir, load_json

from .erp_client import (
    DEFAULT_ROUTE_RESULTS,
    MAX_ROUTE_RESULTS,
    DepartureRecord,
    ErpDeadlinePassed,
    ErpHoldLimit,
    ErpHoldNotFound,
    ErpSoldOut,
    HoldRecord,
    RouteQuery,
    RouteRecord,
)

DATA_DIR = example_data_dir(__file__)
MAX_HOLDS_PER_SESSION = 3
SIBLING_WINDOW_DAYS = 14

# ``keyword_score`` tokenizes on ``[a-z0-9]+``, which finds nothing in a Chinese field, so
# ranking here matches the query's character 2-grams as substrings of each field instead.
_SEARCH_WEIGHTS = {
    "route_name": 3.0,
    "highlights": 2.5,
    "destination": 2.5,
    "fit_tags": 2.0,
    "summary": 1.0,
}
_SYNONYMS = {
    "纯玩": ("无购物",),
    "亲子": ("儿童", "家庭"),
    "小车": ("商务车",),
    "老人": ("轻松", "低强度", "老人友好"),
}
_SEPARATORS = re.compile(r"[\s,，、。·:：;；/\\_-]+")
_PRICES = ("adult_price", "child_price", "child_bed_price", "single_supplement")
_COMPUTED = frozenset({"min_adult_price_in_window", "has_seats_in_window", "party_quote_total"})
_ROUTE_KEYS = tuple(f.name for f in fields(RouteRecord) if f.name not in _COMPUTED)
_DEPARTURE_KEYS = tuple(f.name for f in fields(DepartureRecord) if f.name not in _COMPUTED)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _flatten(text: str) -> str:
    return _SEPARATORS.sub("", text.lower())


def _overlaps(query: str, value: str) -> bool:
    """Substring either way, so 新疆伊犁 still hits the 伊犁 routes."""
    flat_query, flat_value = _flatten(query), _flatten(value)
    return not flat_query or flat_query in flat_value or flat_value in flat_query


def _text_score(query: str, route: dict[str, Any]) -> float:
    """Each character 2-gram of the query (and the whole query) scores the weight of the
    best field it, or one of its synonyms, appears in."""
    flat = {
        "route_name": _flatten(route["route_name"]),
        "highlights": _flatten(" ".join(route.get("highlights", ()))),
        "destination": _flatten(route["destination"]),
        "fit_tags": _flatten(" ".join(route.get("fit_tags", ()))),
        "summary": _flatten(route["summary"]),
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


def _departure_id(route_id: str, depart_date: date) -> str:
    """``DP-<route digits>-<YYYYMMDD>``, rebuilt after the shift so ids match their date."""
    return f"DP-{route_id.split('-')[-1]}-{depart_date.strftime('%Y%m%d')}"


def _party_quote(row: dict[str, Any], adults: int, children: int) -> float:
    """Adults at the adult price, children at the 不占床 price; 占床 is not modelled."""
    return float(adults * row["adult_price"] + children * row["child_price"])


@dataclass(frozen=True)
class _Hold:
    """A live hold and who owns it; ``record`` is the part the caller sees."""

    record: HoldRecord
    advisor_id: str
    session_key: str

    @property
    def party(self) -> int:
        return self.record.adults + self.record.children


@dataclass(frozen=True)
class HoldNotification:
    """An expired hold, for the host to queue as an app event on the advisor's next turn."""

    hold_id: str
    departure_id: str
    advisor_id: str
    session_key: str
    kind: str
    message: str


class MockErpClient:
    """An ``ErpClient`` backed by ``data/routes.json`` and ``data/departures.json``."""

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
        self._routes: dict[str, dict[str, Any]] = {
            route["route_id"]: dict(route) for route in load_json(data_dir, "routes.json")["routes"]
        }
        self._departures: dict[str, dict[str, Any]] = {}
        for entry in raw["departures"]:
            row = dict(entry)
            for key in ("depart_date", "return_date", "booking_deadline"):
                row[key] = date.fromisoformat(row[key]) + shift
            for key in _PRICES:
                if row.get(key) is not None:
                    row[key] = float(row[key])
            row["departure_id"] = _departure_id(row["route_id"], row["depart_date"])
            self._departures[row["departure_id"]] = row
        self._holds: dict[str, _Hold] = {}
        self._notifications: list[HoldNotification] = []

    def sweep(self) -> None:
        """Drop holds past their TTL, put their seats back, and leave a notice each."""
        now = self._now()
        for hold in [h for h in self._holds.values() if h.record.expires_at <= now]:
            del self._holds[hold.record.hold_id]
            self._departures[hold.record.departure_id]["seats_left"] += hold.party
            self._notifications.append(
                HoldNotification(
                    hold_id=hold.record.hold_id,
                    departure_id=hold.record.departure_id,
                    advisor_id=hold.advisor_id,
                    session_key=hold.session_key,
                    kind="hold_expired",
                    message=f"占位已过期：{hold.record.departure_id}，座位已释放，需要重新占位。",
                )
            )

    def collect_notifications(self) -> list[HoldNotification]:
        """Drain the expiry notices for delivery."""
        self.sweep()
        pending, self._notifications = self._notifications, []
        return pending

    async def search_routes(self, q: RouteQuery) -> list[RouteRecord]:
        ranked: list[tuple[float, float, RouteRecord]] = []
        self.sweep()
        for route in self._routes.values():
            window = self._window(route["route_id"], q.depart_from, q.depart_to)
            if not window or not self._passes_filters(route, q):
                continue
            record = self._route_record(route, window, q.adults + q.children)
            price = record.min_adult_price_in_window
            if q.max_adult_price is not None and price > q.max_adult_price:
                continue
            ranked.append((-_text_score(q.destination, route), price, record))
        ranked.sort(key=lambda row: (row[0], row[1]))
        limit = DEFAULT_ROUTE_RESULTS if q.limit < 1 else min(q.limit, MAX_ROUTE_RESULTS)
        return [record for _score, _price, record in ranked[:limit]]

    async def list_departures(
        self,
        route_id: str,
        depart_from: date,
        depart_to: date,
        adults: int,
        children: int,
        child_ages: list[int],
    ) -> list[DepartureRecord]:
        """Every status, closed included: the backend shows those as sold out."""
        self.sweep()
        rows = [
            row
            for row in self._departures.values()
            if row["route_id"] == route_id and depart_from <= row["depart_date"] <= depart_to
        ]
        rows.sort(key=lambda row: row["depart_date"])
        return [self._departure_record(row, adults, children) for row in rows]

    async def get_departure(
        self, departure_id: str, adults: int, children: int, child_ages: list[int]
    ) -> DepartureRecord | None:
        self.sweep()
        row = self._departures.get(departure_id)
        return None if row is None else self._departure_record(row, adults, children)

    async def create_hold(
        self, departure_id: str, adults: int, children: int, advisor_id: str, session_key: str
    ) -> HoldRecord:
        self.sweep()
        row = self._departures.get(departure_id)
        if row is None:
            raise ErpHoldNotFound(f"找不到该团期：{departure_id}")
        if row["group_status"] == "closed":
            raise ErpDeadlinePassed(f"{departure_id} 已关闭，停止收客，无法占位。")
        if row["booking_deadline"] < self.today:
            deadline = row["booking_deadline"].isoformat()
            raise ErpDeadlinePassed(f"{departure_id} 已过报名截止日（{deadline}），无法占位。")
        held = [h for h in self._holds.values() if h.session_key == session_key]
        if any(h.record.departure_id == departure_id for h in held):
            raise ErpHoldLimit(f"{departure_id} 已经占位，无需重复占位。")
        if len(held) >= MAX_HOLDS_PER_SESSION:
            raise ErpHoldLimit(
                f"同一会话最多同时占位 {MAX_HOLDS_PER_SESSION} 个团期，请先释放其中一个。"
            )
        party = adults + children
        if row["seats_left"] < party:
            raise ErpSoldOut(
                f"{departure_id} 只剩 {row['seats_left']} 个位置，不够 {party} 人。",
                self._siblings(row, party),
            )
        row["seats_left"] -= party
        record = HoldRecord(
            hold_id=f"HOLD-{uuid.uuid4().hex[:8].upper()}",
            departure_id=departure_id,
            adults=adults,
            children=children,
            expires_at=self._now() + timedelta(minutes=row["hold_ttl_minutes"]),
            total_price=_party_quote(row, adults, children),
        )
        self._holds[record.hold_id] = _Hold(record, advisor_id, session_key)
        return record

    async def release_hold(self, hold_id: str, advisor_id: str) -> None:
        """A hold owned by another advisor reads as missing: the mock does not need to
        tell the two cases apart, and neither answer leaks the other advisor's work."""
        self.sweep()
        hold = self._holds.get(hold_id)
        if hold is None or hold.advisor_id != advisor_id:
            raise ErpHoldNotFound(f"找不到该占位记录（可能已过期）：{hold_id}")
        del self._holds[hold_id]
        self._departures[hold.record.departure_id]["seats_left"] += hold.party

    async def list_holds(self, advisor_id: str, session_key: str) -> list[HoldRecord]:
        self.sweep()
        mine = [
            h
            for h in self._holds.values()
            if h.advisor_id == advisor_id and h.session_key == session_key
        ]
        return [h.record for h in sorted(mine, key=lambda h: h.record.expires_at)]

    def _bookable(self, row: dict[str, Any]) -> bool:
        return row["group_status"] != "closed" and row["booking_deadline"] >= self.today

    def _window(
        self, route_id: str, depart_from: date | None, depart_to: date | None
    ) -> list[dict[str, Any]]:
        """The route's bookable departures inside the query's date window."""
        return [
            row
            for row in self._departures.values()
            if row["route_id"] == route_id
            and self._bookable(row)
            and (depart_from is None or row["depart_date"] >= depart_from)
            and (depart_to is None or row["depart_date"] <= depart_to)
        ]

    def _passes_filters(self, route: dict[str, Any], q: RouteQuery) -> bool:
        """Everything the advisor stated except price, which needs the window's minimum.
        ``departure_city`` rides along on the query but is not filtered on here."""
        if not _overlaps(q.destination, route["destination"]) and not _overlaps(
            q.destination, route["region"]
        ):
            return False
        if q.days_min is not None and route["days"] < q.days_min:
            return False
        if q.days_max is not None and route["days"] > q.days_max:
            return False
        if q.no_shopping and route["shopping_stops"] != 0:
            return False
        if q.children > 0 and q.child_ages and min(q.child_ages) < route["child_min_age"]:
            return False
        return not (q.hotel_level and q.hotel_level != route["hotel_level"])

    def _siblings(self, row: dict[str, Any], party: int) -> list[str]:
        """Same route, within ±14 days, still able to take the party; nearest date first."""
        depart = row["depart_date"]
        near = [
            other
            for other in self._departures.values()
            if other["route_id"] == row["route_id"]
            and other["departure_id"] != row["departure_id"]
            and abs((other["depart_date"] - depart).days) <= SIBLING_WINDOW_DAYS
            and self._bookable(other)
            and other["seats_left"] >= party
        ]
        near.sort(
            key=lambda other: (abs((other["depart_date"] - depart).days), other["depart_date"])
        )
        return [other["departure_id"] for other in near]

    def _route_record(
        self, route: dict[str, Any], window: list[dict[str, Any]], party: int
    ) -> RouteRecord:
        """The fixture's keys are the record's field names; the two window figures are
        computed here, and ``_COMPUTED`` holds every computed name out of the splat."""
        carried = {key: route[key] for key in _ROUTE_KEYS if key in route}
        carried["highlights"] = tuple(route.get("highlights", ()))
        carried["fit_tags"] = tuple(route.get("fit_tags", ()))
        return RouteRecord(
            **carried,
            min_adult_price_in_window=float(min(row["adult_price"] for row in window)),
            has_seats_in_window=any(row["seats_left"] >= max(1, party) for row in window),
        )

    def _departure_record(self, row: dict[str, Any], adults: int, children: int) -> DepartureRecord:
        carried = {key: row[key] for key in _DEPARTURE_KEYS if key in row}
        return DepartureRecord(**carried, party_quote_total=_party_quote(row, adults, children))
