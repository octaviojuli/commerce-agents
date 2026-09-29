"""Offers, buyer-specific approved prices and customer mappings, immutable quote snapshots."""

from alembic import op

revision = "0006_pricing"
down_revision = "0005_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE distribution_grant ADD version integer NOT NULL DEFAULT 1;
        ALTER TABLE departure ADD UNIQUE(id,supplier_org_id,connection_id);
        ALTER TABLE distribution_grant ADD UNIQUE(id,supplier_org_id,buyer_org_id,connection_id);
        CREATE FUNCTION warehouse_grant_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN NEW.version=OLD.version+1; RETURN NEW; END $$;
        CREATE TRIGGER grant_version BEFORE UPDATE ON distribution_grant FOR EACH ROW EXECUTE FUNCTION warehouse_grant_version();
        CREATE TABLE offer (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(), supplier_org_id uuid NOT NULL,
          connection_id uuid NOT NULL, departure_id uuid NOT NULL, code text NOT NULL DEFAULT 'base',
          name text NOT NULL DEFAULT '基础团费', active boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 1,
          UNIQUE(departure_id,code), UNIQUE(id,supplier_org_id,connection_id),
          FOREIGN KEY(departure_id,supplier_org_id,connection_id) REFERENCES departure(id,supplier_org_id,connection_id),
          FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id)
        );
        INSERT INTO offer(supplier_org_id,connection_id,departure_id) SELECT supplier_org_id,connection_id,id FROM departure;
        CREATE FUNCTION warehouse_default_offer() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO offer(supplier_org_id,connection_id,departure_id) VALUES(NEW.supplier_org_id,NEW.connection_id,NEW.id);
          RETURN NEW;
        END $$;
        CREATE TRIGGER default_offer AFTER INSERT ON departure FOR EACH ROW EXECUTE FUNCTION warehouse_default_offer();
        CREATE TABLE customer_verification (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
          customer_id text NOT NULL, customer_code text NOT NULL, customer_name text NOT NULL,
          created_by uuid NOT NULL REFERENCES warehouse_user(id), verified_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL, evidence_hash text NOT NULL,
          FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id)
        );
        CREATE TABLE buyer_customer_binding (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, buyer_org_id uuid NOT NULL,
          connection_id uuid NOT NULL, grant_id uuid NOT NULL, verification_id uuid NOT NULL REFERENCES customer_verification(id),
          customer_id text NOT NULL, customer_code text NOT NULL, customer_name text NOT NULL,
          active boolean NOT NULL DEFAULT true, version integer NOT NULL DEFAULT 1,
          UNIQUE(connection_id,buyer_org_id), UNIQUE(id,buyer_org_id),
          FOREIGN KEY(grant_id,supplier_org_id,buyer_org_id,connection_id) REFERENCES distribution_grant(id,supplier_org_id,buyer_org_id,connection_id)
        );
        CREATE TABLE binding_acceptance (
          binding_id uuid NOT NULL, version integer NOT NULL, buyer_org_id uuid NOT NULL,
          accepted_by uuid NOT NULL REFERENCES warehouse_user(id), accepted_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(binding_id,version), FOREIGN KEY(binding_id,buyer_org_id) REFERENCES buyer_customer_binding(id,buyer_org_id)
        );
        CREATE TABLE contract_price (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, buyer_org_id uuid NOT NULL,
          connection_id uuid NOT NULL, offer_id uuid NOT NULL, grant_id uuid NOT NULL,
          schedule jsonb NOT NULL, version integer NOT NULL DEFAULT 1, active boolean NOT NULL DEFAULT true,
          source_ref text NOT NULL, valid_from timestamptz NOT NULL, valid_until timestamptz NOT NULL,
          UNIQUE(offer_id,buyer_org_id), CHECK(valid_until>valid_from),
          FOREIGN KEY(offer_id,supplier_org_id,connection_id) REFERENCES offer(id,supplier_org_id,connection_id),
          FOREIGN KEY(grant_id,supplier_org_id,buyer_org_id,connection_id) REFERENCES distribution_grant(id,supplier_org_id,buyer_org_id,connection_id)
        );
        CREATE TABLE quote_snapshot (
          id uuid PRIMARY KEY, buyer_org_id uuid NOT NULL REFERENCES organization(id), actor_id uuid NOT NULL REFERENCES warehouse_user(id),
          offer_id uuid NOT NULL REFERENCES offer(id), grant_id uuid NOT NULL REFERENCES distribution_grant(id),
          idempotency_key text NOT NULL, request_hash text NOT NULL, body jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(), fresh_until timestamptz NOT NULL,
          UNIQUE(buyer_org_id,idempotency_key)
        );
        ALTER TABLE offer ENABLE ROW LEVEL SECURITY;
        ALTER TABLE offer FORCE ROW LEVEL SECURITY;
        CREATE POLICY offer_read ON offer FOR SELECT USING(warehouse_has_org(supplier_org_id) OR (active AND EXISTS(
          SELECT 1 FROM departure d JOIN supplier_product p ON p.id=d.product_id WHERE d.id=offer.departure_id
          AND d.status='published' AND p.status='published' AND warehouse_catalog_access(d.supplier_org_id,d.connection_id))));
        CREATE POLICY offer_insert ON offer FOR INSERT WITH CHECK(warehouse_catalog_write(supplier_org_id));
        CREATE POLICY offer_update ON offer FOR UPDATE USING(warehouse_catalog_write(supplier_org_id)) WITH CHECK(warehouse_catalog_write(supplier_org_id));
        ALTER TABLE customer_verification ENABLE ROW LEVEL SECURITY;
        ALTER TABLE customer_verification FORCE ROW LEVEL SECURITY;
        CREATE POLICY verification_read ON customer_verification FOR SELECT USING(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']));
        CREATE POLICY verification_insert ON customer_verification FOR INSERT WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']) AND created_by=warehouse_user_id());
        CREATE FUNCTION warehouse_live_grant(grant_uuid uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT EXISTS(SELECT 1 FROM distribution_grant g WHERE g.id=grant_uuid AND g.active AND g.valid_from<=now()
            AND (g.expires_at IS NULL OR g.expires_at>now()) AND warehouse_catalog_access(g.supplier_org_id,g.connection_id))
        $$;
    """)
    for table in ("buyer_customer_binding", "contract_price"):
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        )
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT USING(warehouse_has_org(supplier_org_id) OR (warehouse_has_org(buyer_org_id) AND warehouse_live_grant(grant_id)))"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']))"
        )
        op.execute(
            f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin'])) WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']))"
        )
    op.execute("""
        ALTER TABLE binding_acceptance ENABLE ROW LEVEL SECURITY;
        ALTER TABLE binding_acceptance FORCE ROW LEVEL SECURITY;
        CREATE POLICY acceptance_read ON binding_acceptance FOR SELECT USING(warehouse_has_org(buyer_org_id) OR EXISTS(
          SELECT 1 FROM buyer_customer_binding b WHERE b.id=binding_id AND warehouse_has_org(b.supplier_org_id)));
        CREATE POLICY acceptance_insert ON binding_acceptance FOR INSERT WITH CHECK(warehouse_has_org(buyer_org_id)
          AND warehouse_has_role(ARRAY['buyer_admin']) AND accepted_by=warehouse_user_id() AND EXISTS(
            SELECT 1 FROM buyer_customer_binding b WHERE b.id=binding_id AND b.version=binding_acceptance.version AND b.active AND warehouse_live_grant(b.grant_id)));
        -- Consent is append-only; buyers cannot update supplier-mapped IDs.
        CREATE FUNCTION warehouse_accept_binding(binding_uuid uuid, expected integer) RETURNS boolean
        LANGUAGE plpgsql AS $$
        DECLARE updated integer;
        BEGIN
          IF NOT warehouse_has_role(ARRAY['buyer_admin']) THEN RETURN false; END IF;
          INSERT INTO binding_acceptance(binding_id,version,buyer_org_id,accepted_by)
            SELECT id,version,buyer_org_id,warehouse_user_id() FROM buyer_customer_binding
            WHERE id=binding_uuid AND buyer_org_id=warehouse_org_id() AND active AND version=expected AND warehouse_live_grant(grant_id)
            ON CONFLICT(binding_id,version) DO NOTHING;
          GET DIAGNOSTICS updated=ROW_COUNT;
          RETURN updated=1 OR EXISTS(SELECT 1 FROM binding_acceptance WHERE binding_id=binding_uuid AND version=expected AND warehouse_has_org(buyer_org_id));
        END $$;
        ALTER TABLE quote_snapshot ENABLE ROW LEVEL SECURITY;
        ALTER TABLE quote_snapshot FORCE ROW LEVEL SECURITY;
        CREATE POLICY quote_read ON quote_snapshot FOR SELECT USING(warehouse_has_org(buyer_org_id) AND warehouse_live_grant(grant_id) AND EXISTS(SELECT 1 FROM offer WHERE id=offer_id AND active));
        CREATE POLICY quote_insert ON quote_snapshot FOR INSERT WITH CHECK(warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id() AND warehouse_has_role(ARRAY['advisor','buyer_admin']) AND warehouse_live_grant(grant_id) AND EXISTS(SELECT 1 FROM offer WHERE id=offer_id AND active));
        DROP POLICY change_request_insert ON change_request;
        DROP POLICY change_request_update ON change_request;
        CREATE FUNCTION warehouse_change_write(org uuid, change_kind text) RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT warehouse_has_org(org) AND (warehouse_has_role(ARRAY['supplier_admin']) OR
            (change_kind IN ('inventory','excel_import') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
            (change_kind='pricing' AND warehouse_has_role(ARRAY['product_editor'])))
        $$;
        CREATE POLICY change_request_insert ON change_request FOR INSERT WITH CHECK(warehouse_change_write(organization_id,kind));
        CREATE POLICY change_request_update ON change_request FOR UPDATE USING(warehouse_change_write(organization_id,kind)) WITH CHECK(warehouse_change_write(organization_id,kind));
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Pricing snapshots are durable business evidence; restore a verified backup to roll back this migration."
    )
