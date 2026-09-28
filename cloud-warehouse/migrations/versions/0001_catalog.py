"""Organization-scoped catalog and atomic supplier synchronization.

Revision ID: 0001_catalog
"""

from alembic import op

revision = "0001_catalog"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = """
CREATE TABLE organization (
  id uuid PRIMARY KEY, name text NOT NULL, kinds text[] NOT NULL,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (cardinality(kinds)>0 AND kinds <@ ARRAY['supplier','buyer','platform'])
);
CREATE TABLE warehouse_user (
  id uuid PRIMARY KEY, email text NOT NULL UNIQUE, password_hash text,
  active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE membership (
  user_id uuid NOT NULL REFERENCES warehouse_user(id),
  organization_id uuid NOT NULL REFERENCES organization(id),
  roles text[] NOT NULL, active boolean NOT NULL DEFAULT true,
  PRIMARY KEY(user_id, organization_id),
  CHECK(cardinality(roles)>0 AND roles <@ ARRAY['supplier_admin','product_editor','inventory_manager','buyer_admin','advisor','platform_admin','auditor','sync_worker'])
);
CREATE FUNCTION warehouse_user_id() RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT nullif(current_setting('warehouse.user_id',true),'')::uuid
$$;
CREATE FUNCTION warehouse_org_id() RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT nullif(current_setting('warehouse.organization_id',true),'')::uuid
$$;
CREATE FUNCTION warehouse_has_org(org uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT org = warehouse_org_id() AND EXISTS (
   SELECT 1 FROM membership m JOIN warehouse_user u ON u.id=m.user_id
   JOIN organization o ON o.id=m.organization_id
   WHERE m.user_id=warehouse_user_id() AND m.organization_id=org AND m.active AND u.active AND o.active)
$$;
CREATE FUNCTION warehouse_has_role(allowed text[]) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT warehouse_has_org(warehouse_org_id()) AND EXISTS (
   SELECT 1 FROM membership WHERE user_id=warehouse_user_id()
   AND organization_id=warehouse_org_id() AND active AND roles && allowed)
$$;
CREATE TABLE supplier_connection (
  id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL REFERENCES organization(id),
  name text NOT NULL, connector_type text NOT NULL, credential_ref text NOT NULL,
  capabilities jsonb NOT NULL DEFAULT '{}', active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id, supplier_org_id)
);
CREATE TABLE distribution_grant (
  id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL REFERENCES organization(id),
  buyer_org_id uuid NOT NULL REFERENCES organization(id),
  connection_id uuid NOT NULL,
  active boolean NOT NULL DEFAULT true, valid_from timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
  UNIQUE(connection_id,buyer_org_id), FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id),
  CHECK(supplier_org_id<>buyer_org_id), CHECK(expires_at IS NULL OR expires_at>valid_from)
);
CREATE TABLE sync_run (
  id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
  status text NOT NULL CHECK(status IN ('running','published','failed')),
  started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
  window_start date, window_end date, product_count integer NOT NULL DEFAULT 0,
  departure_count integer NOT NULL DEFAULT 0, source_hash text, error_code text,
  actor_id uuid NOT NULL REFERENCES warehouse_user(id),
  FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id)
);
CREATE TABLE supplier_product (
  id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
  external_id text NOT NULL, code text NOT NULL, name text NOT NULL,
  days integer, gateway text, source jsonb NOT NULL, source_hash text NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','paused','archived')),
  version integer NOT NULL DEFAULT 1 CHECK(version>0), observed_at timestamptz NOT NULL,
  sync_run_id uuid NOT NULL REFERENCES sync_run(id),
  UNIQUE(connection_id,external_id), UNIQUE(id,supplier_org_id,connection_id),
  FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id),
  CHECK(days IS NULL OR days>0)
);
CREATE TABLE departure (
  id uuid PRIMARY KEY, product_id uuid NOT NULL, supplier_org_id uuid NOT NULL,
  connection_id uuid NOT NULL, external_id text NOT NULL, code text NOT NULL,
  depart_date date NOT NULL, return_date date NOT NULL, company_id text,
  available_seats integer, source jsonb NOT NULL, source_hash text NOT NULL,
  status text NOT NULL DEFAULT 'published' CHECK(status IN ('published','paused','archived')),
  version integer NOT NULL DEFAULT 1 CHECK(version>0),
  observed_at timestamptz NOT NULL, availability_expires_at timestamptz NOT NULL,
  sync_run_id uuid NOT NULL REFERENCES sync_run(id),
  UNIQUE(connection_id,external_id),
  FOREIGN KEY(product_id,supplier_org_id,connection_id) REFERENCES supplier_product(id,supplier_org_id,connection_id),
  CHECK(return_date>=depart_date), CHECK(available_seats IS NULL OR available_seats>=0)
);
CREATE TABLE source_snapshot (
  id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
  sync_run_id uuid NOT NULL REFERENCES sync_run(id), entity_type text NOT NULL,
  external_id text NOT NULL, body jsonb NOT NULL, body_hash text NOT NULL,
  observed_at timestamptz NOT NULL, UNIQUE(sync_run_id,entity_type,external_id),
  FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id)
);
CREATE TABLE audit_event (
  id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
  actor_id uuid NOT NULL REFERENCES warehouse_user(id), action text NOT NULL,
  resource_id uuid NOT NULL, details jsonb NOT NULL DEFAULT '{}', occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox_event (
  id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
  kind text NOT NULL, resource_id uuid NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), processed_at timestamptz,
  UNIQUE(kind,resource_id)
);
CREATE INDEX product_owner_status ON supplier_product(supplier_org_id,status,id);
CREATE INDEX departure_product_date ON departure(product_id,depart_date,id);
CREATE INDEX departure_connection_date ON departure(connection_id,depart_date,id);
CREATE INDEX grant_buyer ON distribution_grant(buyer_org_id,connection_id) WHERE active;
CREATE INDEX sync_connection ON sync_run(connection_id,started_at DESC);
CREATE INDEX outbox_pending ON outbox_event(created_at) WHERE processed_at IS NULL;

-- Identity tables are accessible only through the migration/authentication role for now.
-- Runtime privileges and policies are explicit: no blanket grant on future tables.
ALTER TABLE supplier_connection ENABLE ROW LEVEL SECURITY;
ALTER TABLE supplier_connection FORCE ROW LEVEL SECURITY;
CREATE POLICY connection_owner_read ON supplier_connection FOR SELECT USING(warehouse_has_org(supplier_org_id));
CREATE POLICY connection_insert ON supplier_connection FOR INSERT WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']));
CREATE POLICY connection_update ON supplier_connection FOR UPDATE USING(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin'])) WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']));
ALTER TABLE distribution_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE distribution_grant FORCE ROW LEVEL SECURITY;
CREATE POLICY grant_read ON distribution_grant FOR SELECT USING(warehouse_has_org(supplier_org_id) OR warehouse_has_org(buyer_org_id));
CREATE POLICY grant_insert ON distribution_grant FOR INSERT WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']));
CREATE POLICY grant_update ON distribution_grant FOR UPDATE USING(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin'])) WITH CHECK(warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin']));
CREATE POLICY connection_buyer_read ON supplier_connection FOR SELECT USING(active AND EXISTS(
 SELECT 1 FROM distribution_grant g WHERE g.connection_id=supplier_connection.id
 AND warehouse_has_org(g.buyer_org_id) AND g.active AND g.valid_from<=now()
 AND (g.expires_at IS NULL OR g.expires_at>now())));
CREATE FUNCTION warehouse_org_active(org uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM organization WHERE id=org AND active)
$$;
CREATE FUNCTION warehouse_catalog_access(owner_id uuid, conn_id uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT warehouse_has_org(owner_id) OR (warehouse_org_active(owner_id) AND EXISTS(
   SELECT 1 FROM supplier_connection c WHERE c.id=conn_id AND c.active) AND EXISTS(
   SELECT 1 FROM distribution_grant g WHERE g.connection_id=conn_id AND g.supplier_org_id=owner_id
   AND g.buyer_org_id=warehouse_org_id() AND warehouse_has_org(g.buyer_org_id)
   AND g.active AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())))
$$;
CREATE FUNCTION warehouse_catalog_write(owner_id uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT warehouse_has_org(owner_id) AND warehouse_has_role(ARRAY['supplier_admin','sync_worker','product_editor'])
$$;
"""


def upgrade() -> None:
    op.execute(SCHEMA)
    for table in ("supplier_product", "departure"):
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        )
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT USING(warehouse_has_org(supplier_org_id) OR (status='published' AND warehouse_catalog_access(supplier_org_id,connection_id)))"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK(warehouse_catalog_write(supplier_org_id))"
        )
        op.execute(
            f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING(warehouse_catalog_write(supplier_org_id)) WITH CHECK(warehouse_catalog_write(supplier_org_id))"
        )
    for table, column in (
        ("sync_run", "supplier_org_id"),
        ("source_snapshot", "supplier_org_id"),
        ("audit_event", "organization_id"),
        ("outbox_event", "organization_id"),
    ):
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        )
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT USING(warehouse_has_org({column}))"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK(warehouse_catalog_write({column}))"
        )
        if table in ("sync_run", "outbox_event"):
            op.execute(
                f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING(warehouse_catalog_write({column})) WITH CHECK(warehouse_catalog_write({column}))"
            )


def downgrade() -> None:
    for table in (
        "outbox_event",
        "audit_event",
        "source_snapshot",
        "departure",
        "supplier_product",
        "sync_run",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute(
        "DROP FUNCTION warehouse_catalog_write(uuid); DROP FUNCTION warehouse_catalog_access(uuid,uuid)"
    )
    op.execute("DROP TABLE distribution_grant; DROP TABLE supplier_connection")
    op.execute("DROP FUNCTION warehouse_org_active(uuid)")
    op.execute("DROP FUNCTION warehouse_has_role(text[]); DROP FUNCTION warehouse_has_org(uuid)")
    op.execute("DROP FUNCTION warehouse_org_id(); DROP FUNCTION warehouse_user_id()")
    op.execute("DROP TABLE membership; DROP TABLE warehouse_user; DROP TABLE organization")
