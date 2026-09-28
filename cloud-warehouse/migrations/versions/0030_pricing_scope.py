"""Evaluate organization and catalog scope once in nested pricing reads."""

from alembic import op

revision = "0030_pricing_scope"
down_revision = "0029_quarantine_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION warehouse_live_grant(grant_uuid uuid) RETURNS boolean
      LANGUAGE sql STABLE AS $$
      SELECT EXISTS (
        SELECT 1 FROM distribution_grant g WHERE g.id=grant_uuid AND g.active
          AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
          AND (
            (g.supplier_org_id=warehouse_org_id()
              AND (SELECT warehouse_has_org(warehouse_org_id())))
            OR (g.buyer_org_id=warehouse_org_id()
              AND g.connection_id IN (SELECT warehouse_buyer_connections()))
          )
      )
    $$;
    ALTER POLICY grant_read ON distribution_grant USING (
      (supplier_org_id=warehouse_org_id() OR buyer_org_id=warehouse_org_id())
      AND (SELECT warehouse_has_org(warehouse_org_id()))
    );
    ALTER POLICY connection_owner_read ON supplier_connection USING (
      supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id()))
    );
    ALTER POLICY connection_buyer_read ON supplier_connection USING (
      active AND (SELECT warehouse_has_org(warehouse_org_id())) AND EXISTS (
        SELECT 1 FROM distribution_grant g WHERE g.connection_id=supplier_connection.id
          AND g.buyer_org_id=warehouse_org_id() AND g.active AND g.valid_from<=now()
          AND (g.expires_at IS NULL OR g.expires_at>now())
      )
    );
    ALTER POLICY contract_price_read ON contract_price USING (
      (supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id())))
      OR (connection_id IN (SELECT warehouse_buyer_connections()) AND (
        (layer='contract' AND buyer_org_id=warehouse_org_id() AND warehouse_live_grant(grant_id))
        OR layer='standard'
        OR (layer='grade' AND EXISTS (
          SELECT 1 FROM buyer_price_grade b WHERE b.connection_id=contract_price.connection_id
            AND b.buyer_org_id=warehouse_org_id() AND b.grade_key=contract_price.grade_key
            AND b.active AND warehouse_live_grant(b.grant_id)
        ))
      ))
    );
    """)


def downgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION warehouse_live_grant(grant_uuid uuid) RETURNS boolean
      LANGUAGE sql STABLE AS $$
      SELECT EXISTS (
        SELECT 1 FROM distribution_grant g WHERE g.id=grant_uuid AND g.active
          AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
          AND warehouse_catalog_access(g.supplier_org_id,g.connection_id)
      )
    $$;
    ALTER POLICY grant_read ON distribution_grant USING (
      warehouse_has_org(supplier_org_id) OR warehouse_has_org(buyer_org_id)
    );
    ALTER POLICY connection_owner_read ON supplier_connection USING (
      warehouse_has_org(supplier_org_id)
    );
    ALTER POLICY connection_buyer_read ON supplier_connection USING (
      active AND EXISTS (
        SELECT 1 FROM distribution_grant g WHERE g.connection_id=supplier_connection.id
          AND warehouse_has_org(g.buyer_org_id) AND g.active AND g.valid_from<=now()
          AND (g.expires_at IS NULL OR g.expires_at>now())
      )
    );
    ALTER POLICY contract_price_read ON contract_price USING (
      warehouse_has_org(supplier_org_id) OR (
        warehouse_catalog_access(supplier_org_id,connection_id) AND (
          (layer='contract' AND buyer_org_id=warehouse_org_id() AND warehouse_live_grant(grant_id))
          OR layer='standard'
          OR (layer='grade' AND EXISTS (
            SELECT 1 FROM buyer_price_grade b WHERE b.connection_id=contract_price.connection_id
              AND b.buyer_org_id=warehouse_org_id() AND b.grade_key=contract_price.grade_key
              AND b.active AND warehouse_live_grant(b.grant_id)
          ))
        )
      )
    );
    """)
