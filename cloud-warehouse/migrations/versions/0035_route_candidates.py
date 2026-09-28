"""Private, resumable model candidates independent of originals and human revisions."""

from alembic import op

revision = "0035_route_candidates"
down_revision = "0034_document_extraction"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE route_content_candidate (
      id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
      product_id uuid NOT NULL, asset_id uuid NOT NULL, parse_id uuid NOT NULL,
      policy text NOT NULL, source_hash text NOT NULL, model text NOT NULL,
      status text NOT NULL CHECK(status IN ('pending','working','complete','obsolete')),
      body jsonb NOT NULL, reports jsonb NOT NULL DEFAULT '[]'::jsonb,
      next_day integer NOT NULL DEFAULT 0 CHECK(next_day>=0),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
      lease_id uuid, lease_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
      UNIQUE(parse_id,policy,source_hash),
      FOREIGN KEY(parse_id,asset_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_parse(id,asset_id,supplier_org_id,connection_id,product_id)
    );
    ALTER TABLE route_content_candidate ENABLE ROW LEVEL SECURITY;
    ALTER TABLE route_content_candidate FORCE ROW LEVEL SECURITY;
    CREATE POLICY route_candidate_read ON route_content_candidate FOR SELECT USING(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker','supplier_admin','product_editor','auditor']));
    CREATE POLICY route_candidate_insert ON route_content_candidate FOR INSERT WITH CHECK(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE POLICY route_candidate_update ON route_content_candidate FOR UPDATE USING(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker'])) WITH CHECK(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE FUNCTION warehouse_candidate_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF OLD.status IN ('complete','obsolete') OR
        ROW(NEW.id,NEW.supplier_org_id,NEW.connection_id,NEW.product_id,NEW.asset_id,NEW.parse_id,NEW.policy,NEW.source_hash,NEW.model,NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id,OLD.supplier_org_id,OLD.connection_id,OLD.product_id,OLD.asset_id,OLD.parse_id,OLD.policy,OLD.source_hash,OLD.model,OLD.created_at)
      THEN RAISE EXCEPTION 'Immutable candidate identity or completed result'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER route_candidate_immutable BEFORE UPDATE ON route_content_candidate
      FOR EACH ROW EXECUTE FUNCTION warehouse_candidate_guard();
    CREATE INDEX route_candidate_pending ON route_content_candidate(supplier_org_id,created_at)
      WHERE status IN ('pending','working');
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup to retain editorial evidence")
