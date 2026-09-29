"""Explicit supplier provisioning and shared-feed publication without business writes."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import text

from . import catalog, search
from .integrations import CatalogBatch, SourceError, canonical
from .persistence import Principal


def identities(feed_id: UUID, supplier: int):
    return {key: uuid5(feed_id, f"{key}:{supplier}") for key in ("org", "user", "connection")}


def provision(admin, feed_id: UUID, query_company: int, suppliers: dict):
    """Offline operator only. Creates no passwords, buyer grants or booking capabilities."""
    with admin.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"goods-stock-provision:{feed_id}"},
        )
        for supplier, names in suppliers.items():
            ids = identities(feed_id, supplier)
            capabilities = {
                "catalog_read": True,
                "inventory_owner": "source",
                "order_write": False,
                "test_catalog": True,
                "goods_stock": {
                    "feed_id": str(feed_id),
                    "query_company_id": query_company,
                    "supplier_company_id": supplier,
                },
                "settlement_pricing": {"mode": "uniform", "version": 1},
            }
            existing = (
                conn.execute(
                    text(
                        "SELECT supplier_org_id,connector_type,capabilities FROM supplier_connection WHERE id=:connection"
                    ),
                    ids,
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                if (
                    existing["supplier_org_id"] != ids["org"]
                    or existing["connector_type"] != "goods_stock"
                    or existing["capabilities"].get("goods_stock") != capabilities["goods_stock"]
                ):
                    raise SourceError("SOURCE_BINDING_CONFLICT", retryable=False)
                continue
            short = names.get("short_name")
            short = short.strip() if isinstance(short, str) else None
            if short and len(short) > 12:
                short = None  # Keep the full source name; never invent an abbreviation.
            conn.execute(
                text(
                    "INSERT INTO organization(id,name,short_name,kinds) VALUES(:org,:name,:short,ARRAY['supplier'])"
                ),
                ids | {"name": names["name"], "short": short},
            )
            conn.execute(
                text("INSERT INTO warehouse_user(id,email) VALUES(:user,:email)"),
                ids | {"email": f"goods-stock-{ids['user']}@worker.invalid"},
            )
            conn.execute(
                text(
                    "INSERT INTO membership(user_id,organization_id,roles) VALUES(:user,:org,ARRAY['sync_worker'])"
                ),
                ids,
            )
            conn.execute(
                text(
                    "INSERT INTO supplier_connection(id,supplier_org_id,name,connector_type,credential_ref,capabilities) VALUES(:connection,:org,:name,'goods_stock',:ref,CAST(:caps AS jsonb))"
                ),
                ids
                | {
                    "name": "团期接口测试",
                    "ref": f"goods-stock:{feed_id}",
                    "caps": canonical(capabilities),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details) VALUES(:audit,:org,:user,'goods_stock.provision',:connection,CAST(:details AS jsonb))"
                ),
                ids
                | {
                    "audit": uuid5(feed_id, f"audit:{supplier}"),
                    "details": canonical(capabilities["goods_stock"]),
                },
            )


@dataclass
class Prepared:
    batch: CatalogBatch

    async def read_catalog(self, start=None, end=None):
        if start != self.batch.window_start or end != self.batch.window_end:
            raise SourceError("SOURCE_WINDOW_MISMATCH")
        return self.batch


async def publish_feed(runtime, feed_id, approved_suppliers, batches, observed, start, end):
    """Only pre-provisioned identities. Missing suppliers get an empty bounded scan."""
    if set(batches) - set(approved_suppliers):
        raise SourceError("SUPPLIER_PROVISIONING_REQUIRED", retryable=False)
    for batch in batches.values():
        batch.validate()
    result = {"suppliers": 0, "products": 0, "departures": 0}
    for supplier in approved_suppliers:
        ids = identities(feed_id, supplier)
        batch = batches.get(supplier, CatalogBatch([], [], start, end, observed))
        count = await catalog.synchronize(
            runtime,
            Principal(ids["user"], ids["org"]),
            ids["connection"],
            Prepared(batch),
            start=start,
            end=end,
            on_publish=lambda conn, _: search.rebuild(conn),
        )
        result["suppliers"] += 1
        for key in ("products", "departures"):
            result[key] += count[key]
    return result
