"""Contract examples, not tests of an implemented transaction state machine."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text

from cloud_warehouse import auth
from cloud_warehouse import order_contracts as contracts
from cloud_warehouse.api import create_app

from .test_api import PASSWORD
from .test_imports import excel_source as excel_source
from .test_quotes import managed_offer as managed_offer


def snapshot(authority="warehouse"):
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "scope": {"buyer_org_id": uuid4(), "supplier_org_id": uuid4(), "connection_id": uuid4()},
        "quote_id": uuid4(),
        "offer_id": uuid4(),
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "inventory": {
            "authority": authority,
            "units": 2,
            "seat_rule_version": "ACME-seat-rule-v1",
            **(
                {"pool_id": uuid4()}
                if authority == "warehouse"
                else {"external_departure_id": "ACME-D", "department_key": "ACME-dept"}
            ),
        },
    }


def reference(connection=None, **updates):
    return {
        "connection_id": connection or uuid4(),
        "department_key": "ACME-dept",
        "object_kind": "order",
        "external_id": "ACME-O",
        **updates,
    }


def test_future_request_cannot_supply_identity_prices_or_inventory():
    request = {"request_id": uuid4(), "quote_id": uuid4()}
    assert contracts.ReservationRequest(**request).quote_id == request["quote_id"]
    for key, value in {
        "buyer_org_id": uuid4(),
        "supplier_org_id": uuid4(),
        "price": "1",
        "units": 1,
        "customer_id": 1,
        "passengers": [{"passport": "ACME"}],
    }.items():
        with pytest.raises(ValidationError):
            contracts.ReservationRequest(**request, **{key: value})


def test_snapshot_authority_reference_and_timestamp_invariants():
    data = snapshot()
    record = contracts.ReservationSnapshot(
        **data, state="HELD", held_until=data["created_at"] + timedelta(minutes=5)
    )
    with pytest.raises(ValidationError):
        record.state = "CONFIRMED"
    with pytest.raises(ValidationError):
        contracts.ReservationSnapshot(**data, state="HELD")
    with pytest.raises(ValidationError):
        contracts.ReservationSnapshot(**data, state="HELD", held_until=datetime.now())
    with pytest.raises(ValidationError):
        contracts.ReservationSnapshot(**data, state="HELD", held_until=data["created_at"])
    with pytest.raises(ValidationError):
        contracts.OrderSnapshot(**data, state="CONFIRMED", external_reference=reference())
    remote = snapshot("supplier")
    ref = reference(remote["scope"]["connection_id"])
    result = contracts.OrderSnapshot(**remote, state="WAITLISTED", external_reference=ref)
    assert result.state == "WAITLISTED" and result.payment_status == "NOT_INTEGRATED"
    for changes in (
        {"connection_id": uuid4()},
        {"department_key": "ACME-other"},
        {"object_kind": "reservation"},
    ):
        with pytest.raises(ValidationError):
            contracts.OrderSnapshot(
                **remote, state="CONFIRMED", external_reference={**ref, **changes}
            )
    with pytest.raises(ValidationError):
        contracts.OrderSnapshot(**remote, state="CONFIRMED")
    with pytest.raises(ValidationError):
        contracts.OrderSnapshot(
            **remote, state="CONFIRMED", external_reference=ref, sale_movement_id=uuid4()
        )
    with pytest.raises(ValidationError):
        contracts.OrderSnapshot(**data, state="CONFIRMED", payment_status="PAID")
    with pytest.raises(ValidationError):
        contracts.OrderSnapshot(**data, state="WAITLISTED", sale_movement_id=uuid4())


@pytest.mark.parametrize("units", [0, -1, True, "2", 1.5])
def test_seat_demand_is_explicit_and_not_coerced(units):
    with pytest.raises(ValidationError):
        contracts.ManagedInventory(pool_id=uuid4(), units=units, seat_rule_version="ACME-v1")


def test_supplier_result_distinguishes_waitlist_and_unknown():
    adapter = TypeAdapter(contracts.SupplierWriteResult)
    waitlist = adapter.validate_python({"outcome": "WAITLISTED", "reference": reference()})
    assert not waitlist.reservation_created
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"outcome": "WAITLISTED", "reference": reference(), "reservation_created": True}
        )
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"outcome": "WAITLISTED", "reference": reference(object_kind="reservation")}
        )
    unknown = adapter.validate_python({"outcome": "UNKNOWN", "attempt_id": uuid4()})
    assert unknown.next_action == "RECONCILE"
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"outcome": "UNKNOWN", "attempt_id": uuid4(), "next_action": "RETRY"}
        )
    with pytest.raises(ValidationError):
        adapter.validate_python({"outcome": "CONFIRMED"})


def test_reconciliation_requires_unique_scoped_evidence_and_never_enables_retries():
    conn = uuid4()
    base = {
        "attempt_id": uuid4(),
        "connection_id": conn,
        "department_key": "ACME-dept",
        "observed_at": datetime.now(UTC),
    }
    result = contracts.ReconciliationReport(**base, result="NO_MATCH")
    assert not result.automatic_write_retry
    one = reference(conn)
    two = reference(conn, external_id="ACME-other")
    assert contracts.ReconciliationReport(**base, result="MATCHED", candidates=(one,))
    assert contracts.ReconciliationReport(**base, result="AMBIGUOUS", candidates=(one, two))
    for state, candidates in (
        ("MATCHED", ()),
        ("NO_MATCH", (one,)),
        ("AMBIGUOUS", (one,)),
        ("AMBIGUOUS", (one, one)),
        ("MATCHED", (reference(),)),
        ("MATCHED", (reference(conn, department_key="ACME-other"),)),
    ):
        with pytest.raises(ValidationError):
            contracts.ReconciliationReport(**base, result=state, candidates=candidates)
    with pytest.raises(ValidationError):
        contracts.ReconciliationReport(**base, result="NO_MATCH", automatic_write_retry=True)


def test_existing_sale_link_does_not_deduct_inventory_again():
    data = {
        "order_id": uuid4(),
        "supplier_org_id": uuid4(),
        "pool_id": uuid4(),
        "sale_movement_id": uuid4(),
    }
    assert contracts.ExistingSaleLink(**data).additional_sold == 0
    with pytest.raises(ValidationError):
        contracts.ExistingSaleLink(**data, additional_sold=2)


def test_capabilities_do_not_infer_idempotency_or_implicit_holds():
    assert not any(contracts.SupplierTransactionCapabilities().model_dump().values())
    for fields in (
        {"create_order_includes_hold": True},
        {"idempotent_write": True},
        {"idempotency_retention_seconds": 60},
        {"hold": "true"},
    ):
        with pytest.raises(ValidationError):
            contracts.SupplierTransactionCapabilities(**fields)
    declared = contracts.SupplierTransactionCapabilities(
        create_order=True, create_order_includes_hold=True
    )
    assert not declared.idempotent_write and not declared.hold


def test_review_artifact_matches_models_and_keeps_transactions_disabled():
    path = Path(__file__).resolve().parents[2] / "docs/cloud-warehouse/order-contracts.json"
    assert json.loads(path.read_text()) == contracts.schemas()
    assert contracts.schemas()["phase_one_transactions_enabled"] is False


def test_future_contracts_do_not_enable_http_or_supplier_writes(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    calls = []

    @asynccontextmanager
    async def forbidden_supplier():
        calls.append("connector opened")
        raise AssertionError("Phase one must never open a transaction connector")
        yield  # pragma: no cover

    def balance():
        with admin.connect() as conn:
            return conn.execute(
                text(
                    "SELECT total,sold,held,blocked,version FROM inventory_pool WHERE supplier_org_id=:org ORDER BY id"
                ),
                {"org": tenant.supplier.organization_id},
            ).all()

    before = balance()
    assert before and all(row.held == 0 for row in before)
    with TestClient(
        create_app(runtime, authentication, {tenant.connection_id: forbidden_supplier})
    ) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        body = contracts.ReservationRequest(request_id=uuid4(), quote_id=uuid4()).model_dump(
            mode="json"
        )
        for path in ("/v1/holds", "/v1/orders", "/v1/payments"):
            assert client.post(path, json=body).status_code == 401
            response = client.post(path, json=body, headers=headers)
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == "TRANSACTIONS_DISABLED"
        for path in (
            "/v1/reservations",
            "/v1/orders/create",
            "/v1/refunds",
            f"/v1/orders/{uuid4()}/cancel",
        ):
            assert client.post(path, json=body, headers=headers).status_code == 404
        schema = client.get("/openapi.json").json()
        assert "ReservationRequest" not in schema["components"]["schemas"]
    assert calls == [] and balance() == before
