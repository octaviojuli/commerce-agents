"""Explicit offline imports and owner-only reads of frozen historical advisor cases."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text

from .changes import Conflict
from .integrations import canonical, fingerprint
from .persistence import Forbidden, Principal, require_role, transaction

MAX_BYTES = 2_000_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Message(StrictModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=100_000)


class Day(StrictModel):
    label: str = Field(max_length=80)
    note: str = Field(max_length=300)
    request: bool


class Price(StrictModel):
    tong_ye_adult: float | None = None
    market_adult: float | None = None
    quote_source: str = Field(max_length=1000)
    party_total: float | None = None


class Diff(StrictModel):
    index: int
    kind: Literal["same", "changed", "added", "removed"]
    fields: list[Literal["label", "note"]]
    label: str
    note: str


class Version(StrictModel):
    version: int = Field(ge=1, strict=True)
    parent_version: int | None
    title: str = Field(max_length=80)
    travel_dates: str | None = Field(max_length=60)
    party: str | None = Field(max_length=20)
    days: list[Day] = Field(min_length=1, max_length=20)
    diff: list[Diff] = Field(max_length=40)
    reference_price: Price | None
    share_token_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: AwareDatetime


class Plan(StrictModel):
    plan_id: str = Field(pattern=r"^PL-[A-Za-z0-9]{8}$")
    route_id: int = Field(gt=0, strict=True)
    route_name: str = Field(max_length=1000)
    line_type: str | None = Field(max_length=200)
    departure_id: int | None = Field(default=None, gt=0, strict=True)
    erp_route_id: int | None = Field(default=None, gt=0, strict=True)
    created_at: AwareDatetime
    versions: list[Version] = Field(max_length=1000)


class Bundle(StrictModel):
    format: Literal["tour-legacy-case-v1"] = "tour-legacy-case-v1"
    source_namespace: UUID
    legacy_session_id: str = Field(min_length=1, max_length=128)
    legacy_user_id: str = Field(min_length=1, max_length=256)
    organization_id: UUID
    user_id: UUID
    connection_id: UUID
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    messages: list[Message] = Field(max_length=5000)
    plans: list[Plan] = Field(max_length=200)


def validate_bundle(bundle: Bundle) -> dict:
    body = bundle.model_dump(mode="json")
    if len(canonical(body).encode()) > MAX_BYTES:
        raise ValueError("历史案例超过导入大小上限，禁止截断导入")
    if bundle.updated_at < bundle.created_at:
        raise ValueError("历史案例时间顺序无效")
    if len({p.plan_id for p in bundle.plans}) != len(bundle.plans):
        raise ValueError("历史方案编号重复")
    shares = set()
    for plan in bundle.plans:
        for index, version in enumerate(plan.versions, 1):
            if version.version != index or (
                version.parent_version is not None
                if index == 1
                else version.parent_version is None or not 1 <= version.parent_version < index
            ):
                raise ValueError("历史方案版本链不完整")
            if version.share_token_hash in shares:
                raise ValueError("历史分享映射重复")
            shares.add(version.share_token_hash)
    return body


def import_case(engine, bundle: Bundle, *, expected_database: str, note: str, apply=False):
    """No API/agent write path. An offline administrator supplies explicit verified ownership.

    Replays must match both ownership and bytes; a frozen case cannot be reassigned or
    silently refreshed from a still-changing legacy store. No live model state is imported.
    """
    body = validate_bundle(bundle)
    note = note.strip()
    if not 1 <= len(note) <= 500:
        raise ValueError("迁移须记录 1 至 500 字的归属核对说明")
    actor = Principal(bundle.user_id, bundle.organization_id)
    with engine.begin() as conn:
        if (
            not expected_database
            or conn.scalar(text("SELECT current_database()")) != expected_database
        ):
            raise ValueError("目标数据库与显式指定名称不一致")
        if not conn.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ):
            raise Forbidden("历史导入仅允许专用离线管理连接")
        conn.execute(
            text("SELECT warehouse_begin_context(:user,:org)"),
            {"user": actor.user_id, "org": actor.organization_id},
        )
        require_role(conn, "advisor", "buyer_admin")
        grant = (
            conn.execute(
                text("""SELECT g.id,g.version FROM distribution_grant g
          JOIN supplier_connection c ON c.id=g.connection_id
          WHERE c.id=:source AND g.buyer_org_id=:org AND warehouse_live_grant(g.id)
          AND c.active AND c.connector_type='tour_b2b'
          AND warehouse_catalog_access(c.supplier_org_id,c.id)"""),
                {"source": bundle.connection_id, "org": actor.organization_id},
            )
            .mappings()
            .one_or_none()
        )
        if grant is None:
            raise Forbidden("目标顾问未获该旧系统来源的当前目录授权")
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int.from_bytes(bundle.source_namespace.bytes[:8], "big", signed=True)},
        )
        previous = (
            conn.execute(
                text("""SELECT id,body_hash FROM legacy_case_archive
            WHERE source_namespace=:namespace AND legacy_session_id=:session"""),
                {"namespace": bundle.source_namespace, "session": bundle.legacy_session_id},
            )
            .mappings()
            .one_or_none()
        )
        digest = fingerprint(body)
        if previous and previous["body_hash"] != digest:
            raise Conflict("该历史案例已有不同内容或归属的档案，禁止覆盖")
        identifier = previous["id"] if previous else uuid4()
        if apply and not previous:
            title = next((m.text[:80] for m in bundle.messages if m.role == "user"), "历史顾问案例")
            conn.execute(
                text("""INSERT INTO legacy_case_archive
              (id,source_namespace,legacy_session_id,organization_id,user_id,connection_id,grant_id,grant_version,
               source_hash,body_hash,body,title,imported_by_database_role,import_note)
              VALUES(:id,:namespace,:session,:org,:user,:source,:grant,:grant_version,:hash,:digest,CAST(:body AS jsonb),
                     :title,current_user,:note)"""),
                {
                    "id": identifier,
                    "namespace": bundle.source_namespace,
                    "session": bundle.legacy_session_id,
                    "org": actor.organization_id,
                    "user": actor.user_id,
                    "source": bundle.connection_id,
                    "grant": grant["id"],
                    "grant_version": grant["version"],
                    "hash": bundle.source_hash,
                    "digest": digest,
                    "body": canonical(body),
                    "title": title,
                    "note": note,
                },
            )
        return {
            "id": str(identifier) if apply or previous else None,
            "applied": apply,
            "already_imported": previous is not None,
            "messages": len(bundle.messages),
            "plans": len(bundle.plans),
            "versions": sum(len(p.versions) for p in bundle.plans),
            "historical_only": True,
            "public_shares_enabled": False,
        }


def list_page(engine, actor, *, before=None, limit=25):
    if not 1 <= limit <= 100:
        raise ValueError("历史档案分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        anchor = None
        if before:
            anchor = (
                conn.execute(
                    text("SELECT id,imported_at FROM legacy_case_archive WHERE id=:id"),
                    {"id": before},
                )
                .mappings()
                .one_or_none()
            )
            if anchor is None:
                raise Forbidden("历史档案不存在或无权读取")
        rows = (
            conn.execute(
                text(
                    "SELECT id,title,imported_at FROM legacy_case_archive "
                    + ("WHERE (imported_at,id)<(:at,:before) " if anchor else "")
                    + "ORDER BY imported_at DESC,id DESC LIMIT :limit"
                ),
                {
                    "at": anchor["imported_at"] if anchor else None,
                    "before": before,
                    "limit": limit + 1,
                },
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def get(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        row = (
            conn.execute(
                text("SELECT id,body,imported_at FROM legacy_case_archive WHERE id=:id"),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("历史档案不存在或无权读取")
        return {
            **dict(row),
            "historical_only": True,
            "requires_revalidation": True,
            "resumable": False,
            "public_shares_enabled": False,
        }
