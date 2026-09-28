"""Scoped price levels, non-overlapping validity windows, and buyer grade assignments."""

from alembic import op

revision = "0019_price_levels"
down_revision = "0018_quote_shares"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    ALTER TABLE contract_price DROP CONSTRAINT contract_price_offer_id_buyer_org_id_key;
    ALTER TABLE contract_price ALTER COLUMN buyer_org_id DROP NOT NULL;
    ALTER TABLE contract_price ALTER COLUMN grant_id DROP NOT NULL;
    ALTER TABLE contract_price ADD layer text NOT NULL DEFAULT 'contract';
    ALTER TABLE contract_price ADD grade_key text;
    ALTER TABLE contract_price ADD CONSTRAINT price_layer_scope CHECK(
      (layer='contract' AND buyer_org_id IS NOT NULL AND grant_id IS NOT NULL AND grade_key IS NULL)
      OR (layer='grade' AND buyer_org_id IS NULL AND grant_id IS NULL AND grade_key IS NOT NULL AND length(grade_key) BETWEEN 1 AND 80)
      OR (layer='standard' AND buyer_org_id IS NULL AND grant_id IS NULL AND grade_key IS NULL));
    ALTER TABLE contract_price ADD CONSTRAINT price_no_overlap EXCLUDE USING gist (
      offer_id WITH =,layer WITH =,
      (coalesce(buyer_org_id,'00000000-0000-0000-0000-000000000000'::uuid)) WITH =,
      (coalesce(grade_key,'')) WITH =,tstzrange(valid_from,valid_until,'[)') WITH &&
    ) WHERE(active);
    CREATE TABLE buyer_price_grade (
      id uuid PRIMARY KEY,supplier_org_id uuid NOT NULL,buyer_org_id uuid NOT NULL,
      connection_id uuid NOT NULL,grant_id uuid NOT NULL,grade_key text NOT NULL CHECK(length(grade_key) BETWEEN 1 AND 80),
      active boolean NOT NULL DEFAULT true,version integer NOT NULL DEFAULT 1 CHECK(version>0),
      UNIQUE(connection_id,buyer_org_id),
      FOREIGN KEY(grant_id,supplier_org_id,buyer_org_id,connection_id)
        REFERENCES distribution_grant(id,supplier_org_id,buyer_org_id,connection_id)
    );
    ALTER TABLE buyer_price_grade ENABLE ROW LEVEL SECURITY;
    ALTER TABLE buyer_price_grade FORCE ROW LEVEL SECURITY;
    CREATE POLICY grade_read ON buyer_price_grade FOR SELECT USING(
      warehouse_has_org(supplier_org_id) OR (warehouse_has_org(buyer_org_id) AND warehouse_live_grant(grant_id)));
    CREATE POLICY grade_insert ON buyer_price_grade FOR INSERT WITH CHECK(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']));
    CREATE POLICY grade_update ON buyer_price_grade FOR UPDATE USING(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']))
      WITH CHECK(warehouse_has_org(supplier_org_id));
    DROP POLICY contract_price_read ON contract_price;
    CREATE POLICY contract_price_read ON contract_price FOR SELECT USING(
      warehouse_has_org(supplier_org_id) OR (
        warehouse_catalog_access(supplier_org_id,connection_id) AND (
          (layer='contract' AND buyer_org_id=warehouse_org_id() AND warehouse_live_grant(grant_id))
          OR layer='standard'
          OR (layer='grade' AND EXISTS(SELECT 1 FROM buyer_price_grade b
            WHERE b.connection_id=contract_price.connection_id AND b.buyer_org_id=warehouse_org_id()
            AND b.grade_key=contract_price.grade_key AND b.active AND warehouse_live_grant(b.grant_id)))
        )
      ));
    CREATE FUNCTION warehouse_applicable_prices(offer_uuid uuid,buyer_uuid uuid)
      RETURNS SETOF contract_price LANGUAGE sql STABLE AS $$
      SELECT p.* FROM contract_price p
      WHERE p.offer_id=offer_uuid AND p.active
      AND (buyer_uuid=warehouse_org_id() OR p.supplier_org_id=warehouse_org_id())
      AND EXISTS(SELECT 1 FROM distribution_grant g WHERE g.connection_id=p.connection_id
        AND g.buyer_org_id=buyer_uuid AND warehouse_live_grant(g.id))
      AND ((p.layer='contract' AND p.buyer_org_id=buyer_uuid AND warehouse_live_grant(p.grant_id))
        OR p.layer='standard'
        OR (p.layer='grade' AND EXISTS(SELECT 1 FROM buyer_price_grade b WHERE b.connection_id=p.connection_id
          AND b.buyer_org_id=buyer_uuid AND b.grade_key=p.grade_key AND b.active AND warehouse_live_grant(b.grant_id))))
    $$;
    CREATE FUNCTION warehouse_effective_price(offer_uuid uuid,buyer_uuid uuid,at_time timestamptz DEFAULT now())
      RETURNS SETOF contract_price LANGUAGE sql STABLE AS $$
      SELECT p.* FROM warehouse_applicable_prices(offer_uuid,buyer_uuid) p
      WHERE p.valid_from<=at_time AND p.valid_until>at_time
      ORDER BY CASE p.layer WHEN 'contract' THEN 0 WHEN 'grade' THEN 1 ELSE 2 END,p.id LIMIT 1
    $$;
    REVOKE ALL ON FUNCTION warehouse_applicable_prices(uuid,uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION warehouse_effective_price(uuid,uuid,timestamptz) FROM PUBLIC;
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid,change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$ SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('pricing','price_book','catalog','merchant','document','departure_sales') AND warehouse_has_role(ARRAY['product_editor']))) $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than discard approved price windows")
