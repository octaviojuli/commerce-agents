"""Approved display-only copy, with source facts and synchronization kept separate."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from . import changes
from .changes import Conflict
from .persistence import Forbidden, require_role, transaction


class DisplayState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_override: str | None = Field(max_length=300)
    description_override: str | None = Field(max_length=10000)

    @field_validator("name_override")
    @classmethod
    def name_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("展示名称不能为空；恢复上游名称请使用 null")
        return value.strip() if value is not None else None


class Command(DisplayState):
    target_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    expected_display_version: int = Field(ge=0, strict=True)
    note: str = Field(min_length=1, max_length=500)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    source_hash: str
    connection_version: int


class Preview(Command):
    before: DisplayState
    source: Source


def current(conn, target_id, *, lock=False):
    row = (
        conn.execute(
            text(
                """SELECT p.*,c.connector_type,c.version AS connection_version
        FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id
        WHERE p.id=:id AND p.supplier_org_id=warehouse_org_id() AND c.active"""
                + (" FOR UPDATE OF p,c" if lock else "")
            ),
            {"id": target_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("线路不存在或无权限")
    return row


def state(row):
    return DisplayState.model_validate({key: row[key] for key in DisplayState.model_fields})


def capture(conn, command):
    require_role(conn, "supplier_admin", "product_editor")
    row = current(conn, command.target_id)
    if row["connector_type"] == "excel":
        raise Conflict("Excel 线路请通过线路内容修改维护，不叠加展示覆盖层")
    if row["version"] != command.expected_version:
        raise Conflict("线路版本已变化，请重新预览")
    if row["display_version"] != command.expected_display_version:
        raise Conflict("展示版本已变化，请重新预览")
    if state(row) == DisplayState.model_validate(
        command.model_dump(include=set(DisplayState.model_fields))
    ):
        raise Conflict("展示内容没有变化")
    return Preview(
        **command.model_dump(),
        before=state(row),
        source=Source.model_validate({key: row[key] for key in Source.model_fields}),
    )


def propose(engine, actor, command):
    with transaction(engine, actor) as conn:
        preview = capture(conn, command)
    return changes.stage(engine, actor, "product_display", preview.model_dump(mode="json"))


def validate_change(conn, actor, payload, *, lock=False):
    command = Preview.model_validate(payload)
    if lock:
        current(conn, command.target_id, lock=True)
    expected = capture(
        conn, Command.model_validate(command.model_dump(include=set(Command.model_fields)))
    )
    if expected != command:
        raise Conflict("上游事实或展示内容与审批预览不一致，请重新提议")
    return command.target_id, command.expected_version


def apply_command(conn, actor, change_id, payload):
    validate_change(conn, actor, payload, lock=True)
    command = Preview.model_validate(payload)
    conn.execute(
        text("""UPDATE supplier_product SET name_override=:name,description_override=:description,
        display_version=display_version+1,display_updated_at=now() WHERE id=:id"""),
        {
            "id": command.target_id,
            "name": command.name_override,
            "description": command.description_override,
        },
    )
    return {
        "product_id": str(command.target_id),
        "version": command.expected_version,
        "display_version": command.expected_display_version + 1,
        "source_changed": False,
    }


def get(engine, actor, target_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")
        row = current(conn, target_id)
        return {
            "target_id": row["id"],
            "version": row["version"],
            "display_version": row["display_version"],
            "connector_type": row["connector_type"],
            "source_name": row["name"],
            "source_description": row["description"],
            "name_override": row["name_override"],
            "description_override": row["description_override"],
            "observed_at": row["observed_at"],
            "display_updated_at": row["display_updated_at"],
        }
