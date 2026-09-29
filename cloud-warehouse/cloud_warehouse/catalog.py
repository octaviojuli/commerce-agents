"""Atomic publication and authorized catalog queries. Upstream snapshots are never orders."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text

from . import document_fetches, sales
from .integrations import CatalogBatch, SourceError, SupplierConnector, canonical, fingerprint
from .persistence import Forbidden, Principal, require_role, transaction

INVENTORY_FRESHNESS_SECONDS = 300


class SyncBusy(RuntimeError):
    pass


def _scope(connection, connection_id: UUID) -> dict:
    require_role(connection, "supplier_admin", "sync_worker")
    row = (
        connection.execute(
            text(
                "SELECT id,supplier_org_id,name,connector_type,capabilities,active FROM supplier_connection WHERE id=:id AND active AND supplier_org_id=warehouse_org_id()"
            ),
            {"id": connection_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("供应商连接不存在或无权限")
    return dict(row)


async def synchronize(
    engine: Engine,
    actor: Principal,
    connection_id: UUID,
    connector: SupplierConnector,
    *,
    start: date | None = None,
    end: date | None = None,
    on_publish: Callable[[Connection, dict], None] | None = None,
) -> dict:
    """Use a session advisory lock, without holding a database transaction across network I/O.

    A process crash releases the lock. The next owner marks a leftover running record as
    interrupted; it never treats a stale status row alone as proof of a live worker.
    """
    if start and end and end < start:
        raise ValueError("Invalid synchronization date window")
    with transaction(engine, actor) as connection:
        scope = _scope(connection, connection_id)
        cooldown = connection.scalar(
            text(
                "SELECT ceil(extract(epoch FROM (provider_not_before-clock_timestamp()))) FROM sync_job WHERE connection_id=:id AND provider_not_before>clock_timestamp()"
            ),
            {"id": connection_id},
        )
        if cooldown:
            raise SourceError("SOURCE_COOLDOWN_ACTIVE", retry_after_seconds=int(cooldown))
    lock_id = int.from_bytes(connection_id.bytes[:8], "big", signed=True)
    with engine.connect() as lock_connection:
        if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}):
            raise SyncBusy("同一供应商连接正在同步")
        lock_connection.commit()
        run_id = uuid4()
        try:
            with transaction(engine, actor) as connection:
                _scope(connection, connection_id)
                connection.execute(
                    text(
                        "UPDATE sync_run SET status='failed',error_code='INTERRUPTED',completed_at=now() WHERE connection_id=:id AND status='running'"
                    ),
                    {"id": connection_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO sync_run(id,supplier_org_id,connection_id,status,actor_id,window_start,window_end) VALUES(:id,:org,:conn,'running',:actor,:start,:end)"
                    ),
                    {
                        "id": run_id,
                        "org": actor.organization_id,
                        "conn": connection_id,
                        "actor": actor.user_id,
                        "start": start,
                        "end": end,
                    },
                )
            try:
                batch = await connector.read_catalog(start, end)
                batch.validate()
                result = publish(engine, actor, scope, run_id, batch, on_publish=on_publish)
            except (Exception, asyncio.CancelledError) as error:
                code = (
                    error.code
                    if isinstance(error, SourceError)
                    else "INVALID_SOURCE_DATA"
                    if isinstance(error, ValidationError)
                    else "INTERRUPTED"
                    if isinstance(error, asyncio.CancelledError)
                    else "SYNC_FAILED"
                )
                with transaction(engine, actor) as connection:
                    connection.execute(
                        text(
                            "UPDATE sync_run SET status='failed',error_code=:code,completed_at=now() WHERE id=:id"
                        ),
                        {"id": run_id, "code": code},
                    )
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, SourceError):
                    raise
                raise SourceError(code) from error
            return result
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
            lock_connection.commit()


def publish(
    engine: Engine,
    actor: Principal,
    scope: dict,
    run_id: UUID,
    batch: CatalogBatch,
    *,
    on_publish: Callable[[Connection, dict], None] | None = None,
) -> dict:
    batch.validate()
    # Freshness begins before the departure scan, not after a potentially long import.
    observed = batch.observed_at or datetime.now(UTC)
    quarantined = sum(row["routeId"] == 0 for row in batch.departures)
    conn_id = scope["id"]
    shared = {"org": actor.organization_id, "conn": conn_id, "run": run_id, "observed": observed}
    with transaction(engine, actor) as connection:
        _scope(connection, conn_id)
        for kind, rows in (("route", batch.routes), ("departure", batch.departures)):
            for row in rows:
                ext = str(row["routeId" if kind == "route" else "periodId"])
                data = {
                    **shared,
                    "id": uuid5(conn_id, kind + ":" + ext),
                    "external": ext,
                    "body": canonical(row),
                    "hash": fingerprint(row),
                }
                connection.execute(
                    text(
                        "INSERT INTO source_snapshot(id,supplier_org_id,connection_id,sync_run_id,entity_type,external_id,body,body_hash,observed_at) VALUES(:snapshot,:org,:conn,:run,:kind,:external,CAST(:body AS jsonb),:hash,:observed)"
                    ),
                    {**data, "snapshot": uuid4(), "kind": kind},
                )
                if kind == "departure" and row["routeId"] == 0:
                    # Explicit invalidation applies even if the source moved the date
                    # outside this scan's old local window. Keep the last valid parent
                    # only as supplier history; RLS excludes the row from buyer reads.
                    connection.execute(
                        text("""UPDATE departure SET source_quarantined=true,
                        source=CAST(:body AS jsonb),source_hash=:hash,observed_at=:observed,sync_run_id=:run,
                        availability_expires_at=LEAST(availability_expires_at,:observed,now()),
                        version=version+CASE WHEN source_hash<>:hash OR NOT source_quarantined THEN 1 ELSE 0 END
                        WHERE connection_id=:conn AND external_id=:external"""),
                        data,
                    )
                    connection.execute(
                        text(
                            "INSERT INTO source_issue(id,supplier_org_id,connection_id,sync_run_id,external_id,code) VALUES(:id,:org,:conn,:run,:external,'UNLINKED_ROUTE')"
                        ),
                        {**data, "id": uuid4()},
                    )
                    continue
                if kind == "route":
                    connection.execute(
                        text("""INSERT INTO supplier_product(id,supplier_org_id,connection_id,external_id,code,name,days,gateway,source,source_hash,status,observed_at,sync_run_id)
                        VALUES(:id,:org,:conn,:external,:code,:name,:days,:gateway,CAST(:body AS jsonb),:hash,'published',:observed,:run)
                        ON CONFLICT(connection_id,external_id) DO UPDATE SET code=EXCLUDED.code,name=EXCLUDED.name,days=EXCLUDED.days,gateway=EXCLUDED.gateway,
                        source=EXCLUDED.source,source_hash=EXCLUDED.source_hash,observed_at=EXCLUDED.observed_at,sync_run_id=EXCLUDED.sync_run_id,
                        version=supplier_product.version + CASE WHEN supplier_product.source_hash<>EXCLUDED.source_hash THEN 1 ELSE 0 END"""),
                        {
                            **data,
                            "code": row["routeCode"],
                            "name": row["routeName"],
                            "days": row.get("days"),
                            "gateway": row.get("departCityName"),
                        },
                    )
                else:
                    connection.execute(
                        text("""INSERT INTO departure(id,product_id,supplier_org_id,connection_id,external_id,code,depart_date,return_date,company_id,available_seats,source,source_hash,observed_at,availability_expires_at,sync_run_id)
                        VALUES(:id,:parent,:org,:conn,:external,:code,:start,:end,:company,:seats,CAST(:body AS jsonb),:hash,:observed,:expires,:run)
                        ON CONFLICT(connection_id,external_id) DO UPDATE SET product_id=EXCLUDED.product_id,code=EXCLUDED.code,depart_date=EXCLUDED.depart_date,return_date=EXCLUDED.return_date,
                        company_id=EXCLUDED.company_id,available_seats=EXCLUDED.available_seats,source=EXCLUDED.source,source_hash=EXCLUDED.source_hash,
                        observed_at=EXCLUDED.observed_at,availability_expires_at=EXCLUDED.availability_expires_at,sync_run_id=EXCLUDED.sync_run_id,
                        source_quarantined=false,
                        version=departure.version+CASE WHEN departure.source_hash<>EXCLUDED.source_hash OR departure.source_quarantined THEN 1 ELSE 0 END"""),
                        {
                            **data,
                            "parent": uuid5(conn_id, "route:" + str(row["routeId"])),
                            "code": row["periodCode"],
                            "start": row["departDate"],
                            "end": row["returnDate"],
                            "company": str(row["companyId"]) if row.get("companyId") else None,
                            "seats": row.get("availableSeats"),
                            "expires": observed + timedelta(seconds=INVENTORY_FRESHNESS_SECONDS),
                        },
                    )
        enforce_selection(connection, conn_id)
        document_fetches.enqueue(connection, conn_id)
        # Absence does not imply deletion. Retain previous records and expire their availability.
        connection.execute(
            text(
                "UPDATE departure SET availability_expires_at=LEAST(availability_expires_at,:now) WHERE connection_id=:conn AND sync_run_id<>:run AND (CAST(:start AS date) IS NULL OR depart_date>=:start) AND (CAST(:end AS date) IS NULL OR depart_date<=:end)"
            ),
            {**shared, "now": observed, "start": batch.window_start, "end": batch.window_end},
        )
        result = {
            "sync_run_id": str(run_id),
            "products": len(batch.routes),
            "departures": len(batch.departures) - quarantined,
            "source_departures": len(batch.departures),
            "quarantined": quarantined,
            "source_hash": fingerprint({"routes": batch.routes, "departures": batch.departures}),
            "status": "published",
        }
        connection.execute(
            text(
                "UPDATE sync_run SET status='published',completed_at=now(),product_count=:products,departure_count=:departures,source_departure_count=:source_departures,quarantined_count=:quarantined,source_hash=:source_hash WHERE id=:run"
            ),
            {**result, "run": run_id},
        )
        connection.execute(
            text(
                "INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details) VALUES(:id,:org,:actor,'catalog.sync',:run,CAST(:body AS jsonb))"
            ),
            {**shared, "id": uuid4(), "actor": actor.user_id, "body": canonical(result)},
        )
        connection.execute(
            text(
                "INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload) VALUES(:id,:org,'catalog.published',:run,CAST(:body AS jsonb))"
            ),
            {**shared, "id": uuid4(), "body": canonical(result)},
        )
        if on_publish is not None:
            on_publish(connection, result)
    return result


def list_products(
    engine: Engine, actor: Principal, *, limit: int = 50, after: UUID | None = None
) -> list[dict]:
    with transaction(engine, actor) as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id,supplier_org_id,code,effective_name AS name,name_origin,description_origin,effective_days AS days,effective_gateway AS gateway,status,version,observed_at FROM product_listing WHERE (CAST(:after AS uuid) IS NULL OR id>:after) ORDER BY id LIMIT :limit"
                ),
                {"after": after, "limit": max(1, min(limit, 100))},
            ).mappings()
        ]


def list_departures(
    engine: Engine,
    actor: Principal,
    *,
    product_id: UUID | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    predicates = ["p.status='published'", "d.status='published'", "NOT d.source_quarantined"]
    for value, predicate in (
        (product_id, "d.product_id=:product"),
        (start, "d.depart_date>=:start"),
        (end, "d.depart_date<=:end"),
    ):
        if value is not None:
            predicates.append(predicate)
    where = " AND ".join(predicates)
    with transaction(engine, actor) as connection:
        rows = [
            {
                **row,
                **sales.evaluate(
                    row["depart_date"],
                    row["business_timezone"],
                    paused=row["sales_paused"],
                    deadline=row["local_booking_deadline"],
                ),
            }
            for row in connection.execute(
                text(f"""SELECT d.supplier_org_id,d.id,d.product_id,d.code,d.depart_date,d.return_date,d.company_id,d.observed_at,
            d.sales_paused,d.local_booking_deadline,COALESCE(c.capabilities->>'business_timezone','Asia/Shanghai') AS business_timezone,
            CASE WHEN i.id IS NULL THEN d.availability_expires_at ELSE NULL END AS availability_expires_at,
            CASE WHEN i.id IS NOT NULL THEN i.total-i.sold-i.held-i.blocked WHEN d.availability_expires_at>now() THEN d.available_seats ELSE NULL END AS available_seats,
            CASE WHEN i.id IS NOT NULL THEN 'managed' WHEN d.availability_expires_at<=now() THEN 'stale' WHEN d.available_seats IS NULL THEN 'unknown' ELSE 'known' END AS availability_status,
            i.id AS inventory_pool_id,i.version AS inventory_version
            FROM departure d JOIN supplier_product p ON p.id=d.product_id
            JOIN supplier_connection c ON c.id=d.connection_id
            LEFT JOIN inventory_pool i ON i.departure_id=d.id
            WHERE {where}
            ORDER BY d.depart_date,d.id LIMIT :limit OFFSET :offset"""),
                {
                    "product": product_id,
                    "start": start,
                    "end": end,
                    "limit": max(1, min(limit, 100)),
                    "offset": max(0, offset),
                },
            ).mappings()
        ]

        return rows


def departure_projection(row, actor):
    """HTTP catalog reads hide exact inventory outside the owning supplier."""
    result = dict(row)
    if result["supplier_org_id"] != actor.organization_id:
        seats = result.pop("available_seats", None)
        result["availability"] = (
            "unknown" if seats is None else "available" if seats > 0 else "unavailable"
        )
    return result


def enforce_selection(connection, connection_id):
    """Keep an operator-selected catalog subset closed across every later source scan.

    Selection never deletes history or automatically republishes previously paused rows.
    """
    for table, key in (("supplier_product", "route_ids"), ("departure", "departure_ids")):
        connection.execute(
            text(f"""UPDATE {table} p SET status='paused',version=p.version+1
          FROM supplier_connection c WHERE c.id=p.connection_id AND c.id=:source
          AND c.supplier_org_id=warehouse_org_id() AND c.capabilities ? 'catalog_selection'
          AND NOT coalesce(c.capabilities->'catalog_selection'->'{key}' ? p.external_id,false)
          AND p.status='published'"""),
            {"source": connection_id},
        )
