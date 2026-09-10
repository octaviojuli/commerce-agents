# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""ACME 旅行社 example API: the 旅行社 ERP behind the shared storefront routes, the
``present_shortlist`` extension and the share link its card carries, the conversation's
live 占位 on every cart payload, and the advisor's own conversation history. There is no
merchant portal in this example.

    uvicorn tour.api.main:app --app-dir examples --reload --port 8004

Each advisor signs in with their own ERP mobile and password (``POST /api/login``): the token
that buys stays in this process, keyed by the ERP employee, and so do their sessions and their
memory, so two advisors of one deployment share nothing. ``api/advisors.py`` holds the logins;
the deployment's own account in the environment boots the catalog and resolves the 同行
customer, and answers no session.

An advisor keeps the workbench open all day, so this example is the one whose sessions and
memory are on disk: ``TOUR_STATE_DIR`` (``data/.state/`` unset) holds ``sessions.sqlite``,
which ``api/store.py``'s ``SqliteSessionStore`` writes, and ``memory-store.json``, which
the core's ``JsonFileMemoryStore`` writes. A restart keeps both, so the client resumes a
conversation by sending its id in ``X-Session-Id`` and needs no route to do it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from commerce_common.memory import JsonFileMemoryStore
from demo_common import (
    REPO_ROOT,
    MemorySeeder,
    SessionRecord,
    UnknownSessionError,
    build_storefront_host,
    load_demo_env,
)
from shopping_agent_runtime import ShoppingAgent

from .advisors import NEED_LOGIN, AdvisorLogin, AdvisorRegistry, FixtureAdvisorRegistry
from .agent_config import brand_name, build_shopping_config
from .erp_client import ErpAuth, ErpClient, ErpError, ErpThrottled
from .http_erp import HttpErpClient
from .mock_erp import MockErpClient
from .shortlist import build_shortlist_extension
from .store import SqliteSessionStore, display_messages
from .tour_backend import DATA_DIR, TourBackend, TourToolExecutor, first_advisor_mobile

load_demo_env(DATA_DIR.parent)

# Where this deployment's own state lives, created at boot. Both files hold what the advisor
# said and what was remembered about them, so a deployment puts them somewhere it backs up
# and nothing here is committed.
STATE_DIR = Path(os.environ.get("TOUR_STATE_DIR", "").strip() or DATA_DIR / ".state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger(__name__)

# How many conversations the history list carries at most.
MAX_HISTORY = 50

# What a live deployment answers the demo's own session start with: a session belongs to the
# ERP employee a login named, and nothing else may name one.
LOGIN_FIRST = "请通过 /api/login 登录"
# The status one ERP refusal of a login is answered with; anything else the ERP does is 500.
_LOGIN_STATUS: dict[type[ErpError], int] = {ErpAuth: 401, ErpThrottled: 429}

# The 同行 customer the fixtures book for; a live deployment names its own by 客户编码.
MOCK_CUSTOMER_ID = 4101


def build_erp() -> ErpClient:
    """A real 旅行社 ERP when TOUR_ERP_BASE_URL names one, the fixtures in ``data/``
    otherwise. The account is the deployment's own, the mobile and the password alone, and it
    answers no session: it boots the listing snapshot and resolves the 同行 customer, and an
    advisor's own login is what their conversation reads through. Neither credential ever
    reaches the model."""
    base_url = os.environ.get("TOUR_ERP_BASE_URL", "").strip()
    if base_url:
        return HttpErpClient.from_login(
            base_url, os.environ["TOUR_ERP_MOBILE"], os.environ["TOUR_ERP_PASSWORD"]
        )
    return MockErpClient()


erp = build_erp()
# Whether the ERP behind the seam is the agency's own. Everything that is a fixture in the
# demo and the deployment's own against a real ERP hangs off this one line: the advisor's
# identity and department come from the ERP login rather than from users.json, the 同行
# customer is resolved from TOUR_ERP_CUSTOMER_CODE (the ERP's csCode) instead of being an id
# in the environment, an order is written through the 门店 TOUR_ERP_STORE_NAME names, the
# store's name is TOUR_BRAND_NAME, no memory is seeded, and the policy tool is not registered,
# because the agency's rules are in a knowledge base this deployment does not read.
# TOUR_ERP_ALLOW_PAST lists departures that already left, for a beta with no future ones.
live = isinstance(erp, HttpErpClient)
# Where the logged-in advisors are. Against the agency's own ERP a login is the advisor's own
# mobile and password, forwarded once and kept as a token; on the fixtures it is a profile in
# data/users.json and any password, because a fixture has none to check against.
registry: AdvisorRegistry = (
    AdvisorRegistry(os.environ["TOUR_ERP_BASE_URL"].strip())
    if live
    else FixtureAdvisorRegistry(erp)
)
backend = TourBackend(
    erp,
    customer_id=0 if live else MOCK_CUSTOMER_ID,
    customer_code=os.environ.get("TOUR_ERP_CUSTOMER_CODE", "").strip(),
    contact_mobile=os.environ.get("TOUR_ERP_MOBILE") or first_advisor_mobile(),
    allow_past=os.environ.get("TOUR_ERP_ALLOW_PAST") == "1",
    live=live,
    store_name=brand_name() if live else "",
    order_store_name=os.environ.get("TOUR_ERP_STORE_NAME", "").strip(),
    registry=registry,
)
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    # TOUR_MEMORY_RETENTION_DAYS reaches the store through the config's
    # memory_retention_days, which the agent's MemoryRuntime wraps the store with.
    config=build_shopping_config(live=live),
    memory_store=JsonFileMemoryStore(STATE_DIR / "memory-store.json"),
    extra_presentation_tools=[build_shortlist_extension()],
    executor_class=TourToolExecutor,
)
sessions = SqliteSessionStore(STATE_DIR / "sessions.sqlite")
# The seeded habits are this example's own invention, so a live deployment starts with an
# empty memory and the advisor's own facts are the ones the conversation extracts.
MEMORY_SEED = DATA_DIR / ("memory-seed-empty.json" if live else "memory-seed.json")


def holds_payload(record: SessionRecord) -> dict:
    """The 占位 behind the conversation's cart lines: the ERP's 订单号, the 团期 it holds,
    and what is left of the backend's own 30-minute window on the host's UTC clock."""
    now = datetime.now(UTC)
    return {
        "holds": [
            {
                "hold_id": order_no,
                "product_id": product_id,
                "expires_at": expires_at.isoformat(),
                "seconds_remaining": max(0, int((expires_at - now).total_seconds())),
            }
            for order_no, product_id, expires_at in backend.holds_snapshot(record.session_id)
        ]
    }


host = build_storefront_host(
    title="ACME 旅行社 demo API",
    example_root=DATA_DIR.parent,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(MEMORY_SEED),
    cart_extras=holds_payload,
    sessions=sessions,
)
app = host.app


def install_login_guard(current: FastAPI) -> None:
    """The two things a live deployment adds around the login.

    The demo's own session start goes. ``POST /api/session`` binds a session to whatever
    principal the caller names, which is the demo's stand-in for a credential, and against the
    agency's own ERP the principal is the employee behind a login. So that one route answers 403
    and the workbench signs in through ``/api/login`` instead; every other route stands,
    resuming a session by its id included.

    And a route that runs into a login the ERP no longer accepts answers 401 with the ERP's own
    words, rather than an outage, so the workbench asks ``/api/advisor`` and shows its sign-in
    screen. Inside the conversation the same text is what the executor relays."""

    @current.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "POST" and request.url.path == "/api/session":
            return JSONResponse({"detail": LOGIN_FIRST}, status_code=403)
        return await call_next(request)

    @current.exception_handler(ErpAuth)
    async def sign_in_again(request: Request, error: Exception) -> Response:
        del request
        return JSONResponse({"detail": str(error)}, status_code=401)


if live:
    install_login_guard(app)

# The listing snapshot has to be there before the first catalog read, and
# build_storefront_host takes a closed on_startup list, so it is loaded by wrapping the
# lifespan the app already has.
_seed_memory = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(current: FastAPI) -> AsyncIterator[None]:
    async with _seed_memory(current):
        await backend.load_listings()
        yield


app.router.lifespan_context = _lifespan


class LoginRequest(BaseModel):
    """The advisor's own ERP account. Neither value is stored: they go to the ERP's own
    ``POST /login`` once, and this process keeps the token it answers with."""

    mobile: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


def advisor_payload(login: AdvisorLogin) -> dict:
    """Who the ERP says the logged-in advisor is: the employee their session is keyed by, their
    name, the department the login landed in, and how many they read across."""
    return {
        "user_id": login.user_id,
        "name": login.name,
        "department": login.department,
        "departments": login.departments,
    }


@app.post("/api/login")
async def login_advisor(request: LoginRequest) -> dict:
    """Sign the advisor in against the ERP and open a conversation for them. The session is
    keyed by the ERP employee, so their earlier conversations and what is remembered about them
    are theirs and nobody else's. The ERP owns the throttle — ten failures lock the mobile for
    fifteen minutes — and its refusal is answered with its own status and its own Chinese
    message; nothing about the attempt but the outcome is logged."""
    try:
        advisor = await registry.login(request.mobile, request.password)
    except ErpError as error:
        status = next((code for cls, code in _LOGIN_STATUS.items() if isinstance(error, cls)), 500)
        log.warning("advisor login failed status=%d", status)
        raise HTTPException(status_code=status, detail=str(error)) from None
    record = host.sessions.start(advisor.user_id)
    return {"session_id": record.session_id, "advisor": advisor_payload(advisor)}


@app.post("/api/logout")
async def logout_advisor(record: host.CurrentSession) -> dict:
    """Drop the advisor's token. The conversation stays where it is — it is on disk, and its
    own reads say to sign in again — and signing in opens a new one."""
    registry.logout(record.user_id)
    return {"ok": True}


@app.get("/api/advisor")
async def current_advisor(record: host.CurrentSession) -> dict:
    """Whether the session's advisor still holds an ERP token, and who the ERP says they are.
    ``logged_in`` is false once the token is gone — a restart, its eight hours, a logout — and
    that is what puts the workbench back on its sign-in screen; the conversation itself is
    still there to carry on with."""
    login = registry.get(record.user_id)
    if login is None:
        return {
            "logged_in": False,
            "user_id": record.user_id,
            "name": "",
            "department": "",
            "departments": 0,
        }
    return {"logged_in": True, **advisor_payload(login)}


@app.post("/api/sessions/new")
async def new_session(record: host.CurrentSession) -> dict:
    """Another conversation for the advisor already signed in. The credentials are not in the
    browser to start one with, and the demo's own start route names a principal, so a 新会话
    is asked for on the session the advisor holds and opened under the same ERP employee. A
    login that is gone — a restart, its eight hours, a logout — is a 401 in the same words the
    conversation uses, and the workbench goes back to its sign-in screen."""
    if registry.get(record.user_id) is None:
        raise HTTPException(status_code=401, detail=NEED_LOGIN)
    fresh = host.sessions.start(record.user_id)
    return {"session_id": fresh.session_id}


@app.get("/api/sessions")
async def list_sessions(record: host.CurrentSession) -> dict:
    """The caller's own conversations, the one written last first, at most
    ``MAX_HISTORY``. The caller is the session id in the header and nothing else, so the
    list is theirs by construction; ``current`` marks the conversation the header names.
    Resuming one takes no route: the client sends its id in ``X-Session-Id``."""
    return {
        "sessions": [
            {
                "session_id": summary.session_id,
                "title": summary.title,
                "updated_at": summary.updated_at.isoformat(),
                "message_count": summary.message_count,
                "current": summary.session_id == record.session_id,
            }
            for summary in sessions.summaries(record.user_id)[:MAX_HISTORY]
        ]
    }


# The path names a conversation of the caller's, not the caller: identity stays in the
# header, and a conversation that is not theirs is not found.
@app.get("/api/sessions/{past_id}/messages")
async def session_messages(past_id: str, record: host.CurrentSession) -> dict:
    """One conversation as a person reads it: the advisor's turns and the assistant's
    replies, with the tool exchange and the host's app-event notes left out."""
    stored = sessions.read_state(past_id)
    if stored is None or stored[1]["user_id"] != record.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_id": past_id, "messages": display_messages(sessions.transcript(past_id))}


class ShareChoiceRequest(BaseModel):
    departure_id: str = Field(min_length=1, max_length=80)


@app.post("/api/share/{token}/choose")
async def share_choose(token: str, request: ShareChoiceRequest) -> dict:
    """The customer's tap on a 团期 in the shortlist the advisor sent them. The customer is
    not a user of this API, so the route takes no session: the token stands for the
    shortlist, and all the call does is tell the advisor's conversation what was chosen.
    The id is checked against the shortlist the server minted, so the note the model reads
    is the host's own text. The app answers only to loopback host names, which its
    ``TrustedHostMiddleware`` applies to every route including this one."""
    shared = backend.share_record(token)
    if shared is None:
        raise HTTPException(status_code=404, detail="分享链接不存在或已失效")
    if request.departure_id not in shared.departure_ids:
        raise HTTPException(status_code=404, detail="该团期不在这份分享清单里")
    try:
        records = [host.sessions.require(shared.session_id)]
    except UnknownSessionError:
        # The conversation that sent the link has ended; whatever the advisor has open now
        # is where they read it.
        records = host.sessions.sessions_for_user(shared.advisor_id)
    if not records:
        raise HTTPException(status_code=410, detail="顾问的会话已结束，请让顾问重新发送清单")
    for record in records:
        record.pending_app_events.append(
            f"客人已在分享页选定团期 {request.departure_id}（分享 {token}）"
        )
        host.sessions.save(record)  # outside a session request, so nothing writes it back
    return {"ok": True}
