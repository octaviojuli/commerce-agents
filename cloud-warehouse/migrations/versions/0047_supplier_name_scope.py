"""Supplier names follow the same live connections as the advisor catalog."""

from alembic import op

revision = "0047_supplier_name_scope"
down_revision = "0046_supplier_short_name"
branch_labels = depends_on = None


def upgrade():
    # Replace the function so databases already at 0046 are repaired too. Existing
    # role grants remain attached; organization itself is never granted to runtime.
    op.execute("""
    CREATE OR REPLACE FUNCTION public.warehouse_supplier_name(supplier uuid) RETURNS text
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
      SELECT coalesce(o.short_name, o.name) FROM public.organization o
      WHERE o.id = supplier AND (public.warehouse_has_org(supplier) OR EXISTS (
        SELECT 1 FROM public.supplier_connection c
        WHERE c.supplier_org_id = supplier
          AND c.id IN (SELECT public.warehouse_buyer_connections())))
    $$;
    REVOKE ALL ON FUNCTION public.warehouse_supplier_name(uuid) FROM PUBLIC;
    """)


def downgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION public.warehouse_supplier_name(supplier uuid) RETURNS text
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
      SELECT coalesce(o.short_name, o.name) FROM public.organization o
      WHERE o.id = supplier AND (public.warehouse_has_org(supplier) OR EXISTS (
        SELECT 1 FROM public.distribution_grant g
        WHERE g.supplier_org_id = supplier AND public.warehouse_has_org(g.buyer_org_id) AND g.active
          AND g.valid_from <= now() AND (g.expires_at IS NULL OR g.expires_at > now())))
    $$;
    """)
