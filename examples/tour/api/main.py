# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""ACME 旅行社 example API: the 旅行社 ERP behind the shared storefront routes, the
``present_shortlist`` extension and the share link its card carries, the advisor's live
seat holds on every cart payload, and the ERP's expiry notices delivered as app events.
There is no merchant portal in this example.

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

from .agent_config import build_shopping_config
from .erp_client import ErpClient
from .http_erp import HttpErpClient
from .mock_erp import MockErpClient
from .shortlist import build_shortlist_extension
from .tour_backend import DATA_DIR, TourBackend, TourToolExecutor

load_demo_env(DATA_DIR.parent)


def build_erp() -> ErpClient:
    """A real 旅行社 ERP when TOUR_ERP_BASE_URL names one, the fixtures in ``data/``
    otherwise. TOUR_ERP_TOKEN is the bearer the host sends; it never reaches the model."""
    base_url = os.environ.get("TOUR_ERP_BASE_URL", "").strip()
    if base_url:
        return HttpErpClient(base_url, os.environ.get("TOUR_ERP_TOKEN", ""))
    return MockErpClient()


erp = build_erp()
backend = TourBackend(erp)
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
    memory_store=InMemoryMemoryStore(),
    extra_presentation_tools=[build_shortlist_extension()],
    executor_class=TourToolExecutor,
)


def holds_payload(record: SessionRecord) -> dict:
    """The 占位 behind the conversation's cart lines, each with what is left of its TTL on
    the host's UTC clock, which the mock ERP stamps its expiries from as well."""
    now = datetime.now(UTC)
    return {
        "holds": [
            {
                "hold_id": hold.hold_id,
                "product_id": hold.departure_id,
                "expires_at": hold.expires_at.isoformat(),
                "seconds_remaining": max(0, int((hold.expires_at - now).total_seconds())),
            }
            for hold in backend.holds_snapshot(record.session_id)
        ]
    }


def deliver_hold_events() -> None:
    """Queue the ERP's expiry notices on every live session of the advisor they concern;
    the next turn hands them to the agent."""
    # Only the mock notices its own expiries, so this is a no-op against an HTTP ERP.
    collect = getattr(erp, "collect_notifications", None)
    for note in collect() if collect else ():
        for record in host.sessions.sessions_for_user(note.advisor_id):
            record.pending_app_events.append(note.message)
            host.sessions.save(record)  # outside a request, so nothing else writes it back


host = build_storefront_host(
    title="ACME 旅行社 demo API",
    example_root=DATA_DIR.parent,
    backend=backend,
    agent=agent,
    memory_seeder=MemorySeeder(DATA_DIR / "memory-seed.json"),
    cart_extras=holds_payload,
    before_turn=deliver_hold_events,
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
