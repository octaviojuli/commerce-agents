from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, conversations, distribution
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from commerce_common.streaming import AgentEvent

from .test_api import PASSWORD


def conversation(runtime, tenant):
    return UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])


def test_conversation_leases_are_persistent_and_idempotent(database, tenant):
    _, runtime = database
    identifier = conversation(runtime, tenant)

    def attempt(key):
        try:
            return conversations.begin(runtime, tenant.buyer, identifier, key, "ACME 查询线路")
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["request-one", "request-two"]))
    assert sum(item is not None for item in results) == 1
    work = next(item for item in results if item)
    key = "request-one" if results[0] else "request-two"
    events = [
        AgentEvent.text_delta("ACME 回答").model_dump(),
        AgentEvent(type="turn_complete", data={}).model_dump(),
    ]
    messages = [*work["messages"], {"role": "assistant", "content": "ACME 回答"}]
    conversations.finish(runtime, tenant.buyer, work, {}, messages, events)
    assert conversations.get(runtime, tenant.buyer, identifier)["turns"][0]["events"] == events
    assert conversations.begin(runtime, tenant.buyer, identifier, key, "ACME 查询线路") == {
        "replay": events
    }
    with pytest.raises(Conflict):
        conversations.begin(runtime, tenant.buyer, identifier, key, "另一条消息")


def test_expired_lease_cannot_overwrite_a_new_turn(database, tenant):
    admin, runtime = database
    identifier = conversation(runtime, tenant)
    abandoned = conversations.begin(runtime, tenant.buyer, identifier, "first", "ACME 第一条")
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE agent_conversation SET lease_until=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": identifier},
        )
    replacement = conversations.begin(runtime, tenant.buyer, identifier, "second", "ACME 第二条")
    with pytest.raises(Conflict):
        conversations.finish(runtime, tenant.buyer, abandoned, {}, abandoned["messages"], [])
    conversations.interrupt(runtime, tenant.buyer, replacement)
    rows = conversations.get(runtime, tenant.buyer, identifier)
    assert [row["status"] for row in rows["turns"]] == [
        "interrupted",
        "interrupted",
    ]
    with pytest.raises(Conflict):
        conversations.begin(runtime, tenant.buyer, identifier, "first", "ACME 第一条")


def test_conversation_user_org_and_authorization_isolation(database, authentication, tenant):
    admin, runtime = database
    identifier = conversation(runtime, tenant)
    other = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
            {"id": other, "email": f"{other}@acme.example"},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,ARRAY['advisor'])"
            ),
            {"id": other, "org": tenant.buyer.organization_id},
        )
    other_actor = Principal(other, tenant.buyer.organization_id)
    assert conversations.list_page(runtime, other_actor)["items"] == []
    for actor in (other_actor, tenant.supplier):
        with pytest.raises(Forbidden):
            conversations.get(runtime, actor, identifier)
        with transaction(runtime, actor) as conn:
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM agent_conversation WHERE id=:id"), {"id": identifier}
                )
                == 0
            )
    grant = distribution.listing(runtime, authentication, tenant.supplier)["items"][0]
    distribution.control(
        runtime,
        tenant.supplier,
        grant["id"],
        distribution.Control(
            version=grant["version"],
            active=False,
            valid_from=grant["valid_from"],
            expires_at=grant["expires_at"],
            note="ACME conversation access revoked",
        ),
    )
    with pytest.raises(Conflict, match="授权"):
        conversations.get(runtime, tenant.buyer, identifier)
    with pytest.raises(Conflict, match="授权"):
        conversations.begin(runtime, tenant.buyer, identifier, "changed", "ACME 查询")
    assert conversations.create(runtime, tenant.buyer, "advisor")["id"]


class FakeAgent:
    def __init__(self, calls, *, fail=False, on_stream=None):
        self.calls, self.fail, self.on_stream = calls, fail, on_stream

    async def stream_turn(self, messages, context, state):
        self.calls.append(context)
        if self.on_stream:
            self.on_stream()
        yield AgentEvent.text_delta("ACME 云仓回答")
        if self.fail:
            raise RuntimeError("sensitive upstream detail that must not be returned")
        messages.append({"role": "assistant", "content": "ACME 云仓回答"})
        yield AgentEvent(type="turn_complete", data={"stop_reason": "end_turn"})


def test_conversation_http_stream_replay_and_interruption(database, authentication, tenant):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    calls = []
    options = {"fail": False}
    app = create_app(
        runtime,
        authentication,
        agent_factory=lambda role, actor, buyer: FakeAgent(calls, **options),
    )
    with TestClient(app) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        created = client.post("/v1/conversations", headers=headers, json={"role": "advisor"})
        assert created.status_code == 201
        identifier = created.json()["id"]
        url = f"/v1/conversations/{identifier}/chat"
        request = {
            "headers": {**headers, "Idempotency-Key": "ACME-1"},
            "json": {"message": "查询线路"},
        }
        first = client.post(url, **request)
        assert first.status_code == 200 and "event: turn_complete" in first.text
        assert "ACME 云仓回答" in first.text and first.headers["cache-control"] == "no-store"
        assert client.post(url, **request).text == first.text and len(calls) == 1
        saved = client.get(f"/v1/conversations/{identifier}", headers=headers).json()
        assert "messages" not in saved and saved["turns"][0]["status"] == "complete"
        listing = client.get("/v1/conversations?role=advisor&limit=1", headers=headers)
        assert listing.status_code == 200 and listing.json()["items"][0]["id"] == identifier
        assert listing.headers["cache-control"] == "no-store"
        assert (
            client.get(f"/v1/conversations/{identifier}?limit=0", headers=headers).status_code
            == 422
        )
        assert client.get("/v1/conversations?role=unknown", headers=headers).status_code == 422
        options["fail"] = True
        failed = client.post(
            url, headers={**headers, "Idempotency-Key": "ACME-2"}, json={"message": "中断测试"}
        )
        assert "event: error" in failed.text and "sensitive" not in failed.text
        assert "event: turn_complete" not in failed.text
        saved = client.get(f"/v1/conversations/{identifier}", headers=headers).json()
        assert "messages" not in saved and saved["turns"][-1]["status"] == "interrupted"


def test_revoked_login_cannot_emit_model_output(database, authentication, tenant):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})

    def revoke():
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE auth_session SET revoked_at=now() WHERE user_id=:id"), {"id": user}
            )

    app = create_app(
        runtime, authentication, agent_factory=lambda *args: FakeAgent([], on_stream=revoke)
    )
    with TestClient(app) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        identifier = client.post(
            "/v1/conversations", headers=headers, json={"role": "advisor"}
        ).json()["id"]
        response = client.post(
            f"/v1/conversations/{identifier}/chat",
            headers={**headers, "Idempotency-Key": "ACME-revoked"},
            json={"message": "ACME 查询"},
        )
        assert "ACME 云仓回答" not in response.text and "event: error" in response.text


def test_history_pages_are_scoped_stable_and_hide_revoked_titles(database, tenant):
    admin, runtime = database
    identifiers = [conversation(runtime, tenant) for _ in range(5)]
    for identifier in identifiers:
        work = conversations.begin(runtime, tenant.buyer, identifier, "one", "ACME 客户询价")
        conversations.finish(runtime, tenant.buyer, work, {}, work["messages"], [])
    page = conversations.list_page(runtime, tenant.buyer, limit=2)
    found = [row["id"] for row in page["items"]]
    # New conversations and changes to updated_at do not shift the created_at cursor.
    new_id = conversation(runtime, tenant)
    while page["next_cursor"]:
        page = conversations.list_page(
            runtime, tenant.buyer, limit=2, before=UUID(page["next_cursor"])
        )
        found.extend(row["id"] for row in page["items"])
    assert set(found) == set(identifiers) and len(found) == 5 and new_id not in found
    with pytest.raises(Forbidden):
        conversations.list_page(runtime, tenant.supplier, role="merchant", before=identifiers[0])
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE id=:id"), {"id": tenant.grant_id}
        )
    page = conversations.list_page(runtime, tenant.buyer)
    assert all(not row["resumable"] and row["title"] is None for row in page["items"])
    for identifier in identifiers:
        with pytest.raises(Conflict):
            conversations.get(runtime, tenant.buyer, identifier)


def test_history_turn_pagination_and_server_only_model_context(database, tenant):
    _, runtime = database
    identifier = conversation(runtime, tenant)
    ids = []
    for n in range(7):
        work = conversations.begin(runtime, tenant.buyer, identifier, str(n), f"ACME {n}")
        assert len(work["messages"]) == 2 * n + 1
        ids.append(work["turn_id"])
        conversations.finish(
            runtime,
            tenant.buyer,
            work,
            {},
            [*work["messages"], {"role": "assistant", "content": "private model context"}],
            [AgentEvent.text_delta(str(n)).model_dump()],
        )
    page = conversations.get(runtime, tenant.buyer, identifier, limit=3)
    assert [row["id"] for row in page["turns"]] == ids[-3:]
    assert "messages" not in page and "state" not in page
    older = conversations.get(
        runtime, tenant.buyer, identifier, limit=3, before=UUID(page["next_cursor"])
    )
    assert [row["id"] for row in older["turns"]] == ids[1:4]
    oldest = conversations.get(
        runtime, tenant.buyer, identifier, limit=3, before=UUID(older["next_cursor"])
    )
    assert [row["id"] for row in oldest["turns"]] == ids[:1] and oldest["next_cursor"] is None
    other = conversation(runtime, tenant)
    with pytest.raises(Forbidden):
        conversations.get(runtime, tenant.buyer, other, before=ids[0])
    for limit in (0, 26):
        with pytest.raises(ValueError):
            conversations.get(runtime, tenant.buyer, identifier, limit=limit)


def test_history_event_bytes_limit_does_not_drop_older_turns(database, tenant):
    _, runtime = database
    identifier = conversation(runtime, tenant)
    for n in range(3):
        work = conversations.begin(runtime, tenant.buyer, identifier, str(n), "ACME")
        conversations.finish(
            runtime,
            tenant.buyer,
            work,
            {},
            [],
            [AgentEvent.text_delta("x" * 1_100_000).model_dump()],
        )
    found = []
    before = None
    while True:
        page = conversations.get(runtime, tenant.buyer, identifier, before=before)
        assert len(page["turns"]) == 1
        found.extend(row["id"] for row in page["turns"])
        if page["next_cursor"] is None:
            break
        before = UUID(page["next_cursor"])
    assert len(set(found)) == 3


def test_merchant_price_context_is_host_selected_persistent_and_not_chat_mutable(
    database, authentication, tenant
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: ["supplier_admin"]})
    contexts = []

    def factory(role, actor, buyer):
        contexts.append((role, buyer))
        return FakeAgent([])

    with TestClient(create_app(runtime, authentication, agent_factory=factory)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        for buyer in (uuid4(), tenant.supplier.organization_id):
            denied = client.post(
                "/v1/conversations",
                headers=headers,
                json={"role": "merchant", "buyer_context": str(buyer)},
            )
            assert denied.status_code == 403
        buyer = str(tenant.buyer.organization_id)
        created = client.post(
            "/v1/conversations",
            headers=headers,
            json={"role": "merchant", "buyer_context": buyer},
        )
        assert created.status_code == 201 and created.json()["buyer_context"] == buyer
        path = f"/v1/conversations/{created.json()['id']}"
        assert client.get(path, headers=headers).json()["buyer_context"] == buyer
        request_headers = {**headers, "Idempotency-Key": "ACME-context"}
        assert (
            client.post(
                path + "/chat",
                headers=request_headers,
                json={"message": "ACME query", "buyer_context": str(uuid4())},
            ).status_code
            == 422
        )
        result = client.post(
            path + "/chat", headers=request_headers, json={"message": "ACME query"}
        )
        assert "event: turn_complete" in result.text
        assert contexts == [("merchant", tenant.buyer.organization_id)]
        assert client.get(path, headers=headers).json()["buyer_context"] == buyer
        general = client.post("/v1/conversations", headers=headers, json={"role": "merchant"})
        assert general.status_code == 201 and general.json()["buyer_context"] is None
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE organization SET active=false WHERE id=:buyer"),
                {"buyer": tenant.buyer.organization_id},
            )
        assert client.get(path, headers=headers).status_code == 409
        partners = client.get("/v1/merchant/partners", headers=headers)
        assert partners.status_code == 200
        assert all(not row["valid"] for row in partners.json()["items"])
        assert (
            client.post(
                "/v1/conversations",
                headers=headers,
                json={"role": "merchant", "buyer_context": buyer},
            ).status_code
            == 403
        )


def test_merchant_conversation_rechecks_natural_grant_expiry(database, tenant):
    admin, runtime = database
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE distribution_grant SET expires_at=now()+interval '2 seconds' WHERE id=:id"
            ),
            {"id": tenant.grant_id},
        )
    identifier = UUID(
        conversations.create(runtime, tenant.supplier, "merchant", tenant.buyer.organization_id)[
            "id"
        ]
    )
    assert (
        conversations.get(runtime, tenant.supplier, identifier)["buyer_context"]
        == tenant.buyer.organization_id
    )
    # Let the existing grant expire without any UPDATE that could bump its version.
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM expires_at-now())+0.02)) FROM distribution_grant WHERE id=:id"
            ),
            {"id": tenant.grant_id},
        )
    with pytest.raises(Conflict, match="授权"):
        conversations.get(runtime, tenant.supplier, identifier)
    with pytest.raises(Conflict, match="授权"):
        conversations.begin(runtime, tenant.supplier, identifier, "expired", "ACME prices")
    with pytest.raises(Forbidden):
        conversations.create(runtime, tenant.supplier, "merchant", tenant.buyer.organization_id)


def test_old_inventory_disclosure_context_cannot_replay_or_restore(database, tenant):
    from cloud_warehouse.integrations import fingerprint

    admin, runtime = database
    identifier = conversation(runtime, tenant)
    work = conversations.begin(runtime, tenant.buyer, identifier, "old-turn", "ACME 库存查询")
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        {},
        work["messages"],
        [AgentEvent.text_delta("ACME 余位 12345").model_dump()],
    )
    with admin.begin() as conn:
        grants = (
            conn.execute(
                text(
                    "SELECT g.id,g.version,c.active,warehouse_org_active(g.supplier_org_id) AS supplier_active FROM distribution_grant g JOIN supplier_connection c ON c.id=g.connection_id WHERE g.buyer_org_id=:buyer AND g.active ORDER BY g.id"
                ),
                {"buyer": tenant.buyer.organization_id},
            )
            .mappings()
            .all()
        )
        old = fingerprint(
            [{k: str(v) if isinstance(v, UUID) else v for k, v in r.items()} for r in grants]
        )
        conn.execute(
            text("UPDATE agent_conversation SET authorization_hash=:hash WHERE id=:id"),
            {"id": identifier, "hash": old},
        )
    for call in [
        lambda: conversations.get(runtime, tenant.buyer, identifier),
        lambda: conversations.begin(runtime, tenant.buyer, identifier, "old-turn", "ACME 库存查询"),
    ]:
        with pytest.raises(Conflict, match="库存展示规则"):
            call()
    page = conversations.list_page(runtime, tenant.buyer)
    assert page["items"][0]["resumable"] is False and page["items"][0]["title"] is None
