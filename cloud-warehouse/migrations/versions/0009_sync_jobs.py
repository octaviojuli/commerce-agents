"""Persistent per-source synchronization schedules and fenced worker leases."""

from alembic import op

revision = "0009_sync_jobs"
down_revision = "0008_conversations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE sync_job (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
      connection_id uuid NOT NULL UNIQUE, worker_id uuid NOT NULL REFERENCES warehouse_user(id),
      interval_seconds integer NOT NULL CHECK(interval_seconds BETWEEN 60 AND 86400),
      max_attempts integer NOT NULL DEFAULT 5 CHECK(max_attempts BETWEEN 1 AND 10),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
      status text NOT NULL DEFAULT 'waiting' CHECK(status IN ('waiting','running','failed')),
      next_run_at timestamptz NOT NULL DEFAULT now(), lease_id uuid, lease_until timestamptz,
      last_error text, last_run_id uuid REFERENCES sync_run(id),
      completed_count integer NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY(connection_id,organization_id) REFERENCES supplier_connection(id,supplier_org_id),
      CHECK((status='running')=(lease_id IS NOT NULL AND lease_until IS NOT NULL))
    );
    CREATE INDEX sync_job_due ON sync_job(organization_id,worker_id,next_run_at)
      WHERE status IN ('waiting','running');
    ALTER TABLE sync_job ENABLE ROW LEVEL SECURITY;
    ALTER TABLE sync_job FORCE ROW LEVEL SECURITY;
    CREATE POLICY sync_job_read ON sync_job FOR SELECT USING(warehouse_has_org(organization_id));
    CREATE POLICY sync_job_insert ON sync_job FOR INSERT WITH CHECK(
      warehouse_has_org(organization_id) AND worker_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE POLICY sync_job_update ON sync_job FOR UPDATE USING(
      warehouse_has_org(organization_id) AND worker_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['sync_worker'])) WITH CHECK(
      warehouse_has_org(organization_id) AND worker_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['sync_worker']));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting synchronization history")
