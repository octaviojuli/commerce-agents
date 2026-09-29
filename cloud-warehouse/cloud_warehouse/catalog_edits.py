"""Approved edits to warehouse-owned catalog facts. API source facts stay upstream-owned."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from .changes import Conflict
from .integrations import canonical
from .persistence import Forbidden, Principal, require_role


class CatalogEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    status: Literal["published", "paused", "draft"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if all(getattr(self, key) is None for key in ("name", "description", "status")):
            raise ValueError("至少选择一个修改字段")
        if self.name is not None and not self.name.strip():
            raise ValueError("线路名称不能为空")
        return self


def current(conn: Connection, command: CatalogEdit, *, lock=False):
    row = (
        conn.execute(
            text(
                "SELECT p.*,c.connector_type FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id "
                "WHERE p.id=:id AND p.supplier_org_id=warehouse_org_id() AND c.active"
                + (" FOR UPDATE OF p" if lock else "")
            ),
            {"id": command.target_id},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise Forbidden("线路不存在或无权限")
    if row["connector_type"] != "excel":
        raise Conflict("API 来源的线路事实和状态由上游维护，不能在云仓覆盖")
    if row["version"] != command.expected_version:
        raise Conflict("线路版本已变化，请重新预览")
    return row


def validate_change(conn: Connection, actor: Principal, payload: dict, *, lock=False):
    require_role(conn, "supplier_admin", "product_editor")
    command = CatalogEdit.model_validate_json(canonical(payload))
    current(conn, command, lock=lock)
    return command.target_id, command.expected_version


def apply_command(conn: Connection, actor: Principal, change_id: UUID, payload: dict):
    command = CatalogEdit.model_validate_json(canonical(payload))
    current(conn, command, lock=True)
    conn.execute(
        text("""UPDATE supplier_product SET name=COALESCE(:name,name),
      description=COALESCE(:description,description),status=COALESCE(:status,status),version=version+1
      WHERE id=:id"""),
        {
            "id": command.target_id,
            "name": command.name,
            "description": command.description,
            "status": command.status,
        },
    )
    return {"product_id": str(command.target_id), "version": command.expected_version + 1}
