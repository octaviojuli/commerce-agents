"""An organisation's short name, the one advisors use when they talk about a supplier."""

from alembic import op

revision = "0046_supplier_short_name"
down_revision = "0045_advisor_v3"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE organization ADD COLUMN short_name text
      CHECK (short_name IS NULL OR char_length(btrim(short_name)) BETWEEN 1 AND 12);
    -- A supplier's name is readable only by the supplier and by buyers it grants a catalog to;
    -- the organization table itself stays closed, so there is no directory to browse.
    CREATE FUNCTION warehouse_supplier_name(supplier uuid) RETURNS text
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
      SELECT coalesce(o.short_name, o.name) FROM organization o
      WHERE o.id = supplier AND (warehouse_has_org(supplier) OR EXISTS(
        SELECT 1 FROM distribution_grant g
        WHERE g.supplier_org_id = supplier AND warehouse_has_org(g.buyer_org_id) AND g.active
          AND g.valid_from <= now() AND (g.expires_at IS NULL OR g.expires_at > now())))
    $$;
    """)


def downgrade():
    op.execute("DROP FUNCTION warehouse_supplier_name(uuid)")
    op.execute("ALTER TABLE organization DROP COLUMN short_name")
