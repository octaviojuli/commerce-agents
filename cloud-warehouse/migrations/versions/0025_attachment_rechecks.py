"""Preserve attachment observations while revisiting unchanged source URLs."""

from alembic import op

revision = "0025_attachment_rechecks"
down_revision = "0024_transaction_context"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE document_fetch ADD UNIQUE(id,supplier_org_id,connection_id,product_id);
    CREATE TABLE document_fetch_observation (
      id uuid PRIMARY KEY,
      fetch_id uuid NOT NULL, supplier_org_id uuid NOT NULL,
      connection_id uuid NOT NULL, product_id uuid NOT NULL,
      asset_id uuid NOT NULL, etag text, last_modified text,
      outcome text NOT NULL CHECK(outcome IN ('initial','unchanged','changed')),
      checked_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(fetch_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_fetch(id,supplier_org_id,connection_id,product_id),
      FOREIGN KEY(asset_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_asset(id,supplier_org_id,connection_id,product_id)
    );
    CREATE INDEX document_observation_job ON document_fetch_observation(fetch_id,checked_at DESC,id DESC);
    CREATE INDEX document_observation_asset ON document_fetch_observation(asset_id,checked_at DESC,id DESC);
    INSERT INTO document_fetch_observation(id,fetch_id,supplier_org_id,connection_id,product_id,
      asset_id,etag,last_modified,outcome,checked_at)
    SELECT gen_random_uuid(),id,supplier_org_id,connection_id,product_id,
      asset_id,etag,last_modified,'initial',COALESCE(completed_at,created_at)
    FROM document_fetch WHERE status='fetched';
    UPDATE document_fetch SET next_attempt_at=GREATEST(COALESCE(completed_at,created_at),now())+interval '1 day'
      WHERE status='fetched';
    ALTER TABLE document_fetch_observation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE document_fetch_observation FORCE ROW LEVEL SECURITY;
    CREATE POLICY document_observation_read ON document_fetch_observation FOR SELECT
      USING(warehouse_has_org(supplier_org_id));
    CREATE POLICY document_observation_insert ON document_fetch_observation FOR INSERT
      WITH CHECK(warehouse_document_write(supplier_org_id) AND EXISTS(
        SELECT 1 FROM document_fetch f JOIN document_asset a ON a.id=document_fetch_observation.asset_id
        WHERE f.id=document_fetch_observation.fetch_id AND a.product_id=f.product_id));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting attachment provenance")
