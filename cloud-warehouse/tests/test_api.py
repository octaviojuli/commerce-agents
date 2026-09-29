from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.persistence import Forbidden

from .test_imports import excel_source as excel_source
from .test_imports import workbook
from .test_quotes import FUTURE
from .test_quotes import managed_offer as managed_offer
from .test_sync import Connector

PASSWORD = "ACME-test-password-only"


@pytest.fixture
def accounts(database, tenant):
    admin, _ = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(admin, email, PASSWORD, {tenant.buyer.organization_id: ["advisor"]})
    return email, user


async def test_api_scope_login_logout_and_disabled_transactions(
    database, authentication, tenant, accounts
):
    admin, runtime = database
    email, user = accounts
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with TestClient(create_app(runtime, authentication)) as client:
        assert client.get("/v1/products").status_code == 401
        response = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200
        token = response.json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        products = client.get("/v1/products", headers=headers)
        assert len(products.json()["items"]) == 1
        assert products.headers["cache-control"] == "no-store"
        assert "source" not in products.json()["items"][0]
        assert len(client.get("/v1/me/organizations", headers=headers).json()["items"]) == 1
        wrong = {**headers, "X-Organization-Id": str(tenant.supplier.organization_id)}
        assert client.get("/v1/products", headers=wrong).status_code == 403
        assert client.get("/v1/sync-runs", headers=headers).status_code == 403
        for path in ("orders", "holds", "payments"):
            assert client.post("/v1/" + path, headers=headers).status_code == 403
        with admin.connect() as conn:
            stored = conn.scalar(
                text("SELECT token_hash FROM auth_session WHERE user_id=:id"), {"id": user}
            )
        assert stored != token and len(stored) == 64
        assert client.post("/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/v1/products", headers=headers).status_code == 401


def test_auth_roles_and_revocation(database, authentication, accounts):
    admin, runtime = database
    email, user = accounts
    auth.require_auth_role(authentication)
    with pytest.raises(RuntimeError):
        auth.require_auth_role(runtime)
    with authentication.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT * FROM supplier_product"))
    with pytest.raises(auth.Unauthenticated):
        auth.login(authentication, email, "wrong", str(uuid4()))
    token = auth.login(authentication, email, PASSWORD, str(uuid4()))["access_token"]
    assert auth.authenticate(authentication, token) == user
    with admin.begin() as conn:
        conn.execute(text("UPDATE warehouse_user SET active=false WHERE id=:id"), {"id": user})
    with pytest.raises(auth.Unauthenticated):
        auth.authenticate(authentication, token)


@pytest.mark.parametrize("change", ["membership", "organization", "user", "session", "expiry"])
def test_scoped_authentication_is_one_live_read_and_revocation_is_immediate(
    database, authentication, tenant, accounts, change
):
    admin, _ = database
    email, user = accounts
    token = auth.login(authentication, email, PASSWORD, str(uuid4()))["access_token"]
    statements = []

    def observed(*args):
        statements.append(args[2])

    event.listen(authentication, "before_cursor_execute", observed)
    try:
        assert (
            auth.authenticate(authentication, token, organization_id=tenant.buyer.organization_id)
            == user
        )
        assert len(statements) == 1
    finally:
        event.remove(authentication, "before_cursor_execute", observed)
    with pytest.raises(Forbidden):
        auth.authenticate(authentication, token, organization_id=tenant.supplier.organization_id)
    with admin.begin() as conn:
        if change == "membership":
            conn.execute(
                text("UPDATE membership SET active=false WHERE user_id=:user"), {"user": user}
            )
        elif change == "organization":
            conn.execute(
                text("UPDATE organization SET active=false WHERE id=:org"),
                {"org": tenant.buyer.organization_id},
            )
        elif change == "user":
            conn.execute(
                text("UPDATE warehouse_user SET active=false WHERE id=:user"), {"user": user}
            )
        elif change == "session":
            conn.execute(
                text("UPDATE auth_session SET revoked_at=now() WHERE user_id=:user"), {"user": user}
            )
        else:
            conn.execute(
                text(
                    "UPDATE auth_session SET created_at=now()-interval '2 seconds',expires_at=now()-interval '1 second' WHERE user_id=:user"
                ),
                {"user": user},
            )
    expected = Forbidden if change in {"membership", "organization"} else auth.Unauthenticated
    with pytest.raises(expected):
        auth.authenticate(authentication, token, organization_id=tenant.buyer.organization_id)


def test_business_transaction_rechecks_membership_after_http_authentication(
    database, authentication, tenant, accounts, monkeypatch
):
    admin, runtime = database
    email, user = accounts
    app = create_app(runtime, authentication)
    with TestClient(app) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        original = auth.authenticate

        def revoke_after_read(*args, **kwargs):
            identity = original(*args, **kwargs)
            if kwargs.get("organization_id") is not None:
                with admin.begin() as conn:
                    conn.execute(
                        text("UPDATE membership SET active=false WHERE user_id=:user"),
                        {"user": user},
                    )
            return identity

        monkeypatch.setattr(auth, "authenticate", revoke_after_read)
        result = client.get(
            "/v1/products",
            headers={
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(tenant.buyer.organization_id),
            },
        )
        assert result.status_code == 403


def test_rate_limit_persists_across_auth_instances(database, authentication, accounts):
    admin, _ = database
    email, _ = accounts
    # Start near the limit to avoid 20 expensive password hashes in a regression test.
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO auth_rate_limit(bucket_hash,attempts) VALUES(:hash,20)"),
            {"hash": auth._digest("user:" + email)},
        )
    with pytest.raises(auth.RateLimited):
        auth.login(authentication, email, PASSWORD, str(uuid4()))


def test_inventory_command_accepts_uuid_json_but_rejects_coerced_quantity(
    database, authentication, tenant
):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: ["supplier_admin"]})
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        payload = {
            "action": "open",
            "target_id": str(uuid4()),
            "expected_version": 0,
            "quantity": "1",
            "business_key": "ACME-OPEN",
            "reason": "opening",
        }
        assert (
            client.post("/v1/inventory/proposals", headers=headers, json=payload).status_code == 422
        )
        payload["quantity"] = 1
        assert (
            client.post("/v1/inventory/proposals", headers=headers, json=payload).status_code == 403
        )


def test_http_import_download_and_merchant_approval(database, authentication, tenant, excel_source):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    auth.create_user(
        admin,
        email,
        PASSWORD,
        {
            tenant.supplier.organization_id: ["supplier_admin"],
            tenant.buyer.organization_id: ["advisor"],
        },
    )
    content = workbook(
        [
            [
                "ACME-R",
                "ACME API 线路",
                1,
                "ACME 城市",
                "ACME-D",
                FUTURE.isoformat(),
                FUTURE.isoformat(),
                10,
                0,
                0,
            ]
        ]
    )
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        buyer_headers = {**headers, "X-Organization-Id": str(tenant.buyer.organization_id)}
        uploaded = client.post(
            "/v1/imports",
            params={"connection_id": str(excel_source)},
            content=content,
            headers=headers,
        )
        assert uploaded.status_code == 201, uploaded.text
        batch_id = uploaded.json()["id"]
        downloaded = client.get(f"/v1/imports/{batch_id}/file", headers=headers)
        assert downloaded.status_code == 200 and downloaded.content == content
        assert downloaded.headers["cache-control"] == "no-store"
        assert client.get(f"/v1/imports/{batch_id}/file", headers=buyer_headers).status_code == 403
        change = client.post(f"/v1/imports/{batch_id}/preview", headers=headers).json()
        assert (
            client.post(
                f"/v1/changes/{change['id']}/approve",
                headers=headers,
                json={"payload_hash": change["payload_hash"]},
            ).status_code
            == 200
        )
        assert client.post(f"/v1/changes/{change['id']}/apply", headers=headers).status_code == 200
        rows = client.get("/v1/merchant/listings", headers=headers).json()["items"]
        assert len(rows) == 1 and rows[0]["price"] is None
        assert client.get("/v1/merchant/listings", headers=buyer_headers).status_code == 403
        snapshot = client.get("/v1/merchant/snapshot", headers=headers).json()
        assert snapshot["orders"] is None and snapshot["sales"] is None
        change = client.post(
            "/v1/merchant/content/proposals",
            headers=headers,
            json={"listing_id": rows[0]["listing_id"], "title": "ACME HTTP 编辑"},
        )
        assert change.status_code == 201, change.text
        body = change.json()
        assert (
            client.post(f"/v1/changes/{body['change_id']}/apply", headers=headers).status_code
            == 403
        )
        assert (
            client.post(
                f"/v1/changes/{body['change_id']}/approve",
                headers=headers,
                json={"payload_hash": body["payload_hash"]},
            ).status_code
            == 200
        )
        assert (
            client.post(f"/v1/changes/{body['change_id']}/apply", headers=headers).status_code
            == 200
        )
        detail = client.get(
            f"/v1/merchant/listings/{rows[0]['listing_id']}", headers=headers
        ).json()
        assert detail["title"] == "ACME HTTP 编辑"
        with admin.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM audit_event WHERE resource_id=:id AND action='import.downloaded'"
                    ),
                    {"id": UUID(batch_id)},
                )
                == 1
            )


@pytest.mark.parametrize("availability", ["known", "unknown", "stale", "managed"])
async def test_departure_response_preserves_inventory_dates_and_sales_fields(
    database, authentication, tenant, accounts, managed_offer, availability
):
    from datetime import UTC, datetime, timedelta

    from fastapi.encoders import jsonable_encoder

    from cloud_warehouse import sales
    from cloud_warehouse.catalog import list_departures
    from cloud_warehouse.persistence import Principal

    from .test_quotes import commit
    from .test_sync import batch

    admin, runtime = database
    source = batch(None if availability == "unknown" else 3)
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source))
    actor = Principal(accounts[1], tenant.buyer.organization_id)
    rows = list_departures(runtime, actor)
    row = next(
        r for r in rows if (r["inventory_pool_id"] is not None) == (availability == "managed")
    )
    if availability == "stale":
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE departure SET availability_expires_at=now()-interval '1 hour' WHERE id=:id"
                ),
                {"id": row["id"]},
            )
    control = sales.get(runtime, tenant.supplier, row["id"])
    commit(
        runtime,
        tenant.supplier,
        sales.propose(
            runtime,
            tenant.supplier,
            sales.Control(
                target_id=row["id"],
                expected_version=control["version"],
                sales_paused=True,
                local_booking_deadline=datetime.now(UTC) + timedelta(days=1),
                reason="ACME response contract fixture",
            ),
        ),
    )
    expected = jsonable_encoder(list_departures(runtime, actor, product_id=row["product_id"]))
    for item in expected:
        seats = item.pop("available_seats")
        item["availability"] = (
            "unknown" if seats is None else "available" if seats > 0 else "unavailable"
        )
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": accounts[0], "password": PASSWORD}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(actor.organization_id),
        }
        response = client.get(
            "/v1/departures", params={"product_id": str(row["product_id"])}, headers=headers
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json() == {"items": expected}
        body = response.json()["items"][0]
        assert body["availability_status"] == availability
        assert "available_seats" not in body
        assert body["availability"] == (
            "available" if availability in {"managed", "known"} else "unknown"
        )
        assert body["sales_paused"] is True and body["can_quote"] is False
        assert body["sales_status"] == "paused" and body["local_booking_deadline"]
        assert datetime.fromisoformat(body["observed_at"]).utcoffset() is not None
        assert (body["availability_expires_at"] is None) == (availability == "managed")
        assert (body["inventory_pool_id"] is None) == (availability != "managed")
        assert "company_id" in body and "inventory_version" in body
        with admin.begin() as conn:
            conn.execute(
                text("UPDATE distribution_grant SET active=false WHERE buyer_org_id=:buyer"),
                {"buyer": actor.organization_id},
            )
        assert client.get("/v1/departures", headers=headers).json() == {"items": []}
