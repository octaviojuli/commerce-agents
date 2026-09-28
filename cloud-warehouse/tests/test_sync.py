import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse.catalog import SyncBusy, list_departures, list_products, synchronize
from cloud_warehouse.integrations import B2BConnector, CatalogBatch, SourceError
from cloud_warehouse.persistence import Forbidden, Principal, require_runtime_role, transaction


def batch(seats=3):
    return CatalogBatch(
        [{"routeId": 1, "routeCode": "ACME-R1", "routeName": "ACME 环线", "days": 3}],
        [
            {
                "periodId": 2,
                "routeId": 1,
                "periodCode": "ACME-D2",
                "departDate": "2026-10-01",
                "returnDate": "2026-10-03",
                "availableSeats": seats,
            }
        ],
    )


class Connector:
    def __init__(self, data=None):
        self.data = data or batch()

    async def read_catalog(self, start=None, end=None):
        return self.data


async def test_pagination_exceeds_old_twenty_page_cap():
    called = []

    async def fetch(path, params):
        page = params["pageNum"]
        called.append(page)
        rows = [{"routeId": n} for n in range((page - 1) * 50 + 1, min(page * 50, 1053) + 1)]
        return {"list": rows, "total": 1053, "totalPages": 22, "pageNum": page}

    rows = await B2BConnector(fetch)._pages("/route/list", {}, "routeId")
    assert len(rows) == 1053
    assert 22 in called and called[-1] == 1


async def test_http_catalog_beyond_twenty_pages_publishes_atomically(database, tenant):
    from tour.api.http_erp import HttpErpClient
    from tour.api.warehouse_connector import TourWarehouseConnector

    _, runtime = database
    total, mode, seats = 1053, "complete", 3
    calls = []

    def handle(request):
        assert request.method == "GET"
        path, params = request.url.path, request.url.params
        assert path in {"/aicli/route/list", "/aicli/period/list"}
        assert params["pageSize"] == "50"
        page = int(params["pageNum"])
        calls.append((path, page))
        identifiers = range((page - 1) * 50 + 1, min(page * 50, total) + 1)
        if path.endswith("/route/list"):
            assert "departDateStart" not in params and "departDateEnd" not in params
            rows = [
                {"routeId": n, "routeCode": f"ACME-R{n}", "routeName": f"ACME route {n}"}
                for n in identifiers
            ]
        else:
            assert params["departDateStart"] == params["departDateEnd"] == "2026-10-01"
            rows = [
                {
                    "periodId": n,
                    "routeId": n,
                    "periodCode": f"ACME-D{n}",
                    "departDate": "2026-10-01",
                    "returnDate": "2026-10-03",
                    "availableSeats": seats,
                }
                for n in identifiers
            ]
            if mode == "truncated" and page == 22:
                rows = rows[:-1]
            if mode == "duplicate" and page == 22:
                rows[-1] = {**rows[-1], "periodId": 1}
        return httpx.Response(
            200,
            json={
                "code": 200,
                "message": "ok",
                "data": {"list": rows, "total": total, "totalPages": 22, "pageNum": page},
            },
        )

    upstream = HttpErpClient.with_token(
        "https://b2b.acme.example/aicli",
        "ACME-test-token",
        datetime.now(UTC).timestamp() + 3600,
        {"companyId": 2},
        [{"companyId": 2}],
        transport=httpx.MockTransport(handle),
    )
    connector = TourWarehouseConnector(upstream)

    async def sync():
        calls.clear()
        return await synchronize(
            runtime,
            tenant.worker,
            tenant.connection_id,
            connector,
            start=date(2026, 10, 1),
            end=date(2026, 10, 1),
        )

    def published():
        with transaction(runtime, tenant.buyer) as conn:
            return conn.execute(
                text("SELECT id,version,available_seats,sync_run_id FROM departure ORDER BY id")
            ).all()

    try:
        result = await sync()
        assert result["departures"] == result["source_departures"] == total
        assert result["quarantined"] == 0
        for path in ("/aicli/route/list", "/aicli/period/list"):
            assert [page for route, page in calls if route == path] == [*range(1, 23), 1]
        before = published()
        assert len(before) == total and {row.available_seats for row in before} == {3}
        with transaction(runtime, tenant.buyer) as conn:
            assert conn.scalar(text("SELECT count(*) FROM supplier_product")) == total
        with transaction(runtime, tenant.worker) as conn:
            assert conn.scalar(text("SELECT count(*) FROM source_snapshot")) == total * 2

        seats = 1
        for fault, code in (
            ("truncated", "INCOMPLETE_SOURCE_SCAN"),
            ("duplicate", "DUPLICATE_SOURCE_ID"),
        ):
            mode = fault
            with pytest.raises(SourceError, match=code):
                await sync()
            assert ("/aicli/period/list", 22) in calls
            assert published() == before
            with transaction(runtime, tenant.worker) as conn:
                assert conn.scalar(text("SELECT count(*) FROM source_snapshot")) == total * 2

        mode = "complete"
        await sync()
        after = published()
        assert [row.id for row in after] == [row.id for row in before]
        assert {row.available_seats for row in after} == {1}
        assert all(new.version == old.version + 1 for old, new in zip(before, after, strict=True))
        with transaction(runtime, tenant.worker) as conn:
            runs = conn.execute(
                text("SELECT status,error_code FROM sync_run ORDER BY started_at")
            ).all()
        assert runs == [
            ("published", None),
            ("failed", "INCOMPLETE_SOURCE_SCAN"),
            ("failed", "DUPLICATE_SOURCE_ID"),
            ("published", None),
        ]
    finally:
        await upstream.aclose()


@pytest.mark.parametrize(
    "fault", ["truncated", "duplicate", "changed", "anchor", "first_page", "anchor_page"]
)
async def test_bad_pagination_is_rejected(fault):
    calls = 0

    async def fetch(path, params):
        nonlocal calls
        calls += 1
        page = params["pageNum"]
        rows = [{"routeId": page}]
        total = 2
        if fault == "truncated" and page == 2:
            rows = []
        if fault == "duplicate":
            rows = [{"routeId": 1}]
        if fault == "changed" and page == 2:
            total = 3
        if fault == "anchor" and calls == 3:
            rows = [{"routeId": 3}]
        response = {"list": rows, "total": total, "totalPages": 2}
        if (fault == "first_page" and calls == 1) or (fault == "anchor_page" and calls == 3):
            response["pageNum"] = 2
        return response

    with pytest.raises(SourceError):
        await B2BConnector(fetch)._pages("/route/list", {}, "routeId")


async def test_repeat_sync_and_org_rls(database, tenant):
    admin, runtime = database
    require_runtime_role(runtime)
    with pytest.raises(RuntimeError):
        require_runtime_role(admin)
    first = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    products = list_products(runtime, tenant.buyer)
    second = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    assert first["source_hash"] == second["source_hash"]
    assert [r["id"] for r in products] == [r["id"] for r in list_products(runtime, tenant.buyer)]
    assert products[0]["version"] == 1
    assert len(list_departures(runtime, tenant.buyer)) == 1
    with runtime.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM supplier_product")) == 0
    with runtime.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT password_hash FROM warehouse_user"))
    with transaction(runtime, tenant.buyer) as conn, pytest.raises(DBAPIError):
        conn.execute(text("SELECT credential_ref FROM supplier_connection"))
    with pytest.raises(Forbidden):
        list_products(runtime, Principal(tenant.buyer.user_id, tenant.supplier.organization_id))
    with pytest.raises(Forbidden):
        await synchronize(runtime, tenant.buyer, tenant.connection_id, Connector())
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE id=:id"), {"id": tenant.grant_id}
        )
    assert list_products(runtime, tenant.buyer) == []
    assert len(list_products(runtime, tenant.worker)) == 1


@pytest.mark.parametrize("revocation", ["supplier", "connection", "membership", "expiry"])
async def test_revocations_take_effect(database, tenant, revocation):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    assert list_products(runtime, tenant.buyer)
    with admin.begin() as conn:
        if revocation == "supplier":
            conn.execute(
                text("UPDATE organization SET active=false WHERE id=:id"),
                {"id": tenant.supplier.organization_id},
            )
        elif revocation == "connection":
            conn.execute(
                text("UPDATE supplier_connection SET active=false WHERE id=:id"),
                {"id": tenant.connection_id},
            )
        elif revocation == "membership":
            conn.execute(
                text("UPDATE membership SET active=false WHERE user_id=:id"),
                {"id": tenant.buyer.user_id},
            )
        else:
            conn.execute(
                text(
                    "UPDATE distribution_grant SET valid_from=now()-interval '2 days',expires_at=now()-interval '1 day' WHERE id=:id"
                ),
                {"id": tenant.grant_id},
            )
    if revocation == "membership":
        with pytest.raises(Forbidden):
            list_products(runtime, tenant.buyer)
    else:
        assert list_products(runtime, tenant.buyer) == []


async def test_failed_sync_keeps_published_data(database, tenant):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    malformed = batch()
    malformed.departures[0]["routeId"] = 999
    with pytest.raises(SourceError, match="SOURCE_REFERENCE_MISSING"):
        await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(malformed))
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 3
    with transaction(runtime, tenant.worker) as conn:
        rows = conn.execute(text("SELECT status FROM sync_run ORDER BY started_at")).scalars().all()
    assert rows == ["published", "failed"]


@pytest.mark.parametrize(
    "revocation", ["grant", "supplier", "connection", "membership", "buyer", "user"]
)
async def test_quarantine_and_live_revocation_in_existing_transaction(database, tenant, revocation):
    admin, runtime = database
    original = batch()
    original.departures.append({**original.departures[0], "periodId": 9})
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(original))
    original.departures[1]["routeId"] = 0
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(original))
    with transaction(runtime, tenant.supplier) as conn:
        assert conn.scalar(text("SELECT count(*) FROM departure")) == 2
        assert conn.scalar(text("SELECT count(*) FROM departure WHERE source_quarantined")) == 1
    table, column, identifier = {
        "grant": ("distribution_grant", "id", tenant.grant_id),
        "supplier": ("organization", "id", tenant.supplier.organization_id),
        "connection": ("supplier_connection", "id", tenant.connection_id),
        "membership": ("membership", "user_id", tenant.buyer.user_id),
        "buyer": ("organization", "id", tenant.buyer.organization_id),
        "user": ("warehouse_user", "id", tenant.buyer.user_id),
    }[revocation]
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM departure")) == 1
        assert conn.scalar(text("SELECT count(*) FROM offer")) == 1
        for active, expected in ((False, 0), (True, 1)):
            with admin.begin() as control:
                control.execute(
                    text(f"UPDATE {table} SET active=:active WHERE {column}=:id"),
                    {"active": active, "id": identifier},
                )
            assert conn.scalar(text("SELECT count(*) FROM departure")) == expected
            assert conn.scalar(text("SELECT count(*) FROM offer")) == expected


async def test_unlinked_source_plan_is_preserved_for_review(database, tenant):
    _, runtime = database
    data = batch()
    data.departures.append({**data.departures[0], "periodId": 9, "routeId": 0})
    result = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    assert result["source_departures"] == 2
    assert result["departures"] == result["quarantined"] == 1
    assert len(list_departures(runtime, tenant.buyer)) == 1
    with transaction(runtime, tenant.worker) as conn:
        assert (
            conn.scalar(text("SELECT count(*) FROM source_snapshot WHERE entity_type='departure'"))
            == 2
        )
        assert conn.scalar(text("SELECT code FROM source_issue")) == "UNLINKED_ROUTE"
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM source_issue")) == 0


@pytest.mark.parametrize("moved_outside_old_window", [False, True])
async def test_previously_published_unlinked_departure_is_hidden_and_recovers(
    database, authentication, tenant, moved_outside_old_window
):
    from cloud_warehouse import pricing, quote_shares, quotes

    _, runtime = database
    original = batch()
    day = (datetime.now(UTC) + timedelta(days=30)).date()
    original.departures[0].update(departDate=str(day), returnDate=str(day + timedelta(days=2)))
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(original))
    departure = list_departures(runtime, tenant.buyer)[0]
    offer = pricing.list_offers(runtime, tenant.buyer, departure["id"])[0]
    request = quotes.QuoteRequest(offer_id=offer["id"], departure_date=day, party=quotes.Party())
    old_quote = await quotes.create(runtime, tenant.buyer, request, "before-unlinked")
    share = quote_shares.create(
        runtime, tenant.buyer, UUID(old_quote["quote_id"]), quote_shares.Create(request_id=uuid4())
    )
    assert quote_shares.read(runtime, authentication, share["token"]) is not None
    changed_day = day + timedelta(days=10) if moved_outside_old_window else day
    invalid = CatalogBatch(
        original.routes,
        [
            {
                **original.departures[0],
                "routeId": 0,
                "departDate": str(changed_day),
                "returnDate": str(changed_day + timedelta(days=2)),
            }
        ],
        window_start=changed_day,
        window_end=changed_day,
    )
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(invalid))
    assert list_departures(runtime, tenant.buyer) == []
    assert pricing.list_offers(runtime, tenant.buyer, departure["id"]) == []
    with transaction(runtime, tenant.buyer) as conn:
        assert conn.scalar(text("SELECT count(*) FROM departure")) == 0
    with pytest.raises(Forbidden):
        await quotes.create(runtime, tenant.buyer, request, "after-unlinked")
    with pytest.raises(Forbidden):
        quotes.get(runtime, tenant.buyer, UUID(old_quote["quote_id"]))
    assert quote_shares.read(runtime, authentication, share["token"]) is None
    with transaction(runtime, tenant.worker) as conn:
        row = conn.execute(text("SELECT * FROM departure")).mappings().one()
        assert row["availability_expires_at"] <= datetime.now(UTC)
        assert row["source"]["routeId"] == 0
        assert row["source_quarantined"]
        first_invalid_version = row["version"]
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(invalid))
    with transaction(runtime, tenant.worker) as conn:
        assert conn.scalar(text("SELECT version FROM departure")) == first_invalid_version
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(original))
    recovered = list_departures(runtime, tenant.buyer)[0]
    assert recovered["id"] == departure["id"] and recovered["available_seats"] == 3
    assert pricing.list_offers(runtime, tenant.buyer, departure["id"])[0]["id"] == offer["id"]
    assert quotes.get(runtime, tenant.buyer, UUID(old_quote["quote_id"]))["snapshot_stale"]
    public = quote_shares.read(runtime, authentication, share["token"])
    assert public["snapshot_stale"] and "available_seats" not in public


async def test_quarantine_is_atomic_and_recovery_keeps_local_sales_pause(database, tenant):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with transaction(runtime, tenant.supplier) as conn:
        conn.execute(text("UPDATE departure SET sales_paused=true,status='paused'"))
    invalid = batch()
    invalid.departures[0]["routeId"] = 0

    def fail_publication(conn, result):
        raise RuntimeError("ACME simulated transaction failure")

    with pytest.raises(SourceError, match="SYNC_FAILED"):
        await synchronize(
            runtime,
            tenant.worker,
            tenant.connection_id,
            Connector(invalid),
            on_publish=fail_publication,
        )
    with transaction(runtime, tenant.worker) as conn:
        assert not conn.scalar(text("SELECT source_quarantined FROM departure"))
        assert conn.scalar(text("SELECT count(*) FROM source_issue")) == 0
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(invalid))
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    with transaction(runtime, tenant.worker) as conn:
        row = conn.execute(text("SELECT * FROM departure")).mappings().one()
        assert not row["source_quarantined"]
        assert row["sales_paused"] and row["status"] == "paused"
    assert list_departures(runtime, tenant.buyer) == []


@pytest.mark.parametrize("recovered", [False, True])
async def test_quarantine_migration_repairs_only_latest_successful_source(
    database, tenant, recovered
):
    import runpy
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    admin, runtime = database
    first = await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    invalid = batch()
    invalid.departures[0]["routeId"] = 0
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(invalid))
    if recovered:
        await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    migration = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1] / "migrations/versions/0027_departure_quarantine.py"
        )
    )
    # Recreate the old schema and stale publication inside a transaction, then roll
    # everything back so other tests keep the current migration and privileges.
    with admin.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(
                text("""ALTER POLICY departure_read ON departure USING(
              warehouse_has_org(supplier_org_id) OR (status='published'
              AND warehouse_catalog_access(supplier_org_id,connection_id)))""")
            )
            conn.execute(text("ALTER TABLE departure DROP COLUMN source_quarantined"))
            conn.execute(
                text("""UPDATE departure d SET source=s.body,source_hash=s.body_hash,
              sync_run_id=s.sync_run_id,version=1,observed_at=s.observed_at
              FROM source_snapshot s WHERE s.sync_run_id=:run AND s.entity_type='departure'
                AND d.connection_id=s.connection_id AND d.external_id=s.external_id"""),
                {"run": UUID(first["sync_run_id"])},
            )
            with Operations.context(MigrationContext.configure(conn)):
                migration["upgrade"]()
            row = (
                conn.execute(
                    text("SELECT * FROM departure WHERE connection_id=:id"),
                    {"id": tenant.connection_id},
                )
                .mappings()
                .one()
            )
            assert row["source_quarantined"] is (not recovered)
            assert row["source"]["routeId"] == (1 if recovered else 0)
            assert row["version"] == (1 if recovered else 2)
        finally:
            tx.rollback()


async def test_unknown_and_missing_departures_never_report_zero(database, tenant):
    _, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(batch(None)))
    dep = list_departures(runtime, tenant.buyer)[0]
    assert dep["available_seats"] is None and dep["availability_status"] == "unknown"
    await synchronize(
        runtime, tenant.worker, tenant.connection_id, Connector(CatalogBatch(batch().routes, []))
    )
    dep = list_departures(runtime, tenant.buyer)[0]
    assert dep["available_seats"] is None and dep["availability_status"] == "stale"


async def test_concurrent_scan_has_single_owner(database, tenant):
    _, runtime = database
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow:
        async def read_catalog(self, start=None, end=None):
            entered.set()
            await release.wait()
            return batch()

    task = asyncio.create_task(synchronize(runtime, tenant.worker, tenant.connection_id, Slow()))
    await entered.wait()
    try:
        with pytest.raises(SyncBusy):
            await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    finally:
        release.set()
        await task


def test_window_and_source_references():
    with pytest.raises(SourceError, match="SOURCE_WINDOW_MISMATCH"):
        CatalogBatch(batch().routes, batch().departures, date(2027, 1, 1)).validate()


async def test_slow_scan_does_not_make_old_inventory_fresh(database, tenant):
    _, runtime = database
    data = CatalogBatch(
        batch().routes, batch().departures, observed_at=datetime.now(UTC) - timedelta(minutes=6)
    )
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    result = list_departures(runtime, tenant.buyer)[0]
    assert result["available_seats"] is None and result["availability_status"] == "stale"


async def test_same_external_ids_do_not_cross_suppliers(database, tenant):
    from cloud_warehouse.admin import onboard

    admin, runtime = database
    ids = onboard(admin, "ACME Another", "ACME Another Buyer", f"{uuid4()}@acme.example")
    from uuid import UUID

    other = Principal(UUID(ids["worker_id"]), UUID(ids["supplier_id"]))
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    await synchronize(runtime, other, UUID(ids["connection_id"]), Connector())
    first, second = list_products(runtime, tenant.worker), list_products(runtime, other)
    assert len(first) == len(second) == 1
    assert first[0]["id"] != second[0]["id"]
