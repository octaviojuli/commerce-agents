# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The three routes an advisor's login is: ``POST /api/login``, ``GET /api/advisor`` and
``POST /api/logout``, over the fixture registry the demo runs on. The guard a live deployment
installs around them is tested on an app of its own, because the module under test builds its
client and its registry at import time and the suite never talks to an agency's ERP."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from demo_common import SESSION_HEADER
from tour.api.advisors import NEED_LOGIN, NOT_A_FIXTURE
from tour.api.erp_client import ErpAuth
from tour.api.main import LOGIN_FIRST, install_login_guard

# The advisor ``data/users.json`` names first: their mobile, the id their session is keyed by,
# the name an order is signed with, and the 门店 the profile states.
MOBILE = "13900000001"
ADVISOR = "demo-user"
NAME = "林晓"
DEPARTMENT = "上海徐汇门店"
# The other fixture advisor, who is a different history and a different set of facts.
OTHER_MOBILE = "13900000002"
OTHER_ADVISOR = "demo-user-2"


@pytest.fixture
def signed_out(main):
    """Leave the process's registry as the test found it: a login lives in it until it is
    dropped, and the module is built once for the whole suite."""
    yield
    for advisor in (ADVISOR, OTHER_ADVISOR):
        main.registry.logout(advisor)


def login(client: TestClient, mobile: str = MOBILE, password: str = "any-password"):
    return client.post("/api/login", json={"mobile": mobile, "password": password})


def test_a_login_opens_a_session_for_the_advisor_the_erp_named(main, client, signed_out):
    body = login(client).json()
    assert body["advisor"] == {
        "user_id": ADVISOR,
        "name": NAME,
        "department": DEPARTMENT,
        "departments": 1,
    }
    # The session is bound to that advisor, so their earlier conversations and their memory
    # are the ones this one reads.
    assert main.host.sessions.require(body["session_id"]).user_id == ADVISOR


def test_two_advisors_get_their_own_sessions(main, client, signed_out):
    mine = login(client).json()["session_id"]
    theirs = login(client, OTHER_MOBILE).json()["session_id"]
    assert main.host.sessions.require(mine).user_id == ADVISOR
    assert main.host.sessions.require(theirs).user_id == OTHER_ADVISOR


def test_the_advisor_route_names_the_login_behind_the_session(client, signed_out):
    session_id = login(client).json()["session_id"]
    body = client.get("/api/advisor", headers={SESSION_HEADER: session_id}).json()
    assert body == {
        "logged_in": True,
        "user_id": ADVISOR,
        "name": NAME,
        "department": DEPARTMENT,
        "departments": 1,
    }


def test_a_logout_drops_the_token_and_the_session_stays(main, client, signed_out):
    headers = {SESSION_HEADER: login(client).json()["session_id"]}
    assert client.post("/api/logout", headers=headers).json() == {"ok": True}
    body = client.get("/api/advisor", headers=headers).json()
    assert body["logged_in"] is False and body["name"] == ""
    # The conversation itself is still the advisor's; only the ERP token is gone.
    assert main.host.sessions.require(headers[SESSION_HEADER]).user_id == ADVISOR


def test_a_session_no_login_stands_behind_is_not_signed_in(client):
    headers = {SESSION_HEADER: client.post("/api/session", json={}).json()["session_id"]}
    assert client.get("/api/advisor", headers=headers).json()["logged_in"] is False


def test_a_mobile_the_fixtures_do_not_name_is_a_401_in_the_erps_own_words(client):
    refused = login(client, "13000000000")
    assert refused.status_code == 401 and refused.json()["detail"] == NOT_A_FIXTURE


def test_a_login_without_both_values_is_not_a_login(client):
    assert client.post("/api/login", json={"mobile": MOBILE}).status_code == 422
    assert client.post("/api/login", json={"mobile": "", "password": "x"}).status_code == 422


def test_the_demos_own_session_start_stands_on_the_fixtures(client):
    """The smoke script and the suite start a session by naming a profile, which is what the
    fixtures are for; the guard below is what takes that off a live deployment."""
    assert client.post("/api/session", json={"user_id": ADVISOR}).status_code == 200


@pytest.fixture
def guarded() -> TestClient:
    """A live deployment's app, as far as the guard is concerned: the demo's session start, one
    route of its own, and one that runs into a login the ERP no longer accepts."""
    app = FastAPI()

    @app.post("/api/session")
    async def start() -> dict:
        return {"session_id": "would-have-been-issued"}

    @app.get("/api/advisor")
    async def advisor() -> dict:
        return {"logged_in": False}

    @app.get("/api/orders")
    async def orders() -> dict:
        raise ErpAuth(NEED_LOGIN)

    install_login_guard(app)
    return TestClient(app)


def test_the_live_guard_answers_the_demos_session_start_with_a_403(guarded):
    refused = guarded.post("/api/session", json={"user_id": "someone-else"})
    assert refused.status_code == 403 and refused.json() == {"detail": LOGIN_FIRST}
    # Every other route is untouched: a session resumed by its id keeps working.
    assert guarded.get("/api/advisor").status_code == 200


def test_a_route_that_needs_a_login_says_so_instead_of_reporting_an_outage(guarded):
    """The workbench reads this as a sign-in and not as a broken ERP, and asks
    ``/api/advisor``."""
    answered = guarded.get("/api/orders")
    assert answered.status_code == 401 and answered.json() == {"detail": NEED_LOGIN}
