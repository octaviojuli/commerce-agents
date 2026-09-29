"""Separate approved display copy from supplier source facts."""

from alembic import op

revision = "0021_product_display"
down_revision = "0020_offer_management"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE supplier_product ADD name_override text;
    ALTER TABLE supplier_product ADD description_override text;
    ALTER TABLE supplier_product ADD display_version integer NOT NULL DEFAULT 0 CHECK(display_version>=0);
    ALTER TABLE supplier_product ADD display_updated_at timestamptz;
    ALTER TABLE supplier_product ADD CHECK(name_override IS NULL OR length(btrim(name_override)) BETWEEN 1 AND 300);
    ALTER TABLE supplier_product ADD CHECK(description_override IS NULL OR length(description_override)<=10000);
    CREATE VIEW product_listing WITH (security_invoker=true) AS
      SELECT p.*,COALESCE(name_override,name) AS effective_name,
        COALESCE(description_override,description) AS effective_description,
        CASE WHEN name_override IS NULL THEN 'source' ELSE 'warehouse_display' END AS name_origin,
        CASE WHEN description_override IS NULL THEN 'source' ELSE 'warehouse_display' END AS description_origin
      FROM supplier_product p;
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid,change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$ SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('product_display','pricing','price_book','offer','catalog','merchant','document','departure_sales') AND warehouse_has_role(ARRAY['product_editor']))) $$;
    """)


def downgrade():
    raise RuntimeError(
        "Restore a verified backup to retain approved display content and provenance"
    )
