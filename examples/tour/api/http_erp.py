# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``ErpClient`` over the B2B 旅行社 ERP's own HTTP API (``docs/erp-contract.md``): the eight
calls as requests, the ERP's JSON as the records in ``erp_client.py``, and its HTTP statuses as
the exceptions the executor relays. Field mapping and error mapping only — no seat arithmetic,
no ranking, no status derivation; those belong to the ERP or to the backend above it.

A login takes the mobile and the password alone and lands in the account's first authorised
department; the reads span all of them. The two write-side calls are department-bound, so the
client holds one token per department: the login token for its own, and a switched one, kept
until it expires, for each other. No password or token is logged, or ever reaches the model."""

from __future__ import annotations

import time
from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any, NoReturn, TypeVar

import httpx

from . import erp_client as erp

READ_TIMEOUT = 3.0
WRITE_TIMEOUT = 5.0
PAGE_SIZE = 50
MAX_PAGES = 3
TOKEN_SKEW = 60.0

UNAVAILABLE = "旅行社 ERP 暂时无法连接，请稍后再试。"
AUTH_FAILED = "旅行社 ERP 登录失败，请检查账号与密码。"
THROTTLED = "旅行社 ERP 登录过于频繁，请稍后再试。"
NOT_FOUND = "旅行社 ERP 中找不到该记录。"
REFUSED = "旅行社 ERP 不接受这笔操作。"
EPOCH = datetime.fromtimestamp(0, UTC)

R = TypeVar("R")

# A record field whose ERP key is not its own name in camelCase; both period endpoints share one
# mapping and both order endpoints another. The order list calls the total ``orderAmount``.
_KEYS: dict[str, str | tuple[str, ...]] = {
    "adults": "adultCount", "children": "childCount", "elders": "elderCount",
    "rooms": "roomCount", "code": "csCode", "price": "priceInfo",
    "attachment_name": "routeAttachmentName", "attachment_url": "routeAttachmentUrl",
    "created_at": "createTime", "reserve_expires_at": "reserveExpireAt",
    "customer_type": "companyType", "name": "companyName", "depart_city": "departCityName",
    "image_url": "firstImageUrl", "is_external": "isExternalOrder",
    "status": "orderStatus", "status_text": "orderStatusText",
    "total_amount": ("totalAmount", "orderAmount"),
}  # fmt: skip
_PRICE_KEYS = ("adultPrice", "childPrice", "elderPrice", "singleRoomDiff")
# What a field whose type states no ``None`` becomes when the ERP leaves it out of a row.
_ZEROS: dict[str, Any] = {
    "int": 0, "float": 0.0, "str": "", "bool": False, "tuple[str, ...]": (),
    "date": EPOCH.date(), "datetime": EPOCH, "PriceInfo": erp.PriceInfo(0.0, 0.0, 0.0, 0.0),
}  # fmt: skip
# The ERP's status is its error class, its Chinese ``message`` is kept as written, and a 5xx is
# an outage; anything unlisted below 500 is the seam's own error.
_ERRORS: dict[int, tuple[type[erp.ErpError], str]] = {
    400: (erp.ErpRefused, REFUSED), 401: (erp.ErpAuth, AUTH_FAILED),
    403: (erp.ErpAuth, AUTH_FAILED), 404: (erp.ErpNotFound, NOT_FOUND),
    429: (erp.ErpThrottled, THROTTLED),
}  # fmt: skip


def _raise(status: int, message: str) -> NoReturn:
    if status >= 500:
        raise erp.ErpUnavailable(UNAVAILABLE)
    cls, fallback = _ERRORS.get(status, (erp.ErpError, UNAVAILABLE))
    raise cls(message or fallback)


def _data(response: httpx.Response, *, credentials: bool = False) -> Any:
    """The envelope's ``data``, or the ERP's status and message as the exception for it. A body
    that is not a JSON object is an outage; a 400 on the login is about the credentials."""
    try:
        payload = response.json() if response.content else {}
    except ValueError as exc:
        raise erp.ErpUnavailable(UNAVAILABLE) from exc
    if not isinstance(payload, dict):
        raise erp.ErpUnavailable(UNAVAILABLE)
    status = int(payload.get("code") or response.status_code)
    if response.is_success and status == 200:
        return payload.get("data")
    _raise(401 if credentials and status == 400 else status, str(payload.get("message") or ""))


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """A filter the caller did not state is left off the request entirely."""
    return {key: value for key, value in params.items() if value is not None and value != ""}


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _keys(name: str) -> tuple[str, ...]:
    """The ERP's keys for a record field, first match wins: ``_KEYS``, else camelCase."""
    head, *rest = name.split("_")
    named = _KEYS.get(name) or head + "".join(word.title() for word in rest)
    return named if isinstance(named, tuple) else (named,)


def _stamp(text: Any) -> datetime | None:
    """Unix seconds, ``2026-09-07 10:30:00``, and the ISO form alike; ``0`` reads as absent."""
    if isinstance(text, (int, float)) or str(text).isdigit():
        seconds = int(text)
        return datetime.fromtimestamp(seconds, tz=UTC) if seconds > 0 else None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _bearer(data: dict[str, Any]) -> tuple[str, float]:
    """The token and its expiry out of a login or a switch; the ERP dates it both ways."""
    token, lifetime = str(data.get("token") or ""), float(data.get("expiresIn") or 0)
    if not token:
        raise erp.ErpAuth(AUTH_FAILED)
    return token, float(data.get("expiresAt") or 0) or time.time() + lifetime


def _value(kind: str, value: Any) -> Any:
    """One JSON value as the field's annotated type; one the ERP left out — a detail-only count
    on a list row — is ``None`` where the type allows it, its zero where not."""
    if value is None or value == "":
        return None if kind.endswith("| None") else _ZEROS[kind]
    if kind.startswith("PriceInfo"):
        amounts = (float(value.get(key) or 0) for key in _PRICE_KEYS)
        return erp.PriceInfo(*amounts, str(value.get("currency") or "CNY"))
    if kind.startswith("datetime"):
        return _stamp(value) or _value(kind, None)
    if kind.startswith("date"):
        return date.fromisoformat(str(value)[:10])
    if kind.startswith("tuple"):
        return tuple(str(item) for item in value)
    if kind.startswith("bool"):
        return bool(value)
    return {"int": int, "float": float, "str": str}[kind.split(" ")[0]](value)


def _record(cls: type[R], row: dict[str, Any]) -> R:
    """The dataclass's own fields drive the mapping: each names its ERP key and, in its
    annotation, how the value is read."""

    def read(name: str) -> Any:
        return next((row[key] for key in _keys(name) if key in row), None)

    return cls(**{f.name: _value(str(f.type), read(f.name)) for f in fields(cls)})


class HttpErpClient:
    """The eight ``ErpClient`` calls against ``base_url`` (the ERP's ``/aicli`` root), as the
    salesperson ``mobile`` names. The ERP picks the department the login lands in, and a
    write-side call switches into the 团期's. ``transport`` is the tests' mock hook."""

    def __init__(
        self,
        base_url: str,
        mobile: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(READ_TIMEOUT), transport=transport
        )
        self._credentials = {"mobile": mobile, "password": password}
        self._token = ""
        self._expires_at = 0.0
        # One (token, expiry) per department switched into; the login department is not in it,
        # because the login token is already that department's. ``user_info`` is the userId,
        # userName, companyId and companyName of the logged-in salesperson — the backend writes
        # that name onto an order — and ``companies`` is the span of this account's reads.
        self._switched: dict[int, tuple[str, float]] = {}
        self.user_info: dict[str, Any] = {}
        self.companies: list[dict[str, Any]] = []

    async def _send(
        self, method: str, path: str, data: dict[str, Any], timeout: float, token: str
    ) -> httpx.Response:
        """A GET sends ``data`` as the query, a POST as the body; a dead wire is an outage."""
        sent = {"params": data} if method == "GET" else {"json": data}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            return await self._client.request(
                method, path, timeout=timeout, headers=headers, **sent
            )
        except httpx.TransportError as exc:
            raise erp.ErpUnavailable(UNAVAILABLE) from exc

    async def _login(self) -> str:
        """Mobile and password alone; the ERP names the department it chose and all of them."""
        response = await self._send("POST", "/login", self._credentials, WRITE_TIMEOUT, "")
        data = _data(response, credentials=True) or {}
        self._token, self._expires_at = _bearer(data)
        self.user_info = dict(data.get("userInfo") or {})
        self.companies = list(data.get("companies") or ())
        return self._token

    async def _token_for(self, company_id: int | None) -> str:
        """The bearer one call goes out under: the login token for a read and for its own
        department, and for any other department its own, switched into once and kept."""
        token = self._token
        if not token or time.time() >= self._expires_at - TOKEN_SKEW:
            token = await self._login()
        if not company_id or company_id == int(self.user_info.get("companyId") or 0):
            return token
        held = self._switched.get(company_id)
        if held and time.time() < held[1] - TOKEN_SKEW:
            return held[0]
        switched = await self._request("POST", "/switch-company", {"companyId": company_id}) or {}
        self._switched[company_id] = _bearer(switched)
        return self._switched[company_id][0]

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any],
        timeout: float = READ_TIMEOUT,
        company_id: int | None = None,
    ) -> Any:
        """One call under the token ``company_id`` picks. A stale token buys one fresh login or
        switch and one retry; a 401 after that is the account's problem, not the session's."""
        for attempt in (1, 2):
            token = await self._token_for(company_id)
            response = await self._send(method, path, data, timeout, token)
            if response.status_code == 401 and attempt == 1:
                # The token it came back to is spent: a switched one is dropped, anything else
                # was the login token; either way the next attempt buys a fresh one.
                if self._switched.pop(company_id or 0, None) is None:
                    self._token = ""
                continue
            # A 404 with no JSON body is still a missing record: beta answers a bare nginx page
            # for a price the catalog lacks, and for a write made in the wrong department.
            if response.status_code == 404 and not response.content.lstrip().startswith(b"{"):
                _raise(404, "")
            return _data(response)

    async def _pages(self, path: str, params: dict[str, Any], size: int) -> list[dict[str, Any]]:
        """At most ``MAX_PAGES``: deeper than that is a search to narrow, not to page."""
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            paged = {**params, "pageNum": page, "pageSize": size}
            data = await self._request("GET", path, paged) or {}
            rows.extend(data.get("list") or [])
            if page >= int(data.get("totalPages") or 1):
                break
        return rows

    async def _optional(self, cls: type[R], path: str, params: dict[str, Any]) -> R | None:
        """A record the ERP may not have: its 404 is no record rather than an error."""
        try:
            data = await self._request("GET", path, params)
        except erp.ErpNotFound:
            return None
        return _record(cls, data) if data else None

    async def search_routes(self, q: erp.RouteQuery) -> list[erp.RouteRecord]:
        window = {"departDateStart": _iso(q.depart_from), "departDateEnd": _iso(q.depart_to)}
        params = _clean({"routeName": q.route_name, "routeCode": q.route_code, **window})
        size = min(q.page_size, PAGE_SIZE) if q.page_size > 0 else PAGE_SIZE
        rows = await self._pages("/route/list", params, size)
        return [_record(erp.RouteRecord, row) for row in rows]

    async def list_departures(
        self, route_id: int, route_name: str, depart_from: date | None, depart_to: date | None
    ) -> list[erp.DepartureRecord]:
        """The period list has no ``routeId`` filter, so the name fetches and the id keeps:
        another route whose name shares a word comes back too, and only this one stays. A period
        carries the route name it was made under, so a renamed route finds nothing by name; the
        window alone is fetched then."""
        window = {"departDateStart": _iso(depart_from), "departDateEnd": _iso(depart_to)}
        rows = await self._pages(
            "/period/list", _clean({"routeName": route_name, **window}), PAGE_SIZE
        )
        mine = [row for row in rows if int(row.get("routeId") or 0) == route_id]
        if not mine and route_name:
            rows = await self._pages("/period/list", _clean(window), PAGE_SIZE)
            mine = [row for row in rows if int(row.get("routeId") or 0) == route_id]
        return [_record(erp.DepartureRecord, row) for row in mine]

    async def get_departure(self, period_id: int) -> erp.DepartureRecord | None:
        return await self._optional(erp.DepartureRecord, "/period/detail", {"periodId": period_id})

    async def search_customers(self, keyword: str) -> list[erp.CustomerRecord]:
        params = _clean({"keyword": keyword, "pageNum": 1, "pageSize": PAGE_SIZE})
        data = await self._request("GET", "/customer/list", params) or {}
        return [_record(erp.CustomerRecord, row) for row in data.get("list") or ()]

    async def quote(self, period_id: int, customer_id: int, company_id: int) -> erp.Quote:
        """The 同业价, department-bound: under any department but the 团期's own the ERP answers
        a bare nginx 404, so the call goes out on that department's token."""
        params = {"periodId": period_id, "customerId": customer_id}
        data = await self._request("GET", "/order/price", params, READ_TIMEOUT, company_id)
        return _record(erp.Quote, data or {})

    async def create_order(self, req: erp.OrderRequest) -> erp.OrderResult:
        """The one write, on the token switched to ``req.company_id``: the department is the
        token's and not the body's, so it is the one field of the request that stays off the
        wire. There is no idempotency key, so a caller that times out lists orders first."""
        named = (f.name for f in fields(req) if f.name != "company_id")
        body = _clean({_keys(name)[0]: getattr(req, name) for name in named})
        data = await self._request("POST", "/order/create", body, WRITE_TIMEOUT, req.company_id)
        return _record(erp.OrderResult, data or {})

    async def list_orders(self, statuses: tuple[int, ...] = ()) -> list[erp.OrderRecord]:
        """The login department's own orders: the endpoint answers for the token's department
        alone, so an order written in another is not listed — a known gap. One status goes on the
        query; several are kept from the page."""
        one = statuses[0] if len(statuses) == 1 else None
        params = _clean({"orderStatus": one, "pageNum": 1, "pageSize": PAGE_SIZE})
        data = await self._request("GET", "/order/list", params) or {}
        rows = [_record(erp.OrderRecord, row) for row in data.get("list") or ()]
        return [row for row in rows if not statuses or row.status in statuses]

    async def get_order(self, order_id: int) -> erp.OrderRecord | None:
        return await self._optional(erp.OrderRecord, "/order/detail", {"orderId": order_id})
