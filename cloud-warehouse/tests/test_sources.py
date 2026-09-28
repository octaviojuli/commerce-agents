from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, imports, quotes, sources
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, list_products
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from cloud_warehouse.quotes import Party, QuoteRequest

from .test_imports import excel_source as excel_source
from .test_imports import workbook
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer


def create_command():
    return sources.Create(
        request_id=uuid4(), name="ACME Excel supplier", note="ACME source handover"
    )


def test_creation_is_idempotent_atomic_and_private(database, tenant, monkeypatch):
    _, runtime = database
    command = create_command()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: sources.create_excel(runtime, tenant.supplier, command), range(2))
        )
    assert results[0]["id"] == results[1]["id"]
    assert sorted(row["duplicate"] for row in results) == [False, True]
    row = results[0]
    assert row["capabilities"] == sources.EXCEL_CAPABILITIES and row["version"] == 1
    assert not {"credential_ref", "creation_key", "creation_hash"} & row.keys()
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM distribution_grant WHERE connection_id=:id"),
                {"id": row["id"]},
            )
            == 0
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_event WHERE action='source.created'")) == 1
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM outbox_event WHERE kind='source.created'")) == 1
        )
    with pytest.raises(Conflict):
        sources.create_excel(
            runtime, tenant.supplier, command.model_copy(update={"name": "ACME different"})
        )
    uploaded = imports.upload(
        runtime,
        tenant.supplier,
        row["id"],
        "ACME-new.xlsx",
        workbook(
            [
                [
                    "ACME-R",
                    "ACME route",
                    1,
                    "ACME city",
                    "ACME-D",
                    FUTURE.isoformat(),
                    FUTURE.isoformat(),
                    20,
                    2,
                    1,
                ]
            ]
        ),
    )
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, uploaded["id"]))
    assert list_departures(runtime, tenant.supplier)[0]["available_seats"] == 17
    assert list_products(runtime, tenant.buyer) == []
    original = sources._record

    def fail(*args):
        original(*args)
        raise RuntimeError("ACME rollback")

    other = create_command()
    with monkeypatch.context() as patch:
        patch.setattr(sources, "_record", fail)
        with pytest.raises(RuntimeError):
            sources.create_excel(runtime, tenant.supplier, other)
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM supplier_connection WHERE creation_key=:key"),
                {"key": other.request_id},
            )
            == 0
        )
    assert not sources.create_excel(runtime, tenant.supplier, other)["duplicate"]


async def test_source_stop_resume_invalidates_quotes_and_imports(
    database, tenant, excel_source, managed_offer
):
    _, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    quote = await quotes.create(
        runtime,
        tenant.buyer,
        QuoteRequest(offer_id=managed_offer, departure_date=FUTURE, party=Party()),
        "ACME-before-source-stop",
    )
    row = next(
        row
        for row in sources.listing(runtime, tenant.supplier)["items"]
        if row["id"] == excel_source
    )
    command = sources.Control(
        version=row["version"], name=row["name"], active=False, note="ACME pause source"
    )
    paused = sources.control(runtime, tenant.supplier, excel_source, command)
    assert paused["version"] == row["version"] + 1
    assert list_products(runtime, tenant.buyer) == []
    with pytest.raises(Forbidden):
        quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))
    with pytest.raises(Forbidden):
        imports.upload(runtime, tenant.supplier, excel_source, "ACME.xlsx", workbook([]))
    with pytest.raises(Conflict):
        sources.control(runtime, tenant.supplier, excel_source, command)
    sources.control(
        runtime,
        tenant.supplier,
        excel_source,
        sources.Control(
            version=paused["version"], name="ACME renamed", active=True, note="ACME resumed"
        ),
    )
    assert quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))["snapshot_stale"]
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 10
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT version FROM distribution_grant WHERE connection_id=:id"),
                {"id": excel_source},
            )
            == 3
        )
        records = (
            conn.execute(text("SELECT details FROM audit_event WHERE action='source.changed'"))
            .scalars()
            .all()
        )
        assert len(records) == 2 and all(row["grants_reversioned"] == 1 for row in records)


def test_source_http_permissions_pagination_and_closed_fields(database, authentication, tenant):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    user = auth.create_user(
        admin,
        email,
        "ACME-source-password",
        {
            tenant.supplier.organization_id: ["supplier_admin"],
            tenant.buyer.organization_id: ["buyer_admin"],
        },
    )
    actor = Principal(user, tenant.supplier.organization_id)
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-source-password"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(actor.organization_id),
        }
        body = create_command().model_dump(mode="json")
        for extra in (
            {"connector_type": "tour_b2b"},
            {"credential_ref": "env:other"},
            {"capabilities": {}},
            {"name": " "},
        ):
            assert (
                client.post(
                    "/v1/merchant/sources", json={**body, **extra}, headers=headers
                ).status_code
                == 422
            )
        assert client.post("/v1/merchant/sources", json=body).status_code == 401
        assert (
            client.post(
                "/v1/merchant/sources",
                json=body,
                headers={**headers, "X-Organization-Id": str(tenant.buyer.organization_id)},
            ).status_code
            == 403
        )
        result = client.post("/v1/merchant/sources", json=body, headers=headers)
        assert result.status_code == 201
        created = result.json()
        assert client.post("/v1/merchant/sources", json=body, headers=headers).json()["duplicate"]
        first = client.get("/v1/merchant/sources?limit=1", headers=headers).json()
        second = client.get(
            "/v1/merchant/sources",
            params={"limit": 1, "after": first["next_cursor"]},
            headers=headers,
        ).json()
        assert first["next_cursor"] and second["next_cursor"] is None
        assert first["items"][0]["id"] != second["items"][0]["id"]
        assert (
            client.get(
                "/v1/merchant/sources", params={"after": str(uuid4())}, headers=headers
            ).status_code
            == 403
        )
        update = {
            "version": 1,
            "name": "ACME renamed",
            "active": True,
            "note": "ACME name reviewed",
        }
        assert (
            client.post(
                f"/v1/merchant/sources/{tenant.connection_id}/control", json=update, headers=headers
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/v1/merchant/sources/{created['id']}/control", json=update, headers=headers
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/merchant/sources/{created['id']}/control", json=update, headers=headers
            ).status_code
            == 409
        )
        assert (
            sources.create_excel(runtime, actor, sources.Create(**body))["name"] == "ACME renamed"
        )
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE membership SET roles=ARRAY['auditor'] WHERE user_id=:id AND organization_id=:org"
                ),
                {"id": user, "org": actor.organization_id},
            )
        assert client.get("/v1/merchant/sources", headers=headers).status_code == 200
        assert client.post("/v1/merchant/sources", json=body, headers=headers).status_code == 403
        assert (
            client.post(
                f"/v1/merchant/sources/{created['id']}/control",
                json={**update, "version": 2, "active": False},
                headers=headers,
            ).status_code
            == 403
        )
