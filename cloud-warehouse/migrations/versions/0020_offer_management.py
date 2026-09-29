"""Approved sellable offers share the departure's managed inventory pool."""

from alembic import op

revision = "0020_offer_management"
down_revision = "0019_price_levels"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE inventory_pool ADD UNIQUE(id,departure_id,supplier_org_id);
    ALTER TABLE offer ADD service_description text NOT NULL DEFAULT '';
    ALTER TABLE offer ADD inventory_pool_id uuid;
    ALTER TABLE offer ADD CONSTRAINT offer_pool_scope
      FOREIGN KEY(inventory_pool_id,departure_id,supplier_org_id)
      REFERENCES inventory_pool(id,departure_id,supplier_org_id);
    UPDATE offer o SET inventory_pool_id=i.id,version=o.version+1
      FROM inventory_pool i WHERE i.departure_id=o.departure_id AND i.supplier_org_id=o.supplier_org_id;
    CREATE FUNCTION warehouse_bind_offer_pool() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      UPDATE offer SET inventory_pool_id=NEW.id WHERE departure_id=NEW.departure_id
        AND supplier_org_id=NEW.supplier_org_id AND inventory_pool_id IS NULL;
      RETURN NEW;
    END $$;
    CREATE TRIGGER bind_offer_pool AFTER INSERT ON inventory_pool
      FOR EACH ROW EXECUTE FUNCTION warehouse_bind_offer_pool();
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid,change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$ SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('pricing','price_book','offer','catalog','merchant','document','departure_sales') AND warehouse_has_role(ARRAY['product_editor']))) $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup to preserve approved offers and quote evidence")
