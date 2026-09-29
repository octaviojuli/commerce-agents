"""Atomic MerchantBackend proposals on the shared approval ledger."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from merchant_agent.changes import GuardrailViolation, check_guardrails
from merchant_agent.config import MerchantAgentConfig
from merchant_agent.types import ChangeItem, ChangeKind

from . import catalog_edits, inventory, pricing, product_display
from .changes import Conflict
from .integrations import canonical
from .persistence import Principal, require_role

POLICY = MerchantAgentConfig(enable_campaigns=False)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ChangeKind
    body: dict


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ChangeKind
    summary: str = Field(min_length=1, max_length=200)
    commands: list[Command] = Field(min_length=1, max_length=25)
    items: list[ChangeItem] = Field(min_length=1)
    currency: str | None = None


def inspect_command(conn: Connection, actor: Principal, command: Command, *, lock=False):
    if command.kind == ChangeKind.LISTING_UPDATE:
        if "display" in command.body:
            if set(command.body) != {"display"}:
                raise Conflict("展示补充不能混合源字段修改")
            payload = command.body["display"]
            target, version = product_display.validate_change(conn, actor, payload, lock=lock)
            edit = product_display.Preview.model_validate(payload)
            items = [
                ChangeItem(
                    target=str(target),
                    field=field,
                    before=getattr(edit.before, column),
                    after=getattr(edit, column),
                )
                for field, column in (
                    ("title", "name_override"),
                    ("long_description", "description_override"),
                )
                if getattr(edit.before, column) != getattr(edit, column)
            ]
            return product_display, target, version, items, None
        handler = catalog_edits
        edit = handler.CatalogEdit.model_validate_json(canonical(command.body))
        if edit.status is not None:
            raise Conflict("上下架必须使用库存状态操作")
        target, version = handler.validate_change(conn, actor, command.body, lock=lock)
        row = handler.current(conn, edit)
        items = [
            ChangeItem(target=str(target), field=field, before=row[column], after=value)
            for field, column, value in (
                ("title", "name", edit.name),
                ("long_description", "description", edit.description),
            )
            if value is not None
        ]
        currency = None
    elif command.kind == ChangeKind.INVENTORY_ACTION:
        if "status" in command.body:
            handler = catalog_edits
            edit = handler.CatalogEdit.model_validate_json(canonical(command.body))
            if (
                edit.name is not None
                or edit.description is not None
                or edit.status not in {"published", "paused"}
            ):
                raise Conflict("库存状态操作只允许上下架")
            target, version = handler.validate_change(conn, actor, command.body, lock=lock)
            row = handler.current(conn, edit)

            def status(value):
                return "active" if value == "published" else value

            items = [
                ChangeItem(
                    target=str(target),
                    field="status",
                    before=status(row["status"]),
                    after=status(edit.status),
                )
            ]
            children = (
                conn.execute(
                    text("SELECT id,status FROM departure WHERE product_id=:id ORDER BY id"),
                    {"id": target},
                )
                .mappings()
                .all()
            )
            if children:
                items = [
                    ChangeItem(
                        target=str(child["id"]),
                        field="status",
                        before=status(
                            child["status"] if row["status"] == "published" else row["status"]
                        ),
                        after=status(
                            child["status"] if edit.status == "published" else edit.status
                        ),
                    )
                    for child in children
                ]
        else:
            require_role(conn, "supplier_admin", "inventory_manager")
            handler = inventory
            edit = handler.InventoryCommand.model_validate_json(canonical(command.body))
            if edit.action != "adjust" or edit.quantity <= 0:
                raise Conflict("商户补库存仅支持增加已托管库存池的总量；销售登记使用专用流程")
            target, version = handler.validate_change(conn, actor, command.body, lock=lock)
            row = handler._target(conn, edit)
            before = row["total"] - row["sold"] - row["held"] - row["blocked"]
            items = [
                ChangeItem(
                    target=str(row["departure_id"]),
                    field="stock",
                    before=before,
                    after=before + edit.quantity,
                )
            ]
        currency = None
    elif command.kind == ChangeKind.PRICE_UPDATE:
        handler = pricing
        edit = handler.ContractProposal.model_validate_json(canonical(command.body))
        target, version = handler.validate_change(conn, actor, command.body, lock=lock)
        row = (
            conn.execute(
                text(
                    "SELECT c.*,o.departure_id FROM contract_price c JOIN offer o ON o.id=c.offer_id WHERE c.id=:id AND c.active AND c.valid_from<=now() AND c.valid_until>now()"
                ),
                {"id": target},
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            raise Conflict("先在协议价管理中发布完整价格，助手不能从未知价格调价")
        before = pricing.PriceSchedule.model_validate(row["schedule"])
        expected = before.model_copy(deep=True)
        expected.settlement.adult = edit.schedule.settlement.adult
        if (
            expected != edit.schedule
            or edit.source_ref != row["source_ref"]
            or edit.valid_from != row["valid_from"]
            or edit.valid_until != row["valid_until"]
        ):
            raise Conflict("助手调价只修改所选采购方的成人同业价，其余条款需专用协议价流程")
        items = [
            ChangeItem(
                target=str(row["departure_id"]),
                field="price",
                before=str(before.settlement.adult)
                if before.settlement.adult is not None
                else None,
                after=str(edit.schedule.settlement.adult)
                if edit.schedule.settlement.adult is not None
                else None,
            )
        ]
        currency = before.currency
    else:
        raise Conflict("一期未开放营销活动")
    return handler, target, version, items, currency


def validate_change(conn: Connection, actor: Principal, payload: dict, *, lock=False):
    batch = Batch.model_validate(payload)
    items, currencies, targets = [], set(), set()
    first = None
    # Stable order makes overlapping multi-pool proposals acquire locks consistently.
    for command in sorted(batch.commands, key=command_order):
        if command.kind != batch.kind:
            raise Conflict("一个审批不能混合不同类型的操作")
        _, target, version, diffs, currency = inspect_command(conn, actor, command, lock=lock)
        if target in targets:
            raise Conflict("同一资源不能在一个审批中重复修改")
        targets.add(target)
        first = first or (target, version)
        items.extend(diffs)
        if currency:
            currencies.add(currency)
    if len(currencies) > 1 or batch.currency != next(iter(currencies), None):
        raise Conflict("价格币种不一致")

    def order(item):
        return item.target, item.field

    if sorted(items, key=order) != sorted(batch.items, key=order):
        raise Conflict("变更预览与当前事实不一致，请重新提议")
    violations = check_guardrails(batch.kind, items, POLICY)
    if violations:
        raise GuardrailViolation(violations)
    return first


def apply_command(conn: Connection, actor: Principal, change_id: UUID, payload: dict):
    batch = Batch.model_validate(payload)
    validate_change(conn, actor, payload, lock=True)
    results = []
    for command in sorted(batch.commands, key=command_order):
        handler, *_ = inspect_command(conn, actor, command, lock=True)
        results.append(
            handler.apply_command(
                conn,
                actor,
                change_id,
                command.body["display"] if handler is product_display else command.body,
            )
        )
    return {"items": results}


def command_order(command):
    return str(
        command.body.get("display", {}).get("target_id")
        or command.body.get("target_id")
        or command.body.get("offer_id")
    )
