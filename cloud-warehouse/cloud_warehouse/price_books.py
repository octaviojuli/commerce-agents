"""Approved price windows and buyer grades, with one whole-schedule selection rule."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import text

from . import changes, pricing
from .changes import Conflict
from .integrations import canonical
from .persistence import Forbidden, require_role, transaction


class ScopeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_version: int = Field(ge=1, strict=True)
    offer_version: int | None = Field(default=None, ge=1, strict=True)
    grant_id: UUID | None = None
    grant_version: int | None = Field(default=None, ge=1, strict=True)


class PriceProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["price_window"] = "price_window"
    price_id: UUID
    offer_id: UUID
    layer: Literal["contract", "grade", "standard"]
    buyer_org_id: UUID | None = None
    grade_key: str | None = Field(default=None, min_length=1, max_length=80)
    expected_version: int = Field(ge=0, strict=True)
    snapshot: ScopeSnapshot | None = None
    schedule: pricing.PriceSchedule
    source_ref: str = Field(min_length=1, max_length=500)
    note: str = Field(min_length=1, max_length=500)
    active: bool = Field(default=True, strict=True)
    valid_from: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def scope_and_dates(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("价表截止时间必须晚于生效时间")
        if (
            (self.layer == "contract" and (self.buyer_org_id is None or self.grade_key is not None))
            or (self.layer == "grade" and (self.buyer_org_id is not None or self.grade_key is None))
            or (
                self.layer == "standard"
                and (self.buyer_org_id is not None or self.grade_key is not None)
            )
        ):
            raise ValueError("协议价指定采购组织，等级价指定等级，标准价不指定对象")
        if (
            not self.note.strip()
            or not self.source_ref.strip()
            or (self.grade_key is not None and self.grade_key != self.grade_key.strip())
        ):
            raise ValueError("说明和价格依据不能为空，等级名称不能带首尾空格")
        return self


class GradeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["buyer_grade"] = "buyer_grade"
    connection_id: UUID
    buyer_org_id: UUID
    grade_key: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(ge=0, strict=True)
    active: bool = Field(default=True, strict=True)
    snapshot: ScopeSnapshot | None = None
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def text_fields(self):
        if (
            not self.note.strip()
            or not self.grade_key.strip()
            or self.grade_key != self.grade_key.strip()
        ):
            raise ValueError("等级和说明不能为空，等级名称不能带首尾空格")
        return self


Proposal = Annotated[PriceProposal | GradeProposal, Field(discriminator="action")]


class PriceState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: pricing.PriceSchedule
    source_ref: str
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    active: bool


class GradeState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grade_key: str
    active: bool


class PricePreview(PriceProposal):
    before: PriceState | None


class GradePreview(GradeProposal):
    before: GradeState | None


Preview = Annotated[PricePreview | GradePreview, Field(discriminator="action")]


def _before(command, old):
    model = PriceState if isinstance(command, PriceProposal) else GradeState
    return model.model_validate({key: old[key] for key in model.model_fields}) if old else None


def check_overlap(conn, *, identifier, offer_id, layer, buyer_org_id, grade_key, start, end):
    if conn.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM contract_price WHERE id<>:id AND offer_id=:offer
      AND layer=:layer AND buyer_org_id IS NOT DISTINCT FROM CAST(:buyer AS uuid)
      AND grade_key IS NOT DISTINCT FROM CAST(:grade AS text) AND active
      AND tstzrange(valid_from,valid_until,'[)') && tstzrange(:start,:end,'[)'))"""),
        {
            "id": identifier,
            "offer": offer_id,
            "layer": layer,
            "buyer": buyer_org_id,
            "grade": grade_key,
            "start": start,
            "end": end,
        },
    ):
        raise Conflict("同层级、同适用对象的有效价表时间重叠，请先调整原价表")


def _prepare(conn, actor, payload, *, lock=False, require_snapshot=True):
    command = TypeAdapter(Preview if require_snapshot else Proposal).validate_python(payload)
    require_role(
        conn,
        *(
            ("supplier_admin",)
            if isinstance(command, GradeProposal)
            else ("product_editor", "supplier_admin")
        ),
    )
    suffix = " FOR UPDATE" if lock else ""
    offer = None
    if isinstance(command, PriceProposal):
        offer = (
            conn.execute(
                text(
                    "SELECT id,connection_id,version FROM offer WHERE id=:id AND supplier_org_id=warehouse_org_id() AND active"
                    + suffix
                ),
                {"id": command.offer_id},
            )
            .mappings()
            .one_or_none()
        )
        if offer is None:
            raise Forbidden("报价方案不存在或无权限")
        connection_id = offer["connection_id"]
    else:
        connection_id = command.connection_id
    source = (
        conn.execute(
            text(
                "SELECT id,version,connector_type FROM supplier_connection WHERE id=:id AND supplier_org_id=warehouse_org_id() AND active"
                + suffix
            ),
            {"id": connection_id},
        )
        .mappings()
        .one_or_none()
    )
    if source is None:
        raise Forbidden("供应连接不存在或无权限")
    if source["connector_type"] != "excel":
        raise Conflict("外部 API 权威报价不能被本地价表或等级覆盖")
    grant = (
        pricing._grant(conn, connection_id, command.buyer_org_id, lock=lock)
        if command.buyer_org_id
        else None
    )
    snapshot = ScopeSnapshot(
        source_version=source["version"],
        offer_version=offer["version"] if offer else None,
        grant_id=grant["id"] if grant else None,
        grant_version=grant["version"] if grant else None,
    )
    if (require_snapshot and command.snapshot is None) or (
        command.snapshot is not None and command.snapshot != snapshot
    ):
        raise Conflict("价格预览的供应源、报价方案或供采授权已变化，请重新提议")
    if isinstance(command, PriceProposal):
        identifier = command.price_id
        old = (
            conn.execute(
                text("SELECT * FROM contract_price WHERE id=:id" + suffix), {"id": identifier}
            )
            .mappings()
            .one_or_none()
        )
        if old and (
            old["supplier_org_id"] != actor.organization_id
            or old["offer_id"] != command.offer_id
            or old["layer"] != command.layer
            or old["buyer_org_id"] != command.buyer_org_id
            or old["grade_key"] != command.grade_key
        ):
            raise Forbidden("已有价表的供应组织、方案和适用对象不能改写")
        if command.active:
            if command.valid_until <= datetime.now(UTC):
                raise Conflict("不能发布已过期价表")
            check_overlap(
                conn,
                identifier=identifier,
                offer_id=command.offer_id,
                layer=command.layer,
                buyer_org_id=command.buyer_org_id,
                grade_key=command.grade_key,
                start=command.valid_from,
                end=command.valid_until,
            )
    else:
        identifier = uuid5(connection_id, "grade:" + str(command.buyer_org_id))
        old = (
            conn.execute(
                text("SELECT * FROM buyer_price_grade WHERE id=:id" + suffix), {"id": identifier}
            )
            .mappings()
            .one_or_none()
        )
    if (old["version"] if old else 0) != command.expected_version:
        raise Conflict("价表或采购等级版本已变化，请重新预览")
    if require_snapshot and command.before != _before(command, old):
        raise Conflict("修改前的价表或采购等级与审批预览不一致，请重新提议")
    return command, identifier, snapshot, connection_id, dict(old) if old else None


def propose(engine, actor, command: PriceProposal | GradeProposal):
    with transaction(engine, actor) as conn:
        _, _, snapshot, _, old = _prepare(
            conn, actor, command.model_dump(mode="json"), require_snapshot=False
        )
    before = _before(command, old)
    return changes.stage(
        engine,
        actor,
        "price_book",
        {
            **command.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
            "before": before.model_dump(mode="json") if before else None,
        },
    )


def validate_change(conn, actor, payload, *, lock=False):
    command, identifier, _, _, _ = _prepare(conn, actor, payload, lock=lock)
    return identifier, command.expected_version


def apply_command(conn, actor, change_id, payload):
    command, identifier, snapshot, connection_id, _ = _prepare(conn, actor, payload, lock=True)
    shared = {
        "id": identifier,
        "supplier": actor.organization_id,
        "connection": connection_id,
        "buyer": command.buyer_org_id,
        "grant": snapshot.grant_id,
        "grade": command.grade_key,
        "active": command.active,
    }
    if isinstance(command, PriceProposal):
        conn.execute(
            text("""INSERT INTO contract_price(id,supplier_org_id,buyer_org_id,connection_id,offer_id,grant_id,layer,grade_key,schedule,source_ref,valid_from,valid_until,active)
          VALUES(:id,:supplier,:buyer,:connection,:offer,:grant,:layer,:grade,CAST(:schedule AS jsonb),:ref,:start,:end,:active)
          ON CONFLICT(id) DO UPDATE SET schedule=EXCLUDED.schedule,source_ref=EXCLUDED.source_ref,valid_from=EXCLUDED.valid_from,valid_until=EXCLUDED.valid_until,active=EXCLUDED.active,version=contract_price.version+1"""),
            {
                **shared,
                "offer": command.offer_id,
                "layer": command.layer,
                "schedule": canonical(command.schedule.model_dump(mode="json")),
                "ref": command.source_ref,
                "start": command.valid_from,
                "end": command.valid_until,
            },
        )
    else:
        conn.execute(
            text("""INSERT INTO buyer_price_grade(id,supplier_org_id,buyer_org_id,connection_id,grant_id,grade_key,active)
          VALUES(:id,:supplier,:buyer,:connection,:grant,:grade,:active)
          ON CONFLICT(id) DO UPDATE SET grade_key=EXCLUDED.grade_key,active=EXCLUDED.active,version=buyer_price_grade.version+1"""),
            shared,
        )
    return {"resource_id": str(identifier), "version": command.expected_version + 1}


def resolve(conn, offer_id, buyer_org_id, *, uniform=False):
    # Price, next validity boundary and grade come from one statement snapshot.
    # Evaluate the authorized candidate set once, retaining all RLS checks.
    row = (
        conn.execute(
            text("""WITH applicable AS MATERIALIZED (
              SELECT * FROM warehouse_applicable_prices(:offer,:buyer)
              WHERE NOT :uniform OR layer='standard'
            )
            SELECT p.*, boundary.next_start, grade.grade_id, grade.grade_version
            FROM (SELECT min(valid_from) AS next_start FROM applicable WHERE valid_from>now()) boundary
            LEFT JOIN LATERAL (
              SELECT * FROM applicable WHERE valid_from<=now() AND valid_until>now()
              ORDER BY CASE layer WHEN 'contract' THEN 0 WHEN 'grade' THEN 1 ELSE 2 END,id LIMIT 1
            ) p ON true
            LEFT JOIN LATERAL (
              SELECT b.id AS grade_id,b.version AS grade_version FROM buyer_price_grade b
              JOIN offer o ON o.connection_id=b.connection_id
              WHERE o.id=:offer AND b.buyer_org_id=:buyer AND NOT :uniform
            ) grade ON true"""),
            {"offer": offer_id, "buyer": buyer_org_id, "uniform": uniform},
        )
        .mappings()
        .one()
    )
    price = dict(row)
    boundary = price.pop("next_start")
    grade_id, grade_version = price.pop("grade_id"), price.pop("grade_version")
    grade = {"id": grade_id, "version": grade_version} if grade_id else None
    return (price if price["id"] else None, boundary, grade)


def list_prices(engine, actor, *, offer_id, after=None, limit=25):
    if not 1 <= limit <= 100:
        raise ValueError("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        if not conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM offer WHERE id=:id AND supplier_org_id=warehouse_org_id())"
            ),
            {"id": offer_id},
        ):
            raise Forbidden("报价方案不存在或无权限")
        if after is not None and not conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM contract_price WHERE id=:id AND offer_id=:offer)"),
            {"id": after, "offer": offer_id},
        ):
            raise Forbidden("无效的价表分页游标")
        rows = (
            conn.execute(
                text(
                    "SELECT id,offer_id,layer,buyer_org_id,grade_key,schedule,source_ref,valid_from,valid_until,active,version FROM contract_price WHERE offer_id=:offer"
                    + (" AND id>:after" if after else "")
                    + " ORDER BY id LIMIT :limit"
                ),
                {"offer": offer_id, "after": after, "limit": limit + 1},
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def list_grades(engine, actor, *, connection_id, after=None, limit=25):
    if not 1 <= limit <= 100:
        raise ValueError("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        pricing._supplier_connection(conn, connection_id)
        if after is not None and not conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM buyer_price_grade WHERE id=:id AND connection_id=:source)"
            ),
            {"id": after, "source": connection_id},
        ):
            raise Forbidden("无效的等级分页游标")
        rows = (
            conn.execute(
                text(
                    "SELECT id,buyer_org_id,grade_key,active,version FROM buyer_price_grade WHERE connection_id=:source"
                    + (" AND id>:after" if after else "")
                    + " ORDER BY id LIMIT :limit"
                ),
                {"source": connection_id, "after": after, "limit": limit + 1},
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }
