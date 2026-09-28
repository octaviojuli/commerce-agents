"""Explicit owner-issued customer links, with no raw tokens stored or private prices exposed."""

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import quotes
from .changes import Conflict, audit
from .persistence import Forbidden, Principal, require_role, transaction

COLUMNS = "id,quote_id,request_id,hours,created_at,expires_at,revoked_at,advisor_record_id"


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    hours: int = Field(default=72, ge=1, le=168, strict=True)
    advisor_record_id: UUID | None = None


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Validate in read() so malformed, unknown and expired capabilities have the same response.
    token: str = Field(max_length=256)


def _owner(conn, actor, quote_id):
    require_role(conn, "advisor", "buyer_admin")
    row = quotes._read(conn, quote_id)
    if row is None or row["actor_id"] != actor.user_id:
        raise Forbidden("只能分享自己创建且仍有读取权限的报价")
    return row


def create(engine, actor, quote_id, command: Create):
    with transaction(engine, actor) as conn:
        row = _owner(conn, actor, quote_id)
        # Serialize owner issuance, so retries and the active-link ceiling agree across processes.
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int.from_bytes(actor.user_id.bytes[:8], "big", signed=True)},
        )
        existing = (
            conn.execute(
                text(f"SELECT {COLUMNS} FROM quote_share WHERE request_id=:id"),
                {"id": command.request_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing:
            if (
                existing["quote_id"] != quote_id
                or existing["hours"] != command.hours
                or existing["advisor_record_id"] != command.advisor_record_id
            ):
                raise Conflict("相同请求编号不能用于不同分享条件")
            return {**existing, "token": None}
        if quotes._display(conn, actor, row)["snapshot_stale"]:
            raise Conflict("报价已失效，请重新询价后再创建分享")
        if command.advisor_record_id is not None:
            from . import copilot_records, copilot_sales

            retail = copilot_records.record(conn, command.advisor_record_id, kind="retail_quote")
            # Share issuance and requirement changes use the same serialization
            # anchor. A link cannot slip in after a concurrent invalidation.
            copilot_records.deal(conn, retail["deal_id"], lock=True)
            copilot_sales.current_retail(conn, actor, retail["deal_id"], retail["id"])
            if retail["body"]["quote_id"] != str(quote_id):
                raise Conflict("销售报价与结算快照不一致")
        active = conn.scalar(
            text(
                "SELECT count(*) FROM quote_share WHERE quote_id=:id AND revoked_at IS NULL AND expires_at>now()"
            ),
            {"id": quote_id},
        )
        if active >= 10:
            raise Conflict("此报价已有 10 个有效分享，请先撤销不再使用的链接")
        token = secrets.token_urlsafe(32)
        identifier = uuid4()
        now = datetime.now(UTC)
        saved = (
            conn.execute(
                text(
                    f"INSERT INTO quote_share(id,buyer_org_id,actor_id,quote_id,request_id,hours,token_hash,created_at,expires_at,advisor_record_id) VALUES(:id,:org,:actor,:quote,:request,:hours,:hash,:now,:expires,:retail) RETURNING {COLUMNS}"
                ),
                {
                    "id": identifier,
                    "org": actor.organization_id,
                    "actor": actor.user_id,
                    "quote": quote_id,
                    "request": command.request_id,
                    "hours": command.hours,
                    "hash": hashlib.sha256(token.encode()).hexdigest(),
                    "now": now,
                    "expires": now + timedelta(hours=command.hours),
                    "retail": command.advisor_record_id,
                },
            )
            .mappings()
            .one()
        )
        audit(
            conn,
            actor,
            "quote.share_created",
            identifier,
            {"quote_id": str(quote_id), "hours": command.hours},
        )
        return {**saved, "token": token}


def list_for_quote(engine, actor, quote_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        # Revocation remains available when supplier permissions no longer permit quote reads.
        return {
            "items": [
                dict(row)
                for row in conn.execute(
                    text(
                        f"SELECT {COLUMNS} FROM quote_share WHERE quote_id=:id ORDER BY created_at DESC,id DESC"
                    ),
                    {"id": quote_id},
                ).mappings()
            ]
        }


def list_page(engine, actor, *, quote_id=None, before=None, limit=25, status="all"):
    """Owner-scoped metadata stays revocable even when quote access has been withdrawn."""
    if not 1 <= limit <= 100 or status not in {"all", "active", "revoked", "expired"}:
        raise ValueError("无效的分享分页或状态")
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        scope = " AND quote_id=:quote" if quote_id is not None else ""
        anchor = None
        if before is not None:
            anchor = (
                conn.execute(
                    text("SELECT id,created_at FROM quote_share WHERE id=:id" + scope),
                    {"id": before, "quote": quote_id},
                )
                .mappings()
                .one_or_none()
            )
            if anchor is None:
                raise Forbidden("无效的分享分页游标")
        condition = {
            "all": "",
            "active": " AND s.revoked_at IS NULL AND s.expires_at>now()",
            "revoked": " AND s.revoked_at IS NOT NULL",
            "expired": " AND s.revoked_at IS NULL AND s.expires_at<=now()",
        }[status]
        rows = (
            conn.execute(
                text(
                    "SELECT s.id,s.quote_id,s.created_at,s.expires_at,s.revoked_at,"
                    "q.body->>'product_name' AS product_name,"
                    "q.body->>'departure_date' AS departure_date,q.id IS NOT NULL AS quote_readable "
                    "FROM quote_share s LEFT JOIN quote_snapshot q ON q.id=s.quote_id WHERE true"
                    + (" AND s.quote_id=:quote" if quote_id is not None else "")
                    + condition
                    + (" AND (s.created_at,s.id)<(:created,:before)" if anchor else "")
                    + " ORDER BY s.created_at DESC,s.id DESC LIMIT :limit"
                ),
                {
                    "quote": quote_id,
                    "before": before,
                    "created": anchor["created_at"] if anchor else None,
                    "limit": limit + 1,
                },
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def revoke(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        row = (
            conn.execute(
                text(f"SELECT {COLUMNS} FROM quote_share WHERE id=:id FOR UPDATE"),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("分享不存在或无权撤销")
        if row["revoked_at"] is None:
            conn.execute(
                text("UPDATE quote_share SET revoked_at=now() WHERE id=:id"), {"id": identifier}
            )
            audit(
                conn, actor, "quote.share_revoked", identifier, {"quote_id": str(row["quote_id"])}
            )
        return {"id": identifier, "revoked": True}


def read(engine, authentication, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with authentication.connect() as conn:
        capability = (
            conn.execute(
                text("SELECT * FROM warehouse_quote_share_lookup(:hash)"), {"hash": digest}
            )
            .mappings()
            .one_or_none()
        )
    if capability is None:
        return None
    actor = Principal(capability["actor_id"], capability["buyer_org_id"])
    try:
        with transaction(engine, actor) as conn:
            share = (
                conn.execute(
                    text(
                        "SELECT quote_id,expires_at,advisor_record_id FROM quote_share WHERE id=:id AND token_hash=:hash AND revoked_at IS NULL AND expires_at>now()"
                    ),
                    {"id": capability["id"], "hash": digest},
                )
                .mappings()
                .one_or_none()
            )
            if share is None:
                return None
            row = _owner(conn, actor, share["quote_id"])
            quote = quotes._display(conn, actor, row)
            if share["advisor_record_id"]:
                from .copilot_sales import customer_projection

                result = customer_projection(conn, actor, share["advisor_record_id"], quote)
            else:
                result = quotes.customer_view(quote)
            return {**result, "share_expires_at": share["expires_at"].isoformat()}
    except (Forbidden, Conflict):
        return None
