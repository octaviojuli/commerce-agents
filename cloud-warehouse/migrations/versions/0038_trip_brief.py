"""Durable owner-scoped advisor requirements with optimistic versions."""

from alembic import op

revision = "0038_trip_brief"
down_revision = "0037_native_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE conversation_brief (
      conversation_id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      body jsonb NOT NULL DEFAULT '{}', version integer NOT NULL CHECK(version > 0),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(conversation_id,organization_id,user_id)
        REFERENCES agent_conversation(id,organization_id,user_id)
    );
    ALTER TABLE conversation_brief ENABLE ROW LEVEL SECURITY;
    ALTER TABLE conversation_brief FORCE ROW LEVEL SECURITY;
    CREATE POLICY conversation_brief_read ON conversation_brief FOR SELECT
      USING(warehouse_conversation_access(organization_id,user_id));
    CREATE POLICY conversation_brief_insert ON conversation_brief FOR INSERT
      WITH CHECK(warehouse_conversation_access(organization_id,user_id));
    CREATE POLICY conversation_brief_update ON conversation_brief FOR UPDATE
      USING(warehouse_conversation_access(organization_id,user_id))
      WITH CHECK(warehouse_conversation_access(organization_id,user_id));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting customer requirements")
