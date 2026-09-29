"""Withdraw previously published departures whose source route becomes unlinked."""

from alembic import op

revision = "0027_departure_quarantine"
down_revision = "0026_legacy_cases"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE departure ADD source_quarantined boolean NOT NULL DEFAULT false;
    -- A past successful scan may have isolated an unlinked source row while retaining
    -- its previously published departure. Repair those records from the latest
    -- successful observation, without inventing a replacement parent or inventory.
    WITH latest AS (
      SELECT DISTINCT ON (s.connection_id,s.external_id)
        s.connection_id,s.external_id,s.body,s.body_hash,s.observed_at,s.sync_run_id
      FROM source_snapshot s JOIN sync_run r ON r.id=s.sync_run_id
      WHERE s.entity_type='departure' AND r.status='published'
      ORDER BY s.connection_id,s.external_id,r.completed_at DESC,r.started_at DESC,r.id DESC
    )
    UPDATE departure d SET source_quarantined=true,source=s.body,source_hash=s.body_hash,
      observed_at=s.observed_at,sync_run_id=s.sync_run_id,
      availability_expires_at=LEAST(d.availability_expires_at,s.observed_at,now()),
      version=d.version+1
    FROM latest s WHERE d.connection_id=s.connection_id AND d.external_id=s.external_id
      AND s.body->>'routeId'='0';
    ALTER POLICY departure_read ON departure USING(
      warehouse_has_org(supplier_org_id) OR (status='published' AND NOT source_quarantined
        AND warehouse_catalog_access(supplier_org_id,connection_id))
    );
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than republishing quarantined departures")
