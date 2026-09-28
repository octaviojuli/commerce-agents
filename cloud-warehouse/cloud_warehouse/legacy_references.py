"""Explicit, buyer-authorized resolution of connection-scoped legacy catalog IDs."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text

from .advisor import departure_id, product_id
from .persistence import Forbidden, Principal, require_role, transaction


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    reference: str = Field(pattern=r"^(RT|DP)-[1-9][0-9]{0,18}$", max_length=22)


def resolve(engine: Engine, actor: Principal, command: Reference) -> dict:
    prefix, external = command.reference.split("-", 1)
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        if prefix == "RT":
            query = """SELECT p.id,p.id AS product_id,p.version,p.observed_at
              FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id
              WHERE p.connection_id=:connection AND p.external_id=:external
                AND p.status='published' AND c.active AND c.connector_type='tour_b2b'
                AND warehouse_catalog_access(p.supplier_org_id,p.connection_id)"""
        else:
            query = """SELECT d.id,p.id AS product_id,d.version,d.observed_at
              FROM departure d JOIN supplier_product p ON p.id=d.product_id
              JOIN supplier_connection c ON c.id=d.connection_id
              WHERE d.connection_id=:connection AND d.external_id=:external
                AND d.status='published' AND p.status='published'
                AND c.active AND c.connector_type='tour_b2b'
                AND warehouse_catalog_access(p.supplier_org_id,p.connection_id)"""
        row = (
            conn.execute(text(query), {"connection": command.connection_id, "external": external})
            .mappings()
            .one_or_none()
        )
        if row is None:
            # Missing references and inaccessible source connections are indistinguishable.
            raise Forbidden("旧编号在指定来源中不存在、未发布或当前采购组织无权访问")
        return {
            "connection_id": str(command.connection_id),
            "legacy_reference": command.reference,
            "kind": "product" if prefix == "RT" else "departure",
            "warehouse_id": (product_id if prefix == "RT" else departure_id)(row["id"]),
            "product_id": product_id(row["product_id"]),
            "current_version": row["version"],
            "observed_at": row["observed_at"],
            "resolution": "current_catalog_identity",
            "requires_revalidation": True,
            "reservation_created": False,
        }
