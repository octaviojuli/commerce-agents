"""Approved buyer-specific price schedules and verified upstream customer bindings."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Engine, text

from . import changes
from .changes import Conflict
from .concurrency import in_worker_thread
from .integrations import SourceError, canonical, fingerprint
from .persistence import Forbidden, Principal, require_role, transaction


class UnitPrices(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    adult: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    child: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    senior: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    single_room: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class Charge(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=200)
    basis: Literal["per_person", "per_booking"]
    market: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    settlement: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class PricePair(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    market: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    settlement: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class PriceSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    market: UnitPrices
    settlement: UnitPrices
    charges: list[Charge] = Field(default_factory=list, max_length=50)
    included_items: list[str] = Field(default_factory=list, max_length=50)
    fees_complete: bool = False
    child_occupies_seat: bool | None = None
    child_age_min: int | None = Field(default=None, ge=0, le=17)
    child_age_max: int | None = Field(default=None, ge=0, le=17)
    room_types: list[str] = Field(default_factory=list, max_length=20)
    room_supplements: dict[Literal["doubles", "twins", "singles"], PricePair] = Field(
        default_factory=dict
    )
    child_bed_prices: dict[Literal["occupied", "unoccupied"], PricePair] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_conditions(self):
        if len({charge.code for charge in self.charges}) != len(self.charges):
            raise ValueError("附加费编号不能重复")
        if (self.child_age_min is None) != (self.child_age_max is None):
            raise ValueError("儿童年龄范围必须同时填写起止值")
        if self.child_age_min is not None and self.child_age_max < self.child_age_min:
            raise ValueError("儿童年龄上限不能低于下限")
        return self


class CustomerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=300)


class SourcePrices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: PriceSchedule
    observed_at: datetime
    expires_at: datetime | None = None
    source_ref: str
    available_seats: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def timestamps(self):
        if self.observed_at.tzinfo is None or (self.expires_at and self.expires_at.tzinfo is None):
            raise ValueError("来源时间必须包含时区")
        if self.expires_at and self.expires_at <= self.observed_at:
            raise ValueError("来源报价已过期")
        return self


class PriceConnector(Protocol):
    async def resolve_customer(self, code: str) -> CustomerIdentity: ...
    async def read_prices(
        self, departure_id: str, customer_id: str, company_id: str
    ) -> SourcePrices: ...


class GrantSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    version: int = Field(ge=1, strict=True)


class ContractProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["contract"] = "contract"
    offer_id: UUID
    price_id: UUID | None = None
    buyer_org_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    grant_snapshot: GrantSnapshot | None = None
    schedule: PriceSchedule
    source_ref: str = Field(min_length=1, max_length=500)
    valid_from: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def dates(self):
        if (
            self.valid_from.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.valid_until <= self.valid_from
        ):
            raise ValueError("协议价生效和截止时间必须包含时区，截止时间晚于生效时间")
        return self


class BindingProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["binding"] = "binding"
    verification_id: UUID
    buyer_org_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    grant_snapshot: GrantSnapshot | None = None


def _supplier_connection(conn: Connection, connection_id: UUID, *, lock=False):
    row = (
        conn.execute(
            text(
                "SELECT id,connector_type FROM supplier_connection WHERE id=:id AND supplier_org_id=warehouse_org_id() AND active"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": connection_id},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise Forbidden("供应商连接不存在或无权限")
    return row


def _grant(conn: Connection, connection_id: UUID, buyer: UUID, *, lock=False):
    row = (
        conn.execute(
            text(
                "SELECT id,version FROM distribution_grant WHERE connection_id=:connection AND supplier_org_id=warehouse_org_id() AND buyer_org_id=:buyer AND warehouse_live_grant(id)"
                + (" FOR UPDATE" if lock else "")
            ),
            {"connection": connection_id, "buyer": buyer},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise Forbidden("双方有效供采授权不存在")
    return row


@in_worker_thread
def _verification_scope(engine, actor, connection_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        _supplier_connection(conn, connection_id)


@in_worker_thread
def _save_verification(engine, actor, connection_id, identity):
    verification_id = uuid4()
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        _supplier_connection(conn, connection_id)
        conn.execute(
            text(
                "INSERT INTO customer_verification(id,supplier_org_id,connection_id,customer_id,customer_code,customer_name,created_by,expires_at,evidence_hash) VALUES(:id,:org,:connection,:customer,:code,:name,:actor,now()+interval '15 minutes',:hash)"
            ),
            {
                "id": verification_id,
                "org": actor.organization_id,
                "connection": connection_id,
                "customer": identity.customer_id,
                "code": identity.code,
                "name": identity.name,
                "actor": actor.user_id,
                "hash": fingerprint(identity.model_dump()),
            },
        )
        changes.audit(
            conn, actor, "customer.verified", verification_id, {"connection_id": str(connection_id)}
        )
    return {"verification_id": str(verification_id), "code": identity.code, "name": identity.name}


async def verify_customer(
    engine: Engine, actor: Principal, connection_id: UUID, code: str, connector: PriceConnector
) -> dict:
    if not code.strip() or len(code) > 128:
        raise ValueError("客户代码不能为空或过长")
    await _verification_scope(engine, actor, connection_id)
    identity = await connector.resolve_customer(code.strip())
    if identity.code != code.strip():
        raise SourceError("CUSTOMER_CODE_MISMATCH")
    return await _save_verification(engine, actor, connection_id, identity)


def propose(engine: Engine, actor: Principal, proposal: ContractProposal | BindingProposal) -> dict:
    with transaction(engine, actor) as conn:
        payload = capture_grant(conn, actor, proposal.model_dump(mode="json"))
    return changes.stage(engine, actor, "pricing", payload)


def capture_grant(conn: Connection, actor: Principal, payload: dict) -> dict:
    proposal, _, meta = _prepare(conn, actor, payload, require_snapshot=False)
    return {
        **proposal.model_dump(mode="json"),
        "grant_snapshot": {"id": str(meta["grant"]), "version": meta["grant_version"]},
    }


def _prepare(
    conn: Connection, actor: Principal, payload: dict, *, lock=False, require_snapshot=True
):
    suffix = " FOR UPDATE" if lock else ""
    if payload.get("action") == "contract":
        require_role(conn, "supplier_admin", "product_editor")
        proposal = ContractProposal.model_validate_json(canonical(payload))
        offer = (
            conn.execute(
                text(
                    "SELECT id,connection_id FROM offer WHERE id=:id AND supplier_org_id=warehouse_org_id() AND active"
                    + suffix
                ),
                {"id": proposal.offer_id},
            )
            .mappings()
            .one_or_none()
        )
        if not offer:
            raise Forbidden("报价方案不存在或无权限")
        source = _supplier_connection(conn, offer["connection_id"])
        if source["connector_type"] != "excel":
            raise Conflict("外部 API 权威报价不能被本地协议价覆盖")
        grant = _grant(conn, source["id"], proposal.buyer_org_id, lock=lock)
        target = proposal.price_id or uuid5(
            proposal.offer_id, "contract:" + str(proposal.buyer_org_id)
        )
        if proposal.valid_until <= datetime.now(UTC):
            raise Conflict("协议价已过期")
        saved = (
            conn.execute(
                text(
                    "SELECT version,offer_id,layer,buyer_org_id FROM contract_price WHERE id=:id"
                    + suffix
                ),
                {"id": target},
            )
            .mappings()
            .one_or_none()
        )
        if saved and (
            saved["offer_id"] != proposal.offer_id
            or saved["buyer_org_id"] != proposal.buyer_org_id
            or saved["layer"] != "contract"
        ):
            raise Forbidden("协议价编号不属于当前方案与采购组织")
        existing = saved["version"] if saved else None
        from .price_books import check_overlap

        check_overlap(
            conn,
            identifier=target,
            offer_id=proposal.offer_id,
            layer="contract",
            buyer_org_id=proposal.buyer_org_id,
            grade_key=None,
            start=proposal.valid_from,
            end=proposal.valid_until,
        )
        meta = {"connection": source["id"], "grant": grant["id"], "grant_version": grant["version"]}
    elif payload.get("action") == "binding":
        require_role(conn, "supplier_admin")
        proposal = BindingProposal.model_validate_json(canonical(payload))
        verified = (
            conn.execute(
                text(
                    "SELECT * FROM customer_verification WHERE id=:id AND supplier_org_id=warehouse_org_id() AND expires_at>now()"
                ),
                {"id": proposal.verification_id},
            )
            .mappings()
            .one_or_none()
        )
        if not verified:
            raise Conflict("上游客户验证不存在或已过期，请重新验证")
        # Lock the stable parent even when this is the first mapping; there is no
        # binding row to lock yet. Concurrent version-zero proposals must not overwrite.
        source = _supplier_connection(conn, verified["connection_id"], lock=lock)
        grant = _grant(conn, source["id"], proposal.buyer_org_id, lock=lock)
        target = uuid5(source["id"], "binding:" + str(proposal.buyer_org_id))
        existing = conn.execute(
            text("SELECT version FROM buyer_customer_binding WHERE id=:id" + suffix), {"id": target}
        ).scalar_one_or_none()
        meta = {
            "connection": source["id"],
            "grant": grant["id"],
            "grant_version": grant["version"],
            "verified": verified,
        }
    else:
        raise Conflict("不支持的价格变更")
    if (existing or 0) != proposal.expected_version:
        raise Conflict("价格或客户映射版本已变化，请重新预览")
    snapshot = proposal.grant_snapshot
    if require_snapshot and snapshot is None:
        raise Conflict("旧预览未绑定供采授权版本，请重新生成预览")
    if snapshot and (snapshot.id != grant["id"] or snapshot.version != grant["version"]):
        raise Conflict("供采授权已变化，请重新生成预览并审批")
    return proposal, target, meta


def validate_change(
    conn: Connection, actor: Principal, payload: dict, *, lock=False
) -> tuple[UUID, int]:
    proposal, target, _ = _prepare(conn, actor, payload, lock=lock)
    return target, proposal.expected_version


def apply_command(conn: Connection, actor: Principal, change_id: UUID, payload: dict) -> dict:
    proposal, target, meta = _prepare(conn, actor, payload, lock=True)
    shared = {
        "id": target,
        "org": actor.organization_id,
        "buyer": proposal.buyer_org_id,
        "connection": meta["connection"],
        "grant": meta["grant"],
    }
    if isinstance(proposal, ContractProposal):
        conn.execute(
            text("""INSERT INTO contract_price(id,supplier_org_id,buyer_org_id,connection_id,offer_id,grant_id,schedule,source_ref,valid_from,valid_until)
            VALUES(:id,:org,:buyer,:connection,:offer,:grant,CAST(:schedule AS jsonb),:ref,:start,:end)
            ON CONFLICT(id) DO UPDATE SET schedule=EXCLUDED.schedule,source_ref=EXCLUDED.source_ref,valid_from=EXCLUDED.valid_from,valid_until=EXCLUDED.valid_until,active=true,version=contract_price.version+1"""),
            {
                **shared,
                "offer": proposal.offer_id,
                "schedule": canonical(proposal.schedule.model_dump(mode="json")),
                "ref": proposal.source_ref,
                "start": proposal.valid_from,
                "end": proposal.valid_until,
            },
        )
    else:
        verified = meta["verified"]
        conn.execute(
            text("""INSERT INTO buyer_customer_binding(id,supplier_org_id,buyer_org_id,connection_id,grant_id,verification_id,customer_id,customer_code,customer_name)
            VALUES(:id,:org,:buyer,:connection,:grant,:verification,:customer,:code,:name)
            ON CONFLICT(id) DO UPDATE SET verification_id=EXCLUDED.verification_id,customer_id=EXCLUDED.customer_id,customer_code=EXCLUDED.customer_code,
            customer_name=EXCLUDED.customer_name,active=true,version=buyer_customer_binding.version+1"""),
            {
                **shared,
                "verification": proposal.verification_id,
                "customer": verified["customer_id"],
                "code": verified["customer_code"],
                "name": verified["customer_name"],
            },
        )
    return {
        "resource_id": str(target),
        "version": proposal.expected_version + 1,
        "buyer_acceptance_required": isinstance(proposal, BindingProposal),
    }


def accept_binding(engine: Engine, actor: Principal, binding_id: UUID, version: int) -> dict:
    with transaction(engine, actor) as conn:
        require_role(conn, "buyer_admin")
        if not conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM buyer_customer_binding WHERE id=:id AND version=:version AND active AND warehouse_live_grant(grant_id))"
            ),
            {"id": binding_id, "version": version},
        ):
            raise Conflict("映射不存在、无权限或版本已变化")
        if not conn.scalar(
            text("SELECT warehouse_accept_binding(:id,:version)"),
            {"id": binding_id, "version": version},
        ):
            raise Conflict("映射不存在、无权限或版本已变化")
        changes.audit(conn, actor, "customer.binding_accepted", binding_id, {"version": version})
    return {"binding_id": str(binding_id), "accepted_version": version}


def list_offers(engine: Engine, actor: Principal, departure_id: UUID) -> list[dict]:
    with transaction(engine, actor) as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT id,departure_id,name,code,version FROM offer WHERE departure_id=:id AND active ORDER BY id"
                ),
                {"id": departure_id},
            ).mappings()
        ]


def list_bindings(
    engine: Engine,
    actor: Principal,
    *,
    after=None,
    limit=100,
    connection_id=None,
    buyer_org_id=None,
) -> list[dict]:
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "buyer_admin")
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT b.id,b.supplier_org_id,b.buyer_org_id,b.connection_id,c.name AS source_name,b.customer_code,b.customer_name,b.version,b.active,warehouse_live_grant(b.grant_id) AS valid,EXISTS(SELECT 1 FROM binding_acceptance a WHERE a.binding_id=b.id AND a.version=b.version) AS accepted FROM buyer_customer_binding b JOIN supplier_connection c ON c.id=b.connection_id WHERE (CAST(:after AS uuid) IS NULL OR b.id>:after) AND (CAST(:connection AS uuid) IS NULL OR b.connection_id=:connection) AND (CAST(:buyer AS uuid) IS NULL OR b.buyer_org_id=:buyer) ORDER BY b.id LIMIT :limit"
                ),
                {
                    "after": after,
                    "limit": max(1, min(limit, 101)),
                    "connection": connection_id,
                    "buyer": buyer_org_id,
                },
            ).mappings()
        ]
