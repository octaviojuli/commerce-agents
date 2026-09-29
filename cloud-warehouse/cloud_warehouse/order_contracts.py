"""Phase-two data contracts only: no persistence, transitions, routes, or supplier calls."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReservationRequest(Contract):
    """Proposed buyer input. Identity, prices, seat demand and supplier scope are server-resolved."""

    request_id: UUID
    quote_id: UUID


class TransactionScope(Contract):
    buyer_org_id: UUID
    supplier_org_id: UUID
    connection_id: UUID

    @model_validator(mode="after")
    def distinct_parties(self):
        if self.buyer_org_id == self.supplier_org_id:
            raise ValueError("采购组织和供应组织必须不同")
        return self


class SeatDemand(Contract):
    # Resolved against a supplier rule, never inferred from the number of travelers.
    units: int = Field(gt=0, strict=True)
    seat_rule_version: str = Field(min_length=1, max_length=128)


class ManagedInventory(SeatDemand):
    authority: Literal["warehouse"] = "warehouse"
    pool_id: UUID


class SupplierInventory(SeatDemand):
    authority: Literal["supplier"] = "supplier"
    external_departure_id: str = Field(min_length=1, max_length=128)
    # None explicitly denotes an account-wide API; B2B uses its authorized companyId.
    department_key: str | None = Field(max_length=128)


InventoryBinding = Annotated[ManagedInventory | SupplierInventory, Field(discriminator="authority")]


class ExternalReference(Contract):
    connection_id: UUID
    department_key: str | None = Field(max_length=128)
    object_kind: Literal["reservation", "order"]
    external_id: str = Field(min_length=1, max_length=128)


class OperationIdentity(Contract):
    """A durable local operation key; it is not evidence of upstream idempotency."""

    buyer_org_id: UUID
    action: Literal["reserve", "create_order", "confirm", "release", "cancel", "amend"]
    resource_id: UUID
    request_id: UUID
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SupplierTransactionCapabilities(Contract):
    """Future verified upstream abilities. Declaring one never enables phase-one writes."""

    hold: bool = Field(default=False, strict=True)
    create_order: bool = Field(default=False, strict=True)
    query_order: bool = Field(default=False, strict=True)
    cancel: bool = Field(default=False, strict=True)
    amend: bool = Field(default=False, strict=True)
    idempotent_write: bool = Field(default=False, strict=True)
    create_order_includes_hold: bool = Field(default=False, strict=True)
    idempotency_retention_seconds: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def capability_dependencies(self):
        if self.create_order_includes_hold and not self.create_order:
            raise ValueError("创建订单包含预留的声明必须同时支持创建订单")
        if self.idempotent_write != (self.idempotency_retention_seconds is not None):
            raise ValueError("上游幂等能力须明确保证时长，未支持时不得声明保证时长")
        return self


class TransactionSnapshot(Contract):
    id: UUID
    scope: TransactionScope
    quote_id: UUID
    offer_id: UUID
    inventory: InventoryBinding
    version: int = Field(gt=0, strict=True)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    external_reference: ExternalReference | None = None

    @model_validator(mode="after")
    def references_and_time(self):
        if self.updated_at < self.created_at:
            raise ValueError("更新时间不能早于创建时间")
        if self.external_reference:
            if not isinstance(self.inventory, SupplierInventory):
                raise ValueError("云仓权威库存不能同时绑定上游库存写入结果")
            if (
                self.external_reference.connection_id != self.scope.connection_id
                or self.external_reference.department_key != self.inventory.department_key
            ):
                raise ValueError("上游结果必须属于本供应连接和授权部门")
        return self


class ReservationSnapshot(TransactionSnapshot):
    state: Literal["PENDING", "HELD", "CONFIRMED", "EXPIRED", "RELEASED", "UNKNOWN"]
    held_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def held_expiry(self):
        if self.state == "HELD" and self.held_until is None:
            raise ValueError("有效占位须有明确到期时间")
        if self.held_until is not None and self.held_until <= self.created_at:
            raise ValueError("占位到期时间必须晚于创建时间")
        if (
            isinstance(self.inventory, SupplierInventory)
            and self.state not in {"PENDING", "UNKNOWN"}
            and self.external_reference is None
        ):
            raise ValueError("上游已知占位结果必须保留可回查引用")
        return self


class OrderSnapshot(TransactionSnapshot):
    state: Literal["PENDING", "CONFIRMED", "WAITLISTED", "CANCELLED", "UNKNOWN"]
    reservation_id: UUID | None = None
    # Reference an existing immutable ledger entry, not an instruction to add sold again.
    sale_movement_id: UUID | None = None
    payment_status: Literal["NOT_INTEGRATED"] = "NOT_INTEGRATED"

    @model_validator(mode="after")
    def authority_links(self):
        if isinstance(self.inventory, SupplierInventory) and self.sale_movement_id is not None:
            raise ValueError("上游库存订单不能再次关联云仓扣库存流水")
        if self.external_reference and self.external_reference.object_kind != "order":
            raise ValueError("订单快照的上游引用必须是订单")
        if (
            isinstance(self.inventory, SupplierInventory)
            and self.state not in {"PENDING", "UNKNOWN"}
            and self.external_reference is None
        ):
            raise ValueError("上游已知订单结果必须保留可回查引用")
        if self.state == "WAITLISTED" and self.sale_movement_id is not None:
            raise ValueError("候补不能记录为已售库存")
        return self


class ExistingSaleLink(Contract):
    order_id: UUID
    supplier_org_id: UUID
    pool_id: UUID
    sale_movement_id: UUID
    additional_sold: Literal[0] = 0


class HeldResult(Contract):
    outcome: Literal["HELD"] = "HELD"
    reference: ExternalReference
    held_until: AwareDatetime


class ConfirmedResult(Contract):
    outcome: Literal["CONFIRMED"] = "CONFIRMED"
    reference: ExternalReference


class WaitlistedResult(Contract):
    outcome: Literal["WAITLISTED"] = "WAITLISTED"
    reference: ExternalReference
    reservation_created: Literal[False] = False

    @model_validator(mode="after")
    def order_reference(self):
        if self.reference.object_kind != "order":
            raise ValueError("候补结果须保留上游订单引用")
        return self


class UnknownResult(Contract):
    outcome: Literal["UNKNOWN"] = "UNKNOWN"
    attempt_id: UUID
    reference: ExternalReference | None = None
    next_action: Literal["RECONCILE"] = "RECONCILE"


class RejectedResult(Contract):
    outcome: Literal["REJECTED"] = "REJECTED"
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


SupplierWriteResult = Annotated[
    HeldResult | ConfirmedResult | WaitlistedResult | UnknownResult | RejectedResult,
    Field(discriminator="outcome"),
]


class ReconciliationReport(Contract):
    attempt_id: UUID
    connection_id: UUID
    department_key: str | None = Field(max_length=128)
    observed_at: AwareDatetime
    result: Literal["MATCHED", "NO_MATCH", "AMBIGUOUS", "UNAVAILABLE"]
    candidates: tuple[ExternalReference, ...] = ()
    # Even a zero-match list is not proof that the supplier did not create an order.
    automatic_write_retry: Literal[False] = False

    @model_validator(mode="after")
    def reconciliation_evidence(self):
        count = len(self.candidates)
        if (
            (self.result == "MATCHED" and count != 1)
            or (self.result == "AMBIGUOUS" and count < 2)
            or (self.result in {"NO_MATCH", "UNAVAILABLE"} and count)
        ):
            raise ValueError("回查结果与候选数量不一致")
        identities = set()
        for item in self.candidates:
            if (
                item.connection_id != self.connection_id
                or item.department_key != self.department_key
            ):
                raise ValueError("回查不能混合供应连接或部门")
            identities.add((item.object_kind, item.external_id))
        if len(identities) != count:
            raise ValueError("重复结果不能当作多个回查候选")
        return self


def schemas() -> dict:
    """Export a stable review artifact. No model here is registered as an HTTP request."""
    contracts = {
        "ReservationRequest": ReservationRequest,
        "OperationIdentity": OperationIdentity,
        "SupplierTransactionCapabilities": SupplierTransactionCapabilities,
        "ReservationSnapshot": ReservationSnapshot,
        "OrderSnapshot": OrderSnapshot,
        "ExistingSaleLink": ExistingSaleLink,
        "SupplierWriteResult": SupplierWriteResult,
        "ReconciliationReport": ReconciliationReport,
    }
    return {
        "contract_version": "order-reservation-v1",
        "phase_one_transactions_enabled": False,
        "schemas": {name: TypeAdapter(model).json_schema() for name, model in contracts.items()},
    }
