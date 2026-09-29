"""Revocable quote capabilities; only hashes persist and anonymous lookup returns no prices."""

from alembic import op

revision = "0018_quote_shares"
down_revision = "0017_departure_sales"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE quote_share (
      id uuid PRIMARY KEY, buyer_org_id uuid NOT NULL REFERENCES organization(id),
      actor_id uuid NOT NULL REFERENCES warehouse_user(id), quote_id uuid NOT NULL REFERENCES quote_snapshot(id),
      request_id uuid NOT NULL, hours integer NOT NULL CHECK(hours BETWEEN 1 AND 168),
      token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{64}$'),
      created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
      revoked_at timestamptz, CHECK(expires_at>created_at AND expires_at<=created_at+interval '168 hours'),
      UNIQUE(buyer_org_id,actor_id,request_id)
    );
    CREATE INDEX quote_share_owner ON quote_share(buyer_org_id,actor_id,quote_id,created_at);
    ALTER TABLE quote_share ENABLE ROW LEVEL SECURITY;
    ALTER TABLE quote_share FORCE ROW LEVEL SECURITY;
    CREATE POLICY share_read ON quote_share FOR SELECT USING(
      warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['advisor','buyer_admin']));
    CREATE POLICY share_insert ON quote_share FOR INSERT WITH CHECK(
      warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['advisor','buyer_admin']) AND EXISTS(
        SELECT 1 FROM quote_snapshot q WHERE q.id=quote_id AND q.buyer_org_id=warehouse_org_id()
        AND q.actor_id=warehouse_user_id() AND q.fresh_until>now()));
    CREATE POLICY share_revoke ON quote_share FOR UPDATE USING(
      warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['advisor','buyer_admin'])) WITH CHECK(
      warehouse_has_org(buyer_org_id) AND actor_id=warehouse_user_id());
    CREATE FUNCTION warehouse_share_revoke_only() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.revoked_at IS NOT NULL THEN RETURN OLD; END IF;
      IF NEW.revoked_at IS NULL THEN RAISE EXCEPTION 'SHARE_REVOKE_ONLY'; END IF;
      NEW.revoked_at=clock_timestamp(); RETURN NEW;
    END $$;
    CREATE TRIGGER share_revoke_only BEFORE UPDATE ON quote_share
      FOR EACH ROW EXECUTE FUNCTION warehouse_share_revoke_only();
    CREATE FUNCTION warehouse_quote_share_lookup(digest text)
      RETURNS TABLE(id uuid,actor_id uuid,buyer_org_id uuid) LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=public,pg_temp AS $$
      SELECT s.id,s.actor_id,s.buyer_org_id FROM quote_share s
      JOIN warehouse_user u ON u.id=s.actor_id AND u.active
      JOIN organization o ON o.id=s.buyer_org_id AND o.active
      JOIN membership m ON m.user_id=s.actor_id AND m.organization_id=s.buyer_org_id
        AND m.active AND m.roles && ARRAY['advisor','buyer_admin']
      WHERE s.token_hash=digest AND s.revoked_at IS NULL AND s.expires_at>now()
    $$;
    REVOKE ALL ON FUNCTION warehouse_quote_share_lookup(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION warehouse_share_revoke_only() FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting share revocations")
