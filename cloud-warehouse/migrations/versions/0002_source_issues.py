"""Preserve unlinked upstream plans for supplier review without fabricating products."""

from alembic import op

revision = "0002_source_issues"
down_revision = "0001_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE sync_run ADD source_departure_count integer NOT NULL DEFAULT 0;
        ALTER TABLE sync_run ADD quarantined_count integer NOT NULL DEFAULT 0;
        CREATE TABLE source_issue (
          id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
          sync_run_id uuid NOT NULL REFERENCES sync_run(id), external_id text NOT NULL,
          code text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE(sync_run_id,external_id,code),
          FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id)
        );
        ALTER TABLE source_issue ENABLE ROW LEVEL SECURITY;
        ALTER TABLE source_issue FORCE ROW LEVEL SECURITY;
        CREATE POLICY issue_read ON source_issue FOR SELECT USING(warehouse_has_org(supplier_org_id));
        CREATE POLICY issue_insert ON source_issue FOR INSERT WITH CHECK(warehouse_catalog_write(supplier_org_id));
        CREATE INDEX issue_connection_run ON source_issue(connection_id,sync_run_id);
    """)


def downgrade() -> None:
    op.execute(
        "DROP TABLE source_issue; ALTER TABLE sync_run DROP source_departure_count; ALTER TABLE sync_run DROP quarantined_count"
    )
