"""Keep departure quarantine while evaluating live grants once per statement."""

from alembic import op

revision = "0029_quarantine_scope"
down_revision = "0028_document_reparses"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER POLICY departure_read ON departure USING (
      (supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id())))
      OR (status='published' AND NOT source_quarantined
        AND connection_id IN (SELECT warehouse_buyer_connections()))
    );
    ALTER POLICY inventory_read ON inventory_pool USING (
      (supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id())))
      OR EXISTS (
        SELECT 1 FROM departure d JOIN supplier_product p ON p.id=d.product_id
        WHERE d.id=inventory_pool.departure_id AND d.status='published' AND p.status='published'
          AND d.connection_id IN (SELECT warehouse_buyer_connections())
      )
    );
    ALTER POLICY offer_read ON offer USING (
      (supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id())))
      OR (active AND EXISTS (
        SELECT 1 FROM departure d JOIN supplier_product p ON p.id=d.product_id
        WHERE d.id=offer.departure_id AND d.status='published' AND p.status='published'
          AND d.connection_id IN (SELECT warehouse_buyer_connections())
      ))
    );
    """)


def downgrade():
    op.execute("""
    ALTER POLICY departure_read ON departure USING (
      warehouse_has_org(supplier_org_id) OR (status='published' AND NOT source_quarantined
        AND warehouse_catalog_access(supplier_org_id,connection_id))
    );
    ALTER POLICY inventory_read ON inventory_pool USING (warehouse_has_org(supplier_org_id)
      OR EXISTS (
        SELECT 1 FROM departure d JOIN supplier_product p ON p.id=d.product_id
        WHERE d.id=inventory_pool.departure_id AND d.status='published' AND p.status='published'
          AND warehouse_catalog_access(d.supplier_org_id,d.connection_id)
      )
    );
    ALTER POLICY offer_read ON offer USING (warehouse_has_org(supplier_org_id)
      OR (active AND EXISTS (
        SELECT 1 FROM departure d JOIN supplier_product p ON p.id=d.product_id
        WHERE d.id=offer.departure_id AND d.status='published' AND p.status='published'
          AND warehouse_catalog_access(d.supplier_org_id,d.connection_id)
      ))
    );
    """)
