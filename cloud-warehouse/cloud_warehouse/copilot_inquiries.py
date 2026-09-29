"""Explicitly submitted, source-bound questions and immutable human supplier replies."""

from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import text

from . import copilot_records as records
from . import documents
from .advisor import parse_id
from .changes import Conflict
from .integrations import canonical
from .persistence import Forbidden, require_role, transaction


class Submit(records.Command):
    product_id: str = Field(max_length=50)
    question: str = Field(min_length=1, max_length=2000)


class Reply(records.Model):
    request_id: UUID
    answer: str = Field(min_length=1, max_length=4000)


def source(conn, product):
    row = (
        conn.execute(
            text("""SELECT p.id,p.supplier_org_id,p.connection_id,p.effective_name AS name,
      p.version,p.display_version,p.effective_days AS days,p.effective_gateway AS gateway,c.version AS connection_version FROM product_listing p
      JOIN supplier_connection c ON c.id=p.connection_id WHERE p.id=:id AND p.status='published' AND c.active"""),
            {"id": product},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise Forbidden("线路不可见或已停止供货")
    return dict(row)


def scope(conn, product, departure=None):
    current = source(conn, product)
    document = documents.current_in_transaction(
        conn, product, departure_id=departure, sales=departure is not None
    )
    return {
        "product_version": current["version"],
        "display_version": current["display_version"],
        "connection_version": current["connection_version"],
        "publication_id": str(document["id"]) if document else None,
    }


def submit(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
        previous = (
            conn.execute(
                text("SELECT * FROM supplier_inquiry WHERE request_id=:id"),
                {"id": request.request_id},
            )
            .mappings()
            .one_or_none()
        )
        product = parse_id(request.product_id, "WP-")
        if previous:
            if (
                previous["deal_id"] != identifier
                or previous["question"] != request.question
                or previous["product_id"] != product
            ):
                raise Conflict("核实单请求编号已被使用")
            return dict(previous)
        brief, version = records.checked(conn, identifier, request.expected_version)
        item = source(conn, product)
        # Only the explicit question and minimum travel context cross to the supplier.
        departure = (
            parse_id(brief.departure_id, "WD-")
            if brief.route_id == request.product_id and brief.departure_id
            else None
        )
        context = {
            "route_name": item["name"],
            "departure_id": str(departure) if departure else None,
            "window": brief.window.model_dump(mode="json")["value"],
            "party": {
                "adults": brief.adults.value,
                "children": brief.children.value,
                "child_ages": brief.child_ages.value,
            },
            "rooms": brief.rooms.model_dump(mode="json")["value"],
            "source_versions": scope(conn, product, departure),
        }
        return dict(
            conn.execute(
                text("""INSERT INTO supplier_inquiry
          (id,deal_id,organization_id,user_id,supplier_org_id,connection_id,product_id,brief_version,question,context,request_id)
          VALUES(:id,:deal,:org,:user,:supplier,:connection,:product,:version,:question,CAST(:context AS jsonb),:request) RETURNING *"""),
                {
                    "id": uuid4(),
                    "deal": identifier,
                    "org": actor.organization_id,
                    "user": actor.user_id,
                    "supplier": item["supplier_org_id"],
                    "connection": item["connection_id"],
                    "product": product,
                    "version": version,
                    "question": request.question,
                    "context": canonical(context),
                    "request": request.request_id,
                },
            )
            .mappings()
            .one()
        )


def page(engine, actor, *, deal_id=None, before=None, limit=30):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin", "supplier_admin", "product_editor", "auditor")
        if deal_id:
            records.deal(conn, deal_id)
        rows = (
            conn.execute(
                text("""SELECT id,deal_id,product_id,brief_version,question,context,created_at
          FROM supplier_inquiry WHERE (CAST(:deal AS uuid) IS NULL OR deal_id=:deal)
          AND (CAST(:before AS uuid) IS NULL OR id<:before) ORDER BY id DESC LIMIT :limit"""),
                {"deal": deal_id, "before": before, "limit": limit + 1},
            )
            .mappings()
            .all()
        )
        items = []
        for row in rows[:limit]:
            replies = [
                dict(r)
                for r in conn.execute(
                    text(
                        "SELECT id,answer,created_at FROM supplier_inquiry_reply WHERE inquiry_id=:id ORDER BY created_at,id"
                    ),
                    {"id": row["id"]},
                ).mappings()
            ]
            acknowledgements = [
                dict(r)
                for r in conn.execute(
                    text(
                        "SELECT reply_id,brief_version,created_at FROM supplier_inquiry_ack WHERE inquiry_id=:id ORDER BY created_at"
                    ),
                    {"id": row["id"]},
                ).mappings()
            ]
            items.append(
                {
                    **row,
                    "replies": replies,
                    "acknowledgements": acknowledgements,
                    "status": "adopted"
                    if acknowledgements
                    else "replied"
                    if replies
                    else "submitted",
                }
            )
        return {
            "items": items,
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def reply(engine, actor, identifier, request):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        row = (
            conn.execute(
                text(
                    "SELECT * FROM supplier_inquiry WHERE id=:id AND supplier_org_id=warehouse_org_id()"
                ),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            raise Forbidden("核实单不存在或不属于本商户")
        previous = (
            conn.execute(
                text(
                    "SELECT * FROM supplier_inquiry_reply WHERE request_id=:id AND author_id=warehouse_user_id()"
                ),
                {"id": request.request_id},
            )
            .mappings()
            .one_or_none()
        )
        if previous:
            if previous["inquiry_id"] != identifier or previous["answer"] != request.answer:
                raise Conflict("回复请求编号已被使用")
            return dict(previous)
        return dict(
            conn.execute(
                text("""INSERT INTO supplier_inquiry_reply(id,inquiry_id,supplier_org_id,author_id,answer,request_id)
          VALUES(:id,:inquiry,:org,:actor,:answer,:request) RETURNING id,answer,created_at"""),
                {
                    "id": uuid4(),
                    "inquiry": identifier,
                    "org": actor.organization_id,
                    "actor": actor.user_id,
                    "answer": request.answer,
                    "request": request.request_id,
                },
            )
            .mappings()
            .one()
        )


def adopt(engine, actor, identifier, reply_id, request):
    with transaction(engine, actor) as conn:
        _, version = records.checked(conn, identifier, request.expected_version)
        row = (
            conn.execute(
                text("""SELECT r.*,i.deal_id,i.brief_version,i.context,i.product_id,i.question
          FROM supplier_inquiry_reply r JOIN supplier_inquiry i ON i.id=r.inquiry_id WHERE r.id=:id AND i.deal_id=:deal"""),
                {"id": reply_id, "deal": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            raise Forbidden("回复不属于此跟单或授权已失效")
        if row["brief_version"] != version:
            raise Conflict("需求已变化，请向商户重新核实适用性")
        context = row["context"]
        if (
            scope(
                conn,
                row["product_id"],
                UUID(context["departure_id"]) if context.get("departure_id") else None,
            )
            != context["source_versions"]
        ):
            raise Conflict("线路或内容已更新，请重新核实")
        result = records.append(
            conn,
            actor,
            identifier,
            "inquiry_adoption",
            {
                "reply_id": str(reply_id),
                "inquiry_id": str(row["inquiry_id"]),
                "question": row["question"],
                "answer": row["answer"],
                "product_id": str(row["product_id"]),
                "context": context,
                "scope": "仅适用于此跟单；不改变全局价格、库存或供应商政策",
            },
            version,
            str(request.request_id),
        )
        conn.execute(
            text("""INSERT INTO supplier_inquiry_ack(id,inquiry_id,reply_id,organization_id,user_id,brief_version)
          VALUES(:id,:inquiry,:reply,:org,:user,:version) ON CONFLICT DO NOTHING"""),
            {
                "id": uuid4(),
                "inquiry": row["inquiry_id"],
                "reply": reply_id,
                "org": actor.organization_id,
                "user": actor.user_id,
                "version": version,
            },
        )
        return result
