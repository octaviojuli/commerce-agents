# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``HttpErpClient`` against a mock wire: what each call puts on the wire and what each
answer, refusal, and outage maps back to. The canned bodies are the beta
environment's own envelopes and keys with ACME's fictional catalog in them, so these tests
hold the client to the ERP's contract without a server."""

import asyncio
import json
import time
from datetime import date, datetime

import httpx
import pytest

from tour.api.erp_client import (
    ErpAuth,
    ErpClient,
    ErpNotFound,
    ErpRefused,
    ErpThrottled,
    ErpUnavailable,
    OrderRequest,
    RouteQuery,
)
from tour.api.http_erp import (
    LIST_TIMEOUT,
    MAX_PAGES,
    READ_TIMEOUT,
    WRITE_TIMEOUT,
    HttpErpClient,
)

BASE_URL = "https://erp.acme-tour.example/aicli"
PREFIX = "/aicli"
MOBILE = "13800138000"
SECRET = "fixture-only-secret"
COMPANY_ID = 2
TOKEN = "erp-token-abc"

OTHER_COMPANY_ID = 5
THIRD_COMPANY_ID = 7
SWITCHED = "erp-token-qinghai"
COMPANIES = [
    {"companyId": COMPANY_ID, "companyName": "ACME 旅行社 新疆部"},
    {"companyId": OTHER_COMPANY_ID, "companyName": "ACME 旅行社 青海部"},
    {"companyId": THIRD_COMPANY_ID, "companyName": "ACME 旅行社 甘肃部"},
]
USER_INFO = {"userId": 6, "userName": "柯海水", "companyId": 2, "companyName": "ACME 旅行社 新疆部"}
LOGIN = {
    "code": 200,
    "message": "登录成功",
    "data": {
        "token": TOKEN,
        "tokenType": "Bearer",
        "expiresIn": 28800,
        "expiresAt": int(time.time()) + 28800,
        "userInfo": USER_INFO,
        "companies": COMPANIES,
    },
}
SWITCH = {
    "code": 200,
    "message": "切换成功",
    "data": {
        "token": SWITCHED,
        "tokenType": "Bearer",
        "expiresIn": 28800,
        "expiresAt": int(time.time()) + 28800,
        "companies": COMPANIES,
    },
}
ROUTE = {
    "routeId": 1021,
    "routeCode": "YLBJ",
    "routeName": "伊犁北疆环线 8 日纯玩小团",
    "fromPrice": 0,
    "days": 8,
    "departCityId": 3,
    "departCityName": "乌鲁木齐",
    "companyId": 2,
    "companyName": "ACME 旅行社 新疆部",
    "groupId": 1,
    "tags": ["纯玩", "小团"],
    "itineraryTags": ["伊犁", "乌鲁木齐出发", "四钻酒店", "纯玩无购物", "含景点首道门票"],
    "itineraryTagsStatus": 1,
    "periodTags": ["预算约5800—6600元"],
    "periodPriceTags": ["预算约5800—6600元"],
    "periodHolidayTags": [],
    "features": [],
    "firstImageId": 121,
    "firstImageUrl": "https://images.example/acme/ylbj.jpg",
    "posterUrls": [],
    "routeAttachmentId": 122,
    "routeAttachmentName": "伊犁北疆环线 8 日.docx",
    "routeAttachmentUrl": "https://files.example/acme/ylbj.docx",
}
PERIOD = {
    "periodId": 3001,
    "periodCode": "XJ-YLBJ-20261012-001",
    "routeId": 1021,
    "routeName": "伊犁北疆环线 8 日纯玩小团",
    "departDate": "2026-10-12",
    "returnDate": "2026-10-19",
    "days": 8,
    "planGuests": 8,
    "minGroupSize": 2,
    "confirmCount": 4,
    "availableSeats": 4,
    "reserveHours": 24,
    "companyId": COMPANY_ID,
    "companyName": "ACME 旅行社 新疆部",
    "departCityName": "乌鲁木齐",
    "groupId": 1,
}
OTHER_PERIOD = {
    **PERIOD,
    "periodId": 3099,
    "routeId": 1022,
    "routeName": "伊犁·喀拉峻草原深度 10 日",
}
PERIOD_DETAIL = {
    **PERIOD,
    "reserveCount": 2,
    "placeholderCount": 0,
    "waitlistCount": 1,
    "flightInfo": [],
    "priceInfo": {
        "adultPrice": 6180,
        "childPrice": 3880,
        "elderPrice": 6180,
        "singleRoomDiff": 1400,
        "currency": "CNY",
    },
}
ITINERARY = {
    "routeId": 1021,
    "version": 7,
    "days": [
        {
            "dayNo": 1,
            "title": "乌鲁木齐集合",
            "morning": "机场接站",
            "midday": "送酒店办理入住",
            "afternoon": "",
            "evening": "行前说明会",
            "transport": ["旅游用车"],
            "hotel": "乌鲁木齐 · 云杉里酒店",
            "meals": "早：不含 中：不含 晚：不含",
        },
        {
            "dayNo": 2,
            "title": "乌鲁木齐—赛里木湖",
            "morning": "前往赛里木湖",
            "midday": "湖畔午餐",
            "afternoon": None,
            "evening": None,
            "transport": ["旅游用车"],
            "hotel": "赛里木湖 · 湖畔星野度假酒店",
            "meals": "早：酒店 中：路餐 晚：湖畔炖鱼",
        },
    ],
}
CUSTOMER = {
    "customerId": 4101,
    "companyName": "ACME 同业 · 北京营业部",
    "csCode": "TX-BJ-0001",
    "companyType": 1,
}
PRICE = {
    "code": 200,
    "message": "操作成功",
    "data": {
        "isExternalOrder": False,
        "priceType": "同行价",
        "priceInfo": {
            "adultPrice": 5980,
            "childPrice": 3680,
            "elderPrice": 5980,
            "singleRoomDiff": 1400,
            "currency": "CNY",
        },
    },
}
ORDER_ROW = {
    "orderId": 70001,
    "orderNo": "ORD070001",
    "periodId": 3001,
    "periodCode": "XJ-YLBJ-20261012-001",
    "routeName": "伊犁北疆环线 8 日纯玩小团",
    "customerId": 4101,
    "customerName": "ACME 同业 · 北京营业部",
    "totalAmount": 16240,
    "receivedAmount": 0,
    "unreceivedAmount": 16240,
    "orderStatus": 0,
    "orderStatusText": "预留",
    "createTime": "2026-09-07 10:30:00",
}
ORDER_DETAIL = {
    **ORDER_ROW,
    "storeId": 1,
    "storeName": "ACME 旅行社",
    "contactName": "柯海水",
    "contactMobile": "13800138000",
    "adultCount": 2,
    "childCount": 1,
    "elderCount": 0,
    "roomCount": 2,
    "singleRoomDiffCount": 0,
    "departDate": "2026-10-12",
    "returnDate": "2026-10-19",
    "reserveExpireAt": "2026-09-08 10:30:00",
    "remark": "",
}


def page(rows: list[dict], *, page_num: int = 1, total_pages: int = 1) -> dict:
    return {
        "code": 200,
        "message": "获取成功",
        "data": {
            "list": rows,
            "total": len(rows) * total_pages,
            "pageNum": page_num,
            "pageSize": 50,
            "totalPages": total_pages,
        },
    }


def ok(data: dict) -> dict:
    return {"code": 200, "message": "操作成功", "data": data}


def paged(by_page: list[list[dict]]):
    """A reply that answers whichever page the request's ``pageNum`` asks for, and names how
    many there are, the way the ERP's own envelope does."""

    def reply(request: httpx.Request) -> dict:
        number = int(dict(request.url.params).get("pageNum") or 1)
        rows = by_page[number - 1] if number <= len(by_page) else []
        return page(rows, page_num=number, total_pages=len(by_page))

    return reply


def switched(request: httpx.Request) -> dict:
    """A switch answered with that department's own token, so a call can be traced to it."""
    company = body_of(request)["companyId"]
    return ok({"token": f"erp-token-{company}", "expiresIn": 28800, "companies": COMPANIES})


class Wire(httpx.AsyncBaseTransport):
    """A transport that answers each path from ``replies`` and suspends on every request the
    way a real wire does. ``httpx.MockTransport`` answers without ever yielding to the event
    loop, so concurrent callers would run one after another and the client's locks would look
    like they were doing something they are not.

    A reply is a JSON body, a ``(status, body)`` pair, raw text, an exception to raise, or a
    list of those in turn; a callable is passed the request and answers whatever it likes."""

    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.seen: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        await asyncio.sleep(0)
        reply = self.replies[request.url.path.removeprefix(PREFIX)]
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, list):
            reply = reply.pop(0)
        status, body = reply if isinstance(reply, tuple) else (200, reply)
        if isinstance(body, Exception):
            raise body
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)


def erp(replies: dict) -> tuple[HttpErpClient, list[httpx.Request]]:
    """A client on a ``Wire`` over ``replies``, and the requests it puts on that wire."""
    transport = Wire(replies)
    client = HttpErpClient(BASE_URL, MOBILE, SECRET, transport=transport)
    return client, transport.seen


def paths(seen: list[httpx.Request]) -> list[str]:
    return [request.url.path.removeprefix(PREFIX) for request in seen]


def read_timeout(request: httpx.Request) -> float:
    """The read timeout the client set on one call, as httpx carries it on the request."""
    return request.extensions["timeout"]["read"]


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


async def test_the_first_call_logs_in_and_carries_the_bearer_it_got():
    client, seen = erp({"/login": LOGIN, "/route/list": page([ROUTE])})
    await client.search_routes(RouteQuery(route_name="伊犁"))
    assert [str(r.url.path) for r in seen] == ["/aicli/login", "/aicli/route/list"]
    # The ERP takes no department on the login: it picks one and names every one the account has.
    assert body_of(seen[0]) == {"mobile": MOBILE, "password": SECRET}
    assert seen[1].headers["Authorization"] == f"Bearer {TOKEN}"
    assert (client.user_info, client.companies) == (USER_INFO, COMPANIES)


async def test_a_live_token_is_reused_rather_than_logged_in_again():
    client, seen = erp({"/login": LOGIN, "/route/list": page([ROUTE])})
    await client.search_routes(RouteQuery())
    await client.search_routes(RouteQuery())
    assert [r.url.path for r in seen].count("/aicli/login") == 1


async def test_a_stale_token_buys_one_fresh_login_and_one_retry():
    unauthorized = (401, {"code": 401, "message": "登录状态已失效", "data": None})
    client, seen = erp(
        {"/login": [LOGIN, LOGIN], "/route/list": [unauthorized, page([ROUTE])]},
    )
    routes = await client.search_routes(RouteQuery())
    assert [r.url.path.removeprefix(PREFIX) for r in seen] == [
        "/login",
        "/route/list",
        "/login",
        "/route/list",
    ]
    assert [route.route_id for route in routes] == [1021]


async def test_a_401_a_fresh_login_did_not_cure_is_an_auth_failure():
    unauthorized = (401, {"code": 401, "message": "登录状态已失效", "data": None})
    client, _ = erp({"/login": LOGIN, "/route/list": unauthorized})
    with pytest.raises(ErpAuth):
        await client.search_routes(RouteQuery())


async def test_the_login_throttle_is_its_own_error():
    throttled = (429, {"code": 429, "message": "登录过于频繁，请 15 分钟后再试", "data": None})
    client, _ = erp({"/login": throttled})
    with pytest.raises(ErpThrottled, match="15 分钟"):
        await client.search_routes(RouteQuery())


async def test_a_department_the_account_cannot_see_is_an_auth_failure():
    forbidden = (403, {"code": 403, "message": "无权访问该部门", "data": None})
    client, _ = erp({"/login": forbidden})
    with pytest.raises(ErpAuth, match="无权访问该部门"):
        await client.search_routes(RouteQuery())


async def test_search_routes_sends_the_window_and_maps_the_row():
    client, seen = erp({"/login": LOGIN, "/route/list": page([ROUTE])})
    query = RouteQuery(
        route_name="伊犁", depart_from=date(2026, 10, 1), depart_to=date(2026, 10, 31)
    )
    (route,) = await client.search_routes(query)
    params = dict(seen[1].url.params)
    assert params["routeName"] == "伊犁"
    assert (params["departDateStart"], params["departDateEnd"]) == ("2026-10-01", "2026-10-31")
    assert int(params["pageSize"]) <= 50
    assert (route.route_id, route.route_code, route.days) == (1021, "YLBJ", 8)
    assert (route.from_price, route.tags) == (0.0, ("纯玩", "小团"))
    assert route.attachment_name == "伊犁北疆环线 8 日.docx"
    # The ERP's own extraction of the itinerary attachment, and the 团期 budget band.
    assert route.itinerary_tags == (
        "伊犁",
        "乌鲁木齐出发",
        "四钻酒店",
        "纯玩无购物",
        "含景点首道门票",
    )
    assert route.price_tags == ("预算约5800—6600元",)


async def test_a_route_row_without_the_tag_lists_maps_them_to_nothing():
    """The extraction is not on every row — a route with no attachment carries none of it —
    and a field the ERP leaves out is empty here, not a failure."""
    bare = {key: value for key, value in ROUTE.items() if not key.startswith("itinerary")}
    client, _ = erp({"/login": LOGIN, "/route/list": page([{**bare, "periodPriceTags": None}])})
    (route,) = await client.search_routes(RouteQuery())
    assert (route.itinerary_tags, route.price_tags) == ((), ())


async def test_list_departures_filters_by_name_and_keeps_only_the_matching_route_id():
    client, seen = erp({"/login": LOGIN, "/period/list": page([PERIOD, OTHER_PERIOD])})
    rows = await client.list_departures(1021, "伊犁北疆环线 8 日纯玩小团", None, None)
    assert dict(seen[1].url.params)["routeName"] == "伊犁北疆环线 8 日纯玩小团"
    assert [row.period_id for row in rows] == [3001]
    assert rows[0].depart_date == date(2026, 10, 12)
    assert rows[0].company_id == COMPANY_ID
    assert rows[0].price is None


async def test_a_renamed_route_finds_its_departures_by_window_alone():
    """Periods keep the route name they were made under, so the named fetch can come back
    empty for a route that was renamed since; the client then fetches the window alone."""
    client, seen = erp({"/login": LOGIN, "/period/list": [page([]), page([PERIOD, OTHER_PERIOD])]})
    rows = await client.list_departures(
        1021, "伊犁北疆环线 8 日纯玩小团（新版）", date(2026, 10, 1), date(2026, 10, 31)
    )
    assert "routeName" in dict(seen[1].url.params)
    assert "routeName" not in dict(seen[2].url.params)
    assert dict(seen[2].url.params)["departDateStart"] == "2026-10-01"
    assert [row.period_id for row in rows] == [3001]


async def test_order_rows_read_unix_stamps_and_the_lists_own_amount_key():
    """``createTime`` and ``reserveExpireAt`` are Unix seconds, and the list row calls the
    total ``orderAmount`` where the detail calls it ``totalAmount``."""
    row = {
        "orderId": 204,
        "orderNo": "ORD202609070001",
        "periodId": 12,
        "periodCode": "-MOMJ-20260505-001",
        "routeName": "北疆 10 天",
        "customerId": 4101,
        "customerName": "北京同行社",
        "orderAmount": 8000,
        "receivedAmount": 0,
        "unreceivedAmount": 8000,
        "orderStatus": 0,
        "orderStatusText": "预留",
        "createTime": 1788770417,
        "reserveExpireAt": 1788856817,
        "adultCount": 2,
        "childCount": 0,
        "elderCount": 0,
        "contactName": "顾问",
        "contactMobile": "13900000001",
        "departDate": "2026-05-05",
    }
    client, _ = erp({"/login": LOGIN, "/order/list": page([row])})
    (order,) = await client.list_orders()
    assert order.total_amount == 8000.0
    assert order.created_at is not None and order.created_at.year == 2026
    assert order.reserve_expires_at is not None and order.reserve_expires_at > order.created_at


async def test_get_departure_carries_the_list_price_and_the_hold_counts():
    client, _ = erp({"/login": LOGIN, "/period/detail": ok(PERIOD_DETAIL)})
    row = await client.get_departure(3001)
    assert row is not None and row.price is not None
    assert (row.price.adult, row.price.single_room_diff) == (6180.0, 1400.0)
    assert row.company_id == COMPANY_ID
    assert (row.reserve_count, row.placeholder_count, row.waitlist_count) == (2, 0, 1)


async def test_an_unknown_departure_is_no_departure_rather_than_an_error():
    missing = (404, {"code": 404, "message": "团期不存在", "data": None})
    client, _ = erp({"/login": LOGIN, "/period/detail": missing})
    assert await client.get_departure(999) is None


async def test_get_itinerary_maps_the_erps_four_periods_into_one_text_a_day():
    client, seen = erp({"/login": LOGIN, "/route/itinerary": ok(ITINERARY)})
    itinerary = await client.get_itinerary(1021)
    assert itinerary is not None
    assert dict(seen[1].url.params) == {"routeId": "1021"}
    assert (itinerary.route_id, itinerary.source, itinerary.source_ref) == (1021, "erp", "7")
    assert [day.day_no for day in itinerary.days] == [1, 2]
    assert itinerary.days[0].title == "乌鲁木齐集合"
    # The periods the ERP filled in, in its own order; the empty one is left out entirely.
    assert itinerary.days[0].text == "机场接站；送酒店办理入住；行前说明会"
    assert itinerary.days[1].text == "前往赛里木湖；湖畔午餐"
    assert itinerary.days[0].hotel == "乌鲁木齐 · 云杉里酒店"
    assert itinerary.days[1].meals == "早：酒店 中：路餐 晚：湖畔炖鱼"


@pytest.mark.parametrize("status", [404, 405])
async def test_a_route_the_erp_writes_no_itinerary_for_is_no_itinerary(status):
    # 405 is what an ERP that has not built ``route/itinerary`` yet answers, and it reads the
    # same as a 404: this 线路 has no days here, and the host reads the attachment instead.
    refused = (status, {"code": status, "message": "接口不存在", "data": None})
    client, _ = erp({"/login": LOGIN, "/route/itinerary": refused})
    assert await client.get_itinerary(1021) is None


async def test_an_itinerary_with_no_days_is_no_itinerary():
    client, _ = erp({"/login": LOGIN, "/route/itinerary": ok({"routeId": 1021, "days": []})})
    assert await client.get_itinerary(1021) is None


async def test_search_customers_maps_the_trade_customer_row():
    client, seen = erp({"/login": LOGIN, "/customer/list": page([CUSTOMER])})
    (customer,) = await client.search_customers("北京")
    assert dict(seen[1].url.params)["keyword"] == "北京"
    assert (customer.customer_id, customer.code, customer.customer_type) == (4101, "TX-BJ-0001", 1)


async def test_quote_is_the_customers_own_price_and_its_label():
    client, seen = erp({"/login": LOGIN, "/order/price": PRICE})
    quote = await client.quote(3001, 4101, COMPANY_ID)
    # The login department needs no switch: its token is the login one.
    assert [r.url.path.removeprefix(PREFIX) for r in seen] == ["/login", "/order/price"]
    assert dict(seen[1].url.params) == {"periodId": "3001", "customerId": "4101"}
    assert (quote.price_type, quote.is_external) == ("同行价", False)
    assert (quote.price.adult, quote.price.child, quote.price.currency) == (5980.0, 3680.0, "CNY")


async def test_a_quote_in_another_department_switches_once_and_reuses_that_token():
    """Reads span every department, but ``order/price`` answers only under the 团期's own, so
    the client buys that department's token once and every later call rides on it."""
    client, seen = erp({"/login": LOGIN, "/switch-company": SWITCH, "/order/price": [PRICE, PRICE]})
    await client.quote(3060, 4101, OTHER_COMPANY_ID)
    await client.quote(3061, 4101, OTHER_COMPANY_ID)
    assert [r.url.path.removeprefix(PREFIX) for r in seen] == [
        "/login",
        "/switch-company",
        "/order/price",
        "/order/price",
    ]
    assert body_of(seen[1]) == {"companyId": OTHER_COMPANY_ID}
    assert seen[1].headers["Authorization"] == f"Bearer {TOKEN}"
    assert [r.headers["Authorization"] for r in seen[2:]] == [f"Bearer {SWITCHED}"] * 2


async def test_a_401_on_a_switched_token_switches_again_rather_than_logging_in_again():
    unauthorized = (401, {"code": 401, "message": "登录状态已失效", "data": None})
    client, seen = erp(
        {
            "/login": LOGIN,
            "/switch-company": [SWITCH, SWITCH],
            "/order/price": [unauthorized, PRICE],
        }
    )
    quote = await client.quote(3060, 4101, OTHER_COMPANY_ID)
    assert [r.url.path.removeprefix(PREFIX) for r in seen] == [
        "/login",
        "/switch-company",
        "/order/price",
        "/switch-company",
        "/order/price",
    ]
    assert quote.price.adult == 5980.0


async def test_list_orders_maps_the_list_row_and_leaves_the_detail_fields_empty():
    client, _ = erp({"/login": LOGIN, "/order/list": page([ORDER_ROW])})
    (order,) = await client.list_orders()
    assert (order.order_no, order.status, order.status_text) == ("ORD070001", 0, "预留")
    assert order.created_at == datetime(2026, 9, 7, 10, 30)
    assert (order.adults, order.contact_name, order.reserve_expires_at) == (0, "", None)


async def test_get_order_adds_the_party_the_contact_and_the_reserve_expiry():
    client, _ = erp({"/login": LOGIN, "/order/detail": ok(ORDER_DETAIL)})
    order = await client.get_order(70001)
    assert order is not None
    assert (order.adults, order.children, order.contact_name) == (2, 1, "柯海水")
    assert order.depart_date == date(2026, 10, 12)
    assert order.reserve_expires_at == datetime(2026, 9, 8, 10, 30)


async def test_an_order_in_another_department_is_written_on_that_departments_token():
    """A write is department-bound the same way, and the token bought for a quote serves it."""
    created = ok(
        {
            "orderId": 70003,
            "needsApproval": False,
            "approvalId": 0,
            "orderStatus": 0,
            "isWaitlist": False,
        }
    )
    client, seen = erp(
        {
            "/login": LOGIN,
            "/switch-company": SWITCH,
            "/order/price": PRICE,
            "/order/create": created,
        }
    )
    await client.quote(3060, 4101, OTHER_COMPANY_ID)
    result = await client.create_order(
        OrderRequest(3060, 4101, OTHER_COMPANY_ID, 2, 0, 0, 1, 0, "柯海水", "13800138000")
    )
    assert [r.url.path.removeprefix(PREFIX) for r in seen].count("/switch-company") == 1
    assert seen[-1].headers["Authorization"] == f"Bearer {SWITCHED}"
    # The department is the token's, not the body's.
    assert "companyId" not in body_of(seen[-1])
    assert result.order_id == 70003


async def test_create_order_sends_the_documented_body_and_maps_the_answer():
    created = ok(
        {
            "orderId": 70001,
            "needsApproval": False,
            "approvalId": 0,
            "orderStatus": 0,
            "isWaitlist": False,
        }
    )
    client, seen = erp({"/login": LOGIN, "/order/create": created})
    result = await client.create_order(
        OrderRequest(
            3001, 4101, COMPANY_ID, 2, 1, 0, 2, 0, "柯海水", "13800138000", store_name="ACME 旅行社"
        )
    )
    assert body_of(seen[1]) == {
        "periodId": 3001,
        "customerId": 4101,
        "adultCount": 2,
        "childCount": 1,
        "elderCount": 0,
        "roomCount": 2,
        "singleRoomDiffCount": 0,
        "contactName": "柯海水",
        "contactMobile": "13800138000",
        "storeName": "ACME 旅行社",
    }
    assert (result.order_id, result.status, result.is_waitlist) == (70001, 0, False)


async def test_an_order_over_the_seats_left_comes_back_as_a_waitlist_not_a_refusal():
    created = (
        201,
        ok(
            {
                "orderId": 70002,
                "needsApproval": False,
                "approvalId": 0,
                "orderStatus": 5,
                "isWaitlist": True,
            }
        ),
    )
    client, _ = erp({"/login": LOGIN, "/order/create": created})
    result = await client.create_order(
        OrderRequest(3001, 4101, COMPANY_ID, 9, 0, 0, 5, 0, "柯海水", "13800138000")
    )
    assert (result.status, result.is_waitlist) == (5, True)


async def test_a_business_refusal_keeps_the_erps_own_chinese_message():
    refused = (400, {"code": 400, "message": "同行客户下单必须填写门店。", "data": None})
    client, _ = erp({"/login": LOGIN, "/order/create": refused})
    with pytest.raises(ErpRefused, match="同行客户下单必须填写门店。"):
        await client.create_order(
            OrderRequest(3001, 4101, COMPANY_ID, 2, 0, 0, 1, 0, "柯海水", "13800138000")
        )


async def test_an_order_against_an_unknown_departure_is_not_found():
    missing = (404, {"code": 404, "message": "团期不存在", "data": None})
    client, _ = erp({"/login": LOGIN, "/order/create": missing})
    with pytest.raises(ErpNotFound, match="团期不存在"):
        await client.create_order(
            OrderRequest(999, 4101, COMPANY_ID, 2, 0, 0, 1, 0, "柯海水", "13800138000")
        )


async def test_a_404_with_no_json_body_is_a_missing_record_not_an_outage():
    """Beta answers a bare nginx page where a departure has no price row of its own; the
    status is the ERP's answer even when the body is not the ERP's envelope."""
    client, _ = erp({"/login": LOGIN, "/order/price": (404, "<html>404 Not Found</html>")})
    with pytest.raises(ErpNotFound):
        await client.quote(3001, 4101, COMPANY_ID)


@pytest.mark.parametrize(
    "reply",
    [
        (500, {"code": 500, "message": "系统异常", "data": None}),
        httpx.ConnectError("connection refused"),
        (200, "<html>gateway</html>"),
    ],
    ids=["fault", "transport", "not-json"],
)
async def test_an_outage_is_never_relayed_as_the_erps_own_words(reply):
    client, _ = erp({"/login": LOGIN, "/route/list": reply})
    with pytest.raises(ErpUnavailable, match="暂时无法连接"):
        await client.search_routes(RouteQuery())


# -- paging, timeouts, and the tokens a fan-out shares --------------------------------------


async def test_a_listing_read_pages_to_exhaustion_and_keeps_every_row():
    """Production's catalog is six pages of 线路 and a plain 60-day window four pages of 团期, so
    a read that stopped at the first page would hide most of both from the advisor."""
    by_page = [[{**ROUTE, "routeId": 1000 + number}] for number in range(3)]
    client, seen = erp({"/login": LOGIN, "/route/list": paged(by_page)})
    routes = await client.search_routes(RouteQuery())
    assert [route.route_id for route in routes] == [1000, 1001, 1002]
    assert [int(dict(r.url.params)["pageNum"]) for r in seen[1:]] == [1, 2, 3]
    assert {int(dict(r.url.params)["pageSize"]) for r in seen[1:]} == {50}


async def test_a_listing_read_stops_at_the_page_ceiling():
    """An answer that claims more pages than the catalog could hold is still bounded: the read
    is a page count and not a promise, and a client that trusted it would never finish."""
    absurd = lambda request: page([ROUTE], page_num=1, total_pages=500)  # noqa: E731
    client, seen = erp({"/login": LOGIN, "/route/list": absurd})
    await client.search_routes(RouteQuery())
    assert paths(seen).count("/route/list") == MAX_PAGES


async def test_a_paged_listing_read_waits_longer_than_every_other_call():
    """``period/list`` over a window alone takes seconds a page where every other read answers
    in a fraction of one, and a login or an order is worth waiting a moment longer for."""
    client, seen = erp(
        {"/login": LOGIN, "/route/list": page([ROUTE]), "/period/detail": ok(PERIOD_DETAIL)}
    )
    await client.search_routes(RouteQuery())
    await client.get_departure(3001)
    login, listing, detail = seen
    assert read_timeout(login) == WRITE_TIMEOUT
    assert read_timeout(listing) == LIST_TIMEOUT
    assert read_timeout(detail) == READ_TIMEOUT


async def test_a_listing_page_that_times_out_is_asked_once_more():
    client, seen = erp({"/login": LOGIN, "/route/list": [httpx.ReadTimeout("slow"), page([ROUTE])]})
    routes = await client.search_routes(RouteQuery())
    assert [route.route_id for route in routes] == [1021]
    assert paths(seen).count("/route/list") == 2


async def test_a_read_that_is_not_a_listing_page_is_not_retried():
    """One attempt: a detail read that hangs is reported as an outage rather than doubled."""
    client, seen = erp(
        {"/login": LOGIN, "/period/detail": [httpx.ReadTimeout("slow"), ok(PERIOD_DETAIL)]}
    )
    with pytest.raises(ErpUnavailable, match="暂时无法连接"):
        await client.get_departure(3001)
    assert paths(seen).count("/period/detail") == 1


async def test_a_cold_fan_out_logs_in_once_and_switches_once_per_department():
    """Six quotes at once on a client that holds no token yet. Every one of them needs the login
    and two of them need each department's switch, and without the locks each caller would buy
    its own: the ERP locks a mobile after ten failed logins, so that is not merely wasteful."""
    client, seen = erp({"/login": LOGIN, "/switch-company": switched, "/order/price": PRICE})
    departments = [OTHER_COMPANY_ID, THIRD_COMPANY_ID] * 3
    await asyncio.gather(
        *(client.quote(3060 + n, 4101, company) for n, company in enumerate(departments))
    )
    assert paths(seen).count("/login") == 1
    assert paths(seen).count("/switch-company") == 2
    assert paths(seen).count("/order/price") == 6
    assert {body_of(r)["companyId"] for r in seen if r.url.path.endswith("switch-company")} == {
        OTHER_COMPANY_ID,
        THIRD_COMPANY_ID,
    }
    quotes = [r for r in seen if r.url.path.endswith("order/price")]
    assert {r.headers["Authorization"] for r in quotes} == {
        f"Bearer erp-token-{OTHER_COMPANY_ID}",
        f"Bearer erp-token-{THIRD_COMPANY_ID}",
    }


async def test_a_401_in_the_middle_of_a_fan_out_buys_one_login_for_all_of_them():
    """The token expired between two turns, so every caller in the fan-out comes back to a 401.
    One login refreshes it and each caller retries once, on the token that login bought."""
    fresh = {"code": 200, "message": "登录成功", "data": {**LOGIN["data"], "token": "erp-token-2"}}
    unauthorized = (401, {"code": 401, "message": "登录状态已失效", "data": None})

    def routes(request: httpx.Request) -> dict | tuple:
        stale = request.headers["Authorization"] == f"Bearer {TOKEN}"
        return unauthorized if stale else page([ROUTE])

    client, seen = erp({"/login": [LOGIN, fresh], "/route/list": routes})
    answers = await asyncio.gather(*(client.search_routes(RouteQuery()) for _ in range(6)))
    assert [[route.route_id for route in answer] for answer in answers] == [[1021]] * 6
    assert paths(seen).count("/login") == 2


async def test_the_window_read_pages_the_whole_window_once_and_holds_it():
    """The one call a search makes in place of a call per 线路: no name on the query, every page
    of it, and every 团期 it brought back kept for the reads that follow inside the same
    moment."""
    client, seen = erp({"/login": LOGIN, "/period/list": paged([[PERIOD], [OTHER_PERIOD]])})
    window = (date(2026, 10, 1), date(2026, 10, 31))
    rows = await client.list_window(*window)
    assert [row.period_id for row in rows] == [3001, 3099]
    assert "routeName" not in dict(seen[1].url.params)
    assert dict(seen[1].url.params)["departDateStart"] == "2026-10-01"
    assert [int(dict(r.url.params)["pageNum"]) for r in seen[1:]] == [1, 2]
    again = await client.list_window(*window)
    assert [row.period_id for row in again] == [3001, 3099]
    assert paths(seen).count("/period/list") == 2


async def test_a_renamed_routes_departures_come_out_of_the_window_read_already_made():
    """A period carries the route name it was made under, so a renamed 线路 finds nothing by
    name; the window's whole read answers for it, and a search that already made that read
    pays nothing for this one."""

    def periods(request: httpx.Request) -> dict:
        if "routeName" in dict(request.url.params):
            return page([])
        return paged([[PERIOD], [OTHER_PERIOD]])(request)

    client, seen = erp({"/login": LOGIN, "/period/list": periods})
    window = (date(2026, 10, 1), date(2026, 10, 31))
    await client.list_window(*window)
    rows = await client.list_departures(1021, "伊犁北疆环线 8 日纯玩小团（新版）", *window)
    assert [row.period_id for row in rows] == [3001]
    # Two pages of the window read and the one named query that came back empty: nothing else.
    assert paths(seen).count("/period/list") == 3


async def test_a_keyword_the_login_department_has_no_customer_for_asks_the_others():
    """The customer book is one department's own, and in production the login lands in a
    department that keeps none: an empty answer there says nothing about the agency's book, so
    each other authorised department is asked in turn until one answers."""

    def customers(request: httpx.Request) -> dict:
        theirs = request.headers["Authorization"] == f"Bearer erp-token-{OTHER_COMPANY_ID}"
        return page([CUSTOMER]) if theirs else page([])

    client, seen = erp({"/login": LOGIN, "/switch-company": switched, "/customer/list": customers})
    (customer,) = await client.search_customers("北京")
    assert customer.customer_id == 4101
    # The login department, then the first other one, and no further: 甘肃部 is never asked.
    assert paths(seen) == ["/login", "/customer/list", "/switch-company", "/customer/list"]
    assert body_of(seen[2]) == {"companyId": OTHER_COMPANY_ID}
    assert dict(seen[3].url.params)["keyword"] == "北京"


async def test_the_customer_book_is_paged_to_exhaustion_under_one_token():
    """Thousands of customers a department, so a keyword that matches past the first page is
    read to the end of the answer — and one department answering is the end of the search."""
    second = {**CUSTOMER, "customerId": 4102, "csCode": "TX-BJ-0002"}
    client, seen = erp(
        {
            "/login": LOGIN,
            "/switch-company": switched,
            "/customer/list": paged([[CUSTOMER], [second]]),
        }
    )
    found = await client.search_customers("北京")
    assert [row.customer_id for row in found] == [4101, 4102]
    assert paths(seen) == ["/login", "/customer/list", "/customer/list"]


def test_the_http_client_is_an_erp_client():
    client: ErpClient = HttpErpClient(BASE_URL, MOBILE, SECRET)
    assert isinstance(client, HttpErpClient)
