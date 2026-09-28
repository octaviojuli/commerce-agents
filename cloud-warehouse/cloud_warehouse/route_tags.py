"""Approved route tags; source candidates and local exclusions remain separate."""

import hashlib
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from . import changes, product_display
from .changes import Conflict
from .integrations import canonical
from .persistence import require_role, transaction


def key(label):
    return unicodedata.normalize("NFKC", label).strip().casefold()


class Tag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=60)
    source_label: str | None = Field(default=None, max_length=200)
    category: Literal[
        "destination", "theme", "transport", "stay", "experience", "audience", "other"
    ] = "other"
    state: Literal["confirmed", "excluded"] = "confirmed"
    search_enabled: bool = True
    customer_visible: bool = False

    @model_validator(mode="after")
    def valid(self):
        self.label = self.label.strip()
        if not self.label or any(ord(c) < 32 for c in self.label):
            raise ValueError("标签不能为空或包含控制字符")
        return self


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: UUID
    expected_display_version: int = Field(ge=0, strict=True)
    tags: list[Tag] = Field(max_length=200)
    note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique(self):
        if len({t.id for t in self.tags}) != len(self.tags) or len(
            {key(t.label) for t in self.tags if t.state == "confirmed"}
        ) != sum(t.state == "confirmed" for t in self.tags):
            raise ValueError("标签标识与有效标签不能重复")
        if not self.note.strip():
            raise ValueError("请填写修改说明")
        return self


def source_candidates(raw, approved):
    suppressed = {key(t.get("source_label") or t["label"]) for t in approved}
    seen = set()
    result = []
    for field in ("tags", "itineraryTags"):
        for label in raw.get(field, []) if isinstance(raw.get(field), list) else []:
            if not isinstance(label, str) or not label.strip() or len(label) > 60:
                continue
            normalized = key(label)
            if normalized in seen or normalized in suppressed:
                continue
            seen.add(normalized)
            result.append(
                {
                    "id": "source-" + hashlib.sha256(normalized.encode()).hexdigest()[:20],
                    "label": label.strip(),
                    "source_label": label.strip(),
                    "category": "other",
                    "state": "candidate",
                    "search_enabled": True,
                    "customer_visible": False,
                }
            )
    return result


def get(engine, actor, product_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")
        row = product_display.current(conn, product_id)
        return {
            "target_id": product_id,
            "display_version": row["display_version"],
            "tags": row["approved_tags"],
            "candidates": source_candidates(row["source"], row["approved_tags"]),
        }


def validate_change(conn, actor, payload, *, lock=False):
    require_role(conn, "supplier_admin", "product_editor")
    command = Command.model_validate(payload)
    row = product_display.current(conn, command.target_id, lock=lock)
    if row["display_version"] != command.expected_display_version:
        raise Conflict("标签或展示内容已更新，请重新核对")
    if [t.model_dump() for t in command.tags] == row["approved_tags"]:
        raise Conflict("标签没有变化")
    return command.target_id, command.expected_display_version + 1


def propose(engine, actor, command):
    return changes.stage(engine, actor, "route_tags", command.model_dump(mode="json"))


def apply_command(conn, actor, change_id, payload):
    validate_change(conn, actor, payload, lock=True)
    command = Command.model_validate(payload)
    tags = [t.model_dump() for t in command.tags]
    searchable = " ".join(
        t["label"] for t in tags if t["state"] == "confirmed" and t["search_enabled"]
    )
    conn.execute(
        text("""UPDATE supplier_product SET approved_tags=CAST(:tags AS jsonb),tags_search=:search,
      display_version=display_version+1,display_updated_at=now() WHERE id=:id"""),
        {"id": command.target_id, "tags": canonical(tags), "search": searchable},
    )
    return {
        "product_id": str(command.target_id),
        "display_version": command.expected_display_version + 1,
        "source_changed": False,
    }
