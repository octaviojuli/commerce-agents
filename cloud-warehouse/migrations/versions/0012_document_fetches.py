"""Durable source attachment retrieval, separate from catalog and human publication."""

from alembic import op

revision = "0012_document_fetches"
down_revision = "0011_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE document_fetch (
      id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
      product_id uuid NOT NULL, product_version integer NOT NULL CHECK(product_version>0),
      source_snapshot_id uuid NOT NULL REFERENCES source_snapshot(id), source_hash text NOT NULL,
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','fetching','fetched','failed','obsolete')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), lease_id uuid, lease_until timestamptz,
      next_attempt_at timestamptz NOT NULL DEFAULT now(), last_error text,
      asset_id uuid, etag text, last_modified text,
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
      UNIQUE(product_id,product_version),
      FOREIGN KEY(product_id,supplier_org_id,connection_id) REFERENCES supplier_product(id,supplier_org_id,connection_id),
      FOREIGN KEY(asset_id,supplier_org_id,connection_id,product_id) REFERENCES document_asset(id,supplier_org_id,connection_id,product_id),
      CHECK((status='fetching')=(lease_id IS NOT NULL AND lease_until IS NOT NULL)),
      CHECK((status='fetched')=(asset_id IS NOT NULL))
    );
    CREATE INDEX document_fetch_pending ON document_fetch(supplier_org_id,connection_id,status,next_attempt_at);
    ALTER TABLE document_fetch ENABLE ROW LEVEL SECURITY;
    ALTER TABLE document_fetch FORCE ROW LEVEL SECURITY;
    CREATE POLICY document_fetch_read ON document_fetch FOR SELECT USING(warehouse_has_org(supplier_org_id));
    CREATE POLICY document_fetch_insert ON document_fetch FOR INSERT WITH CHECK(
      warehouse_catalog_write(supplier_org_id) AND EXISTS(
        SELECT 1 FROM source_snapshot s JOIN supplier_product p ON p.id=product_id
        WHERE s.id=source_snapshot_id AND s.connection_id=document_fetch.connection_id
          AND s.entity_type='route' AND s.external_id=p.external_id AND s.body_hash=document_fetch.source_hash
          AND p.version=document_fetch.product_version));
    CREATE POLICY document_fetch_update ON document_fetch FOR UPDATE
      USING(warehouse_document_write(supplier_org_id)) WITH CHECK(warehouse_document_write(supplier_org_id));
    DROP POLICY document_asset_insert ON document_asset;
    CREATE POLICY document_asset_insert ON document_asset FOR INSERT WITH CHECK(warehouse_document_write(supplier_org_id));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting attachment provenance")
