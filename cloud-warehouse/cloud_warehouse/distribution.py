"""Supplier-owned distribution grant history and explicit human controls."""

from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from .changes import Conflict, audit
from .integrations import canonical
from .persistence import Forbidden, require_role, transaction


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1, strict=True)
    active: bool = Field(strict=True)
    valid_from: AwareDatetime
    expires_at: AwareDatetime | None
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def interval(self):
        if self.expires_at is not None and self.expires_at <= self.valid_from:
            raise ValueError("授权结束时间必须晚于开始时间")
        return self


def listing(engine, authentication, actor, *, after=None, limit=25):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "auditor")
        if after and not conn.scalar(
            text("SELECT id FROM distribution_grant WHERE id=:id AND supplier_org_id=:org"),
            {"id": after, "org": actor.organization_id},
        ):
            raise Forbidden("分页位置不存在或无权限")
        rows = [
            dict(row)
            for row in conn.execute(
                text("""SELECT g.id,g.connection_id,g.buyer_org_id,g.active,g.valid_from,
                    g.expires_at,g.version,c.name AS source_name,c.active AS source_active,
                    c.connector_type,(g.active AND g.valid_from<=now()
                    AND (g.expires_at IS NULL OR g.expires_at>now()) AND c.active
                    AND warehouse_org_active(g.buyer_org_id)) AS effective
                    FROM distribution_grant g JOIN supplier_connection c ON c.id=g.connection_id
                    WHERE g.supplier_org_id=:org AND (CAST(:after AS uuid) IS NULL OR g.id>:after)
                    ORDER BY g.id LIMIT :limit"""),
                {"org": actor.organization_id, "after": after, "limit": limit + 1},
            ).mappings()
        ]
    items = rows[:limit]
    if items:
        # The authentication role resolves only IDs derived from this supplier's grants.
        # There is no searchable directory of other platform organizations.
        with authentication.connect() as conn:
            names = dict(
                conn.execute(
                    text("SELECT id,name FROM organization WHERE id=ANY(:ids)"),
                    {"ids": list({row["buyer_org_id"] for row in items})},
                ).all()
            )
        for row in items:
            row["buyer_name"] = names.get(row["buyer_org_id"], "未知采购组织")
    return {"items": items, "next_cursor": str(items[-1]["id"]) if len(rows) > limit else None}


def control(engine, actor, grant_id, command: Control):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        before = (
            conn.execute(
                text("""SELECT id,connection_id,buyer_org_id,active,valid_from,expires_at,version
                FROM distribution_grant WHERE id=:id AND supplier_org_id=:org FOR UPDATE"""),
                {"id": grant_id, "org": actor.organization_id},
            )
            .mappings()
            .one_or_none()
        )
        if before is None:
            raise Forbidden("分销授权不存在或无权管理")
        if before["version"] != command.version:
            raise Conflict("授权已被修改，请刷新并重新核对")
        now = conn.scalar(text("SELECT now()"))
        if command.active and command.expires_at is not None and command.expires_at <= now:
            raise Conflict("启用授权时结束时间必须晚于当前时间")
        values = command.model_dump(exclude={"note", "version"})
        if all(before[key] == value for key, value in values.items()):
            raise Conflict("授权内容未变化")
        after = dict(
            conn.execute(
                text("""UPDATE distribution_grant SET active=:active,valid_from=:valid_from,
                expires_at=:expires_at WHERE id=:id
                RETURNING id,connection_id,buyer_org_id,active,valid_from,expires_at,version"""),
                {**values, "id": grant_id},
            )
            .mappings()
            .one()
        )

        def snapshot(row):
            return {
                key: value.isoformat()
                if hasattr(value, "isoformat")
                else str(value)
                if key in {"id", "connection_id", "buyer_org_id"}
                else value
                for key, value in row.items()
            }

        operation = uuid4()
        details = {
            "operation_id": str(operation),
            "before": snapshot(before),
            "after": snapshot(after),
            "note": command.note,
        }
        audit(conn, actor, "distribution_grant.changed", grant_id, details)
        conn.execute(
            text("""INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload)
                VALUES(:id,:org,'distribution_grant.changed',:operation,CAST(:payload AS jsonb))"""),
            {
                "id": uuid4(),
                "org": actor.organization_id,
                "operation": operation,
                "payload": canonical({"grant_id": str(grant_id), "version": after["version"]}),
            },
        )
        return after
