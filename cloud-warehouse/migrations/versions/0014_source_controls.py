"""Versioned sources and organization-scoped creation idempotency."""

from alembic import op

revision = "0014_source_controls"
down_revision = "0013_sync_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE supplier_connection ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK(version>0);
    ALTER TABLE supplier_connection ADD COLUMN creation_key uuid;
    ALTER TABLE supplier_connection ADD COLUMN creation_hash text;
    ALTER TABLE supplier_connection ADD UNIQUE(supplier_org_id,creation_key);
    ALTER TABLE supplier_connection ADD CHECK((creation_key IS NULL)=(creation_hash IS NULL));
    CREATE FUNCTION warehouse_source_version() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.id IS DISTINCT FROM OLD.id OR NEW.supplier_org_id IS DISTINCT FROM OLD.supplier_org_id
        OR NEW.creation_key IS DISTINCT FROM OLD.creation_key
        OR NEW.creation_hash IS DISTINCT FROM OLD.creation_hash THEN
        RAISE EXCEPTION 'SOURCE_IDENTITY_IMMUTABLE';
      END IF;
      NEW.version=OLD.version+1;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_version BEFORE UPDATE ON supplier_connection
      FOR EACH ROW EXECUTE FUNCTION warehouse_source_version();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting source history")
