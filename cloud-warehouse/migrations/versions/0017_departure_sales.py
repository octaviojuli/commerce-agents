"""Supplier-approved local sales controls, independent of upstream snapshots."""

from alembic import op

revision = "0017_departure_sales"
down_revision = "0016_sync_cooldown"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE departure ADD COLUMN sales_paused boolean NOT NULL DEFAULT false;
    ALTER TABLE departure ADD COLUMN local_booking_deadline timestamptz;
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid, change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$
      SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('pricing','catalog','merchant','document','departure_sales') AND warehouse_has_role(ARRAY['product_editor'])))
    $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting sales controls")
