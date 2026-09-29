"""Human-managed Excel sources; API credentials remain operator-configured."""

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .changes import Conflict, audit
from .integrations import canonical, fingerprint
from .persistence import Forbidden, require_role, transaction

PROJECTION = "id,name,connector_type,capabilities,active,version,created_at"
EXCEL_CAPABILITIES = {"inventory_owner": "warehouse", "catalog_read": False, "order_write": False}


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    name: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=500)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1, strict=True)
    name: str = Field(min_length=1, max_length=100)
    active: bool = Field(strict=True)
    note: str = Field(min_length=1, max_length=500)


def _record(conn, actor, identifier, action, details):
    audit(conn, actor, action, identifier, details)
    conn.execute(
        text("""INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload)
            VALUES(:id,:org,:kind,:operation,CAST(:payload AS jsonb))"""),
        {
            "id": uuid4(),
            "org": actor.organization_id,
            "kind": action,
            "operation": uuid4(),
            "payload": canonical({"connection_id": str(identifier), **details}),
        },
    )


def listing(engine, actor, *, after=None, limit=25):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "auditor")
        if after and not conn.scalar(
            text("SELECT id FROM supplier_connection WHERE id=:id AND supplier_org_id=:org"),
            {"id": after, "org": actor.organization_id},
        ):
            raise Forbidden("分页位置不存在或无权限")
        rows = [
            dict(row)
            for row in conn.execute(
                text(
                    f"SELECT {PROJECTION} FROM supplier_connection WHERE supplier_org_id=:org AND (CAST(:after AS uuid) IS NULL OR id>:after) ORDER BY id LIMIT :limit"
                ),
                {"org": actor.organization_id, "after": after, "limit": limit + 1},
            ).mappings()
        ]
        return {
            "items": rows[:limit],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def create_excel(engine, actor, command: Create):
    digest = fingerprint(command.model_dump(mode="json", exclude={"request_id"}))
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        identifier = conn.scalar(
            text("""INSERT INTO supplier_connection(id,supplier_org_id,name,connector_type,
                credential_ref,capabilities,creation_key,creation_hash)
                VALUES(:id,:org,:name,'excel','none',CAST(:capabilities AS jsonb),:key,:hash)
                ON CONFLICT(supplier_org_id,creation_key) DO NOTHING RETURNING id"""),
            {
                "id": uuid4(),
                "org": actor.organization_id,
                "name": command.name,
                "capabilities": canonical(EXCEL_CAPABILITIES),
                "key": command.request_id,
                "hash": digest,
            },
        )
        row = dict(
            conn.execute(
                text(
                    f"SELECT {PROJECTION},creation_hash FROM supplier_connection WHERE supplier_org_id=:org AND creation_key=:key"
                ),
                {"org": actor.organization_id, "key": command.request_id},
            )
            .mappings()
            .one()
        )
        if row.pop("creation_hash") != digest:
            raise Conflict("创建请求编号已用于不同内容，请先核对已有供应源")
        if identifier:
            _record(
                conn,
                actor,
                identifier,
                "source.created",
                {"name": command.name, "connector_type": "excel", "note": command.note},
            )
        # Creating a source grants no buyer access and never creates a sync worker.
        return {**row, "duplicate": identifier is None}


def control(engine, actor, identifier, command: Control):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        before = (
            conn.execute(
                text(
                    f"SELECT {PROJECTION} FROM supplier_connection WHERE id=:id AND supplier_org_id=:org FOR UPDATE"
                ),
                {"id": identifier, "org": actor.organization_id},
            )
            .mappings()
            .one_or_none()
        )
        if before is None:
            raise Forbidden("供应源不存在或无权维护")
        if before["connector_type"] != "excel":
            raise Conflict("系统接口来源由接入运维流程维护")
        if before["version"] != command.version:
            raise Conflict("供应源已被修改，请刷新后重新核对")
        if before["name"] == command.name and before["active"] == command.active:
            raise Conflict("供应源内容未变化")
        after = dict(
            conn.execute(
                text(
                    f"UPDATE supplier_connection SET name=:name,active=:active WHERE id=:id RETURNING {PROJECTION}"
                ),
                {"name": command.name, "active": command.active, "id": identifier},
            )
            .mappings()
            .one()
        )
        affected = 0
        if before["active"] != command.active:
            # Advance grant versions on both stop and resume: older quotes, previews
            # and conversations must not regain validity when the source is reenabled.
            affected = conn.execute(
                text(
                    "UPDATE distribution_grant SET active=active WHERE connection_id=:id AND supplier_org_id=:org"
                ),
                {"id": identifier, "org": actor.organization_id},
            ).rowcount
        fields = ("name", "active", "version")
        _record(
            conn,
            actor,
            identifier,
            "source.changed",
            {
                "before": {key: before[key] for key in fields},
                "after": {key: after[key] for key in fields},
                "grants_reversioned": affected,
                "note": command.note,
            },
        )
        return after
