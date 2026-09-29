import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import auth
from cloud_warehouse.chat_delivery import DeliveryPool


async def test_disconnect_does_not_cancel_producer_and_shutdown_does():
    pool = DeliveryPool(limit=1)
    proceed, finished = asyncio.Event(), asyncio.Event()
    closed = []

    async def source():
        try:
            yield "first"
            await proceed.wait()
            finished.set()
            yield "saved"
        finally:
            closed.append(True)

    assert pool.claim() and not pool.claim()
    subscriber = pool.start(source())
    assert await anext(subscriber) == "first"
    await subscriber.aclose()
    assert not finished.is_set()
    proceed.set()
    await asyncio.wait_for(finished.wait(), 1)
    await asyncio.sleep(0)
    assert pool.reserved == 0 and closed == [True]
    proceed.clear()
    assert pool.claim()
    second = pool.start(source())
    assert await anext(second) == "first"
    await pool.close()
    assert closed == [True, True] and pool.reserved == 0
    await second.aclose()


async def test_slow_subscriber_has_bounded_queue_without_stopping_persistence():
    pool = DeliveryPool()
    done = asyncio.Event()

    async def source():
        for n in range(200):
            yield str(n)
        done.set()

    assert pool.claim()
    stream = pool.start(source())
    await asyncio.wait_for(done.wait(), 1)
    assert [value async for value in stream] == []
    assert pool.reserved == 0


def test_sliding_session_cannot_revive_expired_or_revoked_tokens(database, authentication, tenant):
    admin, _ = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin, email, "ACME-session-passphrase", {tenant.buyer.organization_id: ["advisor"]}
    )
    token = auth.login(authentication, email, "ACME-session-passphrase", "ACME-test")[
        "access_token"
    ]
    digest = auth._digest(token)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE auth_session SET expires_at=now()+interval '1 minute' WHERE token_hash=:h"
            ),
            {"h": digest},
        )
    assert (
        auth.authenticate(authentication, token, organization_id=tenant.buyer.organization_id)
        == user
    )
    with admin.connect() as conn:
        assert conn.scalar(
            text("SELECT expires_at>now()+interval '6 days' FROM auth_session WHERE token_hash=:h"),
            {"h": digest},
        )
    auth.logout(authentication, token)
    with pytest.raises(auth.Unauthenticated):
        auth.authenticate(authentication, token)
    token = auth.login(authentication, email, "ACME-session-passphrase", "ACME-test")[
        "access_token"
    ]
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE auth_session SET created_at=now()-interval '8 days',expires_at=now()-interval '1 second' WHERE token_hash=:h"
            ),
            {"h": auth._digest(token)},
        )
    with pytest.raises(auth.Unauthenticated):
        auth.authenticate(authentication, token)


async def test_http_disconnect_finishes_durable_advisor_turn(database, authentication, tenant):
    import json
    from uuid import UUID

    from cloud_warehouse import conversations
    from cloud_warehouse.api import create_app
    from commerce_common.streaming import AgentEvent

    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin, email, "ACME-disconnect-passphrase", {tenant.buyer.organization_id: ["advisor"]}
    )
    from cloud_warehouse.persistence import Principal

    actor = Principal(user, tenant.buyer.organization_id)
    token = auth.login(authentication, email, "ACME-disconnect-passphrase", "ACME-disconnect")[
        "access_token"
    ]
    identifier = UUID(conversations.create(runtime, actor, "advisor")["id"])
    first, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Agent:
        async def stream_turn(self, messages, context, state):
            yield AgentEvent.text_delta("ACME 正在处理")
            await release.wait()
            yield AgentEvent.text_delta("ACME 已保存")
            yield AgentEvent(type="turn_complete", data={})

        async def aclose(self):
            closed.set()

    app = create_app(runtime, authentication, agent_factory=lambda *args: Agent())
    body = json.dumps({"message": "ACME 断线查询"}).encode()
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        await first.wait()
        return {"type": "http.disconnect"}

    async def send(event):
        if event["type"] == "http.response.body" and event.get("body"):
            first.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "scheme": "http",
        "path": f"/v1/conversations/{identifier}/chat",
        "raw_path": f"/v1/conversations/{identifier}/chat".encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("test", 123),
        "http_version": "1.1",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {token}".encode()),
            (b"x-organization-id", str(actor.organization_id).encode()),
            (b"idempotency-key", b"ACME-disconnect"),
        ],
    }
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), 5)
        assert first.is_set() and not closed.is_set()
        release.set()
        await asyncio.wait_for(closed.wait(), 5)
        saved = conversations.get(runtime, actor, identifier)
        assert saved["turns"][-1]["status"] == "complete"
        assert "ACME 已保存" in json.dumps(saved, ensure_ascii=False, default=str)
