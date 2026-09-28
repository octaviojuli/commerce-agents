"""Local departure sales controls; upstream stock and source facts remain unchanged."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import changes
from .changes import Conflict
from .integrations import canonical
from .persistence import Forbidden, require_role, transaction

DEFAULT_BUSINESS_TIMEZONE = "Asia/Shanghai"
LABELS = {
    "paused": "云仓已停售",
    "deadline_passed": "已过云仓报名截止时间",
    "departure_expired": "该团期已过出发日期，只可查看历史数据",
    "timezone_invalid": "供应源业务时区无效，请联系接入管理员核对",
    "confirmation_required": "仍需核实供应商报名条件",
}


def business_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise Conflict(LABELS["timezone_invalid"]) from error


def departure_day_end(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)


def evaluate(day, zone_name, *, paused=False, deadline=None, now=None):
    observed = now or datetime.now(UTC)
    try:
        zone = business_zone(zone_name)
        expired = day < observed.astimezone(zone).date()
    except Conflict:
        status = "timezone_invalid"
    else:
        status = (
            "paused"
            if paused
            else "departure_expired"
            if expired
            else "deadline_passed"
            if deadline is not None and observed >= deadline
            else "confirmation_required"
        )
    return {
        "sales_status": status,
        "sales_status_label": LABELS[status],
        "can_quote": status == "confirmation_required",
        "business_timezone": zone_name,
        "local_booking_deadline": deadline.isoformat() if deadline else None,
    }


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    sales_paused: bool = Field(strict=True)
    local_booking_deadline: AwareDatetime | None
    reason: str = Field(min_length=1, max_length=500)


class Proposal(Control):
    before_paused: bool = Field(strict=True)
    before_deadline: AwareDatetime | None
    source_version: int = Field(ge=1, strict=True)
    business_timezone: str


def _current(conn, identifier, *, lock=False):
    row = (
        conn.execute(
            text(
                """SELECT d.id,d.version,d.sales_paused,d.local_booking_deadline,d.depart_date,
        d.code,p.name AS product_name,c.name AS source_name,c.version AS source_version,
        COALESCE(c.capabilities->>'business_timezone',:zone) AS business_timezone
        FROM departure d JOIN supplier_product p ON p.id=d.product_id
        JOIN supplier_connection c ON c.id=d.connection_id
        WHERE d.id=:id AND d.supplier_org_id=warehouse_org_id() AND c.active"""
                + (" FOR UPDATE OF d,c" if lock else "")
            ),
            {"id": identifier, "zone": DEFAULT_BUSINESS_TIMEZONE},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("团期不存在或无权维护")
    return row


def get(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")
        row = _current(conn, identifier)
        return {
            **row,
            **evaluate(
                row["depart_date"],
                row["business_timezone"],
                paused=row["sales_paused"],
                deadline=row["local_booking_deadline"],
            ),
        }


def propose(engine, actor, command: Control):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        row = _current(conn, command.target_id)
        payload = Proposal(
            **command.model_dump(),
            before_paused=row["sales_paused"],
            before_deadline=row["local_booking_deadline"],
            source_version=row["source_version"],
            business_timezone=row["business_timezone"],
        )
    return changes.stage(engine, actor, "departure_sales", payload.model_dump(mode="json"))


def validate_change(conn, actor, payload, *, lock=False):
    require_role(conn, "supplier_admin", "product_editor")
    command = Proposal.model_validate_json(canonical(payload))
    row = _current(conn, command.target_id, lock=lock)
    if (
        row["source_version"] != command.source_version
        or row["business_timezone"] != command.business_timezone
        or row["version"] != command.expected_version
        or row["sales_paused"] != command.before_paused
        or row["local_booking_deadline"] != command.before_deadline
    ):
        raise Conflict("团期或销售规则已变化，请重新预览")
    if (command.sales_paused, command.local_booking_deadline) == (
        command.before_paused,
        command.before_deadline,
    ):
        raise Conflict("销售规则未变化")
    zone = business_zone(row["business_timezone"])
    if command.local_booking_deadline and command.local_booking_deadline > departure_day_end(
        row["depart_date"], zone
    ):
        raise Conflict("云仓报名截止时间不得晚于团期业务日期结束")
    return command.target_id, command.expected_version


def apply_command(conn, actor, change_id, payload):
    command = Proposal.model_validate_json(canonical(payload))
    validate_change(conn, actor, payload, lock=True)
    conn.execute(
        text(
            "UPDATE departure SET sales_paused=:paused,local_booking_deadline=:deadline,version=version+1 WHERE id=:id"
        ),
        {
            "id": command.target_id,
            "paused": command.sales_paused,
            "deadline": command.local_booking_deadline,
        },
    )
    return {"departure_id": str(command.target_id), "version": command.expected_version + 1}
