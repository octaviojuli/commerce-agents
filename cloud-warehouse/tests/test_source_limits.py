import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import jobs, pricing, quotes, source_limits
from cloud_warehouse.admin import migrate, onboard
from cloud_warehouse.integrations import SourceError
from cloud_warehouse.persistence import Principal, engine_for, transaction
from tour.api.warehouse_connector import CooldownHttpErpClient, TourWarehouseConnector

from .test_quotes import FUTURE, FakeSource, bind
from .test_quotes import external_offer as external_offer
from .test_recovery import empty_database


@asynccontextmanager
async def nothing():
    yield None


def scope(runtime, actor, source):
    return source_limits.open_connector(runtime, actor, source, nothing)


def deadline(admin, source):
    with admin.connect() as conn:
        return conn.execute(
            text("SELECT not_before,requires_review FROM source_cooldown WHERE group_id=:id"),
            {"id": source},
        ).one()


async def test_cooldown_persists_in_another_process_and_never_shortens(database, tenant):
    admin, runtime = database
    async with scope(runtime, tenant.buyer, tenant.connection_id):
        await source_limits.check_or_record(60)
        before = deadline(admin, tenant.connection_id)
        await source_limits.check_or_record(1)
        assert deadline(admin, tenant.connection_id) == before
    code = """import asyncio,json,sys
from contextlib import asynccontextmanager
from uuid import UUID
from cloud_warehouse.persistence import engine_for,Principal
from cloud_warehouse.source_limits import open_connector,check_or_record
from cloud_warehouse.integrations import SourceError
x=json.load(sys.stdin);e=engine_for(x['url'])
@asynccontextmanager
async def factory(): yield None
async def run():
 try:
  async with open_connector(e,Principal(UUID(x['user']),UUID(x['org'])),UUID(x['source']),factory):
   await check_or_record()
 except SourceError as error: print(json.dumps({'code':error.code,'wait':error.retry_after_seconds}))
 finally:e.dispose()
asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(
            {
                "url": runtime.url.render_as_string(hide_password=False),
                "user": str(tenant.worker.user_id),
                "org": str(tenant.worker.organization_id),
                "source": str(tenant.connection_id),
            }
        ),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    body = json.loads(result.stdout)
    assert body["code"] == "SOURCE_RATE_LIMITED" and 0 < body["wait"] <= 60
    with pytest.raises(SourceError, match="SOURCE_LIMIT_CONTEXT_MISSING"):
        await source_limits.check_or_record()


async def test_account_group_merges_deadlines_without_affecting_other_accounts(database, tenant):
    admin, runtime = database
    other = {
        key: UUID(value)
        for key, value in onboard(
            admin, "ACME second", "ACME buyer", f"{uuid4()}@acme.example"
        ).items()
    }
    second = Principal(other["worker_id"], other["supplier_id"])
    group = uuid4()
    async with scope(runtime, tenant.worker, tenant.connection_id):
        await source_limits.check_or_record(60)
    async with scope(runtime, second, other["connection_id"]):
        await source_limits.check_or_record()
        await source_limits.check_or_record(120)
    expected = deadline(admin, other["connection_id"])[0]
    source_limits.group_connections(admin, group, [tenant.connection_id, other["connection_id"]])
    assert deadline(admin, group)[0] == expected
    for actor, source in ((tenant.buyer, tenant.connection_id), (second, other["connection_id"])):
        async with scope(runtime, actor, source):
            with pytest.raises(SourceError, match="SOURCE_RATE_LIMITED") as caught:
                await source_limits.check_or_record()
            assert 100 < caught.value.retry_after_seconds <= 120
    with pytest.raises(ValueError, match="cannot be reassigned"):
        source_limits.group_connections(admin, uuid4(), [tenant.connection_id])
    unrelated = {
        key: UUID(value)
        for key, value in onboard(
            admin, "ACME unrelated", "ACME buyer", f"{uuid4()}@acme.example"
        ).items()
    }
    async with scope(
        runtime,
        Principal(unrelated["worker_id"], unrelated["supplier_id"]),
        unrelated["connection_id"],
    ):
        await source_limits.check_or_record()


async def test_parallel_feedback_and_long_delay_review_survive_expiry(database, tenant):
    admin, runtime = database

    async def record(delay):
        async with scope(runtime, tenant.worker, tenant.connection_id):
            await source_limits.check_or_record(delay)

    start = datetime.now(UTC)
    await asyncio.gather(record(5), record(60), record(1))
    assert deadline(admin, tenant.connection_id)[0] >= start + timedelta(seconds=60)
    with pytest.raises(SourceError, match="SOURCE_COOLDOWN_REQUIRES_REVIEW") as caught:
        await record(604801)
    assert not caught.value.retryable
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE source_cooldown SET not_before=now()-interval '1 day' WHERE group_id=:id"),
            {"id": tenant.connection_id},
        )
    with pytest.raises(SourceError, match="SOURCE_COOLDOWN_REQUIRES_REVIEW"):
        async with scope(runtime, tenant.buyer, tenant.connection_id):
            await source_limits.check_or_record()


async def test_expired_cooldown_allows_read_and_revocation_is_live(database, tenant):
    admin, runtime = database
    async with scope(runtime, tenant.buyer, tenant.connection_id):
        await source_limits.check_or_record(60)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE source_cooldown SET not_before=now()-interval '1 second' WHERE group_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    async with scope(runtime, tenant.buyer, tenant.connection_id):
        await source_limits.check_or_record()
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE distribution_grant SET active=false WHERE id=:id"),
                {"id": tenant.grant_id},
            )
        with pytest.raises(SourceError, match="SOURCE_ACCESS_REVOKED"):
            await source_limits.check_or_record()


def test_runtime_cannot_read_reset_or_regroup_shared_state(database, tenant, authentication):
    _, runtime = database
    for statement in (
        "SELECT * FROM source_cooldown",
        "UPDATE source_cooldown SET not_before='-infinity',requires_review=false",
        "SELECT * FROM source_cooldown_binding",
        "DELETE FROM source_cooldown_binding",
    ):
        with pytest.raises(DBAPIError), transaction(runtime, tenant.supplier) as conn:
            conn.execute(text(statement))
    with pytest.raises(DBAPIError), authentication.begin() as conn:
        conn.execute(
            text("SELECT * FROM warehouse_source_cooldown(:id,0)"), {"id": tenant.connection_id}
        )
    with pytest.raises(DBAPIError), runtime.begin() as conn:
        conn.execute(
            text("SELECT * FROM warehouse_source_cooldown(:id,0)"), {"id": tenant.connection_id}
        )


@pytest.mark.parametrize(
    "status,envelope,delay,expected",
    [
        (429, 429, "120", "SOURCE_RATE_LIMITED"),
        (200, 429, None, "SOURCE_RATE_LIMITED"),
        (503, 503, "90", "SOURCE_UNAVAILABLE"),
    ],
)
async def test_supplier_feedback_blocks_other_client_login_before_network(
    database, tenant, status, envelope, delay, expected
):
    admin, runtime = database
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(
            status,
            headers={"Retry-After": delay} if delay else {},
            json={"code": envelope, "message": "ACME upstream private detail"},
        )

    first = CooldownHttpErpClient.with_token(
        "https://b2b.acme.example",
        "ACME-token",
        datetime.now(UTC).timestamp() + 3600,
        {"companyId": 2},
        [],
        transport=httpx.MockTransport(respond),
    )
    second = CooldownHttpErpClient.from_login(
        "https://b2b.acme.example",
        "ACME-user",
        "ACME-password",
        transport=httpx.MockTransport(respond),
    )
    try:
        async with scope(runtime, tenant.buyer, tenant.connection_id):
            with pytest.raises(SourceError, match=expected):
                await TourWarehouseConnector(first)._get("/route/list", {})
        async with scope(runtime, tenant.worker, tenant.connection_id):
            with pytest.raises(SourceError, match="SOURCE_RATE_LIMITED"):
                await second.identify()
        assert calls == ["/route/list"]
        assert deadline(admin, tenant.connection_id)[0] > datetime.now(UTC)
    finally:
        await first.aclose()
        await second.aclose()


async def test_transport_without_scope_never_sends_credentials():
    calls = []
    client = CooldownHttpErpClient.from_login(
        "https://b2b.acme.example",
        "ACME-user",
        "ACME-password",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    try:
        with pytest.raises(SourceError, match="SOURCE_LIMIT_CONTEXT_MISSING"):
            await client.identify()
        assert calls == []
    finally:
        await client.aclose()


async def test_observed_cooldown_finishes_persistence_after_caller_cancellation(
    database, tenant, monkeypatch
):
    admin, runtime = database
    entered, release = asyncio.Event(), asyncio.Event()
    original = source_limits._state

    async def delayed(scope, seconds):
        entered.set()
        await release.wait()
        return await original(scope, seconds)

    monkeypatch.setattr(source_limits, "_state", delayed)

    async def record():
        async with scope(runtime, tenant.buyer, tenant.connection_id):
            await source_limits.check_or_record(60)

    task = asyncio.create_task(record())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert deadline(admin, tenant.connection_id)[0] > datetime.now(UTC)


async def test_quote_feedback_blocks_background_catalog_worker(database, tenant, external_offer):
    _, runtime = database
    bound = await bind(runtime, tenant, FakeSource())
    pricing.accept_binding(runtime, tenant.buyer, UUID(bound["resource_id"]), 1)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"code": 429})

    @asynccontextmanager
    async def factory():
        client = CooldownHttpErpClient.with_token(
            "https://b2b.acme.example",
            "ACME-token",
            datetime.now(UTC).timestamp() + 3600,
            {"companyId": 2},
            [],
            transport=httpx.MockTransport(respond),
        )
        try:
            yield TourWarehouseConnector(client)
        finally:
            await client.aclose()

    registry = {tenant.connection_id: factory}
    result = await quotes.create(
        runtime,
        tenant.buyer,
        quotes.QuoteRequest(offer_id=external_offer, departure_date=FUTURE, party=quotes.Party()),
        "ACME-cooldown",
        registry,
    )
    assert result["missing_items"] == ["SOURCE_RATE_LIMITED"]
    assert result["market_total"] is None and result["settlement_total"] is None
    jobs.schedule(runtime, tenant.worker, tenant.connection_id)
    work = await jobs.run_once(runtime, tenant.worker, registry)
    assert work["error_code"] == "SOURCE_RATE_LIMITED"
    assert calls == ["/period/detail"]
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(
            text("SELECT provider_not_before>now() FROM sync_job WHERE connection_id=:id"),
            {"id": tenant.connection_id},
        )


def test_migration_preserves_existing_worker_cooldown_and_review(database):
    cluster, _ = database
    with empty_database(cluster, f"warehouse_recovery_{uuid4().hex}_test") as value:
        migrate(value, "0031_authorization_plans")
        admin = engine_for(value)
        try:
            future = datetime.now(UTC) + timedelta(hours=1)
            ids = onboard(admin, "ACME old", "ACME buyer", f"{uuid4()}@acme.example")
            with admin.begin() as conn:
                conn.execute(
                    text("""INSERT INTO sync_job(id,organization_id,connection_id,worker_id,
                  interval_seconds,status,last_error,provider_not_before)
                  VALUES(:id,:org,:source,:worker,180,'failed','SOURCE_COOLDOWN_REQUIRES_REVIEW',:future)"""),
                    {
                        "id": uuid4(),
                        "org": UUID(ids["supplier_id"]),
                        "source": UUID(ids["connection_id"]),
                        "worker": UUID(ids["worker_id"]),
                        "future": future,
                    },
                )
            migrate(value)
            assert deadline(admin, UUID(ids["connection_id"])) == (future, True)
        finally:
            admin.dispose()
