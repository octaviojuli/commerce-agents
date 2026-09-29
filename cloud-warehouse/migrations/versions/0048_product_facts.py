"""Warehouse-decided days and gateway beside the upstream values, which are never rewritten."""

from alembic import op
from sqlalchemy import text

revision = "0048_product_facts"
down_revision = "0047_supplier_name_scope"
branch_labels = depends_on = None

ACTIVE = "{f}_override IS NOT NULL AND {f}_source_at_override IS NOT DISTINCT FROM {f}"


def upgrade():
    op.execute("""
    ALTER TABLE supplier_product ADD days_override integer CHECK(days_override BETWEEN 1 AND 100);
    ALTER TABLE supplier_product ADD days_source_at_override integer;
    ALTER TABLE supplier_product ADD gateway_override text CHECK(gateway_override IS NULL OR length(btrim(gateway_override)) BETWEEN 1 AND 100);
    ALTER TABLE supplier_product ADD gateway_source_at_override text;
    ALTER TABLE supplier_product ADD fact_decisions jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(fact_decisions)='object');
    ALTER TABLE supplier_product ADD facts_version integer NOT NULL DEFAULT 0 CHECK(facts_version>=0);
    ALTER TABLE supplier_product ADD CHECK((days_override IS NULL)=(fact_decisions->'days' IS NULL) AND (gateway_override IS NULL)=(fact_decisions->'gateway' IS NULL));
    ALTER TABLE route_content_revision ADD fact_decisions jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(fact_decisions)='array');
    """)
    # A decision applies only while the source still says what it said when it was made;
    # a later source change falls back to the source and reopens the question.
    active_days, active_gateway = ACTIVE.format(f="days"), ACTIVE.format(f="gateway")
    definition = (
        op.get_bind()
        .execute(text("SELECT pg_get_viewdef('product_listing'::regclass, true)"))
        .scalar_one()
        .strip()
        .rstrip(";")
    )
    head, tail = definition.rsplit("FROM supplier_product p", 1)
    op.execute(f"""CREATE OR REPLACE VIEW product_listing WITH (security_invoker=true) AS {head},
      CASE WHEN {active_days} THEN days_override ELSE days END AS effective_days,
      CASE WHEN {active_gateway} THEN gateway_override ELSE gateway END AS effective_gateway,
      CASE WHEN {active_days} THEN 'warehouse_decision' ELSE 'source' END AS days_origin,
      CASE WHEN {active_gateway} THEN 'warehouse_decision' ELSE 'source' END AS gateway_origin,
      facts_version FROM supplier_product p{tail}""")


def downgrade():
    raise RuntimeError("Restore a verified backup to retain warehouse decisions and provenance")
