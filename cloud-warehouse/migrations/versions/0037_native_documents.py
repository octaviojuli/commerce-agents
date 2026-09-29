"""Retire external extraction jobs without changing historical parse evidence."""

from alembic import op

revision = "0037_native_documents"
down_revision = "0036_platform_sales"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    UPDATE document_asset a SET status='failed',last_error='DOCUMENT_EXTRACTION_RETIRED',
      lease_id=NULL,lease_until=NULL
    WHERE a.status IN ('queued','parsing') AND EXISTS (
      SELECT 1 FROM document_extraction_job j
      WHERE j.asset_id=a.id AND j.generation=a.parse_generation);
    DROP POLICY IF EXISTS document_extraction_job_insert ON document_extraction_job;
    DROP POLICY IF EXISTS document_extraction_job_update ON document_extraction_job;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; extraction evidence is retained")
