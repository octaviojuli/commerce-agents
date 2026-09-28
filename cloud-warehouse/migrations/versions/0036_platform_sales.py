"""Platform sales catalog, uniform pricing and owner-isolated quote evidence."""

from alembic import op

revision = "0036_platform_sales"
down_revision = "0035_route_candidates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE platform_sales_policy (
      singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
      enabled boolean NOT NULL DEFAULT false,
      version integer NOT NULL DEFAULT 1 CHECK(version>0)
    );
    INSERT INTO platform_sales_policy(singleton) VALUES(true);
    REVOKE ALL ON platform_sales_policy FROM PUBLIC;
    CREATE FUNCTION warehouse_sales_version() RETURNS integer
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
      BEGIN RETURN COALESCE((SELECT version FROM public.platform_sales_policy
        WHERE singleton AND enabled AND public.warehouse_has_role(ARRAY['advisor','buyer_admin'])),0);
      END $$;
    REVOKE ALL ON FUNCTION warehouse_sales_version() FROM PUBLIC;
    CREATE OR REPLACE FUNCTION warehouse_buyer_connections() RETURNS SETOF uuid
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
      BEGIN
        IF public.warehouse_sales_version()>0 THEN
          RETURN QUERY SELECT c.id FROM public.supplier_connection c
            JOIN public.organization o ON o.id=c.supplier_org_id WHERE c.active AND o.active;
        ELSE
          RETURN QUERY SELECT c.id FROM public.supplier_connection c
            JOIN public.distribution_grant g ON g.connection_id=c.id AND g.supplier_org_id=c.supplier_org_id
            JOIN public.organization o ON o.id=c.supplier_org_id
            WHERE c.active AND o.active AND g.active AND g.buyer_org_id=public.warehouse_org_id()
              AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
              AND (SELECT public.warehouse_has_org(public.warehouse_org_id()));
        END IF;
      END $$;
    CREATE OR REPLACE FUNCTION warehouse_catalog_access(owner_id uuid, conn_id uuid)
      RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER AS $$
      SELECT warehouse_has_org(owner_id) OR EXISTS (
        SELECT 1 FROM supplier_connection c WHERE c.id=conn_id AND c.supplier_org_id=owner_id
          AND c.id IN (SELECT warehouse_buyer_connections()))
    $$;
    ALTER POLICY connection_buyer_read ON supplier_connection USING (
      id IN (SELECT warehouse_buyer_connections())
    );
    ALTER POLICY document_asset_buyer_read ON document_asset USING (
      (SELECT warehouse_sales_version())=0 AND EXISTS (
        SELECT 1 FROM document_publication d WHERE d.asset_id=document_asset.id
          AND warehouse_catalog_access(d.supplier_org_id,d.connection_id))
    );
    ALTER POLICY contract_price_read ON contract_price USING (
      (supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id())))
      OR (connection_id IN (SELECT warehouse_buyer_connections()) AND (
        layer='standard' OR ((SELECT warehouse_sales_version())=0 AND (
          (layer='contract' AND buyer_org_id=warehouse_org_id() AND warehouse_live_grant(grant_id))
          OR (layer='grade' AND EXISTS (
            SELECT 1 FROM buyer_price_grade b WHERE b.connection_id=contract_price.connection_id
              AND b.buyer_org_id=warehouse_org_id() AND b.grade_key=contract_price.grade_key
              AND b.active AND warehouse_live_grant(b.grant_id)))
        ))
      ))
    );
    CREATE OR REPLACE FUNCTION warehouse_applicable_prices(offer_uuid uuid,buyer_uuid uuid)
      RETURNS SETOF contract_price LANGUAGE sql STABLE AS $$
      SELECT p.* FROM contract_price p WHERE p.offer_id=offer_uuid AND p.active
        AND (buyer_uuid=warehouse_org_id() OR p.supplier_org_id=warehouse_org_id())
        AND (
          ((SELECT warehouse_sales_version())>0 AND buyer_uuid=warehouse_org_id() AND p.layer='standard'
            AND p.connection_id IN (SELECT warehouse_buyer_connections()))
          OR ((SELECT warehouse_sales_version())=0 AND EXISTS(
            SELECT 1 FROM distribution_grant g WHERE g.connection_id=p.connection_id
              AND g.buyer_org_id=buyer_uuid AND warehouse_live_grant(g.id))
            AND ((p.layer='contract' AND p.buyer_org_id=buyer_uuid AND warehouse_live_grant(p.grant_id))
              OR p.layer='standard' OR (p.layer='grade' AND EXISTS(
                SELECT 1 FROM buyer_price_grade b WHERE b.connection_id=p.connection_id
                  AND b.buyer_org_id=buyer_uuid AND b.grade_key=p.grade_key AND b.active AND warehouse_live_grant(b.grant_id)))))
        )
    $$;
    ALTER TABLE quote_snapshot ALTER COLUMN grant_id DROP NOT NULL;
    ALTER TABLE quote_snapshot ADD COLUMN sales_policy_version integer;
    ALTER TABLE quote_snapshot ADD CONSTRAINT quote_scope CHECK (
      (grant_id IS NOT NULL AND sales_policy_version IS NULL)
      OR (grant_id IS NULL AND sales_policy_version IS NOT NULL AND sales_policy_version>0)
    );
    ALTER TABLE quote_snapshot DROP CONSTRAINT quote_snapshot_buyer_org_id_idempotency_key_key;
    ALTER TABLE quote_snapshot ADD UNIQUE(buyer_org_id,actor_id,idempotency_key);
    ALTER POLICY quote_read ON quote_snapshot USING (
      warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['advisor','buyer_admin'])
      AND ((sales_policy_version IS NOT NULL AND (SELECT warehouse_sales_version())>0)
        OR (grant_id IS NOT NULL AND warehouse_live_grant(grant_id)))
      AND EXISTS(SELECT 1 FROM offer WHERE id=offer_id AND active)
    );
    ALTER POLICY quote_insert ON quote_snapshot WITH CHECK (
      warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['advisor','buyer_admin'])
      AND ((sales_policy_version=(SELECT warehouse_sales_version()) AND sales_policy_version>0)
        OR ((SELECT warehouse_sales_version())=0 AND warehouse_live_grant(grant_id)))
      AND EXISTS(SELECT 1 FROM offer WHERE id=offer_id AND active)
    );
    """)


def downgrade():
    raise RuntimeError("Sales quote evidence is durable; use a verified restore to roll back.")
