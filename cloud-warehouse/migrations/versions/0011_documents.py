"""Private source documents, fenced parsing and immutable human-reviewed publications."""

from alembic import op

revision = "0011_documents"
down_revision = "0010_conversation_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE document_asset (
      id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
      product_id uuid NOT NULL, product_version integer NOT NULL CHECK(product_version>0),
      product_snapshot jsonb NOT NULL, file_name text NOT NULL, media_type text NOT NULL,
      file_hash text NOT NULL, byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 20000000),
      created_by uuid NOT NULL REFERENCES warehouse_user(id), created_at timestamptz NOT NULL DEFAULT now(),
      status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','parsing','parsed','failed')),
      attempts integer NOT NULL DEFAULT 0, lease_id uuid, lease_until timestamptz,
      last_error text, UNIQUE(product_id,product_version,file_hash),
      UNIQUE(id,supplier_org_id,connection_id,product_id),
      FOREIGN KEY(product_id,supplier_org_id,connection_id) REFERENCES supplier_product(id,supplier_org_id,connection_id),
      CHECK((status='parsing')=(lease_id IS NOT NULL AND lease_until IS NOT NULL))
    );
    CREATE INDEX document_asset_pending ON document_asset(supplier_org_id,status,created_at,id);
    CREATE TABLE document_parse (
      id uuid PRIMARY KEY, asset_id uuid NOT NULL UNIQUE, supplier_org_id uuid NOT NULL,
      connection_id uuid NOT NULL, product_id uuid NOT NULL,
      parser_version text NOT NULL, body jsonb NOT NULL, body_hash text NOT NULL,
      field_sources jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(id,asset_id,supplier_org_id,connection_id,product_id),
      FOREIGN KEY(asset_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_asset(id,supplier_org_id,connection_id,product_id)
    );
    CREATE TABLE document_publication (
      id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
      product_id uuid NOT NULL, product_version integer NOT NULL,
      asset_id uuid NOT NULL, parse_id uuid NOT NULL,
      body jsonb NOT NULL, body_hash text NOT NULL, field_sources jsonb NOT NULL,
      reviewed_by uuid NOT NULL REFERENCES warehouse_user(id), review_note text NOT NULL,
      change_id uuid NOT NULL UNIQUE REFERENCES change_request(id),
      created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(product_id,product_version),
      FOREIGN KEY(parse_id,asset_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_parse(id,asset_id,supplier_org_id,connection_id,product_id)
    );
    ALTER TABLE supplier_product ADD COLUMN published_document_id uuid REFERENCES document_publication(id);
    CREATE FUNCTION warehouse_document_write(org uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
      SELECT warehouse_has_org(org) AND warehouse_has_role(ARRAY['supplier_admin','product_editor','sync_worker'])
    $$;
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid, change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$
      SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('pricing','catalog','merchant','document') AND warehouse_has_role(ARRAY['product_editor'])))
    $$;
    """)
    for table in ("document_asset", "document_parse", "document_publication"):
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY;"
        )
        op.execute(
            f"CREATE POLICY {table}_owner_read ON {table} FOR SELECT USING(warehouse_has_org(supplier_org_id));"
        )
        roles = {
            "document_asset": "ARRAY['supplier_admin','product_editor']",
            "document_parse": "ARRAY['sync_worker']",
            "document_publication": "ARRAY['supplier_admin']",
        }[table]
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role({roles}));"
        )
    op.execute("""
    CREATE POLICY document_asset_update ON document_asset FOR UPDATE USING(warehouse_document_write(supplier_org_id))
      WITH CHECK(warehouse_document_write(supplier_org_id));
    CREATE POLICY document_publication_buyer_read ON document_publication FOR SELECT USING(
      warehouse_catalog_access(supplier_org_id,connection_id) AND EXISTS(
        SELECT 1 FROM supplier_product p WHERE p.id=product_id AND p.status='published'));
    CREATE POLICY document_asset_buyer_read ON document_asset FOR SELECT USING(EXISTS(
      SELECT 1 FROM document_publication d WHERE d.asset_id=document_asset.id
        AND warehouse_catalog_access(d.supplier_org_id,d.connection_id)));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting document history")
