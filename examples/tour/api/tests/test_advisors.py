# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``AdvisorRegistry``: one advisor's own ERP login, what the registry keeps of it, and what
happens once the token behind it is gone. The ERP is the mock wire ``test_http_erp.py`` answers
the real client from, so the login on it is the ERP's own envelope and no server is involved.
``FixtureAdvisorRegistry`` is the same registry over ``data/users.json``, which holds no
passwords at all."""

import time

import pytest

from tour.api.advisors import (
    NOT_A_FIXTURE,
    AdvisorRegistry,
    FixtureAdvisorRegistry,
)
from tour.api.erp_client import ErpAuth, ErpThrottled, ErpUnavailable, RouteQuery
from tour.api.http_erp import EXPIRED
from tour.api.mock_erp import MockErpClient

from .test_http_erp import (
    BASE_URL,
    COMPANIES,
    LOGIN,
    MOBILE,
    ROUTE,
    SECRET,
    TOKEN,
    USER_INFO,
    Wire,
    page,
    paths,
)

# The advisor the fixture ERP logs in, as ``user_id`` names them everywhere above this module.
ADVISOR = f"erp-{USER_INFO['userId']}"
# What the ERP answers a wrong password and a locked mobile with.
WRONG = (401, {"code": 401, "message": "手机号或密码错误", "data": None})
LOCKED = (429, {"code": 429, "message": "登录过于频繁，请 15 分钟后再试", "data": None})
# A login whose token is already past its expiry: a restart-free way to reach the moment the
# ERP's eight hours are up.
STALE = {**LOGIN, "data": {**LOGIN["data"], "expiresIn": 0, "expiresAt": int(time.time()) - 10}}
# The demo advisor ``data/users.json`` names first, and the two values the fixture login is.
FIXTURE_MOBILE = "13900000001"
FIXTURE_ADVISOR = "demo-user"


def registry(replies: dict) -> tuple[AdvisorRegistry, list]:
    """A registry over a mock wire, and the requests its logins and reads put on it."""
    transport = Wire(replies)
    return AdvisorRegistry(BASE_URL, transport=transport), transport.seen


async def test_a_login_is_kept_as_the_token_and_the_salesperson_the_erp_named():
    live, seen = registry({"/login": LOGIN, "/route/list": page([ROUTE])})
    login = await live.login(MOBILE, SECRET)
    assert (login.user_id, login.name, login.mobile) == (ADVISOR, USER_INFO["userName"], MOBILE)
    assert (login.department, login.departments) == (USER_INFO["companyName"], len(COMPANIES))
    assert live.get(ADVISOR) is login
    # The client that stays reads on that token and logs in no second time.
    await login.client.search_routes(RouteQuery())
    assert paths(seen) == ["/login", "/route/list"]
    assert seen[1].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_the_password_reaches_the_erp_and_nothing_here_keeps_it():
    live, seen = registry({"/login": LOGIN, "/route/list": page([ROUTE])})
    login = await live.login(MOBILE, SECRET)
    assert SECRET in seen[0].content.decode()
    assert SECRET not in repr(vars(login.client))


async def test_a_wrong_password_is_the_erps_own_refusal_and_keeps_nobody():
    live, _ = registry({"/login": WRONG})
    with pytest.raises(ErpAuth, match="手机号或密码错误"):
        await live.login(MOBILE, "not-the-password")
    assert live.get(ADVISOR) is None


async def test_the_erps_own_throttle_is_relayed_as_it_is():
    live, _ = registry({"/login": LOCKED})
    with pytest.raises(ErpThrottled, match="15 分钟"):
        await live.login(MOBILE, SECRET)


async def test_an_erp_that_cannot_be_reached_is_an_outage_and_not_a_refusal():
    live, _ = registry({"/login": (500, {"code": 500, "message": "系统异常", "data": None})})
    with pytest.raises(ErpUnavailable):
        await live.login(MOBILE, SECRET)


async def test_a_login_the_erp_named_no_salesperson_on_is_not_a_login():
    nameless = {**LOGIN, "data": {**LOGIN["data"], "userInfo": {}}}
    live, _ = registry({"/login": nameless})
    with pytest.raises(ErpAuth):
        await live.login(MOBILE, SECRET)


async def test_a_token_past_its_expiry_asks_the_advisor_to_sign_in_again():
    live, seen = registry({"/login": STALE, "/route/list": page([ROUTE])})
    login = await live.login(MOBILE, SECRET)
    # The registry stops naming them, so the workbench shows its sign-in screen …
    assert live.get(ADVISOR) is None
    # … and the client holds no password to log in with, so a read says so and buys no token.
    with pytest.raises(ErpAuth, match=EXPIRED):
        await login.client.search_routes(RouteQuery())
    assert paths(seen) == ["/login"]


async def test_a_logout_drops_the_token_and_the_session_keeps_its_advisor():
    live, _ = registry({"/login": LOGIN})
    login = await live.login(MOBILE, SECRET)
    live.logout(login.user_id)
    assert live.get(ADVISOR) is None
    live.logout(login.user_id)  # again, on a registry that no longer holds them


async def test_two_advisors_are_two_logins():
    other = {"userId": 9, "userName": "沈岚", "companyId": 5, "companyName": "ACME 旅行社 青海部"}
    live, _ = registry({"/login": [LOGIN, {**LOGIN, "data": {**LOGIN["data"], "userInfo": other}}]})
    first = await live.login(MOBILE, SECRET)
    second = await live.login("13800138999", SECRET)
    assert {first.user_id, second.user_id} == {ADVISOR, "erp-9"}
    assert live.get("erp-9") is second and live.get(ADVISOR) is first
    assert first.client is not second.client


async def test_the_fixtures_have_no_passwords_so_any_password_signs_an_advisor_in():
    client = MockErpClient()
    live = FixtureAdvisorRegistry(client)
    login = await live.login(FIXTURE_MOBILE, "whatever-the-workbench-sent")
    # The 工号 the display name carries is not part of a 联系人 the ERP would accept.
    assert (login.user_id, login.name) == (FIXTURE_ADVISOR, "林晓")
    assert (login.department, login.departments) == ("上海徐汇门店", 1)
    assert login.client is client and live.get(FIXTURE_ADVISOR) is login


async def test_a_mobile_the_fixtures_do_not_name_is_refused():
    live = FixtureAdvisorRegistry(MockErpClient())
    with pytest.raises(ErpAuth, match=NOT_A_FIXTURE):
        await live.login("13000000000", "anything")
