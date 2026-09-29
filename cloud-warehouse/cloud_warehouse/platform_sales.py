"""Explicit platform catalog policy and independent sales-account provisioning."""

from uuid import uuid4

from sqlalchemy import text

from .auth import password_hash
from .persistence import Principal, require_role, transaction


def version(conn):
    return conn.scalar(text("SELECT warehouse_sales_version()"))


def context(engine, actor):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        policy = version(conn)
        return {"mode": "platform" if policy else "organization", "version": policy}


def configure(admin, *, enabled: bool):
    """Offline admin only; neither runtime nor authentication role can change policy."""
    with admin.begin() as conn:
        conn.execute(
            text("""UPDATE platform_sales_policy SET enabled=:enabled,version=version+1
              WHERE singleton AND enabled IS DISTINCT FROM :enabled"""),
            {"enabled": enabled},
        )
        return dict(
            conn.execute(text("SELECT enabled,version FROM platform_sales_policy")).mappings().one()
        )


def create_advisor(admin, *, email: str, password: str):
    """A private technical workspace is not an agency or a supplier distribution grant."""
    user_id, workspace = uuid4(), uuid4()
    encoded = password_hash(password)
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO warehouse_user(id,email,password_hash) VALUES(:id,:email,:password)"),
            {"id": user_id, "email": email.strip().lower(), "password": encoded},
        )
        conn.execute(
            text(
                "INSERT INTO organization(id,name,kinds) VALUES(:id,'个人销售工作空间',ARRAY['buyer'])"
            ),
            {"id": workspace},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:user,:workspace,ARRAY['advisor'])"
            ),
            {"user": user_id, "workspace": workspace},
        )
    return Principal(user_id, workspace)
