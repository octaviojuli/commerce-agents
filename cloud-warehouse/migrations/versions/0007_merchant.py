"""Persistent merchant batches, catalog history and truthful discard attribution."""

from alembic import op

revision = "0007_merchant"
down_revision = "0006_pricing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE supplier_product ADD COLUMN description text NOT NULL DEFAULT '';
    ALTER TABLE change_request ADD COLUMN discarded_by uuid REFERENCES warehouse_user(id),
      ADD COLUMN discarded_at timestamptz, ADD COLUMN discarded_by_kind text
      CHECK(discarded_by_kind IN ('operator','agent'));
    CREATE TABLE product_revision (
      product_id uuid NOT NULL REFERENCES supplier_product(id), version integer NOT NULL,
      supplier_org_id uuid NOT NULL REFERENCES organization(id), body jsonb NOT NULL,
      actor_id uuid REFERENCES warehouse_user(id), recorded_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(product_id,version)
    );
    INSERT INTO product_revision(product_id,version,supplier_org_id,body)
      SELECT id,version,supplier_org_id,to_jsonb(p) FROM supplier_product p;
    ALTER TABLE product_revision ENABLE ROW LEVEL SECURITY;
    ALTER TABLE product_revision FORCE ROW LEVEL SECURITY;
    CREATE POLICY revision_read ON product_revision FOR SELECT USING(warehouse_has_org(supplier_org_id));
    CREATE POLICY revision_insert ON product_revision FOR INSERT WITH CHECK(warehouse_catalog_write(supplier_org_id));
    CREATE FUNCTION warehouse_record_product_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' OR NEW.version<>OLD.version THEN
        INSERT INTO product_revision(product_id,version,supplier_org_id,body,actor_id)
          VALUES(NEW.id,NEW.version,NEW.supplier_org_id,to_jsonb(NEW),warehouse_user_id());
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER product_revision AFTER INSERT OR UPDATE ON supplier_product
      FOR EACH ROW EXECUTE FUNCTION warehouse_record_product_revision();
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid, change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$
      SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('pricing','catalog','merchant') AND warehouse_has_role(ARRAY['product_editor'])))
    $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting catalog history")
