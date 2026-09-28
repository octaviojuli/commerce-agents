"""Quote document lifetime, customer document encryption and revocable plan capabilities."""

import json

from alembic import op
from sqlalchemy import text

revision = "0045_advisor_v3"
down_revision = "0044_advisor_retention"
branch_labels = depends_on = None


def upgrade():
    from cloud_warehouse.copilot_privacy import seal

    conn = op.get_bind()
    for row in (
        conn.execute(
            text(
                "SELECT id,body FROM advisor_customer WHERE jsonb_path_exists(body, '$.travelers[*] ? (@.document_number != \"\")')"
            )
        )
        .mappings()
        .all()
    ):
        conn.execute(
            text("UPDATE advisor_customer SET body=CAST(:body AS jsonb) WHERE id=:id"),
            {"id": row["id"], "body": json.dumps(seal(row["body"], row["id"]), ensure_ascii=False)},
        )
    op.execute("""
    ALTER TABLE advisor_customer ADD CONSTRAINT advisor_document_encrypted
      CHECK (NOT jsonb_path_exists(body, '$.travelers[*] ? (@.document_number != "")'));
    DROP POLICY share_insert ON quote_share;
    CREATE POLICY share_insert ON quote_share FOR INSERT WITH CHECK(
      warehouse_advisor_owner(buyer_org_id,actor_id) AND EXISTS(
        SELECT 1 FROM quote_snapshot q WHERE q.id=quote_id AND q.buyer_org_id=warehouse_org_id()
        AND q.actor_id=warehouse_user_id()
        AND COALESCE((q.body->>'quote_valid_until')::timestamptz,q.created_at+interval '24 hours')>now()
        AND quote_share.expires_at<=COALESCE((q.body->>'quote_valid_until')::timestamptz,q.created_at+interval '24 hours')));
    CREATE TABLE advisor_plan_share (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
      user_id uuid NOT NULL REFERENCES warehouse_user(id), plan_id uuid NOT NULL REFERENCES advisor_record(id),
      request_id uuid NOT NULL, token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{64}$'),
      created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
      revoked_at timestamptz, CHECK(expires_at>created_at AND expires_at<=created_at+interval '168 hours'),
      UNIQUE(organization_id,user_id,request_id)
    );
    ALTER TABLE advisor_plan_share ENABLE ROW LEVEL SECURITY;
    ALTER TABLE advisor_plan_share FORCE ROW LEVEL SECURITY;
    CREATE POLICY plan_share_read ON advisor_plan_share FOR SELECT USING(warehouse_advisor_owner(organization_id,user_id));
    CREATE POLICY plan_share_insert ON advisor_plan_share FOR INSERT WITH CHECK(
      warehouse_advisor_owner(organization_id,user_id) AND EXISTS(SELECT 1 FROM advisor_record r WHERE r.id=plan_id AND r.kind='plan'));
    CREATE POLICY plan_share_revoke ON advisor_plan_share FOR UPDATE USING(warehouse_advisor_owner(organization_id,user_id)) WITH CHECK(warehouse_advisor_owner(organization_id,user_id));
    CREATE TRIGGER plan_share_revoke_only BEFORE UPDATE ON advisor_plan_share FOR EACH ROW EXECUTE FUNCTION warehouse_share_revoke_only();
    CREATE FUNCTION warehouse_plan_share_lookup(digest text)
      RETURNS TABLE(id uuid,user_id uuid,organization_id uuid) LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=public,pg_temp AS $$
      SELECT s.id,s.user_id,s.organization_id FROM advisor_plan_share s
      JOIN warehouse_user u ON u.id=s.user_id AND u.active
      JOIN organization o ON o.id=s.organization_id AND o.active
      JOIN membership m ON m.user_id=s.user_id AND m.organization_id=s.organization_id
        AND m.active AND m.roles && ARRAY['advisor','buyer_admin']
      WHERE s.token_hash=digest AND s.revoked_at IS NULL AND s.expires_at>now()
    $$;
    REVOKE ALL ON FUNCTION warehouse_plan_share_lookup(text) FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than decrypting customer records")
