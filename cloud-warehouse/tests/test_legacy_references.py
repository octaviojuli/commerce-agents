from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from cloud_warehouse import auth, legacy_references
from cloud_warehouse.admin import onboard
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_api import PASSWORD
from .test_sync import Connector, batch


def source_data():
    data = batch()
    data.routes[0]["routeId"] = 36
    data.departures[0].update(periodId=36, routeId=36)
    return data


def reference(connection, value="RT-36"):
    return legacy_references.Reference(connection_id=connection, reference=value)


async def test_explicit_connection_separates_equal_legacy_ids_and_live_grants(database, tenant):
    admin, runtime = database
    ids = onboard(admin, "ACME Second Source", "ACME Second Buyer", f"{uuid4()}@acme.example")
    other = Principal(UUID(ids["worker_id"]), UUID(ids["supplier_id"]))
    second = UUID(ids["connection_id"])
    for actor, connection in ((tenant.worker, tenant.connection_id), (other, second)):
        await synchronize(runtime, actor, connection, Connector(source_data()))

    def resolve(connection, value="RT-36"):
        return legacy_references.resolve(runtime, tenant.buyer, reference(connection, value))

    first = resolve(tenant.connection_id)
    assert first["warehouse_id"].startswith("WP-") and first["current_version"] == 1
    with pytest.raises(Forbidden) as unseen:
        resolve(second)
    with pytest.raises(Forbidden) as absent:
        resolve(uuid4())
    assert str(unseen.value) == str(absent.value)
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:supplier,:buyer,:connection)"
            ),
            {
                "id": uuid4(),
                "supplier": other.organization_id,
                "buyer": tenant.buyer.organization_id,
                "connection": second,
            },
        )
    assert resolve(second)["warehouse_id"] != first["warehouse_id"]
    departure = resolve(tenant.connection_id, "DP-36")
    assert (
        departure["warehouse_id"].startswith("WD-")
        and departure["product_id"] == first["warehouse_id"]
    )
    assert departure["warehouse_id"] != resolve(second, "DP-36")["warehouse_id"]
    for result in (first, departure):
        assert result["requires_revalidation"] and result["reservation_created"] is False
        assert not {"price", "available_seats", "credential_ref", "source"}.intersection(result)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE id=:id"), {"id": tenant.grant_id}
        )
    with pytest.raises(Forbidden):
        resolve(tenant.connection_id)
    assert resolve(second)["warehouse_id"]  # Revoking one source does not affect another.


@pytest.mark.parametrize(
    "value",
    ["RT-0", "DP--1", "RT-036", "RT-36\n", "rt-36", "WP-36", "RT-36/../../", "RT-" + "1" * 20],
)
def test_legacy_reference_is_strict_and_source_is_required(value):
    with pytest.raises(ValidationError):
        reference(uuid4(), value)
    with pytest.raises(ValidationError):
        legacy_references.Reference(reference="RT-36")


async def test_hidden_or_wrong_kind_sources_and_quarantined_ids_are_not_resolved(database, tenant):
    admin, runtime = database
    data = source_data()
    data.departures.append({**data.departures[0], "periodId": 37, "routeId": 0})
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    for actor, value in (
        (tenant.worker, "RT-36"),
        (tenant.supplier, "RT-36"),
        (tenant.buyer, "DP-37"),
    ):
        with pytest.raises(Forbidden):
            legacy_references.resolve(runtime, actor, reference(tenant.connection_id, value))
    for sql in (
        "UPDATE supplier_product SET status='paused' WHERE connection_id=:id",
        "UPDATE supplier_connection SET active=false WHERE id=:id",
        "UPDATE supplier_connection SET active=true,connector_type='excel' WHERE id=:id",
    ):
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE supplier_product SET status='published' WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
            conn.execute(text(sql), {"id": tenant.connection_id})
        for value in ("RT-36", "DP-36"):
            with pytest.raises(Forbidden):
                legacy_references.resolve(
                    runtime, tenant.buyer, reference(tenant.connection_id, value)
                )


async def test_resolver_reports_current_identity_without_repricing_or_writing(database, tenant):
    admin, runtime = database
    data = source_data()
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    original = legacy_references.resolve(runtime, tenant.buyer, reference(tenant.connection_id))
    data.routes[0]["routeName"] = "ACME Updated"
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE departure SET availability_expires_at=now()-interval '1 day' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    current = legacy_references.resolve(runtime, tenant.buyer, reference(tenant.connection_id))
    assert current["warehouse_id"] == original["warehouse_id"] and current["current_version"] == 2
    dep = legacy_references.resolve(runtime, tenant.buyer, reference(tenant.connection_id, "DP-36"))
    assert dep["resolution"] == "current_catalog_identity" and "available_seats" not in dep
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM quote_snapshot")) == 0
        assert conn.scalar(text("SELECT count(*) FROM agent_conversation")) == 0
    forged = Principal(tenant.buyer.user_id, tenant.supplier.organization_id)
    with pytest.raises(Forbidden):
        legacy_references.resolve(runtime, forged, reference(tenant.connection_id))


async def test_legacy_resolver_http_requires_platform_identity_and_explicit_source(
    database, authentication, tenant
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    path = "/v1/advisor/legacy-references/resolve"
    body = {"reference": "DP-36", "connection_id": str(tenant.connection_id)}
    with TestClient(create_app(runtime, authentication)) as client:
        assert client.post(path, json=body).status_code == 401
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 200 and "no-store" in result.headers["cache-control"]
        assert result.json()["warehouse_id"].startswith("WD-")
        assert client.post(path, json={"reference": "RT-36"}, headers=headers).status_code == 422
        assert (
            client.post(path, json={**body, "customer_id": 1}, headers=headers).status_code == 422
        )
        assert (
            client.post(
                path, json={**body, "connection_id": str(uuid4())}, headers=headers
            ).status_code
            == 403
        )
        headers["X-Organization-Id"] = str(tenant.supplier.organization_id)
        assert client.post(path, json=body, headers=headers).status_code == 403
