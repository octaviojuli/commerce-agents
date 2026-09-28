"""One-use invitations require a buyer request and a separate supplier decision."""

import hashlib
import secrets
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .changes import Conflict
from .integrations import fingerprint
from .persistence import Forbidden, require_role, transaction

PUBLIC_COLUMNS = (
    "id,supplier_org_id,connection_id,source_version,supplier_name,source_name,created_by,created_at,"
    "invite_expires_at,valid_from,expires_at,status,version,buyer_org_id,buyer_name,claimed_by,claimed_at,"
    "decided_by,decided_at,grant_id"
)


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    connection_id: UUID
    valid_from: AwareDatetime
    expires_at: AwareDatetime | None
    invite_hours: int = Field(default=72, ge=1, le=168, strict=True)
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def interval(self):
        if self.expires_at is not None and self.expires_at <= self.valid_from:
            raise ValueError("授权结束时间必须晚于开始时间")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class Decide(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1, strict=True)
    action: Literal["approve", "revoke"]
    note: str = Field(min_length=1, max_length=500)


ERRORS = {
    "INVITATION_REQUEST_CONFLICT": "同一请求编号已用于不同邀请，请核对已有记录",
    "INVITATION_SOURCE_CHANGED": "供应源已停用或发生变化，请重新创建邀请",
    "INVITATION_INVALID": "请填写有效的授权时间、邀请期限及操作说明",
    "INVITATION_UNAVAILABLE": "邀请不可用、已过期或已被其他组织申请",
    "INVITATION_STALE": "邀请状态已变化，请刷新后重新确认",
    "INVITATION_CLOSED": "邀请已结束；已批准关系请通过分销授权页面维护",
    "INVITATION_BUYER_CHANGED": "采购方或申请管理员权限已变化，需要重新确认",
    "INVITATION_RELATION_EXISTS": "该采购组织已有此来源授权，请在分销授权中维护",
}


def _call(conn, statement, parameters):
    try:
        return conn.scalar(text(statement), parameters)
    except DBAPIError as error:
        code = getattr(getattr(error.orig, "diag", None), "message_primary", "")
        if code == "INVITATION_FORBIDDEN":
            raise Forbidden("邀请不存在或当前组织无权执行") from None
        if code in ERRORS:
            raise Conflict(ERRORS[code]) from None
        raise


def _get(conn, identifier):
    row = (
        conn.execute(
            text(
                f"SELECT {PUBLIC_COLUMNS},(invite_expires_at<=now()) AS expired FROM distribution_invitation WHERE id=:id"
            ),
            {"id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("邀请不存在或当前组织无权读取")
    return dict(row)


def listing(engine, actor, *, after=None, limit=25):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "buyer_admin", "auditor")
        if after:
            _get(conn, after)
        rows = [
            dict(row)
            for row in conn.execute(
                text(
                    f"SELECT {PUBLIC_COLUMNS},(invite_expires_at<=now()) AS expired FROM distribution_invitation WHERE (CAST(:after AS uuid) IS NULL OR id>:after) ORDER BY id LIMIT :limit"
                ),
                {"after": after, "limit": limit + 1},
            ).mappings()
        ]
        return {
            "items": rows[:limit],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def create(engine, actor, command: Create):
    code = secrets.token_urlsafe(32)
    with transaction(engine, actor) as conn:
        result = _call(
            conn,
            "SELECT warehouse_invitation_create(:source,:request,:digest,:secret,:starts,:ends,:hours,:note)",
            {
                "source": command.connection_id,
                "request": command.request_id,
                "digest": fingerprint(command.model_dump(mode="json", exclude={"request_id"})),
                "secret": hashlib.sha256(code.encode()).hexdigest(),
                "starts": command.valid_from,
                "ends": command.expires_at,
                "hours": command.invite_hours,
                "note": command.note,
            },
        )
        return {
            **_get(conn, result["id"]),
            "duplicate": result["duplicate"],
            "code": None if result["duplicate"] else code,
        }


def claim(engine, actor, command: Claim):
    with transaction(engine, actor) as conn:
        identifier = _call(
            conn,
            "SELECT warehouse_invitation_claim(:secret)",
            {"secret": hashlib.sha256(command.code.encode()).hexdigest()},
        )
        return _get(conn, identifier)


def preview(engine, actor, command: Claim):
    with transaction(engine, actor) as conn:
        return _call(
            conn,
            "SELECT warehouse_invitation_preview(:secret)",
            {"secret": hashlib.sha256(command.code.encode()).hexdigest()},
        )


def decide(engine, actor, identifier, command: Decide):
    with transaction(engine, actor) as conn:
        _call(
            conn,
            "SELECT warehouse_invitation_decide(:id,:version,:action,:note)",
            {
                "id": identifier,
                "version": command.version,
                "action": command.action,
                "note": command.note,
            },
        )
        return _get(conn, identifier)
