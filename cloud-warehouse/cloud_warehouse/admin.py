"""Offline schema migration, role provisioning and explicit platform onboarding."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text


def migrate(url: str, revision: str = "head") -> None:
    local = Path(__file__).resolve().parent.parent / "migrations"
    packaged = Path(__file__).resolve().parent / "migrations"
    config = Config()
    config.set_main_option("script_location", str(local if local.exists() else packaged))
    config.attributes["database_url"] = url
    command.upgrade(config, revision)


def grant_runtime(engine: Engine, role: str) -> None:
    """Explicit grants; identity credentials are never readable by the service role."""
    from .invitations import PUBLIC_COLUMNS

    with engine.begin() as conn:
        quoted = conn.dialect.identifier_preparer.quote(role)
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {quoted}"))
        conn.execute(text(f"GRANT SELECT ON product_listing TO {quoted}"))
        conn.execute(text(f"GRANT SELECT ON legacy_case_archive TO {quoted}"))
        conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        for table in (
            "distribution_grant",
            "sync_run",
            "sync_job",
            "supplier_product",
            "departure",
            "outbox_event",
            "product_search",
            "outbox_receipt",
            "search_worker_state",
            "inventory_pool",
            "change_request",
            "import_batch",
            "offer",
            "buyer_customer_binding",
            "contract_price",
            "buyer_price_grade",
            "agent_conversation",
            "conversation_brief",
            "agent_turn",
        ):
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {quoted}"))
        conn.execute(
            text(
                f"GRANT SELECT,INSERT,UPDATE(body,version,updated_at) ON advisor_customer TO {quoted}"
            )
        )
        conn.execute(text(f"GRANT UPDATE(customer_id,title) ON advisor_deal TO {quoted}"))
        conn.execute(text(f"GRANT UPDATE(retain_until) ON advisor_asset TO {quoted}"))
        conn.execute(text(f"GRANT SELECT,INSERT ON supplier_inquiry_ack TO {quoted}"))
        for table in (
            "advisor_deal",
            "advisor_record",
            "advisor_asset",
            "supplier_inquiry",
            "supplier_inquiry_reply",
        ):
            conn.execute(text(f"GRANT SELECT,INSERT ON {table} TO {quoted}"))
        for table in (
            "source_snapshot",
            "source_issue",
            "audit_event",
            "inventory_movement",
            "change_approval",
            "customer_verification",
            "binding_acceptance",
            "quote_snapshot",
            "product_revision",
            "document_asset",
            "document_parse",
            "document_media",
            "document_publication",
            "route_content_revision",
            "document_fetch",
            "document_fetch_observation",
        ):
            conn.execute(text(f"GRANT SELECT, INSERT ON {table} TO {quoted}"))
        conn.execute(
            text(
                f"GRANT UPDATE(status,attempts,lease_id,lease_until,next_attempt_at,last_error,asset_id,etag,last_modified,completed_at) ON document_fetch TO {quoted}"
            )
        )
        conn.execute(
            text(
                f"GRANT UPDATE(status,attempts,lease_id,lease_until,last_error,parse_generation) ON document_asset TO {quoted}"
            )
        )
        conn.execute(text(f"REVOKE INSERT, UPDATE ON document_extraction_job FROM {quoted}"))
        conn.execute(
            text(f"REVOKE UPDATE(payload,not_before) ON document_extraction_job FROM {quoted}")
        )
        conn.execute(text(f"GRANT SELECT ON document_extraction_job TO {quoted}"))
        conn.execute(text(f"GRANT SELECT,INSERT ON route_content_candidate TO {quoted}"))
        conn.execute(
            text(
                f"GRANT UPDATE(body,reports,next_day,status,attempts,lease_id,lease_until,completed_at) ON route_content_candidate TO {quoted}"
            )
        )
        conn.execute(
            text(
                f"GRANT SELECT(id,supplier_org_id,name,connector_type,capabilities,active,created_at,version,creation_key,creation_hash), INSERT, UPDATE ON supplier_connection TO {quoted}"
            )
        )
        conn.execute(text("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC"))
        conn.execute(text(f"GRANT SELECT({PUBLIC_COLUMNS}) ON distribution_invitation TO {quoted}"))
        conn.execute(text(f"GRANT SELECT,INSERT ON quote_share TO {quoted}"))
        conn.execute(text(f"GRANT SELECT,INSERT ON advisor_plan_share TO {quoted}"))
        conn.execute(text(f"GRANT UPDATE(revoked_at) ON advisor_plan_share TO {quoted}"))
        conn.execute(
            text(
                f"GRANT EXECUTE ON FUNCTION warehouse_applicable_prices(uuid,uuid), warehouse_effective_price(uuid,uuid,timestamptz) TO {quoted}"
            )
        )
        conn.execute(text(f"GRANT UPDATE(revoked_at) ON quote_share TO {quoted}"))
        for signature in (
            "warehouse_user_id()",
            "warehouse_advisor_owner(uuid,uuid)",
            "warehouse_org_id()",
            "warehouse_has_org(uuid)",
            "warehouse_begin_context(uuid,uuid)",
            "warehouse_source_cooldown(uuid,bigint)",
            "warehouse_has_role(text[])",
            "warehouse_org_active(uuid)",
            "warehouse_supplier_name(uuid)",
            "warehouse_catalog_access(uuid,uuid)",
            "warehouse_buyer_connections()",
            "warehouse_sales_version()",
            "warehouse_catalog_write(uuid)",
            "warehouse_inventory_write(uuid)",
            "warehouse_live_grant(uuid)",
            "warehouse_accept_binding(uuid,integer)",
            "warehouse_change_write(uuid,text)",
            "warehouse_conversation_access(uuid,uuid)",
            "warehouse_document_write(uuid)",
            "warehouse_sync_control(uuid,integer,text,integer,integer,boolean,text)",
            "warehouse_invitation_create(uuid,uuid,text,text,timestamptz,timestamptz,integer,text)",
            "warehouse_invitation_claim(text)",
            "warehouse_invitation_preview(text)",
            "warehouse_invitation_decide(uuid,integer,text,text)",
        ):
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {quoted}"))


def onboard(engine: Engine, supplier_name: str, buyer_name: str, worker_email: str) -> dict:
    """Create a single supplier connection and an explicitly named buyer grant.

    Credentials remain in the configured secret provider. This identity can run syncs,
    but has no login password; human accounts are provisioned separately.
    """
    ids = {
        name: uuid4()
        for name in ("supplier_id", "buyer_id", "worker_id", "connection_id", "grant_id")
    }
    with engine.begin() as conn:
        if conn.scalar(
            text("SELECT EXISTS(SELECT 1 FROM warehouse_user WHERE email=:email)"),
            {"email": worker_email},
        ):
            raise ValueError("同步账号已存在；请使用已有连接，避免重复导入")
        for key, name, kind in (
            ("supplier_id", supplier_name, "supplier"),
            ("buyer_id", buyer_name, "buyer"),
        ):
            conn.execute(
                text(
                    "INSERT INTO organization(id,name,kinds) VALUES(:id,:name,CAST(:kinds AS text[]))"
                ),
                {"id": ids[key], "name": name, "kinds": [kind]},
            )
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:worker_id,:email)"),
            {**ids, "email": worker_email},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:worker_id,:supplier_id,ARRAY['sync_worker'])"
            ),
            ids,
        )
        conn.execute(
            text(
                "INSERT INTO supplier_connection(id,supplier_org_id,name,connector_type,credential_ref,capabilities) VALUES(:connection_id,:supplier_id,'B2B catalog','tour_b2b','env:TOUR_ERP',CAST(:capabilities AS jsonb))"
            ),
            {
                **ids,
                "capabilities": '{"catalog_read":true,"inventory_owner":"source","order_write":false}',
            },
        )
        conn.execute(
            text(
                "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:grant_id,:supplier_id,:buyer_id,:connection_id)"
            ),
            ids,
        )
    return {key: str(value) for key, value in ids.items()}


def set_short_name(engine: Engine, organization_id, short_name: str | None) -> dict:
    """Name a supplier the way advisors do; an empty name falls back to the full name."""
    value = (short_name or "").strip() or None
    if value and len(value) > 12:
        raise ValueError("简称最多 12 个字")
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    "UPDATE organization SET short_name=:short WHERE id=:id AND 'supplier'=ANY(kinds)"
                    " RETURNING id,name,short_name"
                ),
                {"id": organization_id, "short": value},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise ValueError("没有这个供应商组织")
    return {"id": str(row["id"]), "name": row["name"], "short_name": row["short_name"]}


def grant_auth(engine: Engine, role: str) -> None:
    with engine.begin() as conn:
        quoted = conn.dialect.identifier_preparer.quote(role)
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {quoted}"))
        conn.execute(
            text(f"GRANT EXECUTE ON FUNCTION warehouse_plan_share_lookup(text) TO {quoted}")
        )
        conn.execute(
            text(f"GRANT EXECUTE ON FUNCTION warehouse_quote_share_lookup(text) TO {quoted}")
        )
        for table in ("warehouse_user", "organization", "membership"):
            conn.execute(text(f"GRANT SELECT ON {table} TO {quoted}"))
        for table in ("auth_session", "auth_rate_limit"):
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {quoted}"))
