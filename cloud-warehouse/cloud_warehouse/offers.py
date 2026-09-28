"""Approved Excel sellable plans; multiple offers reference one departure inventory pool."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from . import changes
from .changes import Conflict
from .persistence import Forbidden, require_role, transaction


class OfferState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    service_description: str = Field(max_length=10000)
    active: bool = Field(strict=True)


class Command(OfferState):
    offer_id: UUID
    departure_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def new_code(self):
        if self.expected_version == 0 and self.code == "base":
            raise ValueError("base 是系统基础方案编号，请编辑已有基础方案")
        return self


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departure_version: int
    source_version: int
    product_version: int
    inventory_pool_id: UUID


class Preview(Command):
    before: OfferState | None
    scope: Scope


def _prepare(conn, actor, command, *, lock=False, require_preview=True):
    require_role(conn, "supplier_admin", "product_editor")
    # Match price-book updates: lock an existing offer before its source and parents.
    old = (
        conn.execute(
            text("SELECT * FROM offer WHERE id=:id" + (" FOR UPDATE" if lock else "")),
            {"id": command.offer_id},
        )
        .mappings()
        .one_or_none()
    )
    row = (
        conn.execute(
            text(
                """SELECT d.id,d.connection_id,d.version AS departure_version,
      c.version AS source_version,p.version AS product_version,c.connector_type,i.id AS inventory_pool_id
      FROM departure d JOIN supplier_product p ON p.id=d.product_id
      JOIN supplier_connection c ON c.id=d.connection_id LEFT JOIN inventory_pool i ON i.departure_id=d.id
      WHERE d.id=:id AND d.supplier_org_id=warehouse_org_id() AND c.active"""
                + (" FOR UPDATE OF d,p,c" if lock else "")
            ),
            {"id": command.departure_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("团期不存在或无权限")
    if row["connector_type"] != "excel":
        raise Conflict("API 来源的报价方案由上游适配器维护，不能本地新增或覆盖")
    if row["inventory_pool_id"] is None:
        raise Conflict("请先完成该团期的库存交接")
    scope = Scope.model_validate({key: row[key] for key in Scope.model_fields})
    if old and (
        old["supplier_org_id"] != actor.organization_id
        or old["departure_id"] != command.departure_id
        or old["code"] != command.code
        or old["inventory_pool_id"] != scope.inventory_pool_id
    ):
        raise Forbidden("已有方案的供应商、团期、编号和库存池不可更换")
    if (old["version"] if old else 0) != command.expected_version:
        raise Conflict("方案版本已变化，请重新生成预览")
    if conn.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM offer WHERE departure_id=:departure AND code=:code AND id<>:id)"
        ),
        {"departure": command.departure_id, "code": command.code, "id": command.offer_id},
    ):
        raise Conflict("该团期已存在相同方案编号，停用方案也保留原编号")
    before = (
        OfferState.model_validate({key: old[key] for key in OfferState.model_fields})
        if old
        else None
    )
    after = OfferState.model_validate(command.model_dump(include=set(OfferState.model_fields)))
    if before == after:
        raise Conflict("方案内容没有变化")
    if require_preview and (command.scope != scope or command.before != before):
        raise Conflict("方案、团期、产品或供应源与审批预览不一致，请重新提议")
    return row, scope, before


def propose(engine, actor, command: Command):
    with transaction(engine, actor) as conn:
        _, scope, before = _prepare(conn, actor, command, require_preview=False)
    payload = Preview(**command.model_dump(), scope=scope, before=before)
    return changes.stage(engine, actor, "offer", payload.model_dump(mode="json"))


def validate_change(conn, actor, payload, *, lock=False):
    command = Preview.model_validate(payload)
    _prepare(conn, actor, command, lock=lock)
    return command.offer_id, command.expected_version


def apply_command(conn, actor, change_id, payload):
    command = Preview.model_validate(payload)
    row, scope, _ = _prepare(conn, actor, command, lock=True)
    conn.execute(
        text("""INSERT INTO offer(id,supplier_org_id,connection_id,departure_id,code,name,service_description,active,inventory_pool_id)
      VALUES(:id,:supplier,:connection,:departure,:code,:name,:description,:active,:pool)
      ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,service_description=EXCLUDED.service_description,
        active=EXCLUDED.active,version=offer.version+1"""),
        {
            "id": command.offer_id,
            "supplier": actor.organization_id,
            "connection": row["connection_id"],
            "departure": command.departure_id,
            "code": command.code,
            "name": command.name,
            "description": command.service_description,
            "active": command.active,
            "pool": scope.inventory_pool_id,
        },
    )
    return {
        "offer_id": str(command.offer_id),
        "version": command.expected_version + 1,
        "inventory_changed": False,
    }


def list_for_departure(engine, actor, departure_id, *, after=None, limit=25, merchant=False):
    if not 1 <= limit <= 100:
        raise ValueError("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(
            conn,
            *(
                ("supplier_admin", "product_editor", "inventory_manager", "auditor")
                if merchant
                else ("advisor", "buyer_admin")
            ),
        )
        if not conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM departure WHERE id=:id"
                + (" AND supplier_org_id=warehouse_org_id()" if merchant else "")
                + ")"
            ),
            {"id": departure_id},
        ):
            raise Forbidden("团期不存在或无权限")
        where = "departure_id=:departure" + ("" if merchant else " AND active")
        if after and not conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM offer WHERE " + where + " AND id=:after)"),
            {"departure": departure_id, "after": after},
        ):
            raise Forbidden("无效的报价方案游标")
        rows = (
            conn.execute(
                text(
                    "SELECT id,departure_id,code,name,service_description,active,version,inventory_pool_id FROM offer WHERE "
                    + where
                    + (" AND id>:after" if after else "")
                    + " ORDER BY id LIMIT :limit"
                ),
                {"departure": departure_id, "after": after, "limit": limit + 1},
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }
