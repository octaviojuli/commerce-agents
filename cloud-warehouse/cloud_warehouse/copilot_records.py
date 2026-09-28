"""Advisor-owned customer and deal records; every consequential artifact is immutable."""

import base64
import json
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import text

from . import conversations, trip_brief
from .changes import Conflict
from .integrations import canonical, fingerprint
from .persistence import Forbidden, require_role, transaction
from .trip_brief import Model

Money = Annotated[Decimal, Field(ge=0, le=100000000, decimal_places=2, allow_inf_nan=False)]


class Traveler(Model):
    name: str = Field(max_length=100)
    birthday: date | None = None
    document_type: Literal["passport", "identity", "other", "unknown"] = "unknown"
    document_number: str = Field(default="", max_length=80)
    document_expiry: date | None = None
    confirmed: bool = False


class Customer(Model):
    name: str = Field(min_length=1, max_length=100)
    contact: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)
    travelers: list[Traveler] = Field(default_factory=list, max_length=100)


class CustomerWrite(Model):
    request_id: UUID
    expected_version: int = Field(default=0, ge=0)
    customer: Customer


class DealCreate(Model):
    request_id: UUID
    customer_id: UUID | None = None
    title: str = Field(default="新需求", min_length=1, max_length=100)


class Command(Model):
    request_id: UUID
    expected_version: int = Field(ge=0)


class Note(Command):
    kind: Literal["note", "memory", "qa", "task"]
    text: str = Field(min_length=1, max_length=4000)
    publication_id: UUID | None = None
    node_id: str | None = Field(default=None, max_length=100)


class Associate(Command):
    customer_id: UUID


def ensure(conn, actor, identifier, *, customer=None, title=""):
    conversation = conversations._load(conn, identifier, check_authorization=False)
    if conversation["role"] != "advisor":
        raise Forbidden("仅顾问会话可以建立跟单")
    conn.execute(
        text("""INSERT INTO advisor_deal(id,organization_id,user_id,customer_id,title)
      VALUES(:id,:org,:user,:customer,:title) ON CONFLICT(id) DO NOTHING"""),
        {
            "id": identifier,
            "org": actor.organization_id,
            "user": actor.user_id,
            "customer": customer,
            "title": title,
        },
    )


def deal(conn, identifier, *, lock=False):
    if lock:
        # The immutable deal has no UPDATE grant. Its owner conversation is the
        # serialization anchor shared by chat, actions and business commands.
        conversations._load(conn, identifier, lock=True, check_authorization=False)
    row = (
        conn.execute(
            text("SELECT * FROM advisor_deal WHERE id=:id"),
            {"id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("跟单不存在或不属于当前顾问")
    return row


def checked(conn, identifier, expected, *, private_only=False):
    conversation = conversations._load(
        conn, identifier, lock=True, check_authorization=not private_only
    )
    if conversation["busy"]:
        raise Conflict("助手正在处理本轮，请稍后操作")
    deal(conn, identifier, lock=True)
    if private_only:
        brief = trip_brief.TripBrief()
        version = (
            conn.scalar(
                text("SELECT version FROM conversation_brief WHERE conversation_id=:id"),
                {"id": identifier},
            )
            or 0
        )
    else:
        brief, version = trip_brief.load(conn, identifier)
    if expected != version:
        raise Conflict("需求已变化，请刷新后重新核对")
    return brief, version


def append(conn, actor, identifier, kind, body, version, key):
    digest = fingerprint({"deal": str(identifier), "kind": kind, "body": body, "version": version})
    previous = (
        conn.execute(text("SELECT * FROM advisor_record WHERE request_key=:key"), {"key": key})
        .mappings()
        .one_or_none()
    )
    if previous:
        if previous["request_hash"] != digest:
            raise Conflict("相同操作编号不能用于不同内容")
        return dict(previous)
    return dict(
        conn.execute(
            text("""INSERT INTO advisor_record
      (id,deal_id,organization_id,user_id,kind,brief_version,body,request_key,request_hash)
      VALUES(:id,:deal,:org,:user,:kind,:version,CAST(:body AS jsonb),:key,:hash) RETURNING *"""),
            {
                "id": uuid4(),
                "deal": identifier,
                "org": actor.organization_id,
                "user": actor.user_id,
                "kind": kind,
                "version": version,
                "body": canonical(body),
                "key": key,
                "hash": digest,
            },
        )
        .mappings()
        .one()
    )


def record(conn, identifier, *, kind=None, deal_id=None):
    row = (
        conn.execute(text("SELECT * FROM advisor_record WHERE id=:id"), {"id": identifier})
        .mappings()
        .one_or_none()
    )
    if row is None or (kind and row["kind"] != kind) or (deal_id and row["deal_id"] != deal_id):
        raise Forbidden("记录不存在或不属于当前跟单")
    return row


def snapshot(conn, actor, identifier, brief, version):
    ensure(conn, actor, identifier)
    append(
        conn,
        actor,
        identifier,
        "state",
        brief.model_dump(mode="json"),
        version,
        f"state:{identifier}:{version}",
    )


def customers(engine, actor, *, query="", before=None, limit=30):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        rows = (
            conn.execute(
                text("""SELECT id,body,version,updated_at FROM advisor_customer
          WHERE (CAST(:before AS uuid) IS NULL OR id<:before)
          AND position(lower(:query) in lower(body->>'name'))>0
          ORDER BY id DESC LIMIT :limit"""),
                {"before": before, "query": query, "limit": limit + 1},
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def write_customer(engine, actor, request, identifier=None):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        if identifier is None:
            identifier = request.request_id
            existing = (
                conn.execute(
                    text("SELECT * FROM advisor_customer WHERE id=:id FOR UPDATE"),
                    {"id": identifier},
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                if existing["body"] != request.customer.model_dump(mode="json"):
                    raise Conflict("客户创建编号已用于其他内容")
                return dict(existing)
            return dict(
                conn.execute(
                    text("""INSERT INTO advisor_customer(id,organization_id,user_id,body,request_id)
              VALUES(:id,:org,:user,CAST(:body AS jsonb),:id) RETURNING *"""),
                    {
                        "id": identifier,
                        "org": actor.organization_id,
                        "user": actor.user_id,
                        "body": request.customer.model_dump_json(),
                    },
                )
                .mappings()
                .one()
            )
        row = (
            conn.execute(
                text("""UPDATE advisor_customer SET body=CAST(:body AS jsonb),
          version=version+1,updated_at=now() WHERE id=:id AND version=:version RETURNING *"""),
                {
                    "id": identifier,
                    "version": request.expected_version,
                    "body": request.customer.model_dump_json(),
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Conflict("客户不存在或已更新，请刷新")
        return dict(row)


def create_deal(engine, actor, request):
    with transaction(engine, actor) as conn:
        authorization = conversations._authorization(conn, "advisor")
        existing = (
            conn.execute(
                text("SELECT * FROM advisor_deal WHERE id=:id"), {"id": request.request_id}
            )
            .mappings()
            .one_or_none()
        )
        if existing:
            if existing["customer_id"] != request.customer_id or existing["title"] != request.title:
                raise Conflict("跟单创建编号已用于其他内容")
            return dict(existing)
        if request.customer_id and not conn.scalar(
            text("SELECT id FROM advisor_customer WHERE id=:id"), {"id": request.customer_id}
        ):
            raise Forbidden("客户不存在或不属于当前顾问")
        conn.execute(
            text("""INSERT INTO agent_conversation(id,organization_id,user_id,role,authorization_hash)
          VALUES(:id,:org,:user,'advisor',:authorization)"""),
            {
                "id": request.request_id,
                "org": actor.organization_id,
                "user": actor.user_id,
                "authorization": authorization,
            },
        )
        ensure(conn, actor, request.request_id, customer=request.customer_id, title=request.title)
        return dict(deal(conn, request.request_id))


def list_deals(engine, actor, *, before=None, query="", limit=30):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin")
        rows = (
            conn.execute(
                text("""SELECT d.*,c.body->>'name' AS customer_name,
          CASE WHEN a.authorization_hash=:authorization THEN b.body ELSE NULL END AS brief,
          COALESCE(b.version,0) AS brief_version,a.updated_at
          FROM advisor_deal d JOIN agent_conversation a ON a.id=d.id
          LEFT JOIN advisor_customer c ON c.id=d.customer_id LEFT JOIN conversation_brief b ON b.conversation_id=d.id
          WHERE (CAST(:before AS uuid) IS NULL OR d.id<:before)
          AND position(lower(:query) in lower(concat_ws(' ',d.title,c.body->>'name',b.body->'destinations'->>'value')))>0
          ORDER BY d.id DESC LIMIT :limit"""),
                {
                    "before": before,
                    "query": query,
                    "limit": limit + 1,
                    "authorization": conversations._authorization(conn, "advisor"),
                },
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def detail(engine, actor, identifier):
    from . import copilot_inquiries, copilot_sales, quotes

    with transaction(engine, actor) as conn:
        ensure(conn, actor, identifier)
        item = dict(deal(conn, identifier))
        available = True
        try:
            brief, version = trip_brief.load(conn, identifier)
        except Conflict:
            # Supplier permissions may retire a conversation, never its owner's
            # customer file or manual accounting. Hide supplier-derived context.
            available = False
            brief = trip_brief.TripBrief()
            version = (
                conn.scalar(
                    text("SELECT version FROM conversation_brief WHERE conversation_id=:id"),
                    {"id": identifier},
                )
                or 0
            )
        if brief.quote_id:
            row = quotes._read(conn, brief.quote_id)
            try:
                brief.quote = (
                    quotes._display(conn, actor, row)
                    if row
                    else {**(brief.quote or {}), "snapshot_stale": True}
                )
            except (Forbidden, Conflict):
                brief.quote = {**(brief.quote or {}), "snapshot_stale": True}
        records = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT * FROM advisor_record WHERE deal_id=:id ORDER BY created_at DESC,id DESC LIMIT 120"
                ),
                {"id": identifier},
            ).mappings()
        ]
        if not available:
            records = [r for r in records if r["kind"] in PRIVATE_KINDS]
        for r in records:
            if r["kind"] == "material_review" and r["body"].get("encrypted_candidates"):
                from . import copilot_crypto

                readable = conn.scalar(
                    text(
                        "SELECT id FROM advisor_asset WHERE id=CAST(:id AS uuid) AND purged_at IS NULL AND retain_until>now()"
                    ),
                    {"id": r["body"]["asset_id"]},
                )
                r["body"] = {
                    **r["body"],
                    "candidates": json.loads(
                        copilot_crypto.decrypt(
                            base64.b64decode(r["body"]["encrypted_candidates"]),
                            r["body"]["asset_id"],
                        )
                    )
                    if readable
                    else [],
                }
                r["body"].pop("encrypted_candidates")
            r["stale"] = r["brief_version"] != version and r["kind"] in {
                "proposal",
                "plan",
                "qa",
                "confirmation",
                "retail_quote",
                "inquiry_adoption",
            }
            if r["kind"] == "retail_quote":
                try:
                    r["stale"] = copilot_sales.current_retail(
                        conn, actor, identifier, r["id"], require_fresh=False
                    )[2]
                except (Forbidden, Conflict):
                    r["stale"] = True
            if r["kind"] in {"plan", "qa", "inquiry_adoption"}:
                contexts = [
                    f["scope"]
                    for f in r["body"].get("facts", [])
                    if f.get("scope", {}).get("product_id")
                ]
                if r["kind"] == "inquiry_adoption":
                    contexts.append(
                        {
                            "product_id": "WP-" + r["body"]["product_id"],
                            **r["body"]["context"]["source_versions"],
                        }
                    )
                for scope in contexts:
                    if "product_version" not in scope:
                        continue
                    try:
                        current = copilot_inquiries.scope(
                            conn, UUID(scope["product_id"].removeprefix("WP-"))
                        )
                        r["stale"] |= any(current[k] != scope.get(k) for k in current)
                    except (Forbidden, Conflict, ValueError):
                        r["stale"] = True
        customer = (
            conn.execute(
                text("SELECT id,body,version FROM advisor_customer WHERE id=:id"),
                {"id": item["customer_id"]},
            )
            .mappings()
            .one_or_none()
        )
        assets = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT id,filename,media_type,created_at,retain_until FROM advisor_asset WHERE deal_id=:id AND purged_at IS NULL AND retain_until>now() ORDER BY created_at DESC LIMIT 100"
                ),
                {"id": identifier},
            ).mappings()
        ]
        sale = (
            conn.execute(
                text(
                    "SELECT * FROM advisor_record WHERE deal_id=:id AND kind='sale' ORDER BY created_at DESC,id DESC LIMIT 1"
                ),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        receipts = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT * FROM advisor_record WHERE deal_id=:id AND kind='receipt' ORDER BY created_at DESC,id DESC LIMIT 30"
                ),
                {"id": identifier},
            ).mappings()
        ]
        received = conn.scalar(
            text(
                "SELECT COALESCE(sum((body->>'amount')::numeric * CASE WHEN body->>'category'='refund' THEN -1 ELSE 1 END),0) FROM advisor_record WHERE deal_id=:id AND kind='receipt'"
            ),
            {"id": identifier},
        )
        return {
            **item,
            "customer": dict(customer) if customer else None,
            "brief": trip_brief.envelope(brief, version),
            "records": records,
            "assets": assets,
            "conversation_available": available,
            "ledger": {
                "sale": dict(sale) if sale else None,
                "receipts": receipts,
                "received": str(received),
            },
        }


PRIVATE_KINDS = {"sale", "receipt", "note", "memory", "task", "task_done", "material_review"}


def history(engine, actor, identifier, *, before=None, limit=40):
    with transaction(engine, actor) as conn:
        deal(conn, identifier)
        # History pagination must apply the same context-retirement boundary.
        try:
            conversations._load(conn, identifier)
            available = True
        except Conflict:
            available = False
        anchor = record(conn, before, deal_id=identifier) if before else None
        rows = (
            conn.execute(
                text("""SELECT * FROM advisor_record WHERE deal_id=:deal
          AND (:has_anchor=false OR (created_at,id)<(:time,:id))
          AND (:available OR kind=ANY(:private_kinds))
          ORDER BY created_at DESC,id DESC LIMIT :limit"""),
                {
                    "deal": identifier,
                    "has_anchor": bool(anchor),
                    "time": anchor["created_at"] if anchor else None,
                    "id": before,
                    "limit": limit + 1,
                    "available": available,
                    "private_kinds": sorted(PRIVATE_KINDS),
                },
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def note(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
        _, version = checked(conn, identifier, request.expected_version, private_only=True)
        if bool(request.node_id) != bool(request.publication_id):
            raise ValueError("行程备注必须同时指定发布版本和节点")
        return append(
            conn,
            actor,
            identifier,
            request.kind,
            request.model_dump(mode="json", exclude={"request_id", "expected_version", "kind"}),
            version,
            str(request.request_id),
        )


def task_done(engine, actor, identifier, task_id, request):
    with transaction(engine, actor) as conn:
        _, version = checked(conn, identifier, request.expected_version, private_only=True)
        record(conn, task_id, kind="task", deal_id=identifier)
        return append(
            conn,
            actor,
            identifier,
            "task_done",
            {"task_id": str(task_id)},
            version,
            str(request.request_id),
        )


def associate(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
        _, version = checked(conn, identifier, request.expected_version, private_only=True)
        if not conn.scalar(
            text("SELECT id FROM advisor_customer WHERE id=:id"), {"id": request.customer_id}
        ):
            raise Forbidden("客户不存在或不属于当前顾问")
        receipt = append(
            conn,
            actor,
            identifier,
            "note",
            {"text": "关联客户档案已更新", "customer_id": str(request.customer_id)},
            version,
            str(request.request_id),
        )
        conn.execute(
            text("UPDATE advisor_deal SET customer_id=:customer WHERE id=:id"),
            {"id": identifier, "customer": request.customer_id},
        )
        return receipt
