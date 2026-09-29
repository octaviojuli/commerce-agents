from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, distribution, quotes
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, list_products
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from cloud_warehouse.quotes import Party, QuoteRequest

from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer


def command(row, **updates):
    return distribution.Control(
        **{
            **{key: row[key] for key in ("version", "active", "valid_from", "expires_at")},
            "note": "ACME authorization reviewed",
            **updates,
        }
    )


async def test_revocation_and_reenable_invalidate_catalog_and_quote(
    database, authentication, tenant, excel_source, managed_offer
):
    _, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    request = QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party(adults=2))
    quote = await quotes.create(runtime, tenant.buyer, request, "ACME-before-revoke")
    available = list_departures(runtime, tenant.buyer)[0]["available_seats"]
    row = next(
        row
        for row in distribution.listing(runtime, authentication, tenant.supplier)["items"]
        if row["connection_id"] == excel_source
    )
    stopped = distribution.control(runtime, tenant.supplier, row["id"], command(row, active=False))
    assert stopped["version"] == row["version"] + 1
    assert list_products(runtime, tenant.buyer) == []
    assert list_departures(runtime, tenant.buyer) == []
    with pytest.raises(Forbidden):
        quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))
    with pytest.raises(Conflict):
        distribution.control(runtime, tenant.supplier, row["id"], command(row, active=False))
    restored = distribution.control(
        runtime, tenant.supplier, row["id"], command(stopped, active=True)
    )
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == available
    assert quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))["snapshot_stale"]
    renewed = await quotes.create(runtime, tenant.buyer, request, "ACME-after-reenable")
    assert not renewed["snapshot_stale"] and renewed["versions"]["grant"] == restored["version"]
    with transaction(runtime, tenant.supplier) as conn:
        records = (
            conn.execute(
                text(
                    "SELECT details FROM audit_event WHERE action='distribution_grant.changed' ORDER BY occurred_at,id"
                )
            )
            .scalars()
            .all()
        )
        assert len(records) == 2
        assert records[0]["before"]["active"] and not records[0]["after"]["active"]
        assert records[1]["after"]["active"]
        assert all(record["note"] == "ACME authorization reviewed" for record in records)
        assert (
            conn.scalar(
                text("SELECT count(*) FROM outbox_event WHERE kind='distribution_grant.changed'")
            )
            == 2
        )


def test_control_is_atomic_and_concurrent_versions_conflict(
    database, authentication, tenant, monkeypatch
):
    _, runtime = database
    row = distribution.listing(runtime, authentication, tenant.supplier)["items"][0]
    original = distribution.audit

    def fail(*args):
        original(*args)
        raise RuntimeError("ACME rollback")

    with monkeypatch.context() as patch:
        patch.setattr(distribution, "audit", fail)
        with pytest.raises(RuntimeError):
            distribution.control(runtime, tenant.supplier, row["id"], command(row, active=False))
    assert distribution.listing(runtime, authentication, tenant.supplier)["items"][0] == row
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM audit_event WHERE action='distribution_grant.changed'")
            )
            == 0
        )

    def update(_):
        try:
            return distribution.control(
                runtime, tenant.supplier, row["id"], command(row, active=False)
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, range(2)))
    assert sum(result is not None for result in results) == 1
    current = distribution.listing(runtime, authentication, tenant.supplier)["items"][0]
    assert current["version"] == row["version"] + 1 and not current["active"]


def test_http_grant_pagination_permissions_validation_and_expiry(
    database, authentication, tenant, excel_source
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin,
        email,
        "ACME-only-password",
        {
            tenant.supplier.organization_id: ["supplier_admin"],
            # A buyer organization can also have supplier permissions, but cannot control
            # a grant owned by the actual supplier, even though RLS lets the buyer read it.
            tenant.buyer.organization_id: ["supplier_admin", "buyer_admin"],
        },
    )
    actor = Principal(user, tenant.supplier.organization_id)
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-only-password"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(actor.organization_id),
        }
        buyer = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        page = client.get("/v1/merchant/grants?limit=1", headers=headers).json()
        assert len(page["items"]) == 1 and page["next_cursor"]
        last = client.get(
            "/v1/merchant/grants",
            params={"limit": 1, "after": page["next_cursor"]},
            headers=headers,
        ).json()
        assert len(last["items"]) == 1 and last["next_cursor"] is None
        assert last["items"][0]["id"] != page["items"][0]["id"]
        row = page["items"][0]
        assert row["buyer_name"] == "ACME Buyer" and "credential_ref" not in str(page)
        body = {key: row[key] for key in ("version", "active", "valid_from", "expires_at")}
        body.update({"active": False, "note": "ACME review"})
        path = f"/v1/merchant/grants/{row['id']}/control"
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=buyer).status_code == 403
        assert client.get("/v1/merchant/grants", headers=buyer).json()["items"] == []
        assert (
            client.get(
                "/v1/merchant/grants", params={"after": row["id"]}, headers=buyer
            ).status_code
            == 403
        )
        for change in [
            {"note": "  "},
            {"active": "false"},
            {"expires_at": "2020-01-01T00:00:00Z"},
            {"valid_from": "2026-01-01T00:00:00"},
            {"buyer_org_id": str(uuid4())},
        ]:
            assert client.post(path, json={**body, **change}, headers=headers).status_code == 422
        assert client.get("/v1/merchant/grants?limit=101", headers=headers).status_code == 422
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:id AND organization_id=:org"
                ),
                {"id": user, "org": actor.organization_id},
            )
        assert client.get("/v1/merchant/grants", headers=headers).status_code == 200
        assert client.post(path, json=body, headers=headers).status_code == 403
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET roles=ARRAY['supplier_admin'] WHERE user_id=:id AND organization_id=:org"
                ),
                {"id": user, "org": actor.organization_id},
            )
        now = datetime.now(UTC)
        assert (
            client.post(
                path,
                json={
                    **body,
                    "active": True,
                    "valid_from": (now - timedelta(days=2)).isoformat(),
                    "expires_at": (now - timedelta(days=1)).isoformat(),
                },
                headers=headers,
            ).status_code
            == 409
        )
        scheduled = client.post(
            path,
            json={**body, "active": True, "valid_from": (now + timedelta(days=1)).isoformat()},
            headers=headers,
        )
        assert scheduled.status_code == 200
        items = client.get("/v1/merchant/grants", headers=headers).json()["items"]
        assert not next(item for item in items if item["id"] == row["id"])["effective"]
        assert client.post(path, json=body, headers=headers).status_code == 409
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET active=false WHERE user_id=:id AND organization_id=:org"
                ),
                {"id": user, "org": actor.organization_id},
            )
        assert client.get("/v1/merchant/grants", headers=headers).status_code == 403
