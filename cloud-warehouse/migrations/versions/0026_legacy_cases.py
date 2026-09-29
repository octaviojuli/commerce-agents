"""Offline imported advisor archives, isolated from executable conversation state."""

from alembic import op

revision = "0026_legacy_cases"
down_revision = "0025_attachment_rechecks"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE legacy_case_archive (
      id uuid PRIMARY KEY, source_namespace uuid NOT NULL, legacy_session_id text NOT NULL,
      organization_id uuid NOT NULL REFERENCES organization(id),
      user_id uuid NOT NULL REFERENCES warehouse_user(id),
      connection_id uuid NOT NULL REFERENCES supplier_connection(id),
      grant_id uuid NOT NULL REFERENCES distribution_grant(id), grant_version integer NOT NULL,
      source_hash text NOT NULL, body_hash text NOT NULL, body jsonb NOT NULL,
      title text NOT NULL, imported_by_database_role text NOT NULL, import_note text NOT NULL,
      imported_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(source_namespace,legacy_session_id),
      CHECK(length(source_hash)=64 AND length(body_hash)=64),
      CHECK(length(legacy_session_id) BETWEEN 1 AND 128),
      CHECK(octet_length(body::text)<=4000000)
    );
    CREATE INDEX legacy_case_owner ON legacy_case_archive(organization_id,user_id,imported_at DESC,id);
    ALTER TABLE legacy_case_archive ENABLE ROW LEVEL SECURITY;
    ALTER TABLE legacy_case_archive FORCE ROW LEVEL SECURITY;
    CREATE POLICY legacy_case_read ON legacy_case_archive FOR SELECT USING(
      warehouse_has_org(organization_id) AND user_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['advisor','buyer_admin'])
      AND EXISTS(SELECT 1 FROM supplier_connection c WHERE c.id=connection_id AND c.active
        AND warehouse_catalog_access(c.supplier_org_id,c.id))
      AND EXISTS(SELECT 1 FROM distribution_grant g WHERE g.id=grant_id
        AND g.version=grant_version AND warehouse_live_grant(g.id))
    );
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting legacy case history")
