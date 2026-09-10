# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``ErpClient`` over the B2B 旅行社 ERP's own HTTP API (``docs/erp-contract.md``): the nine
calls as requests — and the window read that spares a search a call per 线路 — the ERP's JSON
as the records in ``erp_client.py``, and its HTTP statuses as the exceptions the executor
relays. Field mapping and error mapping only — no seat arithmetic, no ranking, no status
derivation; those belong to the ERP or to the backend above it.

A login takes the mobile and the password alone and lands in the account's first authorised
department; the reads span all of them. The two write-side calls are department-bound, so the
client holds one token per department: the login token for its own, and a switched one, kept
until it expires, for each other. One lock guards the login and one guards each department's
switch, so a fan-out of concurrent calls on a cold client buys one token apiece instead of one
per caller. No password or token is logged, or ever reaches the model.

There are two ways to build one, and the difference is what it holds. ``from_login`` holds the
mobile and the password, so it logs in on its first call and again whenever the token it has is
spent. ``with_token`` holds a token a login already bought and no password at all, which is what
a host keeping one client per logged-in advisor builds (``advisors.py``): it buys a
``switch-company`` token the same way, because a switch is bought with the token, and once the
token's own eight hours are up it says so — ``ErpAuth(EXPIRED)`` — rather than logging in again
behind the advisor."""

from __future__ import annotations

import asyncio
import time
from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any, NoReturn, TypeVar

import httpx

from . import erp_client as erp

READ_TIMEOUT = 3.0
# A paged listing read: ``period/list`` over a window alone answers in seconds per page where
# every other read answers in well under one, and a page that times out is retried once.
LIST_TIMEOUT = 8.0
WRITE_TIMEOUT = 5.0
PAGE_SIZE = 50
# The catalog is paged to exhaustion — production carries some 270 线路, 160 of them inside a
# plain 60-day window, and 190 团期 in that window — with a ceiling that keeps a runaway
# ``totalPages`` from paging for ever.
MAX_PAGES = 20
# How many pages after the first are read at once: the first names ``totalPages``, and the
# rest are one round trip each and independent of one another.
PAGE_CONCURRENCY = 4
# How long one window's whole 团期 read is reused, so a search's fan-out and the details read
# that follows it share one call. Seat counts on a listed row age within it; a quoted
# departure's own detail call is what the numbers the advisor reads come from.
WINDOW_TTL = 120.0
TOKEN_SKEW = 60.0

UNAVAILABLE = "旅行社 ERP 暂时无法连接，请稍后再试。"
AUTH_FAILED = "旅行社 ERP 登录失败，请检查账号与密码。"
THROTTLED = "旅行社 ERP 登录过于频繁，请稍后再试。"
EXPIRED = "登录已过期，请重新登录"
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
    "price_tags": "periodPriceTags",
    "created_at": "createTime", "reserve_expires_at": "reserveExpireAt",
    "customer_type": "companyType", "name": "companyName", "depart_city": "departCityName",
    "image_url": "firstImageUrl", "is_external": "isExternalOrder",
    "status": "orderStatus", "status_text": "orderStatusText",
    "total_amount": ("totalAmount", "orderAmount"),
}  # fmt: skip
_PRICE_KEYS = ("adultPrice", "childPrice", "elderPrice", "singleRoomDiff")
# The ERP's own four periods of an itinerary day, in the order it writes them.
_DAY_PERIODS = ("morning", "midday", "afternoon", "evening")
# What a field whose type states no ``None`` becomes when the ERP leaves it out of a row.
_ZEROS: dict[str, Any] = {
    "int": 0, "float": 0.0, "str": "", "bool": False, "tuple[str, ...]": (),
    "date": EPOCH.date(), "datetime": EPOCH, "PriceInfo": erp.PriceInfo(0.0, 0.0, 0.0, 0.0),
}  # fmt: skip
# The ERP's status is its error class, its Chinese ``message`` is kept as written, and a 5xx is
# an outage; anything unlisted below 500 is the seam's own error. A 405 is an endpoint this
# deployment's ERP does not carry — ``route/itinerary`` is asked for and not yet built — and
# reads as nothing found, which is what ``_optional_data`` answers a caller with.
_ERRORS: dict[int, tuple[type[erp.ErpError], str]] = {
    400: (erp.ErpRefused, REFUSED), 401: (erp.ErpAuth, AUTH_FAILED),
    403: (erp.ErpAuth, AUTH_FAILED), 404: (erp.ErpNotFound, NOT_FOUND),
    405: (erp.ErpNotFound, NOT_FOUND), 429: (erp.ErpThrottled, THROTTLED),
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


def _day(row: dict[str, Any]) -> erp.ItineraryDay:
    """One ``route/itinerary`` day row as an ``ItineraryDay``. The ERP writes a day in its own
    four periods — 上午, 中午, 下午, 晚上 — and one text is what the advisor reads back, so the
    periods it filled in are joined in order and the empty ones are left out. ``transport`` has
    nowhere to go in an ``ItineraryDay`` and is not read."""
    periods = (str(row.get(key) or "").strip() for key in _DAY_PERIODS)
    return erp.ItineraryDay(
        day_no=int(row.get("dayNo") or 0),
        title=str(row.get("title") or "").strip(),
        text="；".join(period for period in periods if period),
        hotel=str(row.get("hotel") or "").strip() or None,
        meals=str(row.get("meals") or "").strip() or None,
    )


def _record(cls: type[R], row: dict[str, Any]) -> R:
    """The dataclass's own fields drive the mapping: each names its ERP key and, in its
    annotation, how the value is read."""

    def read(name: str) -> Any:
        return next((row[key] for key in _keys(name) if key in row), None)

    return cls(**{f.name: _value(str(f.type), read(f.name)) for f in fields(cls)})


class HttpErpClient:
    """The nine ``ErpClient`` calls against ``base_url`` (the ERP's ``/aicli`` root), as the
    salesperson ``mobile`` names, and the ``WindowReader`` call beside them. The ERP picks the
    department the login lands in, and a write-side call switches into the 团期's. Every listing
    read is paged to exhaustion, and one window's 团期 are read once and held for a moment.
    ``from_login`` and ``with_token`` are the two constructors; ``transport`` is the tests' mock
    hook."""

    def __init__(
        self,
        base_url: str,
        mobile: str,
        password: str = "",
        *,
        token: str = "",
        expires_at: float = 0.0,
        user_info: dict[str, Any] | None = None,
        companies: list[dict[str, Any]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(READ_TIMEOUT), transport=transport
        )
        # An empty password is a client built from a token: there is nothing here to log in
        # with, and ``_login`` says so instead of trying.
        self._credentials = {"mobile": mobile, "password": password}
        self._token = token
        self._expires_at = expires_at
        # One (token, expiry) per department switched into; the login department is not in it,
        # because the login token is already that department's. ``user_info`` is the userId,
        # userName, companyId and companyName of the logged-in salesperson — the backend writes
        # that name onto an order — and ``companies`` is the span of this account's reads.
        self._switched: dict[int, tuple[str, float]] = {}
        self.user_info: dict[str, Any] = dict(user_info or {})
        self.companies: list[dict[str, Any]] = list(companies or ())
        # A search fans out over one cold client: without these every caller would log in, and
        # every caller wanting the same department would switch into it. The login lock guards
        # the login token, one lock per department guards that department's switch, and the
        # caller that waited reads the token the first one wrote instead of buying another.
        # Ten failed logins lock the mobile, so a repeated login is not merely wasteful.
        self._login_lock = asyncio.Lock()
        self._switch_locks: dict[int, asyncio.Lock] = {}
        # One window's whole 团期 read, by window, with the moment it was read: the backend's
        # search fan-out and the details read after it ask for the same window.
        self._windows: dict[
            tuple[date | None, date | None], tuple[float, list[erp.DepartureRecord]]
        ] = {}

    @classmethod
    def from_login(
        cls,
        base_url: str,
        mobile: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> HttpErpClient:
        """A client that holds the credentials: it logs in on its first call and again whenever
        the token it holds is spent. This is the deployment's own account."""
        return cls(base_url, mobile, password, transport=transport)

    @classmethod
    def with_token(
        cls,
        base_url: str,
        token: str,
        expires_at: float,
        user_info: dict[str, Any],
        companies: list[dict[str, Any]],
        mobile: str = "",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> HttpErpClient:
        """A client that holds what one login answered — the token, its expiry, the salesperson
        and the departments — and no password. It reads and switches departments on that token;
        past its expiry there is nothing to log in with, so the call raises
        ``ErpAuth(EXPIRED)`` and whoever holds the client logs the advisor in again."""
        return cls(
            base_url,
            mobile,
            token=token,
            expires_at=expires_at,
            user_info=user_info,
            companies=companies,
            transport=transport,
        )

    def token(self) -> tuple[str, float]:
        """The login token and the moment it expires, for a host that keeps the token and drops
        the password (``advisors.py``); empty until this client has logged in."""
        return self._token, self._expires_at

    async def aclose(self) -> None:
        """Close the connections. A host that logs in only to keep the token closes the client
        that held the password."""
        await self._client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        data: dict[str, Any],
        timeout: float,
        token: str,
        *,
        retry_timeout: bool = False,
    ) -> httpx.Response:
        """A GET sends ``data`` as the query, a POST as the body; a dead wire is an outage. A
        paged listing read is slow enough to time out on a busy ERP, so it gets one more try;
        every other call is one attempt, because a read that hangs is better reported."""
        sent = {"params": data} if method == "GET" else {"json": data}
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for attempt in (1, 2):
            try:
                return await self._client.request(
                    method, path, timeout=timeout, headers=headers, **sent
                )
            except httpx.TimeoutException as exc:
                if not (retry_timeout and attempt == 1):
                    raise erp.ErpUnavailable(UNAVAILABLE) from exc
            except httpx.TransportError as exc:
                raise erp.ErpUnavailable(UNAVAILABLE) from exc
        raise erp.ErpUnavailable(UNAVAILABLE)

    async def _login(self) -> str:
        """Mobile and password alone; the ERP names the department it chose and all of them.
        The lock makes a concurrent fan-out cost one login: whoever waited for it finds the
        token already there and takes it. A client built from a token holds no password, and
        its spent token is the advisor's to renew, not this client's."""
        async with self._login_lock:
            if self._token and time.time() < self._expires_at - TOKEN_SKEW:
                return self._token
            if not self._credentials["password"]:
                raise erp.ErpAuth(EXPIRED)
            response = await self._send("POST", "/login", self._credentials, WRITE_TIMEOUT, "")
            data = _data(response, credentials=True) or {}
            self._token, self._expires_at = _bearer(data)
            self.user_info = dict(data.get("userInfo") or {})
            self.companies = list(data.get("companies") or ())
            return self._token

    async def identify(self) -> dict[str, Any]:
        """The logged-in salesperson — userId, userName, companyId, companyName — logging in
        first when this client has not yet. Only the login carries it, so a caller that wants
        the advisor's own name before any catalog read (the workbench asks on its first turn)
        asks here rather than waiting for one."""
        if not self.user_info:
            await self._login()
        return self.user_info

    async def _token_for(self, company_id: int | None) -> str:
        """The bearer one call goes out under: the login token for a read and for its own
        department, and for any other department its own, switched into once and kept. The
        department's own lock is what keeps a fan-out to one switch apiece."""
        token = self._token
        if not token or time.time() >= self._expires_at - TOKEN_SKEW:
            token = await self._login()
        if not company_id or company_id == int(self.user_info.get("companyId") or 0):
            return token
        async with self._switch_locks.setdefault(company_id, asyncio.Lock()):
            held = self._switched.get(company_id)
            if held and time.time() < held[1] - TOKEN_SKEW:
                return held[0]
            switch = await self._request("POST", "/switch-company", {"companyId": company_id})
            self._switched[company_id] = _bearer(switch or {})
            return self._switched[company_id][0]

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any],
        timeout: float = READ_TIMEOUT,
        company_id: int | None = None,
        *,
        retry_timeout: bool = False,
    ) -> Any:
        """One call under the token ``company_id`` picks. A stale token buys one fresh login or
        switch and one retry; a 401 after that is the account's problem, not the session's."""
        for attempt in (1, 2):
            token = await self._token_for(company_id)
            response = await self._send(
                method, path, data, timeout, token, retry_timeout=retry_timeout
            )
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

    async def _page(
        self, path: str, params: dict[str, Any], size: int, page: int, company_id: int | None
    ) -> dict[str, Any]:
        paged = {**params, "pageNum": page, "pageSize": size}
        return (
            await self._request("GET", path, paged, LIST_TIMEOUT, company_id, retry_timeout=True)
            or {}
        )

    async def _pages(
        self,
        path: str,
        params: dict[str, Any],
        size: int,
        company_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Every page the answer says there is, to ``MAX_PAGES``: the catalog is larger than any
        one page, and a listing read that stopped early would hide 线路 and 团期 the advisor
        asked for. The first page names ``totalPages`` and the rest are read a few at a time,
        because each is its own round trip and the ERP's slowest read is this one."""
        first = await self._page(path, params, size, 1, company_id)
        rows: list[dict[str, Any]] = list(first.get("list") or [])
        total = min(int(first.get("totalPages") or 1), MAX_PAGES)
        gate = asyncio.Semaphore(PAGE_CONCURRENCY)

        async def read(page: int) -> list[dict[str, Any]]:
            async with gate:
                return list(
                    (await self._page(path, params, size, page, company_id)).get("list") or []
                )

        rest = await asyncio.gather(*(read(page) for page in range(2, total + 1)))
        for page_rows in rest:
            rows.extend(page_rows)
        return rows

    async def _optional_data(self, path: str, params: dict[str, Any]) -> Any:
        """A read the ERP may have no answer to: its 404 — and its 405, which is what an
        endpoint the deployment's ERP does not carry answers — is nothing found rather than an
        error, so the caller reads ``None`` and carries on."""
        try:
            return await self._request("GET", path, params)
        except erp.ErpNotFound:
            return None

    async def _optional(self, cls: type[R], path: str, params: dict[str, Any]) -> R | None:
        """A record the ERP may not have, mapped by its own fields where there is one."""
        data = await self._optional_data(path, params)
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
        window's whole read answers for it then, and that read is the one ``list_window``
        holds, so a search that already made it costs nothing here."""
        window = {"departDateStart": _iso(depart_from), "departDateEnd": _iso(depart_to)}
        rows = await self._pages(
            "/period/list", _clean({"routeName": route_name, **window}), PAGE_SIZE
        )
        listed = (_record(erp.DepartureRecord, row) for row in rows)
        mine = [row for row in listed if row.route_id == route_id]
        if not mine and route_name:
            whole = await self.list_window(depart_from, depart_to)
            mine = [row for row in whole if row.route_id == route_id]
        return mine

    async def list_window(
        self, depart_from: date | None, depart_to: date | None
    ) -> list[erp.DepartureRecord]:
        """Every 团期 the window holds, across every 线路 and every department the account
        reads: one paged call in place of a call per route, which is the only way to learn many
        routes' departures from an endpoint that filters on the route *name* and not on an id.
        The answer is kept for ``WINDOW_TTL`` seconds, because a search's fan-out and the
        details read that follows it ask for the same window."""
        key = (depart_from, depart_to)
        held = self._windows.get(key)
        if held and time.time() - held[0] < WINDOW_TTL:
            return held[1]
        params = _clean({"departDateStart": _iso(depart_from), "departDateEnd": _iso(depart_to)})
        rows = await self._pages("/period/list", params, PAGE_SIZE)
        records = [_record(erp.DepartureRecord, row) for row in rows]
        self._windows[key] = (time.time(), records)
        return records

    async def get_itinerary(self, route_id: int) -> erp.Itinerary | None:
        """The 线路's structured 行程. The agency's API does not carry ``route/itinerary`` yet —
        it is asked for in ``docs/erp-contract.md`` — so in production this answers a 404 or a
        405 and the call reads ``None``, which is also what a 线路 with no days reads. The
        行程附件 the catalog links is the backend's to read (``api/itinerary_source.py``) and not
        this client's: field mapping and nothing else."""
        data = await self._optional_data("/route/itinerary", {"routeId": route_id})
        if not data:
            return None
        days = tuple(_day(row) for row in data.get("days") or ())
        version = data.get("version")
        return (
            erp.Itinerary(
                route_id=int(data.get("routeId") or route_id),
                source="erp",
                source_ref=None if version is None else str(version),
                days=days,
            )
            if days
            else None
        )

    async def get_departure(self, period_id: int) -> erp.DepartureRecord | None:
        return await self._optional(erp.DepartureRecord, "/period/detail", {"periodId": period_id})

    async def search_customers(self, keyword: str) -> list[erp.CustomerRecord]:
        """The customer book is the one read that does *not* span every department: a
        department that keeps no customers of its own — the login lands in one such in
        production — answers nothing whatever the keyword is. So the login token asks first,
        and while the answer is empty each other authorised department is asked in turn, on its
        own switched token, until one answers. The keyword is what narrows this: the pages are
        read to exhaustion, and a department holds thousands of customers."""
        params = _clean({"keyword": keyword})
        rows = await self._pages("/customer/list", params, PAGE_SIZE)
        login_company = int(self.user_info.get("companyId") or 0)
        if not rows:
            for company in self.companies:
                company_id = int(company.get("companyId") or 0)
                if not company_id or company_id == login_company:
                    continue
                rows = await self._pages("/customer/list", params, PAGE_SIZE, company_id)
                if rows:
                    break
        return [_record(erp.CustomerRecord, row) for row in rows]

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
