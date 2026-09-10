# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``HttpErpClient`` against an ``httpx.MockTransport``: what each call puts on the wire and
what each answer, refusal, and outage maps back to. The canned bodies are the beta
environment's own envelopes and keys with ACME's fictional catalog in them, so these tests
hold the client to the ERP's contract without a server."""

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
from tour.api.http_erp import HttpErpClient

BASE_URL = "https://erp.acme-tour.example/aicli"
PREFIX = "/aicli"
MOBILE = "13800138000"
SECRET = "fixture-only-secret"
COMPANY_ID = 2
TOKEN = "erp-token-abc"

OTHER_COMPANY_ID = 5
SWITCHED = "erp-token-qinghai"
COMPANIES = [
    {"companyId": COMPANY_ID, "companyName": "ACME 旅行社 新疆部"},
    {"companyId": OTHER_COMPANY_ID, "companyName": "ACME 旅行社 青海部"},
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


def page(rows: list[dict]) -> dict:
    return {
        "code": 200,
        "message": "获取成功",
        "data": {
            "list": rows,
            "total": len(rows),
            "pageNum": 1,
            "pageSize": 50,
            "totalPages": 1,
        },
    }


def ok(data: dict) -> dict:
    return {"code": 200, "message": "操作成功", "data": data}


def erp(replies: dict) -> tuple[HttpErpClient, list[httpx.Request]]:
    """A client whose transport answers each path from ``replies``: a JSON body, a
    ``(status, body)`` pair, raw text, an exception to raise, or a list of those in turn."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = replies[request.url.path.removeprefix(PREFIX)]
        if isinstance(reply, list):
            reply = reply.pop(0)
        status, body = reply if isinstance(reply, tuple) else (200, reply)
        if isinstance(body, Exception):
            raise body
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    client = HttpErpClient(BASE_URL, MOBILE, SECRET, transport=transport)
    return client, seen


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


def test_the_http_client_is_an_erp_client():
    client: ErpClient = HttpErpClient(BASE_URL, MOBILE, SECRET)
    assert isinstance(client, HttpErpClient)
