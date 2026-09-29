"""Reuse authorization execution plans while reading live permissions each statement."""

from alembic import op

revision = "0031_authorization_plans"
down_revision = "0030_pricing_scope"
branch_labels = None
depends_on = None

# Fixed, parameterized reads only. CREATE OR REPLACE retains the existing ACLs.
FUNCTIONS = (
    (
        "warehouse_has_org(org uuid)",
        "boolean",
        "SECURITY DEFINER SET search_path=public,pg_temp",
        """SELECT org=public.warehouse_org_id() AND EXISTS (
          SELECT 1 FROM public.membership m JOIN public.warehouse_user u ON u.id=m.user_id
          JOIN public.organization o ON o.id=m.organization_id
          WHERE m.user_id=public.warehouse_user_id() AND m.organization_id=org
            AND m.active AND u.active AND o.active
        )""",
    ),
    (
        "warehouse_has_role(allowed text[])",
        "boolean",
        "SECURITY DEFINER SET search_path=public,pg_temp",
        """SELECT public.warehouse_has_org(public.warehouse_org_id()) AND EXISTS (
          SELECT 1 FROM public.membership WHERE user_id=public.warehouse_user_id()
            AND organization_id=public.warehouse_org_id() AND active AND roles && allowed
        )""",
    ),
    (
        "warehouse_buyer_connections()",
        "SETOF uuid",
        "SECURITY DEFINER SET search_path=public,pg_temp",
        """SELECT c.id FROM public.supplier_connection c
          JOIN public.distribution_grant g
            ON g.connection_id=c.id AND g.supplier_org_id=c.supplier_org_id
          JOIN public.organization o ON o.id=c.supplier_org_id
          WHERE c.active AND o.active AND g.active AND g.buyer_org_id=public.warehouse_org_id()
            AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
            AND (SELECT public.warehouse_has_org(public.warehouse_org_id()))""",
    ),
    (
        "warehouse_live_grant(grant_uuid uuid)",
        "boolean",
        "SECURITY INVOKER",
        """SELECT EXISTS (
          SELECT 1 FROM public.distribution_grant g WHERE g.id=grant_uuid AND g.active
            AND g.valid_from<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
            AND (
              (g.supplier_org_id=public.warehouse_org_id()
                AND (SELECT public.warehouse_has_org(public.warehouse_org_id())))
              OR (g.buyer_org_id=public.warehouse_org_id()
                AND g.connection_id IN (SELECT public.warehouse_buyer_connections()))
            )
        )""",
    ),
)


def upgrade():
    for signature, result, security, query in FUNCTIONS:
        statement = f"RETURN QUERY {query};" if result.startswith("SETOF") else f"RETURN ({query});"
        op.execute(f"""CREATE OR REPLACE FUNCTION public.{signature} RETURNS {result}
          LANGUAGE plpgsql STABLE {security} AS $$ BEGIN {statement} END $$;""")


def downgrade():
    for signature, result, security, query in FUNCTIONS:
        op.execute(f"""CREATE OR REPLACE FUNCTION public.{signature} RETURNS {result}
          LANGUAGE sql STABLE {security} AS $$ {query} $$;""")
