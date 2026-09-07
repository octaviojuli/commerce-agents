# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``ErpClient`` over a real 旅行社 ERP's HTTP API: the six calls as JSON requests, the
JSON back as the records in ``erp_client.py``, and the ERP's error codes as the exceptions
the executor already relays. Field mapping and error mapping only: the ranking, the window,
the sold-out siblings, and the hold rules stay in the ERP. The bearer token comes from the
host's environment, never from the model, and a hold carries an ``Idempotency-Key`` over
the party that asked for it, so a retried request cannot take the seats twice."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import MISSING, fields
from datetime import UTC, date, datetime
from typing import Any, TypeVar

import httpx

from .erp_client import (
    DepartureRecord,
    ErpDeadlinePassed,
    ErpError,
    ErpHoldLimit,
    ErpHoldNotFound,
    ErpSoldOut,
    ErpUnavailable,
    HoldRecord,
    RouteQuery,
    RouteRecord,
)

logger = logging.getLogger(__name__)

READ_TIMEOUT = 3.0
HOLD_TIMEOUT = 5.0
DEPARTURE_LIMIT = 24
UNAVAILABLE = "旅行社 ERP 暂时无法连接，请稍后再试。"

# The codes the ERP refuses with. ``SOLD_OUT`` is raised ahead of this lookup, because it
# carries the sibling departures; an unlisted code stays a plain ``ErpError``.
_ERRORS: dict[str, type[ErpError]] = {
    "DEADLINE_PASSED": ErpDeadlinePassed,
    "HOLD_LIMIT": ErpHoldLimit,
    "DEPARTURE_NOT_FOUND": ErpHoldNotFound,
    "HOLD_NOT_FOUND": ErpHoldNotFound,
    "NOT_OWNER": ErpHoldNotFound,
}

Record = TypeVar("Record", RouteRecord, DepartureRecord, HoldRecord)


def _aware(text: str) -> datetime:
    """An ISO instant; a naive one is read as UTC, the clock the host counts TTLs on."""
    stamp = datetime.fromisoformat(text)
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


# The record fields JSON does not carry in their final shape: ISO strings become dates and
# instants, lists become tuples, amounts in 元 become floats however the ERP wrote them.
_DATES = ("depart_date", "return_date", "booking_deadline")
_NUMBERS = (
    "adult_price",
    "child_price",
    "child_bed_price",
    "single_supplement",
    "party_quote_total",
    "total_price",
    "min_adult_price_in_window",
    "max_drive_hours_per_day",
)
_COERCE: dict[str, Callable[[Any], Any]] = (
    dict.fromkeys(_DATES, date.fromisoformat)
    | dict.fromkeys(_NUMBERS, float)
    | {"expires_at": _aware, "highlights": tuple, "fit_tags": tuple}
)


def _record(cls: type[Record], row: dict[str, Any]) -> Record:
    """The dataclass's own fields drive the mapping: a field without a default is required,
    and a row missing one raises ``KeyError``; an optional field that is absent or null
    keeps the record's default."""
    built: dict[str, Any] = {}
    for spec in fields(cls):
        value = row.get(spec.name, MISSING)
        if value is MISSING or value is None:
            if spec.default is MISSING:
                raise KeyError(spec.name)
            continue
        coerce = _COERCE.get(spec.name)
        built[spec.name] = coerce(value) if coerce else value
    return cls(**built)


def _body(response: httpx.Response) -> dict[str, Any]:
    """A body that is not a JSON object is an outage, not a refusal: the ERP is answering
    with something this client cannot read at all."""
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise ErpUnavailable(UNAVAILABLE) from exc
    if not isinstance(payload, dict):
        raise ErpUnavailable(UNAVAILABLE)
    return payload


class HttpErpClient:
    """The six ``ErpClient`` calls against ``base_url``. One ``httpx.AsyncClient`` is built
    here and reused: reads get ``READ_TIMEOUT``, the two hold writes ``HOLD_TIMEOUT``.
    ``transport`` is where the tests hang an ``httpx.MockTransport``."""

    def __init__(
        self, base_url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(READ_TIMEOUT),
            transport=transport,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = READ_TIMEOUT,
        absent: str | None = None,
    ) -> dict[str, Any] | None:
        """One call, mapped. A transport failure or a 5xx is ``ErpUnavailable``; a 4xx is
        the ERP's own refusal, named by its ``code`` and worded by its Chinese ``message``.
        A 404 carrying ``absent`` is not a refusal but an answer of ``None``."""
        try:
            response = await self._client.request(
                method, path, params=params, json=json, headers=headers, timeout=timeout
            )
        except httpx.TransportError as exc:  # timeouts and refused connections alike
            raise ErpUnavailable(UNAVAILABLE) from exc
        if response.status_code >= 500:
            raise ErpUnavailable(UNAVAILABLE)
        payload = _body(response)
        if response.is_success:
            return payload
        code = str(payload.get("code") or "")
        message = str(payload.get("message") or UNAVAILABLE)
        if absent is not None and response.status_code == 404 and code == absent:
            return None
        if code == "SOLD_OUT":
            raise ErpSoldOut(message, [str(i) for i in payload.get("sibling_departure_ids") or ()])
        raise _ERRORS.get(code, ErpError)(message)

    async def search_routes(self, q: RouteQuery) -> list[RouteRecord]:
        """Every filter the advisor stated goes on the body; the ERP applies them and ranks
        what comes back. A route missing a required field is dropped with a warning rather
        than raised, so one bad row in the supplier's catalog does not cost the search."""
        body: dict[str, Any] = {
            "destination": q.destination,
            "adults": q.adults,
            "children": q.children,
            "limit": q.limit,
        }
        stated = {
            "depart_from": q.depart_from.isoformat() if q.depart_from else None,
            "depart_to": q.depart_to.isoformat() if q.depart_to else None,
            "days_min": q.days_min,
            "days_max": q.days_max,
            "child_ages": list(q.child_ages) or None,
            "departure_city": q.departure_city,
            "no_shopping": q.no_shopping or None,
            "hotel_level": q.hotel_level,
            "max_adult_price": q.max_adult_price,
        }
        body.update({key: value for key, value in stated.items() if value is not None})
        payload = await self._request("POST", "/routes/search", json=body) or {}
        routes: list[RouteRecord] = []
        for row in payload.get("routes") or ():
            try:
                routes.append(_record(RouteRecord, row))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.warning("ERP route dropped, unusable field: %s", exc)
        return routes

    async def list_departures(
        self,
        route_id: str,
        depart_from: date,
        depart_to: date,
        adults: int,
        children: int,
        child_ages: list[int],
    ) -> list[DepartureRecord]:
        params = self._party(adults, children, child_ages) | {
            "depart_from": depart_from.isoformat(),
            "depart_to": depart_to.isoformat(),
            "limit": DEPARTURE_LIMIT,
        }
        payload = await self._request("GET", f"/routes/{route_id}/departures", params=params) or {}
        return [_record(DepartureRecord, row) for row in payload.get("departures") or ()]

    async def get_departure(
        self, departure_id: str, adults: int, children: int, child_ages: list[int]
    ) -> DepartureRecord | None:
        payload = await self._request(
            "GET",
            f"/departures/{departure_id}",
            params=self._party(adults, children, child_ages),
            absent="DEPARTURE_NOT_FOUND",
        )
        return None if payload is None else _record(DepartureRecord, payload)

    async def create_hold(
        self, departure_id: str, adults: int, children: int, advisor_id: str, session_key: str
    ) -> HoldRecord:
        """``child_ages`` is not on the ``ErpClient`` signature, so the body's optional
        field is left off; the ERP priced the party on the quote calls that came before."""
        key = f"{session_key}{departure_id}{adults}{children}"
        payload = await self._request(
            "POST",
            "/holds",
            json={
                "departure_id": departure_id,
                "adults": adults,
                "children": children,
                "advisor_id": advisor_id,
                "session_key": session_key,
            },
            headers={"Idempotency-Key": hashlib.sha256(key.encode("utf-8")).hexdigest()},
            timeout=HOLD_TIMEOUT,
        )
        return _record(HoldRecord, payload or {})

    async def release_hold(self, hold_id: str, advisor_id: str) -> None:
        """Another advisor's hold answers 403 and a gone one 404; both are
        ``ErpHoldNotFound``, so neither answer tells this advisor about the other's work."""
        await self._request(
            "DELETE",
            f"/holds/{hold_id}",
            headers={"X-Advisor-Id": advisor_id},
            timeout=HOLD_TIMEOUT,
        )

    async def list_holds(self, advisor_id: str, session_key: str) -> list[HoldRecord]:
        params = {"advisor_id": advisor_id, "session_key": session_key}
        payload = await self._request("GET", "/holds", params=params) or {}
        return [_record(HoldRecord, row) for row in payload.get("holds") or ()]

    @staticmethod
    def _party(adults: int, children: int, child_ages: list[int]) -> dict[str, Any]:
        """The party a quote is made for; the ages ride the query string comma-joined."""
        params: dict[str, Any] = {"adults": adults, "children": children}
        if child_ages:
            params["child_ages"] = ",".join(str(age) for age in child_ages)
        return params
