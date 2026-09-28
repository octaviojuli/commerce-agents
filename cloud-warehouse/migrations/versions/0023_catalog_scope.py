"""Evaluate live buyer connection grants once per catalog statement."""

from alembic import op

revision = "0023_catalog_scope"
down_revision = "0022_search_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION warehouse_buyer_connections() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
      SELECT c.id FROM supplier_connection c
      JOIN distribution_grant g ON g.connection_id=c.id AND g.supplier_org_id=c.supplier_org_id
      JOIN organization o ON o.id=c.supplier_org_id
      WHERE c.active AND o.active AND g.active AND g.buyer_org_id=warehouse_org_id()
        AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
        AND (SELECT warehouse_has_org(warehouse_org_id()))
    $$;
    REVOKE ALL ON FUNCTION warehouse_buyer_connections() FROM PUBLIC;
    """)
    for table in ("supplier_product", "departure"):
        op.execute(f"DROP POLICY {table}_read ON {table}")
        op.execute(f"""CREATE POLICY {table}_read ON {table} FOR SELECT USING (
          (supplier_org_id=warehouse_org_id() AND (SELECT warehouse_has_org(warehouse_org_id())))
          OR (status='published' AND connection_id IN (SELECT warehouse_buyer_connections()))
        )""")


def downgrade():
    for table in ("supplier_product", "departure"):
        op.execute(f"DROP POLICY {table}_read ON {table}")
        op.execute(f"""CREATE POLICY {table}_read ON {table} FOR SELECT USING (
          warehouse_has_org(supplier_org_id) OR
          (status='published' AND warehouse_catalog_access(supplier_org_id,connection_id))
        )""")
    op.execute("DROP FUNCTION warehouse_buyer_connections()")
