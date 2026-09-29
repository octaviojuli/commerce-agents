"""Supplier-directed cooldowns survive worker restarts and human queue controls."""

from alembic import op

revision = "0016_sync_cooldown"
down_revision = "0015_distribution_invitations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE sync_job ADD COLUMN provider_not_before timestamptz;
    CREATE FUNCTION warehouse_sync_cooldown_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
    BEGIN
      IF OLD.provider_not_before>clock_timestamp() THEN
        NEW.provider_not_before:=greatest(OLD.provider_not_before,NEW.provider_not_before);
      END IF;
      IF NEW.provider_not_before>clock_timestamp() THEN
        IF NEW.status='running' THEN RAISE EXCEPTION 'SYNC_SOURCE_COOLDOWN'; END IF;
        NEW.next_run_at:=greatest(NEW.next_run_at,NEW.provider_not_before);
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER sync_cooldown_guard BEFORE UPDATE ON sync_job
      FOR EACH ROW EXECUTE FUNCTION warehouse_sync_cooldown_guard();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than removing supplier cooldowns")
