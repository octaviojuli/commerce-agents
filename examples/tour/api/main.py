# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""ACME 旅行社 example API: the 旅行社 ERP behind the shared storefront routes, the
``present_shortlist`` extension and the share link its card carries, and the conversation's
live 占位 on every cart payload. There is no merchant portal in this example.

    uvicorn tour.api.main:app --app-dir examples --reload --port 8004
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from commerce_common.memory import InMemoryMemoryStore
from demo_common import (
    REPO_ROOT,
    MemorySeeder,
    SessionRecord,
    UnknownSessionError,
    build_storefront_host,
    load_demo_env,
)
from shopping_agent_runtime import ShoppingAgent

from .agent_config import brand_name, build_shopping_config
from .erp_client import ErpClient
from .http_erp import HttpErpClient
from .mock_erp import MockErpClient
from .shortlist import build_shortlist_extension
from .tour_backend import DATA_DIR, TourBackend, TourToolExecutor, first_advisor_mobile

load_demo_env(DATA_DIR.parent)

# The 同行 customer the fixtures book for; a live deployment names its own by 客户编码.
MOCK_CUSTOMER_ID = 4101


def build_erp() -> ErpClient:
    """A real 旅行社 ERP when TOUR_ERP_BASE_URL names one, the fixtures in ``data/``
    otherwise. The login is the advisor's own ERP account, the mobile and the password alone:
    the ERP picks the department and the client switches into a 团期's own for a write.
    Neither credential ever reaches the model."""
    base_url = os.environ.get("TOUR_ERP_BASE_URL", "").strip()
    if base_url:
        return HttpErpClient(
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
backend = TourBackend(
    erp,
    customer_id=0 if live else MOCK_CUSTOMER_ID,
    customer_code=os.environ.get("TOUR_ERP_CUSTOMER_CODE", "").strip(),
    contact_mobile=os.environ.get("TOUR_ERP_MOBILE") or first_advisor_mobile(),
    allow_past=os.environ.get("TOUR_ERP_ALLOW_PAST") == "1",
    live=live,
    store_name=brand_name() if live else "",
    order_store_name=os.environ.get("TOUR_ERP_STORE_NAME", "").strip(),
)
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(live=live),
    memory_store=InMemoryMemoryStore(),
    extra_presentation_tools=[build_shortlist_extension()],
    executor_class=TourToolExecutor,
)
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
)
app = host.app

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
