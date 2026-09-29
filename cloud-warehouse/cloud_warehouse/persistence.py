"""PostgreSQL transactions with validated organization context and transaction-local RLS."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from .database_pool import pooled_engine


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    organization_id: UUID


class Forbidden(PermissionError):
    """The authenticated principal has no membership or action permission."""


def engine_for(url: str) -> Engine:
    return pooled_engine(url)


@contextmanager
def transaction(engine: Engine, principal: Principal) -> Iterator[Connection]:
    with engine.begin() as connection:
        # SET LOCAL is undone on commit/rollback, including pooled connections.
        allowed = connection.scalar(
            text("SELECT public.warehouse_begin_context(:user_id,:organization_id)"),
            {"user_id": principal.user_id, "organization_id": principal.organization_id},
        )
        if not allowed:
            raise Forbidden("当前账号不属于该有效组织")
        yield connection


def require_role(connection: Connection, *roles: str) -> None:
    if not connection.scalar(
        text("SELECT warehouse_has_role(CAST(:roles AS text[]))"), {"roles": list(roles)}
    ):
        raise Forbidden("当前组织角色无权执行此操作")


def require_runtime_role(engine: Engine) -> None:
    """Fail closed when an API/worker is accidentally configured with a migration role."""
    with engine.connect() as connection:
        privileged = connection.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
        owns = connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='supplier_product' AND tableowner=current_user)"
            )
        )
        if privileged or owns:
            raise RuntimeError("运行服务必须使用非表所有者、非超级用户且无 BYPASSRLS 的数据库角色")
