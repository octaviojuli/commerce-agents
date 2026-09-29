"""Versioned approvals, managed stock balances and immutable stock movements."""

from alembic import op

revision = "0003_managed_inventory"
down_revision = "0002_source_issues"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE departure ADD UNIQUE(id,supplier_org_id);
        CREATE TABLE inventory_pool (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, departure_id uuid NOT NULL UNIQUE,
          total integer NOT NULL DEFAULT 0 CHECK(total>=0), sold integer NOT NULL DEFAULT 0 CHECK(sold>=0),
          held integer NOT NULL DEFAULT 0 CHECK(held=0), blocked integer NOT NULL DEFAULT 0 CHECK(blocked>=0),
          version integer NOT NULL DEFAULT 1 CHECK(version>0),
          CHECK(total>=sold+held+blocked), UNIQUE(id,supplier_org_id),
          FOREIGN KEY(departure_id,supplier_org_id) REFERENCES departure(id,supplier_org_id)
        );
        CREATE TABLE change_request (
          id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
          kind text NOT NULL, payload jsonb NOT NULL, payload_hash text NOT NULL,
          target_id uuid NOT NULL, expected_version integer NOT NULL CHECK(expected_version>=0),
          created_by uuid NOT NULL REFERENCES warehouse_user(id), created_at timestamptz NOT NULL DEFAULT now(),
          status text NOT NULL DEFAULT 'staged' CHECK(status IN ('staged','applied','discarded')),
          applied_by uuid REFERENCES warehouse_user(id), applied_at timestamptz, result jsonb,
          UNIQUE(id,organization_id)
        );
        CREATE TABLE change_approval (
          change_id uuid PRIMARY KEY, organization_id uuid NOT NULL,
          approved_by uuid NOT NULL REFERENCES warehouse_user(id),
          payload_hash text NOT NULL, expected_version integer NOT NULL,
          approved_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
          FOREIGN KEY(change_id,organization_id) REFERENCES change_request(id,organization_id),
          CHECK(expires_at>approved_at)
        );
        CREATE TABLE inventory_movement (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, pool_id uuid NOT NULL,
          change_id uuid NOT NULL UNIQUE, kind text NOT NULL CHECK(kind IN ('open','sale','adjust','block','reverse')),
          business_key text NOT NULL, delta_total integer NOT NULL DEFAULT 0,
          delta_sold integer NOT NULL DEFAULT 0, delta_blocked integer NOT NULL DEFAULT 0,
          reverses_id uuid UNIQUE REFERENCES inventory_movement(id),
          reason text NOT NULL, actor_id uuid NOT NULL REFERENCES warehouse_user(id),
          occurred_at timestamptz NOT NULL DEFAULT now(), UNIQUE(pool_id,business_key),
          FOREIGN KEY(pool_id,supplier_org_id) REFERENCES inventory_pool(id,supplier_org_id),
          FOREIGN KEY(change_id,supplier_org_id) REFERENCES change_request(id,organization_id)
        );
        CREATE INDEX changes_pending ON change_request(organization_id,created_at) WHERE status='staged';
        CREATE INDEX movements_pool ON inventory_movement(pool_id,occurred_at);
        CREATE FUNCTION warehouse_inventory_write(org uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT warehouse_has_org(org) AND warehouse_has_role(ARRAY['supplier_admin','inventory_manager'])
        $$;
        CREATE FUNCTION warehouse_inventory_authority() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS(SELECT 1 FROM departure d JOIN supplier_connection c ON c.id=d.connection_id
            WHERE d.id=NEW.departure_id AND c.active AND c.capabilities->>'inventory_owner'='warehouse') THEN
            RAISE EXCEPTION 'Managed inventory requires warehouse authority';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER inventory_authority BEFORE INSERT OR UPDATE ON inventory_pool
          FOR EACH ROW EXECUTE FUNCTION warehouse_inventory_authority();
        CREATE FUNCTION warehouse_change_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.id,NEW.organization_id,NEW.kind,NEW.payload,NEW.payload_hash,NEW.target_id,NEW.expected_version,NEW.created_by,NEW.created_at)
             IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.kind,OLD.payload,OLD.payload_hash,OLD.target_id,OLD.expected_version,OLD.created_by,OLD.created_at)
             OR OLD.status <> 'staged' THEN
            RAISE EXCEPTION 'Change contents and finalized changes are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER change_immutable BEFORE UPDATE ON change_request
          FOR EACH ROW EXECUTE FUNCTION warehouse_change_immutable();
        ALTER TABLE inventory_pool ENABLE ROW LEVEL SECURITY;
        ALTER TABLE inventory_pool FORCE ROW LEVEL SECURITY;
        CREATE POLICY inventory_read ON inventory_pool FOR SELECT USING(warehouse_has_org(supplier_org_id) OR EXISTS(
          SELECT 1 FROM departure d JOIN supplier_product p ON p.id=d.product_id
          WHERE d.id=inventory_pool.departure_id AND d.status='published' AND p.status='published'
          AND warehouse_catalog_access(d.supplier_org_id,d.connection_id)));
        CREATE POLICY inventory_insert ON inventory_pool FOR INSERT WITH CHECK(warehouse_inventory_write(supplier_org_id));
        CREATE POLICY inventory_update ON inventory_pool FOR UPDATE USING(warehouse_inventory_write(supplier_org_id)) WITH CHECK(warehouse_inventory_write(supplier_org_id));
    """)
    for table, col in (
        ("change_request", "organization_id"),
        ("change_approval", "organization_id"),
        ("inventory_movement", "supplier_org_id"),
    ):
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
        )
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT USING(warehouse_has_org({col}))"
        )
        expression = (
            f"warehouse_inventory_write({col})"
            if table != "change_approval"
            else f"warehouse_has_org({col}) AND warehouse_has_role(ARRAY['supplier_admin'])"
        )
        op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK({expression})")
        if table == "change_request":
            op.execute(
                f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING({expression}) WITH CHECK({expression})"
            )
    # Every authenticated organization may append its own audit trail, never rewrite it.
    op.execute(
        "DROP POLICY audit_event_insert ON audit_event; CREATE POLICY audit_event_insert ON audit_event FOR INSERT WITH CHECK(warehouse_has_org(organization_id) AND actor_id=warehouse_user_id())"
    )
    op.execute(
        "DROP POLICY outbox_event_insert ON outbox_event; CREATE POLICY outbox_event_insert ON outbox_event FOR INSERT WITH CHECK(warehouse_catalog_write(organization_id) OR warehouse_inventory_write(organization_id))"
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE inventory_movement; DROP TABLE change_approval; DROP TABLE change_request; DROP TABLE inventory_pool"
    )
    op.execute(
        "DROP FUNCTION warehouse_change_immutable(); DROP FUNCTION warehouse_inventory_authority()"
    )
    op.execute(
        "DROP POLICY outbox_event_insert ON outbox_event; CREATE POLICY outbox_event_insert ON outbox_event FOR INSERT WITH CHECK(warehouse_catalog_write(organization_id))"
    )
    op.execute("DROP FUNCTION warehouse_inventory_write(uuid)")
    op.execute(
        "DROP POLICY audit_event_insert ON audit_event; CREATE POLICY audit_event_insert ON audit_event FOR INSERT WITH CHECK(warehouse_catalog_write(organization_id))"
    )
    op.execute("ALTER TABLE departure DROP CONSTRAINT departure_id_supplier_org_id_key")
