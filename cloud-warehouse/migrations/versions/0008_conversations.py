"""Organization/user-scoped agent conversations and durable turn leases."""

from alembic import op

revision = "0008_conversations"
down_revision = "0007_merchant"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE agent_conversation (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
      user_id uuid NOT NULL REFERENCES warehouse_user(id), role text NOT NULL CHECK(role IN ('advisor','merchant')),
      buyer_context uuid REFERENCES organization(id), authorization_hash text NOT NULL,
      state jsonb NOT NULL DEFAULT '{}', messages jsonb NOT NULL DEFAULT '[]',
      version integer NOT NULL DEFAULT 1, lease_id uuid, lease_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(id,organization_id,user_id)
    );
    CREATE TABLE agent_turn (
      id uuid PRIMARY KEY, conversation_id uuid NOT NULL, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      request_key text NOT NULL, request_hash text NOT NULL, message text NOT NULL,
      status text NOT NULL CHECK(status IN ('running','complete','interrupted')),
      events jsonb NOT NULL DEFAULT '[]', started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
      FOREIGN KEY(conversation_id,organization_id,user_id) REFERENCES agent_conversation(id,organization_id,user_id),
      UNIQUE(conversation_id,request_key)
    );
    CREATE INDEX conversations_owner ON agent_conversation(organization_id,user_id,updated_at DESC);
    CREATE FUNCTION warehouse_conversation_access(org uuid, actor uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
      SELECT warehouse_has_org(org) AND actor=warehouse_user_id()
        AND warehouse_has_role(ARRAY['supplier_admin','product_editor','inventory_manager','auditor','advisor','buyer_admin'])
    $$;
    """)
    for table in ("agent_conversation", "agent_turn"):
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        )
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT USING(warehouse_conversation_access(organization_id,user_id))"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK(warehouse_conversation_access(organization_id,user_id))"
        )
        op.execute(
            f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING(warehouse_conversation_access(organization_id,user_id)) WITH CHECK(warehouse_conversation_access(organization_id,user_id))"
        )


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting conversations")
