"""Durable provider jobs and immutable extraction evidence for document parsing."""

from alembic import op

revision = "0034_document_extraction"
down_revision = "0033_route_content"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE document_extraction_job (
      asset_id uuid NOT NULL, generation integer NOT NULL CHECK(generation>0),
      supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL, product_id uuid NOT NULL,
      payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
      not_before timestamptz NOT NULL, expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(asset_id,generation),
      FOREIGN KEY(asset_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_asset(id,supplier_org_id,connection_id,product_id)
    );
    ALTER TABLE document_extraction_job ENABLE ROW LEVEL SECURITY;
    ALTER TABLE document_extraction_job FORCE ROW LEVEL SECURITY;
    CREATE POLICY document_extraction_job_read ON document_extraction_job FOR SELECT
      USING(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE POLICY document_extraction_job_insert ON document_extraction_job FOR INSERT
      WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE POLICY document_extraction_job_update ON document_extraction_job FOR UPDATE
      USING(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']))
      WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']));
    ALTER TABLE document_parse ADD extraction jsonb;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup to retain extraction evidence")
