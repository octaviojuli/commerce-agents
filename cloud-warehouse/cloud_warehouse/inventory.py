"""Supplier offline sales and adjustments. No advisor holds, orders or payments."""

from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Engine, text

from . import changes
from .changes import Conflict as Conflict
from .changes import apply as apply
from .changes import approve as approve
from .changes import discard as discard
from .integrations import canonical
from .persistence import Forbidden, Principal, require_role, transaction


class InventoryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["open", "sale", "adjust", "block", "reverse"]
    target_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    quantity: int = Field(default=0, ge=-1_000_000, le=1_000_000, strict=True)
    business_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)
    reverses_id: UUID | None = None
    initial_sold: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    initial_blocked: int = Field(default=0, ge=0, le=1_000_000, strict=True)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "open":
            if self.initial_sold + self.initial_blocked > self.quantity:
                raise ValueError("期初总量不得小于已售和停售量")
        elif self.initial_sold or self.initial_blocked:
            raise ValueError("期初余额只允许初始化时填写")
        if self.action in {"sale", "block"} and self.quantity <= 0:
            raise ValueError("Sales and blocks require a positive quantity")
        if self.action == "open" and (self.quantity < 0 or self.expected_version != 0):
            raise ValueError("Opening stock must be nonnegative with expected version zero")
        if self.action == "adjust" and self.quantity == 0:
            raise ValueError("Adjustment cannot be zero")
        if self.action == "reverse":
            if not self.reverses_id or self.quantity != 0:
                raise ValueError("Reversal names one movement; its quantity is derived")
        elif self.reverses_id:
            raise ValueError("Only a reversal can name an original movement")
        return self


def _target(conn: Connection, command: InventoryCommand, *, lock=False) -> dict:
    suffix = " FOR UPDATE OF d" if lock else ""
    if command.action == "open":
        row = (
            conn.execute(
                text(
                    "SELECT d.id,d.supplier_org_id,0 AS version,c.capabilities FROM departure d JOIN supplier_connection c ON c.id=d.connection_id WHERE d.id=:id AND d.supplier_org_id=warehouse_org_id() AND c.active"
                    + suffix
                ),
                {"id": command.target_id},
            )
            .mappings()
            .one_or_none()
        )
        exists = conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM inventory_pool WHERE departure_id=:id)"),
            {"id": command.target_id},
        )
        if exists:
            raise Conflict("该团期已初始化库存，不能覆盖已售流水")
    else:
        row = (
            conn.execute(
                text(
                    "SELECT d.*,c.capabilities FROM inventory_pool d JOIN departure dep ON dep.id=d.departure_id JOIN supplier_connection c ON c.id=dep.connection_id WHERE d.id=:id AND d.supplier_org_id=warehouse_org_id() AND c.active"
                    + suffix
                ),
                {"id": command.target_id},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise Forbidden("库存对象不存在或无权限")
    if row["capabilities"].get("inventory_owner") != "warehouse":
        raise Conflict("外部系统权威库存不能通过云仓销售登记或调整")
    if row["version"] != command.expected_version:
        raise Conflict("库存版本已变化，请刷新预览并重新审批")
    return dict(row)


def stage(engine: Engine, actor: Principal, command: InventoryCommand) -> dict:
    return changes.stage(engine, actor, "inventory", command.model_dump(mode="json"))


def validate_change(
    conn: Connection, actor: Principal, payload: dict, *, lock=False
) -> tuple[UUID, int]:
    command = InventoryCommand.model_validate_json(canonical(payload))
    _target(conn, command, lock=lock)
    return command.target_id, command.expected_version


def apply_command(conn: Connection, actor: Principal, change_id: UUID, payload: dict) -> dict:
    command = InventoryCommand.model_validate_json(canonical(payload))
    current = _target(conn, command, lock=True)
    pool_id = (
        uuid5(command.target_id, "inventory") if command.action == "open" else command.target_id
    )
    if conn.scalar(
        text(
            "SELECT EXISTS(SELECT 1 FROM inventory_movement WHERE pool_id=:pool AND business_key=:key)"
        ),
        {"pool": pool_id, "key": command.business_key},
    ):
        raise Conflict("该业务编号已经登记，不能重复扣减", code="DUPLICATE_STOCK_REFERENCE")
    total_delta = sold_delta = blocked_delta = 0
    if command.action in {"open", "adjust"}:
        total_delta = command.quantity
    elif command.action == "sale":
        sold_delta = command.quantity
    elif command.action == "block":
        blocked_delta = command.quantity
    else:
        original = (
            conn.execute(
                text("SELECT * FROM inventory_movement WHERE id=:id AND pool_id=:pool"),
                {"id": command.reverses_id, "pool": pool_id},
            )
            .mappings()
            .one_or_none()
        )
        if not original or original["kind"] not in {"sale", "adjust", "block"}:
            raise Conflict("只能冲正本库存池的销售、调整或停售流水")
        if conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM inventory_movement WHERE reverses_id=:id)"),
            {"id": command.reverses_id},
        ):
            raise Conflict("原流水已冲正")
        total_delta, sold_delta, blocked_delta = (
            -original[k] for k in ("delta_total", "delta_sold", "delta_blocked")
        )
    if command.action == "open":
        sold_delta, blocked_delta = command.initial_sold, command.initial_blocked
        total, sold, blocked, version = total_delta, sold_delta, blocked_delta, 1
        conn.execute(
            text(
                "INSERT INTO inventory_pool(id,supplier_org_id,departure_id,total,sold,blocked) VALUES(:id,:org,:departure,:total,:sold,:blocked)"
            ),
            {
                "id": pool_id,
                "org": actor.organization_id,
                "departure": command.target_id,
                "total": total,
                "sold": sold,
                "blocked": blocked,
            },
        )
    else:
        total, sold, blocked, version = (
            current["total"] + total_delta,
            current["sold"] + sold_delta,
            current["blocked"] + blocked_delta,
            current["version"] + 1,
        )
        if min(total, sold, blocked) < 0 or total < sold + blocked:
            raise Conflict(
                "可用库存不足，或调整后总量小于已售和停售量", code="INVENTORY_LIMIT_REJECTED"
            )
        conn.execute(
            text(
                "UPDATE inventory_pool SET total=:total,sold=:sold,blocked=:blocked,version=:version WHERE id=:id"
            ),
            {
                "id": pool_id,
                "total": total,
                "sold": sold,
                "blocked": blocked,
                "version": version,
            },
        )
    movement = uuid4()
    conn.execute(
        text(
            "INSERT INTO inventory_movement(id,supplier_org_id,pool_id,change_id,kind,business_key,delta_total,delta_sold,delta_blocked,reverses_id,reason,actor_id) VALUES(:id,:org,:pool,:change,:kind,:key,:total,:sold,:blocked,:reverses,:reason,:actor)"
        ),
        {
            "id": movement,
            "org": actor.organization_id,
            "pool": pool_id,
            "change": change_id,
            "kind": command.action,
            "key": command.business_key,
            "total": total_delta,
            "sold": sold_delta,
            "blocked": blocked_delta,
            "reverses": command.reverses_id,
            "reason": command.reason,
            "actor": actor.user_id,
        },
    )
    result = {
        "change_id": str(change_id),
        "pool_id": str(pool_id),
        "movement_id": str(movement),
        "available": total - sold - blocked,
        "version": version,
        "status": "applied",
    }
    return result


def movements(
    engine: Engine, actor: Principal, pool_id: UUID, *, before: UUID | None = None, limit: int = 50
) -> dict:
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "inventory_manager", "auditor")
        if not conn.scalar(
            text(
                "SELECT id FROM inventory_pool WHERE id=:id AND supplier_org_id=warehouse_org_id()"
            ),
            {"id": pool_id},
        ):
            raise Forbidden("库存对象不存在或无权限")
        anchor = None
        if before:
            anchor = conn.execute(
                text("SELECT occurred_at FROM inventory_movement WHERE id=:id AND pool_id=:pool"),
                {"id": before, "pool": pool_id},
            ).scalar_one_or_none()
            if anchor is None:
                raise Forbidden("分页位置不存在或不属于当前库存池")
        rows = [
            dict(row)
            for row in conn.execute(
                text("""SELECT m.id,m.kind,m.business_key,m.delta_total,m.delta_sold,
                    m.delta_blocked,m.reverses_id,m.reason,m.occurred_at,m.change_id,
                    original.business_key AS reverses_business_key,
                    reversal.id AS reversed_by_id
                    FROM inventory_movement m
                    LEFT JOIN inventory_movement original ON original.id=m.reverses_id
                      AND original.pool_id=m.pool_id
                    LEFT JOIN inventory_movement reversal ON reversal.reverses_id=m.id
                      AND reversal.pool_id=m.pool_id
                    WHERE m.pool_id=:pool
                      AND (CAST(:before AS uuid) IS NULL OR (m.occurred_at,m.id)<(:occurred,:before))
                    ORDER BY m.occurred_at DESC,m.id DESC LIMIT :limit"""),
                {"pool": pool_id, "before": before, "occurred": anchor, "limit": limit + 1},
            ).mappings()
        ]
        return {
            "items": rows[:limit],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }
