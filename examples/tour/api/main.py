# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""ACME 旅行社 example API: the mock 旅行社 ERP behind the shared storefront routes, the
advisor's live seat holds on every cart payload, and the ERP's expiry notices delivered as
app events. There is no merchant portal in this example.

    uvicorn tour.api.main:app --app-dir examples --reload --port 8004
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from commerce_common.memory import InMemoryMemoryStore
from demo_common import (
    REPO_ROOT,
    MemorySeeder,
    SessionRecord,
    build_storefront_host,
    load_demo_env,
)
from shopping_agent_runtime import ShoppingAgent

from .agent_config import build_shopping_config
from .mock_erp import MockErpClient
from .tour_backend import DATA_DIR, TourBackend, TourToolExecutor

load_demo_env(DATA_DIR.parent)

erp = MockErpClient()
backend = TourBackend(erp)
agent = ShoppingAgent(
    backend=backend,
    skills_dir=REPO_ROOT / "shopping-agent" / "skills",
    config=build_shopping_config(),
    memory_store=InMemoryMemoryStore(),
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
    for note in erp.collect_notifications():
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
