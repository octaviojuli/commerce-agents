from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth, imports, invitations, sources
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_products
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction

from .test_imports import workbook
from .test_quotes import FUTURE, commit


@pytest.fixture
def source(database, tenant):
    _, runtime = database
    return sources.create_excel(
        runtime,
        tenant.supplier,
        sources.Create(
            request_id=uuid4(), name="ACME invited source", note="ACME inventory handover"
        ),
    )


def invitation(runtime, tenant, source):
    return invitations.create(
        runtime,
        tenant.supplier,
        invitations.Create(
            request_id=uuid4(),
            connection_id=source["id"],
            valid_from=datetime.now(UTC) - timedelta(minutes=1),
            expires_at=datetime.now(UTC) + timedelta(days=30),
            note="ACME private supplier operation note",
        ),
    )


def claim(row):
    return invitations.Claim(code=row["code"])


def decision(row, action="approve"):
    return invitations.Decide(
        version=row["version"], action=action, note="ACME counterparty checked"
    )


def another_buyer(admin):
    organization = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO organization(id,name,kinds) VALUES(:id,'ACME Other Buyer',ARRAY['buyer'])"
            ),
            {"id": organization},
        )
    user = auth.create_user(
        admin, f"{uuid4()}@acme.example", "ACME-invite-password", {organization: ["buyer_admin"]}
    )
    return Principal(user, organization)


@pytest.fixture
def buyer_admin(database, tenant):
    admin, _ = database
    user = auth.create_user(
        admin,
        f"{uuid4()}@acme.example",
        "ACME-invite-password",
        {tenant.buyer.organization_id: ["buyer_admin"]},
    )
    return Principal(user, tenant.buyer.organization_id)


def test_http_mutual_onboarding_and_private_token(database, authentication, tenant, source):
    admin, runtime = database
    body = workbook(
        [
            [
                "ACME-R",
                "ACME invited route",
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
    )
    uploaded = imports.upload(runtime, tenant.supplier, source["id"], "ACME.xlsx", body)
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, uploaded["id"]))
    with TestClient(create_app(runtime, authentication)) as client:
        headers = {}
        for role, actor in [("supplier_admin", tenant.supplier), ("buyer_admin", tenant.buyer)]:
            email = f"{uuid4()}@acme.example"
            auth.create_user(admin, email, "ACME-invite-password", {actor.organization_id: [role]})
            token = client.post(
                "/v1/auth/login", json={"email": email, "password": "ACME-invite-password"}
            ).json()["access_token"]
            headers[role] = {
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(actor.organization_id),
            }
        supplier, buyer = headers["supplier_admin"], headers["buyer_admin"]
        request = {
            "request_id": str(uuid4()),
            "connection_id": str(source["id"]),
            "valid_from": datetime.now(UTC).isoformat(),
            "expires_at": None,
            "note": "ACME private supplier operation note",
        }
        assert client.post("/v1/distribution-invitations", json=request).status_code == 401
        assert (
            client.post("/v1/distribution-invitations", json=request, headers=buyer).status_code
            == 403
        )
        created = client.post("/v1/distribution-invitations", json=request, headers=supplier)
        assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
        offered = created.json()
        assert len(offered["code"]) == 43 and offered["status"] == "offered"
        assert not any(
            key in offered
            for key in ["token_hash", "request_key", "request_hash", "note", "decision_note"]
        )
        duplicate = client.post(
            "/v1/distribution-invitations", json=request, headers=supplier
        ).json()
        assert (
            duplicate["id"] == offered["id"]
            and duplicate["duplicate"]
            and duplicate["code"] is None
        )
        assert (
            client.post(
                "/v1/distribution-invitations",
                json={**request, "note": "changed"},
                headers=supplier,
            ).status_code
            == 409
        )
        code = {"code": offered["code"]}
        preview = client.post("/v1/distribution-invitations/preview", json=code, headers=buyer)
        assert preview.status_code == 200 and preview.json()["source_name"] == source["name"]
        assert "private" not in preview.text and "code" not in preview.json()
        assert client.get("/v1/distribution-invitations", headers=buyer).json()["items"] == []
        assert list_products(runtime, tenant.buyer) == []
        claimed = client.post("/v1/distribution-invitations/claim", json=code, headers=buyer).json()
        assert claimed["status"] == "requested" and claimed["version"] == 2
        assert list_products(runtime, tenant.buyer) == []
        approval = {"version": 2, "action": "approve", "note": "ACME buyer name and source checked"}
        path = f"/v1/distribution-invitations/{offered['id']}/decision"
        assert client.post(path, json=approval, headers=buyer).status_code == 403
        approved = client.post(path, json=approval, headers=supplier)
        assert approved.status_code == 200 and approved.json()["grant_id"]
        assert len(list_products(runtime, tenant.buyer)) == 1
        assert client.post(path, json=approval, headers=supplier).status_code == 409
        assert (
            client.get("/v1/distribution-invitations", headers=buyer).json()["items"][0]["status"]
            == "approved"
        )
        with transaction(runtime, tenant.buyer) as conn:
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(text("SELECT token_hash FROM distribution_invitation"))
            with pytest.raises(DBAPIError), conn.begin_nested():
                conn.execute(text("UPDATE distribution_invitation SET status='approved'"))
        with admin.connect() as conn:
            stored = conn.execute(
                text("SELECT row_to_json(i)::text FROM distribution_invitation i WHERE id=:id"),
                {"id": offered["id"]},
            ).scalar_one()
            assert offered["code"] not in stored
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM buyer_customer_binding WHERE connection_id=:id"),
                    {"id": source["id"]},
                )
                == 0
            )


def test_concurrent_claim_and_approval_are_single_use(database, tenant, source, buyer_admin):
    admin, runtime = database
    second = another_buyer(admin)
    offered = invitation(runtime, tenant, source)
    actors = [buyer_admin, second]

    def attempt(actor):
        try:
            return invitations.claim(runtime, actor, claim(offered))
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, actors))
    assert sum(row is not None for row in results) == 1
    winner = next(i for i, row in enumerate(results) if row)
    requested = results[winner]
    assert invitations.claim(runtime, actors[winner], claim(offered)) == requested
    assert invitations.listing(runtime, actors[1 - winner])["items"] == []
    with pytest.raises(Forbidden):
        invitations.listing(runtime, actors[1 - winner], after=offered["id"])

    def approve(_):
        try:
            return invitations.decide(runtime, tenant.supplier, offered["id"], decision(requested))
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        approvals = list(pool.map(approve, range(2)))
    assert sum(row is not None for row in approvals) == 1
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM distribution_grant WHERE connection_id=:id"),
                {"id": source["id"]},
            )
            == 1
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_event WHERE action='invitation.approve'"))
            == 1
        )


def test_invitation_roles_pagination_and_create_replay(database, tenant, source, buyer_admin):
    admin, runtime = database
    command = invitations.Create(
        request_id=uuid4(),
        connection_id=source["id"],
        valid_from=datetime.now(UTC),
        expires_at=None,
        note="ACME invitation delivery",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(
            pool.map(lambda _: invitations.create(runtime, tenant.supplier, command), range(2))
        )
    assert created[0]["id"] == created[1]["id"]
    assert sum(row["code"] is not None for row in created) == 1
    offered = next(row for row in created if row["code"])
    for operation in [invitations.preview, invitations.claim]:
        with pytest.raises(Forbidden):
            operation(runtime, tenant.buyer, claim(offered))
    with pytest.raises(Forbidden):
        invitations.listing(runtime, tenant.buyer)
    auditor_id = auth.create_user(
        admin,
        f"{uuid4()}@acme.example",
        "ACME-invite-password",
        {tenant.supplier.organization_id: ["auditor"]},
    )
    auditor = Principal(auditor_id, tenant.supplier.organization_id)
    assert invitations.listing(runtime, auditor)["items"][0]["id"] == offered["id"]
    with pytest.raises(Forbidden):
        invitations.create(runtime, auditor, command)
    with pytest.raises(Forbidden):
        invitations.decide(runtime, auditor, offered["id"], decision(offered, "revoke"))
    ids = {offered["id"]}
    for _ in range(3):
        ids.add(invitation(runtime, tenant, source)["id"])
    found, cursor = [], None
    while True:
        page = invitations.listing(runtime, tenant.supplier, after=cursor, limit=2)
        found.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(found) == len(set(found)) == len(ids) and set(found) == ids
    with pytest.raises(Forbidden):
        invitations.listing(runtime, buyer_admin, after=offered["id"])
    with pytest.raises(Conflict):
        invitations.listing(runtime, tenant.supplier, limit=101)
    for operation in [invitations.preview, invitations.claim]:
        with pytest.raises(Conflict):
            operation(runtime, buyer_admin, invitations.Claim(code="x" * 43))


@pytest.mark.parametrize("fault", ["expired", "grant_expired", "revoked", "source"])
def test_unavailable_invitations_never_create_access(database, tenant, source, buyer_admin, fault):
    admin, runtime = database
    offered = invitation(runtime, tenant, source)
    if fault == "expired":
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE distribution_invitation SET invite_expires_at=now()-interval '1 second' WHERE id=:id"
                ),
                {"id": offered["id"]},
            )
    elif fault == "grant_expired":
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE distribution_invitation SET expires_at=now()-interval '1 second' WHERE id=:id"
                ),
                {"id": offered["id"]},
            )
    elif fault == "revoked":
        invitations.decide(runtime, tenant.supplier, offered["id"], decision(offered, "revoke"))
    else:
        sources.control(
            runtime,
            tenant.supplier,
            source["id"],
            sources.Control(version=1, name=source["name"], active=False, note="ACME stopped"),
        )
    for function in [invitations.preview, invitations.claim]:
        with pytest.raises(Conflict):
            function(runtime, buyer_admin, claim(offered))
    assert invitations.listing(runtime, buyer_admin)["items"] == []


def test_concurrent_creation_key_cannot_name_two_sources(database, tenant, source):
    _, runtime = database
    other = sources.create_excel(
        runtime,
        tenant.supplier,
        sources.Create(request_id=uuid4(), name="ACME Second Source", note="ACME handover"),
    )
    command = invitations.Create(
        request_id=uuid4(),
        connection_id=source["id"],
        valid_from=datetime.now(UTC),
        expires_at=None,
        note="ACME invitation",
    )

    def create(identifier):
        try:
            return invitations.create(
                runtime, tenant.supplier, command.model_copy(update={"connection_id": identifier})
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [source["id"], other["id"]]))
    assert sum(row is not None for row in results) == 1
    assert len(invitations.listing(runtime, tenant.supplier)["items"]) == 1


@pytest.mark.parametrize("fault", ["source", "buyer", "expired", "existing", "outbox"])
def test_approval_rechecks_parties_and_rolls_back(database, tenant, source, buyer_admin, fault):
    admin, runtime = database
    offered = invitation(runtime, tenant, source)
    requested = invitations.claim(runtime, buyer_admin, claim(offered))
    with admin.begin() as conn:
        if fault == "source":
            conn.execute(
                text("UPDATE supplier_connection SET name='ACME revised' WHERE id=:id"),
                {"id": source["id"]},
            )
        elif fault == "buyer":
            conn.execute(
                text("UPDATE membership SET active=false WHERE user_id=:id"),
                {"id": buyer_admin.user_id},
            )
        elif fault == "expired":
            conn.execute(
                text(
                    "UPDATE distribution_invitation SET invite_expires_at=now()-interval '1 second' WHERE id=:id"
                ),
                {"id": offered["id"]},
            )
        elif fault == "existing":
            conn.execute(
                text(
                    "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id,active) VALUES(:id,:supplier,:buyer,:source,false)"
                ),
                {
                    "id": uuid4(),
                    "supplier": tenant.supplier.organization_id,
                    "buyer": tenant.buyer.organization_id,
                    "source": source["id"],
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload) VALUES(:id,:org,'invitation.approve',:invite,'{}')"
                ),
                {"id": uuid4(), "org": tenant.supplier.organization_id, "invite": offered["id"]},
            )
    with pytest.raises(DBAPIError if fault == "outbox" else Conflict):
        invitations.decide(runtime, tenant.supplier, offered["id"], decision(requested))
    current = invitations.listing(runtime, tenant.supplier)["items"][0]
    assert current["status"] == "requested" and current["grant_id"] is None
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM distribution_grant WHERE connection_id=:id AND active"),
                {"id": source["id"]},
            )
            == 0
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_event WHERE action='invitation.approve'"))
            == 0
        )
