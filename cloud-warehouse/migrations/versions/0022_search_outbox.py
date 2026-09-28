"""Rebuildable product search and per-consumer durable Outbox receipts."""

from alembic import op

revision = "0022_search_outbox"
down_revision = "0021_product_display"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE TABLE product_search (
      product_id uuid PRIMARY KEY REFERENCES supplier_product(id),
      supplier_org_id uuid NOT NULL REFERENCES organization(id),
      product_version integer NOT NULL, display_version integer NOT NULL,
      document text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX product_search_owner ON product_search(supplier_org_id);
    CREATE INDEX product_search_text ON product_search USING gin(document gin_trgm_ops);
    ALTER TABLE product_search ENABLE ROW LEVEL SECURITY;
    ALTER TABLE product_search FORCE ROW LEVEL SECURITY;
    CREATE POLICY product_search_read ON product_search FOR SELECT USING (
      EXISTS(SELECT 1 FROM supplier_product p WHERE p.id=product_id AND p.supplier_org_id=product_search.supplier_org_id));
    CREATE POLICY product_search_insert ON product_search FOR INSERT WITH CHECK (
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker'])
      AND EXISTS(SELECT 1 FROM supplier_product p WHERE p.id=product_id AND p.supplier_org_id=product_search.supplier_org_id));
    CREATE POLICY product_search_update ON product_search FOR UPDATE USING (
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']))
      WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker'])
      AND EXISTS(SELECT 1 FROM supplier_product p WHERE p.id=product_id AND p.supplier_org_id=product_search.supplier_org_id));
    CREATE TABLE outbox_receipt (
      event_id uuid NOT NULL REFERENCES outbox_event(id),
      consumer text NOT NULL, organization_id uuid NOT NULL REFERENCES organization(id),
      status text NOT NULL CHECK(status IN ('done','ignored','waiting','failed')),
      attempts integer NOT NULL CHECK(attempts>0),
      max_attempts integer NOT NULL DEFAULT 5 CHECK(max_attempts>=attempts),
      next_attempt_at timestamptz NOT NULL DEFAULT now(),
      last_error text, completed_at timestamptz,
      PRIMARY KEY(consumer,event_id),
      CHECK((status IN ('done','ignored')) = (completed_at IS NOT NULL))
    );
    CREATE INDEX outbox_receipt_owner ON outbox_receipt(organization_id,consumer,status);
    ALTER TABLE outbox_receipt ENABLE ROW LEVEL SECURITY;
    ALTER TABLE outbox_receipt FORCE ROW LEVEL SECURITY;
    CREATE POLICY outbox_receipt_read ON outbox_receipt FOR SELECT USING(warehouse_has_org(organization_id));
    CREATE POLICY outbox_receipt_insert ON outbox_receipt FOR INSERT WITH CHECK (
      warehouse_has_org(organization_id) AND warehouse_has_role(ARRAY['sync_worker'])
      AND EXISTS(SELECT 1 FROM outbox_event e WHERE e.id=event_id AND e.organization_id=outbox_receipt.organization_id));
    CREATE POLICY outbox_receipt_update ON outbox_receipt FOR UPDATE USING (
      warehouse_has_org(organization_id) AND warehouse_has_role(ARRAY['sync_worker']))
      WITH CHECK(warehouse_has_org(organization_id) AND warehouse_has_role(ARRAY['sync_worker'])
      AND EXISTS(SELECT 1 FROM outbox_event e WHERE e.id=event_id AND e.organization_id=outbox_receipt.organization_id));
    CREATE TABLE search_worker_state (
      organization_id uuid PRIMARY KEY REFERENCES organization(id),
      heartbeat_at timestamptz NOT NULL DEFAULT now(), last_success_at timestamptz,
      last_error text
    );
    ALTER TABLE search_worker_state ENABLE ROW LEVEL SECURITY;
    ALTER TABLE search_worker_state FORCE ROW LEVEL SECURITY;
    CREATE POLICY search_worker_read ON search_worker_state FOR SELECT USING(warehouse_has_org(organization_id));
    CREATE POLICY search_worker_insert ON search_worker_state FOR INSERT WITH CHECK (
      warehouse_has_org(organization_id) AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE POLICY search_worker_update ON search_worker_state FOR UPDATE USING (
      warehouse_has_org(organization_id) AND warehouse_has_role(ARRAY['sync_worker']))
      WITH CHECK(warehouse_has_org(organization_id) AND warehouse_has_role(ARRAY['sync_worker']));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup to retain consumer receipts")
