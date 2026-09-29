from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import auth
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.inventory import Conflict, InventoryCommand, apply, approve, movements, stage
from cloud_warehouse.persistence import Forbidden, transaction

from .test_api import PASSWORD
from .test_sync import Connector, batch


@pytest.fixture
async def stock(database, tenant):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_connection SET connector_type='excel',capabilities=capabilities || CAST(:caps AS jsonb) WHERE id=:id"
            ),
            {"id": tenant.connection_id, "caps": '{"inventory_owner":"warehouse"}'},
        )
    return list_departures(runtime, tenant.supplier)[0]["id"]


def propose(runtime, actor, action, target, version, quantity=0, **kwargs):
    return stage(
        runtime,
        actor,
        InventoryCommand(
            action=action,
            target_id=target,
            expected_version=version,
            quantity=quantity,
            business_key=kwargs.pop("business_key", str(uuid4())),
            reason="ACME fixture confirmed offline sale",
            **kwargs,
        ),
    )


def commit(runtime, actor, proposal):
    identity = UUID(proposal["id"])
    approve(runtime, actor, identity, proposal["payload_hash"])
    return apply(runtime, actor, identity)


def test_requires_approval_and_replay_does_not_double_count(database, tenant, stock):
    _, runtime = database
    opening = propose(runtime, tenant.supplier, "open", stock, 0, 5)
    with pytest.raises(Forbidden):
        apply(runtime, tenant.supplier, UUID(opening["id"]))
    with pytest.raises(Conflict):
        approve(runtime, tenant.supplier, UUID(opening["id"]), "modified")
    result = commit(runtime, tenant.supplier, opening)
    assert result["available"] == 5
    assert apply(runtime, tenant.supplier, UUID(opening["id"])) == result
    pool = UUID(result["pool_id"])
    assert len(movements(runtime, tenant.supplier, pool)["items"]) == 1
    sale = propose(runtime, tenant.supplier, "sale", pool, 1, 2, business_key="ACME-SALE-1")
    sold = commit(runtime, tenant.supplier, sale)
    assert sold["available"] == 3
    duplicate = propose(runtime, tenant.supplier, "sale", pool, 2, 2, business_key="ACME-SALE-1")
    with pytest.raises(Conflict, match="重复"):
        commit(runtime, tenant.supplier, duplicate)
    assert len(movements(runtime, tenant.supplier, pool)["items"]) == 2
    with pytest.raises(Forbidden):
        movements(runtime, tenant.buyer, pool)


@pytest.mark.parametrize("withdrawal", ["grant", "product", "departure", "quarantine"])
def test_inventory_and_offer_reads_follow_live_catalog_scope(database, tenant, stock, withdrawal):
    admin, runtime = database
    commit(runtime, tenant.supplier, propose(runtime, tenant.supplier, "open", stock, 0, 5))
    with transaction(runtime, tenant.buyer) as conn:
        for table in ("inventory_pool", "offer"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 1
        with admin.begin() as control:
            queries = {
                "grant": "UPDATE distribution_grant SET active=false WHERE connection_id=:id",
                "product": "UPDATE supplier_product SET status='paused' WHERE connection_id=:id",
                "departure": "UPDATE departure SET status='paused' WHERE connection_id=:id",
                "quarantine": "UPDATE departure SET source_quarantined=true WHERE connection_id=:id",
            }
            control.execute(text(queries[withdrawal]), {"id": tenant.connection_id})
        for table in ("inventory_pool", "offer"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    with transaction(runtime, tenant.supplier) as conn:
        for table in ("inventory_pool", "offer"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 1


def test_last_place_concurrency_and_ledger_reconstruction(database, tenant, stock):
    _, runtime = database
    opened = commit(
        runtime, tenant.supplier, propose(runtime, tenant.supplier, "open", stock, 0, 1)
    )
    pool = UUID(opened["pool_id"])
    proposals = [propose(runtime, tenant.supplier, "sale", pool, 1, 1) for _ in range(2)]
    for item in proposals:
        approve(runtime, tenant.supplier, UUID(item["id"]), item["payload_hash"])

    def attempt(item):
        try:
            return apply(runtime, tenant.supplier, UUID(item["id"]))["status"]
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, proposals)) == ["applied", "conflict"]
    ledger = movements(runtime, tenant.supplier, pool)["items"]
    assert sum(r["delta_total"] for r in ledger) == sum(r["delta_sold"] for r in ledger) == 1
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT total-sold-held-blocked FROM inventory_pool WHERE id=:id"),
                {"id": pool},
            )
            == 0
        )


def test_reversal_and_invalid_adjustment_are_atomic(database, tenant, stock):
    _, runtime = database
    opened = commit(
        runtime, tenant.supplier, propose(runtime, tenant.supplier, "open", stock, 0, 2)
    )
    pool = UUID(opened["pool_id"])
    sold = commit(runtime, tenant.supplier, propose(runtime, tenant.supplier, "sale", pool, 1, 2))
    bad = propose(runtime, tenant.supplier, "adjust", pool, 2, -1)
    with pytest.raises(Conflict):
        commit(runtime, tenant.supplier, bad)
    reversal = propose(
        runtime, tenant.supplier, "reverse", pool, 2, reverses_id=UUID(sold["movement_id"])
    )
    assert commit(runtime, tenant.supplier, reversal)["available"] == 2
    repeat = propose(
        runtime, tenant.supplier, "reverse", pool, 3, reverses_id=UUID(sold["movement_id"])
    )
    with pytest.raises(Conflict, match="已冲正"):
        commit(runtime, tenant.supplier, repeat)
    assert len(movements(runtime, tenant.supplier, pool)["items"]) == 3


def test_expired_approval_and_tampered_proposal_fail(database, tenant, stock):
    admin, runtime = database
    proposal = propose(runtime, tenant.supplier, "open", stock, 0, 2)
    identity = UUID(proposal["id"])
    approve(runtime, tenant.supplier, identity, proposal["payload_hash"])
    with transaction(runtime, tenant.supplier) as conn, pytest.raises(DBAPIError):
        conn.execute(
            text("UPDATE change_request SET payload_hash='forged' WHERE id=:id"), {"id": identity}
        )
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE change_approval SET approved_at=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE change_id=:id"
            ),
            {"id": identity},
        )
    with pytest.raises(Forbidden):
        apply(runtime, tenant.supplier, identity)
    with pytest.raises(Forbidden):
        approve(runtime, tenant.buyer, identity, proposal["payload_hash"])


async def test_external_inventory_cannot_become_local_by_accident(database, tenant):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    departure = list_departures(runtime, tenant.supplier)[0]["id"]
    with pytest.raises(Conflict, match="外部系统"):
        propose(runtime, tenant.supplier, "open", departure, 0, 5)


async def test_full_ledger_pagination_and_cross_page_reversal(
    database, authentication, tenant, stock
):
    admin, runtime = database
    opened = commit(
        runtime, tenant.supplier, propose(runtime, tenant.supplier, "open", stock, 0, 10)
    )
    pool = UUID(opened["pool_id"])
    sold = commit(runtime, tenant.supplier, propose(runtime, tenant.supplier, "sale", pool, 1, 3))
    # Fictional immutable history with identical timestamps exercises the UUID tie-breaker.
    # Each adjustment has its own change and the aggregate balance/version is consistent.
    with admin.begin() as conn:
        for index in range(505):
            change = uuid4()
            conn.execute(
                text("""INSERT INTO change_request(id,organization_id,kind,payload,payload_hash,
                    target_id,expected_version,created_by,status)
                    VALUES(:id,:org,'inventory','{}','ACME-history',:pool,:version,:user,'applied')"""),
                {
                    "id": change,
                    "org": tenant.supplier.organization_id,
                    "pool": pool,
                    "version": index + 2,
                    "user": tenant.supplier.user_id,
                },
            )
            conn.execute(
                text("""INSERT INTO inventory_movement(id,supplier_org_id,pool_id,change_id,
                    kind,business_key,delta_total,reason,actor_id)
                    VALUES(:id,:org,:pool,:change,'adjust',:key,1,'ACME historical adjustment',:user)"""),
                {
                    "id": uuid4(),
                    "org": tenant.supplier.organization_id,
                    "pool": pool,
                    "change": change,
                    "key": f"ACME-HISTORY-{index}",
                    "user": tenant.supplier.user_id,
                },
            )
        conn.execute(
            text("UPDATE inventory_pool SET total=total+505,version=version+505 WHERE id=:id"),
            {"id": pool},
        )
    reversed_sale = commit(
        runtime,
        tenant.supplier,
        propose(
            runtime, tenant.supplier, "reverse", pool, 507, reverses_id=UUID(sold["movement_id"])
        ),
    )
    assert reversed_sale["available"] == 515
    first = movements(runtime, tenant.supplier, pool, limit=25)
    assert first["items"][0]["reverses_id"] == UUID(sold["movement_id"])
    assert first["items"][0]["reverses_business_key"]
    # A new row during traversal does not shift or duplicate the older pages.
    commit(runtime, tenant.supplier, propose(runtime, tenant.supplier, "adjust", pool, 508, 1))
    rows = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = movements(runtime, tenant.supplier, pool, before=UUID(cursor), limit=25)
        rows.extend(page["items"])
        cursor = page["next_cursor"]
    assert len(rows) == len({row["id"] for row in rows}) == 508
    assert [(r["occurred_at"], r["id"]) for r in rows] == sorted(
        [(r["occurred_at"], r["id"]) for r in rows], reverse=True
    )
    original = next(row for row in rows if row["id"] == UUID(sold["movement_id"]))
    assert original["reversed_by_id"] == UUID(reversed_sale["movement_id"])
    with pytest.raises(Forbidden):
        movements(runtime, tenant.supplier, pool, before=uuid4())
    with pytest.raises(Forbidden):
        movements(runtime, tenant.supplier, uuid4(), before=original["id"])
    source = batch()
    source.departures.append({**source.departures[0], "periodId": 3, "periodCode": "ACME-D3"})
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source))
    other_departure = next(
        row["id"] for row in list_departures(runtime, tenant.supplier) if row["id"] != stock
    )
    other_pool = UUID(
        commit(
            runtime,
            tenant.supplier,
            propose(runtime, tenant.supplier, "open", other_departure, 0, 10),
        )["pool_id"]
    )
    with pytest.raises(Forbidden, match="当前库存池"):
        movements(runtime, tenant.supplier, other_pool, before=original["id"])
    with pytest.raises(Forbidden):
        movements(runtime, tenant.buyer, pool, before=original["id"])
    with pytest.raises(Conflict):
        movements(runtime, tenant.supplier, pool, limit=101)

    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: ["auditor"]})
    with TestClient(create_app(runtime, authentication)) as client:
        path = f"/v1/inventory/{pool}/movements"
        assert client.get(path).status_code == 401
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
        }
        page = client.get(path + "?limit=100", headers=headers)
        assert page.status_code == 200 and len(page.json()["items"]) == 100
        assert page.json()["next_cursor"]
        assert client.get(path + "?limit=101", headers=headers).status_code == 422
        assert client.get(path + f"?before={uuid4()}", headers=headers).status_code == 403
