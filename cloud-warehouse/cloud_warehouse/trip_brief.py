"""Owner-scoped, versioned travel requirements and deterministic quote readiness."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from . import conversations, travel_requirements
from .changes import Conflict
from .integrations import canonical
from .persistence import Forbidden, transaction
from .quotes import Party

T = TypeVar("T")
Count = Annotated[int, Field(ge=0, le=100, strict=True)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrEnum):
    said = "said"
    inferred = "inferred"
    advisor = "advisor"


class Field_(Model, Generic[T]):
    value: T | None = None
    source: Source | None = None
    evidence: str = Field(default="", max_length=4000)
    hint: str = Field(default="", max_length=500)


class Window(Model):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("出行结束日期不能早于开始日期")
        return self


class Days(Model):
    min: int = Field(ge=1, le=365)
    max: int = Field(ge=1, le=365)

    @model_validator(mode="after")
    def ordered(self):
        if self.max < self.min:
            raise ValueError("天数范围无效")
        return self


class Rooms(Model):
    doubles: Count = 0
    twins: Count = 0
    singles: Count = 0
    child_bed: bool | None = None
    raw: str = Field(default="", max_length=300)


class Preference(Model):
    key: Literal["no_shopping", "no_self_pay", "slow_pace", "family"]
    label: str = Field(min_length=1, max_length=50)


class Budget(Model):
    max_per_person: Decimal = Field(gt=0, le=100000000, allow_inf_nan=False)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")


class Requirements(Model):
    destinations: Field_[
        Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=20)]
    ] = Field(default_factory=Field_)
    destination_examples: Field_[
        Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=20)]
    ] = Field(default_factory=Field_)
    destination_regions: Field_[
        Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=20)]
    ] = Field(default_factory=Field_)
    excluded_destinations: Field_[
        Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=20)]
    ] = Field(default_factory=Field_)
    window: Field_[Window] = Field(default_factory=Field_)
    days: Field_[Days] = Field(default_factory=Field_)
    depart_city: Field_[Annotated[str, Field(max_length=100)]] = Field(default_factory=Field_)
    party_total: Field_[Annotated[int, Field(ge=1, le=100, strict=True)]] = Field(
        default_factory=Field_
    )
    adults: Field_[Count] = Field(default_factory=Field_)
    children: Field_[Count] = Field(default_factory=Field_)
    seniors: Field_[Count] = Field(default_factory=Field_)
    child_ages: Field_[
        Annotated[list[Annotated[int, Field(ge=0, le=17, strict=True)]], Field(max_length=100)]
    ] = Field(default_factory=Field_)
    rooms: Field_[Rooms] = Field(default_factory=Field_)
    preferences: Field_[Annotated[list[Preference], Field(max_length=4)]] = Field(
        default_factory=Field_
    )
    budget: Field_[Budget] = Field(default_factory=Field_)


FIELDS = tuple(Requirements.model_fields)
ROUTE_FIELDS = {
    "destinations",
    "destination_examples",
    "destination_regions",
    "excluded_destinations",
    "window",
    "days",
    "depart_city",
    "preferences",
}
QUOTE_FIELDS = ROUTE_FIELDS | {
    "party_total",
    "adults",
    "children",
    "seniors",
    "child_ages",
    "rooms",
}


class TripBrief(Requirements):
    route_id: str | None = None
    route_title: str | None = None
    departure_title: str | None = None
    departure_id: str | None = None
    offer_id: UUID | None = None
    quote_id: UUID | None = None
    quote_brief_version: int | None = None
    quote_fields_version: int = 0
    share_token: str | None = None
    offline_status: Literal["none", "customer_confirmed", "offline_hold_recorded"] = "none"
    offline_hold_note: str = Field(default="", max_length=2000)
    quote: dict | None = None


class BriefPatch(Model):
    expected_version: int = Field(ge=0)
    fields: dict = Field(max_length=len(FIELDS))


class BriefIncomplete(ValueError):
    def __init__(self, missing):
        self.missing = missing
        labels = {
            "destinations": "目的地",
            "window": "出行时间",
            "adults": "成人数",
            "children": "儿童数",
            "child_ages": "儿童年龄",
            "rooms": "房型",
            "rooms.child_bed": "儿童占床",
            "party_total_mismatch": "人数构成与总人数不一致",
            "rooms_exceed_party": "单房数量超过人数",
        }
        super().__init__("需求单待补充：" + "、".join(labels.get(key, key) for key in missing))


def readiness(brief: TripBrief) -> dict:
    search = [name for name in ("destinations", "window") if not getattr(brief, name).value]
    missing = list(search)
    for name in ("adults", "children"):
        if getattr(brief, name).value is None:
            missing.append(name)
    children = brief.children.value
    if children is not None and len(brief.child_ages.value or []) != children:
        missing.append("child_ages")
    rooms = brief.rooms.value
    if not rooms or not rooms.doubles + rooms.twins + rooms.singles:
        missing.append("rooms")
    if children and (not rooms or rooms.child_bed is None):
        missing.append("rooms.child_bed")
    if brief.adults.value is not None and children is not None:
        total = brief.adults.value + children + (brief.seniors.value or 0)
        if not 1 <= total <= 100 or (
            brief.party_total.value is not None and total != brief.party_total.value
        ):
            missing.append("party_total_mismatch")
        if rooms and rooms.singles > total:
            missing.append("rooms_exceed_party")
    inferred = [
        name
        for name in FIELDS
        if getattr(brief, name).source == Source.inferred and getattr(brief, name).value is not None
    ]
    return {
        "search": {
            "ready": not search,
            "missing": search,
            "inferred": [
                name
                for name in inferred
                if name
                in (
                    "destinations",
                    "destination_examples",
                    "window",
                    "days",
                    "depart_city",
                    "preferences",
                )
            ],
        },
        "quote": {"ready": not missing, "missing": missing, "inferred": inferred},
    }


def to_party(brief: TripBrief) -> Party:
    missing = readiness(brief)["quote"]["missing"]
    if missing:
        raise BriefIncomplete(missing)
    rooms = brief.rooms.value
    # Explicitly populate every Party field: no implicit one-adult default.
    return Party(
        adults=brief.adults.value,
        children=brief.children.value,
        seniors=brief.seniors.value or 0,
        single_rooms=rooms.singles,
        child_ages=brief.child_ages.value or [],
        room_type=(
            "双人标准间"
            if rooms.doubles and not rooms.twins and not rooms.singles
            else "双床标准间"
            if rooms.twins and not rooms.doubles and not rooms.singles
            else "单人间"
            if rooms.singles and not rooms.doubles and not rooms.twins
            else "混合房型待供应商确认"
        ),
        rooms=rooms.model_dump(mode="json"),
    )


def title(brief):
    parts = []
    if brief.party_total.value:
        parts.append(f"{brief.party_total.value} 人")
    if brief.destinations.value:
        parts.append("·".join(brief.destinations.value))
    if brief.window.value:
        parts.append(brief.window.evidence[:20] or brief.window.value.start.isoformat())
    return " · ".join(parts) or "新客人需求"


def load(conn, identifier, *, lock=False):
    conversation = conversations._load(conn, identifier, lock=lock)
    if conversation["role"] != "advisor":
        raise Forbidden("仅顾问会话使用需求单")
    row = (
        conn.execute(
            text("SELECT body,version FROM conversation_brief WHERE conversation_id=:id"),
            {"id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    return TripBrief.model_validate(row["body"]) if row else TripBrief(), row[
        "version"
    ] if row else 0


def envelope(brief, version):
    from .advisor_stages import derive

    return {
        "body": brief.model_dump(mode="json"),
        "version": version,
        "readiness": readiness(brief),
        "title": title(brief),
        **derive(brief),
    }


def get(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        brief, version = load(conn, identifier)
        return envelope(brief, version)


def save(conn, actor, identifier, brief, version):
    from .copilot_engine import invalidate
    from .copilot_records import snapshot

    previous, previous_version = load(conn, identifier)
    if previous_version:
        invalidate(conn, identifier, previous, brief)
    conn.execute(
        text(
            "INSERT INTO conversation_brief(conversation_id,organization_id,user_id,body,version) VALUES(:id,:org,:user,CAST(:body AS jsonb),:version) ON CONFLICT(conversation_id) DO UPDATE SET body=EXCLUDED.body,version=EXCLUDED.version,updated_at=now()"
        ),
        {
            "id": identifier,
            "org": actor.organization_id,
            "user": actor.user_id,
            "body": canonical(brief.model_dump(mode="json")),
            "version": version,
        },
    )
    conn.execute(
        text("UPDATE agent_conversation SET updated_at=now() WHERE id=:id"), {"id": identifier}
    )
    try:
        retained_departure = (
            UUID(brief.departure_id.removeprefix("WD-")) if brief.departure_id else None
        )
    except ValueError:
        retained_departure = None
    if retained_departure:
        from .assets import OBJECT_WRITE_LOCK

        conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": OBJECT_WRITE_LOCK})
        conn.execute(
            text(
                "UPDATE advisor_asset SET retain_until=GREATEST(retain_until,(SELECT (return_date+91)::timestamp AT TIME ZONE 'Asia/Shanghai' FROM departure WHERE id=CAST(:departure AS uuid))) WHERE deal_id=:id AND purged_at IS NULL AND retain_until>now()"
            ),
            {"id": identifier, "departure": retained_departure},
        )
    snapshot(conn, actor, identifier, brief, version)
    return envelope(brief, version)


def patch(engine, actor, identifier, request: BriefPatch, *, model=False):
    with transaction(engine, actor) as conn:
        brief, version = load(conn, identifier, lock=True)
        if request.expected_version != version:
            raise Conflict("需求单已更新，请刷新后再修改")
        conversation = conversations._load(conn, identifier)
        if conversation["busy"] and not model:
            raise Conflict("助手正在处理需求，请等待本轮完成")
        message = ""
        if model:
            message = (
                conn.scalar(
                    text("SELECT message FROM agent_turn WHERE id=:id AND status='running'"),
                    {"id": conversation["lease_id"]},
                )
                if conversation["busy"]
                else None
            )
            if not message:
                raise Conflict("需求解析必须来自当前有效会话轮次")
        values = brief.model_dump(mode="json")
        conflicts = []
        fields = request.fields
        if model:
            # Validate original proposals before deterministic interpretation; a
            # normalized value must never launder invented evidence or authority.
            for name, value in fields.items():
                if name not in FIELDS:
                    raise ValueError("不允许修改该需求字段")
                proposed = Field_.model_validate(value)
                if proposed.source == Source.said and (
                    not proposed.evidence or proposed.evidence not in message
                ):
                    raise ValueError("客人说的依据必须是本轮原文的子串")
                if proposed.source == Source.inferred and not proposed.hint.strip():
                    raise ValueError("推断必须说明依据并标为待确认")
                if proposed.source not in (Source.said, Source.inferred):
                    raise ValueError("模型不能写入顾问修改来源")
                Requirements.model_validate({name: value})
            started = conn.scalar(
                text("SELECT started_at FROM agent_turn WHERE id=:id"),
                {"id": conversation["lease_id"]},
            )
            fields = travel_requirements.normalize(
                fields, message, brief, started.astimezone(ZoneInfo("Asia/Shanghai")).date()
            )
        for name, value in fields.items():
            if name not in FIELDS:
                raise ValueError("不允许修改该需求字段")
            if model:
                field = Field_.model_validate(value)
                if field.source == Source.said and (
                    not field.evidence or field.evidence not in message
                ):
                    raise ValueError("客人说的依据必须是本轮原文的子串")
                if field.source == Source.inferred and not field.hint.strip():
                    raise ValueError("推断必须说明依据并标为待确认")
                if field.source not in (Source.said, Source.inferred):
                    raise ValueError("模型不能写入顾问修改来源")
                current = getattr(brief, name)
                if current.source == Source.advisor:
                    # Validate the proposal before presenting it, but never change
                    # the advisor's value or provenance, including explicit nulls.
                    proposed = Requirements.model_validate({name: value})
                    if current.value != getattr(proposed, name).value:
                        conflicts.append(
                            {
                                "field": name,
                                "current": values[name]["value"],
                                "proposed": proposed.model_dump(mode="json")[name]["value"],
                            }
                        )
                    continue
                values[name] = value
            else:
                values[name] = {"value": value, "source": "advisor", "evidence": "", "hint": ""}
        updated = TripBrief.model_validate(values)
        changed = {
            name for name in fields if getattr(brief, name).value != getattr(updated, name).value
        }
        if changed & QUOTE_FIELDS:
            updated.quote_fields_version = version + 1
            updated.share_token = None
            updated.offline_status = "none"
            updated.offline_hold_note = ""
        if changed & ROUTE_FIELDS:
            # A new route search must not inherit a previously chosen route or
            # departure, including choices made by older automatic workflows.
            updated.route_id = None
            updated.route_title = None
            updated.departure_id = None
            updated.departure_title = None
            updated.offer_id = None
        result = save(conn, actor, identifier, updated, version + 1)
        result["conflicts"] = conflicts
        return result
