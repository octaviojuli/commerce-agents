# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``ErpClient`` over the B2B 旅行社 ERP's own HTTP API (``docs/erp-contract.md``): the eight
calls as requests, the ERP's JSON as the records in ``erp_client.py``, and its HTTP statuses
as the exceptions the executor relays. Field mapping and error mapping only — no seat
arithmetic, no ranking, no status derivation; those belong to the ERP or to the backend above
it. The client logs itself in on the first call and caches the token for its eight hours; a
401, or a token about to expire, buys exactly one fresh login. The password and the token are
never logged, and neither ever reaches the model."""

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
AUTH_FAILED = "旅行社 ERP 登录失败，请检查账号、密码与所属部门。"
THROTTLED = "旅行社 ERP 登录过于频繁，请稍后再试。"
NOT_FOUND = "旅行社 ERP 中找不到该记录。"
REFUSED = "旅行社 ERP 不接受这笔操作。"
EPOCH = datetime.fromtimestamp(0, UTC)

R = TypeVar("R")

# A record field whose ERP key is not its own name in camelCase. Both period endpoints share
# one mapping and both order endpoints another, so a key named here means the same everywhere.
_KEYS = {
    "adults": "adultCount",
    "attachment_name": "routeAttachmentName",
    "attachment_url": "routeAttachmentUrl",
    "children": "childCount",
    "code": "csCode",
    "created_at": "createTime",
    "customer_type": "companyType",
    "depart_city": "departCityName",
    "elders": "elderCount",
    "image_url": "firstImageUrl",
    "is_external": "isExternalOrder",
    "name": "companyName",
    "price": "priceInfo",
    "reserve_expires_at": "reserveExpireAt",
    "rooms": "roomCount",
    "status": "orderStatus",
    "status_text": "orderStatusText",
}
_ZEROS: dict[str, Any] = {
    "int": 0,
    "float": 0.0,
    "str": "",
    "bool": False,
    "tuple[str, ...]": (),
    "date": EPOCH.date(),
    "datetime": EPOCH,
    "PriceInfo": erp.PriceInfo(0.0, 0.0, 0.0, 0.0),
}


def _raise(status: int, message: str) -> NoReturn:
    """The ERP's status is its error class; its ``message`` is advisor-facing Chinese and is
    kept as written whenever it has one."""
    if status == 400:
        raise erp.ErpRefused(message or REFUSED)
    if status in (401, 403):
        raise erp.ErpAuth(message or AUTH_FAILED)
    if status == 404:
        raise erp.ErpNotFound(message or NOT_FOUND)
    if status == 429:
        raise erp.ErpThrottled(message or THROTTLED)
    if status >= 500:
        raise erp.ErpUnavailable(UNAVAILABLE)
    raise erp.ErpError(message or UNAVAILABLE)


def _body(response: httpx.Response) -> dict[str, Any]:
    """A body that is not a JSON object is an outage, not a refusal: the ERP is answering
    with something this client cannot read at all."""
    try:
        payload = response.json() if response.content else {}
    except ValueError as exc:
        raise erp.ErpUnavailable(UNAVAILABLE) from exc
    if not isinstance(payload, dict):
        raise erp.ErpUnavailable(UNAVAILABLE)
    return payload


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """A filter the caller did not state is left off the request entirely."""
    return {key: value for key, value in params.items() if value is not None and value != ""}


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _key(name: str) -> str:
    """The ERP's key for a record field: ``_KEYS`` where it renames one, camelCase otherwise."""
    head, *rest = name.split("_")
    return _KEYS.get(name) or head + "".join(word.title() for word in rest)


def _stamp(text: Any) -> datetime | None:
    """``2026-09-07 10:30:00`` and the ISO form alike; anything else reads as absent."""
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _price(row: Any) -> erp.PriceInfo:
    return erp.PriceInfo(
        adult=float(row.get("adultPrice") or 0),
        child=float(row.get("childPrice") or 0),
        elder=float(row.get("elderPrice") or 0),
        single_room_diff=float(row.get("singleRoomDiff") or 0),
        currency=str(row.get("currency") or "CNY"),
    )


def _value(kind: str, value: Any) -> Any:
    """One JSON value as the record field's annotated type. A field the ERP left out — a
    detail-only count on a list row — is ``None`` where the type allows it, its zero where not."""
    if value is None or value == "":
        return None if kind.endswith("| None") else _ZEROS[kind]
    if kind.startswith("PriceInfo"):
        return _price(value)
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
    """The dataclass's own fields drive the mapping: each field names its ERP key and, in its
    annotation, how the value is read."""
    return cls(**{f.name: _value(str(f.type), row.get(_key(f.name))) for f in fields(cls)})


class HttpErpClient:
    """The eight ``ErpClient`` calls against ``base_url`` (the ERP's ``/aicli`` root), as the
    salesperson ``mobile`` names inside ``company_id``; ``transport`` is the tests' mock hook."""

    def __init__(
        self,
        base_url: str,
        mobile: str,
        password: str,
        company_id: int,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(READ_TIMEOUT), transport=transport
        )
        self._credentials = {"mobile": mobile, "password": password, "companyId": company_id}
        self._token = ""
        self._expires_at = 0.0
        # userId, userName, companyId and companyName of the logged-in salesperson; the backend
        # writes that name onto an order as its contact.
        self.user_info: dict[str, Any] = {}

    async def _login(self) -> str:
        """``companyId`` rides along because an account without a default department cannot log
        in without it. A refusal here is about the credentials, so a 400 reads as a 401."""
        try:
            response = await self._client.post(
                "/login", json=self._credentials, timeout=WRITE_TIMEOUT
            )
        except httpx.TransportError as exc:
            raise erp.ErpUnavailable(UNAVAILABLE) from exc
        payload = _body(response)
        status = int(payload.get("code") or response.status_code)
        if not response.is_success or status != 200:
            _raise(401 if status == 400 else status, str(payload.get("message") or ""))
        data = payload.get("data") or {}
        self._token = str(data.get("token") or "")
        if not self._token:
            raise erp.ErpAuth(AUTH_FAILED)
        expires_in = float(data.get("expiresIn") or 0)
        self._expires_at = float(data.get("expiresAt") or 0) or time.time() + expires_in
        self.user_info = dict(data.get("userInfo") or {})
        return self._token

    async def _authorize(self) -> str:
        if self._token and time.time() < self._expires_at - TOKEN_SKEW:
            return self._token
        return await self._login()

    async def _request(
        self, method: str, path: str, data: dict[str, Any], timeout: float = READ_TIMEOUT
    ) -> Any:
        """One call, mapped: a GET sends ``data`` as the query string and the one POST sends it
        as the body. A stale token buys one fresh login and one retry; a 401 after that is the
        account's problem, not the session's."""
        for attempt in (1, 2):
            token = await self._authorize()
            sent = {"params": data} if method == "GET" else {"json": data}
            try:
                response = await self._client.request(
                    method,
                    path,
                    timeout=timeout,
                    headers={"Authorization": f"Bearer {token}"},
                    **sent,
                )
            except httpx.TransportError as exc:
                raise erp.ErpUnavailable(UNAVAILABLE) from exc
            if response.status_code == 401 and attempt == 1:
                self._token = ""
                continue
            # A 404 with no JSON body at all is still a missing record: beta answers a bare
            # nginx page for a departure the catalog has no price row for.
            if response.status_code == 404 and not response.content.lstrip().startswith(b"{"):
                _raise(404, "")
            payload = _body(response)
            status = int(payload.get("code") or response.status_code)
            if response.is_success and status == 200:
                return payload.get("data")
            _raise(status, str(payload.get("message") or ""))

    async def _pages(self, path: str, params: dict[str, Any], size: int) -> list[dict[str, Any]]:
        """At most ``MAX_PAGES`` pages: deeper than that is a search to narrow, not to page."""
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            paged = {**params, "pageNum": page, "pageSize": size}
            data = await self._request("GET", path, paged) or {}
            rows.extend(data.get("list") or [])
            if page >= int(data.get("totalPages") or 1):
                break
        return rows

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
        another route whose name shares a word comes back too, and only this one stays."""
        window = {"departDateStart": _iso(depart_from), "departDateEnd": _iso(depart_to)}
        params = _clean({"routeName": route_name, **window})
        rows = await self._pages("/period/list", params, PAGE_SIZE)
        mine = [row for row in rows if int(row.get("routeId") or 0) == route_id]
        return [_record(erp.DepartureRecord, row) for row in mine]

    async def get_departure(self, period_id: int) -> erp.DepartureRecord | None:
        try:
            data = await self._request("GET", "/period/detail", {"periodId": period_id})
        except erp.ErpNotFound:
            return None
        return _record(erp.DepartureRecord, data) if data else None

    async def search_customers(self, keyword: str) -> list[erp.CustomerRecord]:
        params = _clean({"keyword": keyword, "pageNum": 1, "pageSize": PAGE_SIZE})
        data = await self._request("GET", "/customer/list", params) or {}
        return [_record(erp.CustomerRecord, row) for row in data.get("list") or ()]

    async def quote(self, period_id: int, customer_id: int) -> erp.Quote:
        params = {"periodId": period_id, "customerId": customer_id}
        return _record(erp.Quote, await self._request("GET", "/order/price", params) or {})

    async def create_order(self, req: erp.OrderRequest) -> erp.OrderResult:
        """The one write. There is no idempotency key on this endpoint, so a caller that times
        out lists orders before deciding anything and never retries blindly. ``storeName`` and
        ``remark`` go on the body only when the caller stated them."""
        body = _clean({_key(f.name): getattr(req, f.name) for f in fields(req)})
        data = await self._request("POST", "/order/create", body, WRITE_TIMEOUT) or {}
        return _record(erp.OrderResult, data)

    async def list_orders(self, statuses: tuple[int, ...] = ()) -> list[erp.OrderRecord]:
        """One status goes on the query; several are kept from the page that comes back."""
        one = statuses[0] if len(statuses) == 1 else None
        params = _clean({"orderStatus": one, "pageNum": 1, "pageSize": PAGE_SIZE})
        data = await self._request("GET", "/order/list", params) or {}
        rows = [_record(erp.OrderRecord, row) for row in data.get("list") or ()]
        return [row for row in rows if not statuses or row.status in statuses]

    async def get_order(self, order_id: int) -> erp.OrderRecord | None:
        try:
            data = await self._request("GET", "/order/detail", {"orderId": order_id})
        except erp.ErpNotFound:
            return None
        return _record(erp.OrderRecord, data) if data else None
