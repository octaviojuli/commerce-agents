from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, changes
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import transaction

from .test_imports import excel_source as excel_source
from .test_quotes import FakeSource, contract, schedule
from .test_quotes import external_offer as external_offer
from .test_quotes import managed_offer as managed_offer


def test_contract_rejects_changed_grant_before_approval_and_after_approval(
    database, tenant, managed_offer
):
    admin, runtime = database
    proposal = contract(runtime, tenant, managed_offer)
    snapshot = proposal["preview"]["grant_snapshot"]
    assert snapshot["version"] == 1
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=active WHERE id=:id"),
            {"id": UUID(snapshot["id"])},
        )
    with pytest.raises(Conflict, match="供采授权已变化"):
        changes.approve(runtime, tenant.supplier, UUID(proposal["id"]), proposal["payload_hash"])
    updated = contract(runtime, tenant, managed_offer)
    assert updated["preview"]["grant_snapshot"]["version"] == 2
    changes.approve(runtime, tenant.supplier, UUID(updated["id"]), updated["payload_hash"])
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=active WHERE id=:id"),
            {"id": UUID(snapshot["id"])},
        )
    with pytest.raises(Conflict, match="供采授权已变化"):
        changes.apply(runtime, tenant.supplier, UUID(updated["id"]))
    with transaction(runtime, tenant.supplier) as conn:
        assert conn.scalar(text("SELECT count(*) FROM contract_price")) == 0


def login(client, admin, organizations):
    email = f"{uuid4()}@acme.example"
    password = "ACME-test-password-only"
    auth.create_user(admin, email, password, organizations)
    token = client.post("/v1/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": "Bearer " + token, "X-Organization-Id": str(next(iter(organizations)))}


def test_price_management_reads_and_complete_approval(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    with TestClient(create_app(runtime, authentication)) as client:
        headers = login(
            client,
            admin,
            {
                tenant.supplier.organization_id: ["supplier_admin"],
                tenant.buyer.organization_id: ["buyer_admin"],
            },
        )
        buyer = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        options = client.get("/v1/merchant/offers", headers=headers).json()["items"]
        assert len(options) == 1 and options[0]["id"] == str(managed_offer)
        assert options[0]["product_name"] == "ACME 环线" and options[0]["connector_type"] == "excel"
        params = {"offer_id": str(managed_offer), "buyer_org_id": str(tenant.buyer.organization_id)}
        blank = client.get("/v1/merchant/contract", headers=headers, params=params)
        assert blank.status_code == 200 and blank.json()["contract"] is None
        assert client.get("/v1/merchant/contract", headers=buyer, params=params).status_code == 403
        assert (
            client.get(
                "/v1/merchant/contract",
                headers=headers,
                params={**params, "buyer_org_id": str(uuid4())},
            ).status_code
            == 403
        )
        body = {
            **params,
            "expected_version": 0,
            "schedule": schedule().model_dump(mode="json"),
            "source_ref": "ACME tariff",
            "valid_from": datetime.now(UTC).isoformat(),
            "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "grant_snapshot": blank.json()["grant_snapshot"],
        }
        proposal = client.post("/v1/prices/proposals", headers=headers, json=body)
        assert proposal.status_code == 201, proposal.text
        change = client.get("/v1/changes", headers=headers).json()["items"][0]
        assert change["buyer_name"] == "ACME Buyer" and "ACME 环线" in change["target_label"]
        assert change["payload"]["schedule"]["settlement"]["adult"] == "100.00"
        assert (
            client.post(
                f"/v1/changes/{change['id']}/approve",
                headers=headers,
                json={"payload_hash": change["payload_hash"]},
            ).status_code
            == 200
        )
        assert client.post(f"/v1/changes/{change['id']}/apply", headers=headers).status_code == 200
        saved = client.get("/v1/merchant/contract", headers=headers, params=params).json()[
            "contract"
        ]
        assert saved["version"] == 1 and saved["schedule"] == body["schedule"]


async def test_binding_workbench_requires_supplier_and_buyer_confirmations(
    database, authentication, tenant, external_offer
):
    admin, runtime = database
    source = FakeSource()

    @asynccontextmanager
    async def connector():
        yield source

    with TestClient(
        create_app(runtime, authentication, {tenant.connection_id: connector})
    ) as client:
        headers = login(
            client,
            admin,
            {
                tenant.supplier.organization_id: ["supplier_admin"],
                tenant.buyer.organization_id: ["buyer_admin"],
            },
        )
        buyer = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        verified = client.post(
            "/v1/customer-bindings/verify",
            headers=headers,
            json={"connection_id": str(tenant.connection_id), "customer_code": "ACME-CODE"},
        )
        assert verified.status_code == 200, verified.text
        proposal = client.post(
            "/v1/customer-bindings/proposals",
            headers=headers,
            json={
                "verification_id": verified.json()["verification_id"],
                "buyer_org_id": str(tenant.buyer.organization_id),
                "expected_version": 0,
            },
        )
        assert proposal.status_code == 201, proposal.text
        change = client.get("/v1/changes", headers=headers).json()["items"][0]
        assert change["target_label"] == "ACME verified buyer（ACME-CODE） · B2B catalog"
        assert change["buyer_name"] == "ACME Buyer"
        assert client.get("/v1/customer-bindings", headers=buyer).json()["items"] == []
        client.post(
            f"/v1/changes/{change['id']}/approve",
            headers=headers,
            json={"payload_hash": change["payload_hash"]},
        )
        assert client.post(f"/v1/changes/{change['id']}/apply", headers=headers).status_code == 200
        pending = client.get("/v1/customer-bindings", headers=buyer).json()["items"][0]
        assert pending["supplier_name"] == "ACME Supplier" and pending["buyer_name"] == "ACME Buyer"
        assert pending["customer_code"] == "ACME-CODE" and not pending["accepted"]
        accept = f"/v1/customer-bindings/{pending['id']}/accept"
        assert (
            client.post(accept, headers=headers, json={"version": pending["version"]}).status_code
            == 403
        )
        assert (
            client.post(accept, headers=buyer, json={"version": pending["version"] + 1}).status_code
            == 409
        )
        assert (
            client.post(accept, headers=buyer, json={"version": pending["version"]}).status_code
            == 200
        )
        assert client.get("/v1/customer-bindings", headers=buyer).json()["items"][0]["accepted"]
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE distribution_grant SET active=false WHERE id=:id"),
                {"id": tenant.grant_id},
            )
        assert client.get("/v1/customer-bindings", headers=buyer).json()["items"] == []
