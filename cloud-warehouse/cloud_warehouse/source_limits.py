"""Persistent supplier cooldowns; no guessed request quota or in-process permission cache."""

import asyncio
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .concurrency import in_worker_thread
from .integrations import SourceError
from .persistence import Forbidden, Principal, transaction


@dataclass(frozen=True)
class Scope:
    engine: Engine
    actor: Principal
    connection_id: UUID


_scope: ContextVar[Scope | None] = ContextVar("warehouse_source_scope", default=None)


@asynccontextmanager
async def open_connector(engine, actor, connection_id, factory):
    token = _scope.set(Scope(engine, actor, connection_id))
    try:
        async with factory() as connector:
            yield connector
    finally:
        _scope.reset(token)


@in_worker_thread
def _state(scope: Scope, seconds: int):
    try:
        with transaction(scope.engine, scope.actor) as conn:
            conn.execute(text("SET LOCAL lock_timeout='1s'"))
            conn.execute(text("SET LOCAL statement_timeout='2s'"))
            return conn.execute(
                text("SELECT * FROM warehouse_source_cooldown(:source,:delay)"),
                {"source": scope.connection_id, "delay": seconds},
            ).one()
    except Forbidden:
        raise SourceError("SOURCE_ACCESS_REVOKED", retryable=False) from None
    except SQLAlchemyError as error:
        if getattr(getattr(error, "orig", None), "sqlstate", None) == "42501":
            raise SourceError("SOURCE_ACCESS_REVOKED", retryable=False) from None
        raise SourceError("SOURCE_COOLDOWN_UNAVAILABLE") from None


async def check_or_record(seconds: int = 0):
    scope = _scope.get()
    if scope is None:
        raise SourceError("SOURCE_LIMIT_CONTEXT_MISSING", retryable=False)
    if seconds:
        # Once an upstream cooldown is observed, finish its short database write
        # even if the HTTP caller disconnects. Never hold a transaction over IO.
        pending = asyncio.create_task(_state(scope, seconds))
        try:
            wait, review = await asyncio.shield(pending)
        except asyncio.CancelledError:
            with suppress(Exception):
                await pending
            raise
    else:
        wait, review = await _state(scope, seconds)
    if review:
        raise SourceError("SOURCE_COOLDOWN_REQUIRES_REVIEW", retryable=False)
    # Observers preserve the original supplier failure classification. Subsequent
    # attempts are refused here before any HTTP request, including login or retry.
    if wait and seconds == 0:
        raise SourceError("SOURCE_RATE_LIMITED", retry_after_seconds=wait)


def group_connections(engine: Engine, group_id: UUID, connections: list[UUID]):
    """Offline administrator only; preserve all deadlines when grouping one account.

    Runtime roles have no table rights. Existing explicit bindings cannot be reassigned.
    The caller must establish that these connections really use the same supplier account.
    """
    if not connections or len(set(connections)) != len(connections):
        raise ValueError("Specify distinct supplier connections")
    with engine.begin() as conn:
        conn.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended('warehouse:source-cooldown-bindings',0))"
            )
        )
        sources = (
            conn.execute(
                text(
                    "SELECT id FROM supplier_connection WHERE id=ANY(:ids) AND connector_type='tour_b2b' ORDER BY id FOR UPDATE"
                ),
                {"ids": connections},
            )
            .scalars()
            .all()
        )
        if set(sources) != set(connections):
            raise ValueError("Supplier connections must exist and use the B2B adapter")
        bindings = (
            conn.execute(
                text("SELECT group_id FROM source_cooldown_binding WHERE connection_id=ANY(:ids)"),
                {"ids": connections},
            )
            .scalars()
            .all()
        )
        if any(bound != group_id for bound in bindings):
            raise ValueError("Existing account cooldown bindings cannot be reassigned")
        # A UUID used as another connection's default group cannot become an unrelated alias.
        if conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM supplier_connection WHERE id=:id AND NOT id=ANY(:ids))"
            ),
            {"id": group_id, "ids": connections},
        ):
            raise ValueError("Group identifier belongs to another connection")
        conn.execute(
            text("""INSERT INTO source_cooldown(group_id,not_before,requires_review)
              SELECT :group,coalesce(max(not_before),'-infinity'),coalesce(bool_or(requires_review),false)
              FROM source_cooldown WHERE group_id=ANY(:ids)
              ON CONFLICT(group_id) DO UPDATE SET
                not_before=greatest(source_cooldown.not_before,EXCLUDED.not_before),
                requires_review=source_cooldown.requires_review OR EXCLUDED.requires_review"""),
            {"group": group_id, "ids": [group_id, *connections]},
        )
        conn.execute(
            text(
                "INSERT INTO source_cooldown_binding(connection_id,group_id) VALUES(:source,:group) ON CONFLICT(connection_id) DO NOTHING"
            ),
            [{"source": source, "group": group_id} for source in connections],
        )
