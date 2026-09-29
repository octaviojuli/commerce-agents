"""Database schedules with expiring, token-fenced ownership of catalog publication."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from .catalog import SyncBusy, _scope, synchronize
from .integrations import SourceError, SupplierConnector
from .persistence import Forbidden, Principal, require_role, transaction

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15
SCAN_TIMEOUT_SECONDS = 900
DEFAULT_INTERVAL_SECONDS = 180
ConnectorFactory = Callable[[], AbstractAsyncContextManager[SupplierConnector]]


class LeaseLost(SourceError):
    def __init__(self):
        super().__init__("JOB_LEASE_LOST")


@dataclass(frozen=True)
class Claim:
    id: UUID
    connection_id: UUID
    lease_id: UUID
    attempts: int


def schedule(
    engine: Engine,
    actor: Principal,
    connection_id: UUID,
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    max_attempts: int = 5,
) -> UUID:
    """Idempotent startup registration; restarting never resets a failed schedule."""
    if not 60 <= interval_seconds <= 86400 or not 1 <= max_attempts <= 10:
        raise ValueError("同步间隔必须为 60–86400 秒，最多尝试 1–10 次")
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        source = _scope(conn, connection_id)
        if not source["capabilities"].get("catalog_read") or source["connector_type"] == "excel":
            raise Forbidden("此连接不支持外部目录同步")
        conn.execute(
            text("""INSERT INTO sync_job(id,organization_id,connection_id,worker_id,interval_seconds,max_attempts)
            VALUES(:id,:org,:source,:worker,:interval,:attempts) ON CONFLICT(connection_id) DO NOTHING"""),
            {
                "id": uuid4(),
                "org": actor.organization_id,
                "source": connection_id,
                "worker": actor.user_id,
                "interval": interval_seconds,
                "attempts": max_attempts,
            },
        )
        row = (
            conn.execute(
                text("SELECT * FROM sync_job WHERE connection_id=:id"), {"id": connection_id}
            )
            .mappings()
            .one()
        )
        if row["worker_id"] != actor.user_id:
            raise ValueError("已有任务属于另一同步账号，启动不会覆盖")
        # Creation defaults never overwrite the operator's persisted configuration.
        return row["id"]


def claim(engine: Engine, actor: Principal, connections: list[UUID]) -> Claim | None:
    if not connections:
        return None
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        conn.execute(
            text("""UPDATE sync_job SET worker_last_seen_at=now()
            WHERE worker_id=:worker AND connection_id=ANY(CAST(:sources AS uuid[]))
            AND (worker_last_seen_at IS NULL OR worker_last_seen_at<now()-interval '15 seconds')"""),
            {"worker": actor.user_id, "sources": connections},
        )
        # A final-attempt crash also reaches a terminal state instead of hanging forever.
        conn.execute(
            text("""UPDATE sync_job SET status='failed',lease_id=NULL,lease_until=NULL,
          last_error='INTERRUPTED',updated_at=now()
          WHERE worker_id=:worker AND connection_id=ANY(CAST(:sources AS uuid[]))
          AND status='running' AND lease_until<=now() AND attempts>=max_attempts"""),
            {"worker": actor.user_id, "sources": connections},
        )
        # A paused job whose process died must not look perpetually running. Its
        # consumed attempts remain intact; no replacement execution is claimed.
        conn.execute(
            text("""UPDATE sync_job SET status='waiting',lease_id=NULL,lease_until=NULL,
          last_error='INTERRUPTED',updated_at=now()
          WHERE worker_id=:worker AND connection_id=ANY(CAST(:sources AS uuid[]))
          AND NOT enabled AND status='running' AND lease_until<=now()"""),
            {"worker": actor.user_id, "sources": connections},
        )
        row = (
            conn.execute(
                text("""SELECT j.id,j.connection_id,j.attempts FROM sync_job j
          JOIN supplier_connection c ON c.id=j.connection_id AND c.active
          WHERE j.worker_id=:worker AND j.connection_id=ANY(CAST(:sources AS uuid[]))
          AND j.enabled AND c.connector_type<>'excel' AND c.capabilities->>'catalog_read'='true'
          AND j.attempts<j.max_attempts AND ((j.status='waiting' AND j.next_run_at<=now())
          OR (j.status='running' AND j.lease_until<=now()))
          ORDER BY j.next_run_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1"""),
                {"worker": actor.user_id, "sources": connections},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        token = uuid4()
        conn.execute(
            text("""UPDATE sync_job SET status='running',attempts=attempts+1,
          lease_id=:token,lease_until=now()+make_interval(secs=>:seconds),updated_at=now()
          WHERE id=:id"""),
            {"id": row["id"], "token": token, "seconds": LEASE_SECONDS},
        )
        return Claim(row["id"], row["connection_id"], token, row["attempts"] + 1)


def heartbeat(engine: Engine, actor: Principal, job: Claim) -> None:
    with transaction(engine, actor) as conn:
        changed = conn.execute(
            text("""UPDATE sync_job
          SET lease_until=now()+make_interval(secs=>:seconds),updated_at=now(),worker_last_seen_at=now()
          WHERE id=:id AND lease_id=:token AND status='running' AND lease_until>now()"""),
            {"id": job.id, "token": job.lease_id, "seconds": LEASE_SECONDS},
        ).rowcount
        if changed != 1:
            raise LeaseLost()


def published(conn: Connection, job: Claim, result: dict) -> None:
    """Fence and acknowledge in the catalog transaction, including audit and Outbox."""
    changed = conn.execute(
        text("""UPDATE sync_job SET status='waiting',attempts=0,
      lease_id=NULL,lease_until=NULL,last_error=NULL,provider_not_before=NULL,last_run_id=:run,
      completed_count=completed_count+1,next_run_at=clock_timestamp()+make_interval(secs=>interval_seconds),
      updated_at=clock_timestamp() WHERE id=:id AND lease_id=:token AND status='running'
      AND lease_until>clock_timestamp()"""),
        {"id": job.id, "token": job.lease_id, "run": UUID(result["sync_run_id"])},
    ).rowcount
    if changed != 1:
        raise LeaseLost()


def failed(
    engine: Engine,
    actor: Principal,
    job: Claim,
    code: str,
    *,
    busy: bool = False,
    retryable: bool = True,
    retry_after_seconds: int = 0,
) -> None:
    # Bounded exponential backoff with jitter; only stable codes reach persisted history.
    delay = (
        random.uniform(15, 30)
        if busy
        else random.uniform(0.5, 1) * min(1800, 30 * 2 ** (job.attempts - 1))
    )
    with transaction(engine, actor) as conn:
        conn.execute(
            text("""UPDATE sync_job SET
          status=CASE WHEN :busy THEN 'waiting' WHEN NOT :retryable OR attempts>=max_attempts THEN 'failed' ELSE 'waiting' END,
          attempts=attempts-CASE WHEN :busy THEN 1 ELSE 0 END,
          next_run_at=now()+make_interval(secs=>:delay),last_error=:code,
          provider_not_before=CASE WHEN :cooldown>0 THEN clock_timestamp()+make_interval(secs=>:cooldown) ELSE NULL END,
          lease_id=NULL,lease_until=NULL,updated_at=now()
          WHERE id=:id AND lease_id=:token AND status='running' AND lease_until>now()"""),
            {
                "id": job.id,
                "token": job.lease_id,
                "code": code,
                "delay": delay,
                "busy": busy,
                "retryable": retryable,
                "cooldown": retry_after_seconds,
            },
        )


def retry(engine: Engine, actor: Principal, connection_id: UUID) -> bool:
    """Explicit operator retry after the cause of a terminal failure has been addressed."""
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        _scope(conn, connection_id)
        return (
            conn.execute(
                text("""UPDATE sync_job SET status='waiting',attempts=0,
          next_run_at=now(),updated_at=now() WHERE connection_id=:id AND status='failed'"""),
                {"id": connection_id},
            ).rowcount
            == 1
        )


async def run_once(
    engine: Engine, actor: Principal, connectors: Mapping[UUID, ConnectorFactory]
) -> dict | None:
    job = claim(engine, actor, list(connectors))
    if job is None:
        return None

    async def sync():
        from .source_limits import open_connector

        async with open_connector(
            engine, actor, job.connection_id, connectors[job.connection_id]
        ) as connector:
            return await synchronize(
                engine,
                actor,
                job.connection_id,
                connector,
                on_publish=lambda conn, result: published(conn, job, result),
            )

    async def keep_alive():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            heartbeat(engine, actor, job)

    work, pulse = asyncio.create_task(sync()), asyncio.create_task(keep_alive())
    try:
        async with asyncio.timeout(SCAN_TIMEOUT_SECONDS):
            done, _ = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
            # Successful publication clears its lease in the same transaction. A concurrent
            # heartbeat may then see that clear; the completed catalog result takes precedence.
            if work in done:
                return {"job_id": str(job.id), **work.result()}
            pulse.result()
            raise LeaseLost()
    except asyncio.CancelledError:
        failed(engine, actor, job, "INTERRUPTED")
        raise
    except Exception as error:
        code = (
            error.code
            if isinstance(error, SourceError)
            else "SYNC_BUSY"
            if isinstance(error, SyncBusy)
            else "SCAN_TIMEOUT"
            if isinstance(error, TimeoutError)
            else "ACCESS_REVOKED"
            if isinstance(error, Forbidden)
            else "SYNC_FAILED"
        )
        failed(
            engine,
            actor,
            job,
            code,
            busy=isinstance(error, SyncBusy),
            retryable=error.retryable if isinstance(error, SourceError) else True,
            retry_after_seconds=error.retry_after_seconds if isinstance(error, SourceError) else 0,
        )
        return {"job_id": str(job.id), "status": "failed", "error_code": code}
    finally:
        for task in (work, pulse):
            task.cancel()
        for task in (work, pulse):
            with suppress(asyncio.CancelledError, Exception):
                await task


async def work(
    engine: Engine,
    actor: Principal,
    connectors: Mapping[UUID, ConnectorFactory],
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
) -> dict | None:
    """One scoped worker per supplier; another process can safely take over expired leases."""
    from .persistence import require_runtime_role

    require_runtime_role(engine)
    for connection_id in connectors:
        schedule(engine, actor, connection_id, interval_seconds=interval_seconds)
    while True:
        result = await run_once(engine, actor, connectors)
        if once:
            return result
        if result is None:
            await asyncio.sleep(5)
