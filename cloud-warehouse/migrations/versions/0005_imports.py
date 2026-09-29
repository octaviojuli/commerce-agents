"""Private import files and validated batches; one approved import may affect several pools."""

from alembic import op

revision = "0005_imports"
down_revision = "0004_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inventory_movement DROP CONSTRAINT inventory_movement_change_id_key;
        ALTER TABLE inventory_movement ADD UNIQUE(change_id,pool_id);
        CREATE TABLE import_batch (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
          file_hash text NOT NULL, file_name text NOT NULL, file_body bytea NOT NULL,
          rows jsonb NOT NULL, errors jsonb NOT NULL, version integer NOT NULL DEFAULT 1,
          status text NOT NULL CHECK(status IN ('invalid','validated','published')),
          created_by uuid NOT NULL REFERENCES warehouse_user(id), created_at timestamptz NOT NULL DEFAULT now(),
          published_change_id uuid REFERENCES change_request(id),
          UNIQUE(connection_id,file_hash),
          FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id),
          CHECK(octet_length(file_body)<=10485760)
        );
        ALTER TABLE import_batch ENABLE ROW LEVEL SECURITY;
        ALTER TABLE import_batch FORCE ROW LEVEL SECURITY;
        CREATE POLICY import_read ON import_batch FOR SELECT USING(warehouse_has_org(supplier_org_id));
        CREATE POLICY import_insert ON import_batch FOR INSERT WITH CHECK(warehouse_inventory_write(supplier_org_id));
        CREATE POLICY import_update ON import_batch FOR UPDATE USING(warehouse_inventory_write(supplier_org_id)) WITH CHECK(warehouse_inventory_write(supplier_org_id));
        CREATE FUNCTION warehouse_import_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.id,NEW.supplier_org_id,NEW.connection_id,NEW.file_hash,NEW.file_name,NEW.file_body,NEW.rows,NEW.errors,NEW.created_by,NEW.created_at)
             IS DISTINCT FROM ROW(OLD.id,OLD.supplier_org_id,OLD.connection_id,OLD.file_hash,OLD.file_name,OLD.file_body,OLD.rows,OLD.errors,OLD.created_by,OLD.created_at)
             OR OLD.status='published' THEN
            RAISE EXCEPTION 'Import source and published batch are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER import_immutable BEFORE UPDATE ON import_batch
          FOR EACH ROW EXECUTE FUNCTION warehouse_import_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TABLE import_batch; DROP FUNCTION warehouse_import_immutable()")
    # A destructive downgrade would fail once one import has movements for multiple pools.
    op.execute(
        "ALTER TABLE inventory_movement DROP CONSTRAINT inventory_movement_change_id_pool_id_key; ALTER TABLE inventory_movement ADD UNIQUE(change_id)"
    )
