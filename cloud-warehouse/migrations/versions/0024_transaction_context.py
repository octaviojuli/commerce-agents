"""Initialize and validate transaction-local identity in one database round trip."""

from alembic import op

revision = "0024_transaction_context"
down_revision = "0023_catalog_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION warehouse_begin_context(target_user uuid, target_organization uuid)
    RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY INVOKER AS $$
    BEGIN
      PERFORM pg_catalog.set_config('warehouse.user_id',target_user::text,true);
      PERFORM pg_catalog.set_config('warehouse.organization_id',target_organization::text,true);
      RETURN public.warehouse_has_org(target_organization);
    END $$;
    REVOKE ALL ON FUNCTION warehouse_begin_context(uuid,uuid) FROM PUBLIC;
    """)
    # No function-level SET clause: these GUCs must live until transaction end.
    # Every called function is schema-qualified; no elevated execution privileges.


def downgrade():
    op.execute("DROP FUNCTION warehouse_begin_context(uuid,uuid)")
