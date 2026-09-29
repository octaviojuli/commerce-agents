"""One persistent proposal/approval/apply transaction shared by merchant commands."""

from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .integrations import canonical, fingerprint
from .persistence import Forbidden, Principal, require_role, transaction


class Conflict(ValueError):
    """The approved preview no longer matches live data, or its business key was consumed."""

    def __init__(self, message: str, *, code: str = "APPLY_CONFLICT"):
        super().__init__(message)
        self.code = code


def audit(conn: Connection, actor: Principal, action: str, resource: UUID, details: dict):
    conn.execute(
        text(
            "INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details) VALUES(:id,:org,:actor,:action,:resource,CAST(:details AS jsonb))"
        ),
        {
            "id": uuid4(),
            "org": actor.organization_id,
            "actor": actor.user_id,
            "action": action,
            "resource": resource,
            "details": canonical(details),
        },
    )


def _handler(kind: str):
    # Closed registry. A user/model cannot name an import path or supply executable handlers.
    if kind == "route_content":
        from . import route_editor

        return route_editor
    if kind == "route_tags":
        from . import route_tags

        return route_tags
    if kind == "product_display":
        from . import product_display

        return product_display
    if kind == "offer":
        from . import offers

        return offers
    if kind == "price_book":
        from . import price_books

        return price_books
    if kind == "departure_sales":
        from . import sales

        return sales
    if kind == "document":
        from . import documents

        return documents
    if kind == "merchant":
        from . import merchant_commands

        return merchant_commands
    if kind == "catalog":
        from . import catalog_edits

        return catalog_edits
    if kind == "inventory":
        from . import inventory

        return inventory
    if kind == "excel_import":
        from . import imports

        return imports
    if kind == "pricing":
        from . import pricing

        return pricing
    raise Conflict("不支持的变更类型")


def stage(engine: Engine, actor: Principal, kind: str, payload: dict) -> dict:
    change_id, digest = uuid4(), fingerprint(payload)
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "inventory_manager", "product_editor")
        target, version = _handler(kind).validate_change(conn, actor, payload)
        conn.execute(
            text(
                "INSERT INTO change_request(id,organization_id,kind,payload,payload_hash,target_id,expected_version,created_by) VALUES(:id,:org,:kind,CAST(:payload AS jsonb),:hash,:target,:version,:actor)"
            ),
            {
                "id": change_id,
                "org": actor.organization_id,
                "kind": kind,
                "payload": canonical(payload),
                "hash": digest,
                "target": target,
                "version": version,
                "actor": actor.user_id,
            },
        )
        audit(conn, actor, kind + ".proposed", change_id, {"hash": digest})
    return {"id": str(change_id), "payload_hash": digest, "status": "staged", "preview": payload}


def approve(engine: Engine, actor: Principal, change_id: UUID, expected_hash: str) -> dict:
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        row = (
            conn.execute(
                text("SELECT * FROM change_request WHERE id=:id FOR UPDATE"), {"id": change_id}
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("变更不存在或无权限")
        if (
            row["status"] != "staged"
            or row["payload_hash"] != expected_hash
            or fingerprint(row["payload"]) != expected_hash
        ):
            raise Conflict("审批内容不匹配或变更已结束")
        target, version = _handler(row["kind"]).validate_change(conn, actor, row["payload"])
        if target != row["target_id"] or version != row["expected_version"]:
            raise Conflict("资源或版本与预览不一致")
        existing = (
            conn.execute(
                text(
                    "SELECT approved_by,payload_hash,expires_at>now() AS valid FROM change_approval WHERE change_id=:id"
                ),
                {"id": change_id},
            )
            .mappings()
            .one_or_none()
        )
        if existing:
            if (
                existing["approved_by"] != actor.user_id
                or existing["payload_hash"] != expected_hash
                or not existing["valid"]
            ):
                raise Conflict("审批已过期或属于其他审批人，请重新提议")
        else:
            conn.execute(
                text(
                    "INSERT INTO change_approval(change_id,organization_id,approved_by,payload_hash,expected_version,expires_at) VALUES(:id,:org,:actor,:hash,:version,now()+interval '5 minutes')"
                ),
                {
                    "id": change_id,
                    "org": actor.organization_id,
                    "actor": actor.user_id,
                    "hash": expected_hash,
                    "version": version,
                },
            )
            audit(conn, actor, row["kind"] + ".approved", change_id, {"hash": expected_hash})
    return {"id": str(change_id), "approval": "recorded"}


def apply(engine: Engine, actor: Principal, change_id: UUID) -> dict:
    from .operations import record_change_failure

    try:
        return _apply(engine, actor, change_id)
    except (Conflict, SQLAlchemyError) as error:
        record_change_failure(
            engine,
            actor,
            change_id,
            error.code if isinstance(error, Conflict) else "APPLY_DATABASE_ERROR",
        )
        raise


def _apply(engine: Engine, actor: Principal, change_id: UUID) -> dict:
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        row = (
            conn.execute(
                text("SELECT * FROM change_request WHERE id=:id FOR UPDATE"), {"id": change_id}
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("变更不存在或无权限")
        if row["status"] == "applied":
            return row["result"]
        if row["status"] != "staged":
            raise Conflict("变更已作废")
        approval = (
            conn.execute(
                text(
                    "SELECT * FROM change_approval WHERE change_id=:id AND approved_by=:actor AND expires_at>now()"
                ),
                {"id": change_id, "actor": actor.user_id},
            )
            .mappings()
            .one_or_none()
        )
        if (
            not approval
            or approval["payload_hash"] != fingerprint(row["payload"])
            or approval["payload_hash"] != row["payload_hash"]
            or approval["expected_version"] != row["expected_version"]
        ):
            raise Forbidden("必须先由当前操作员确认相同内容和版本的审批")
        handler = _handler(row["kind"])
        target, version = handler.validate_change(conn, actor, row["payload"], lock=True)
        if target != row["target_id"] or version != row["expected_version"]:
            raise Conflict("资源或版本与预览不一致")
        result = {
            **handler.apply_command(conn, actor, change_id, row["payload"]),
            "change_id": str(change_id),
            "status": "applied",
        }
        conn.execute(
            text(
                "UPDATE change_request SET status='applied',applied_by=:actor,applied_at=now(),result=CAST(:result AS jsonb) WHERE id=:id"
            ),
            {"actor": actor.user_id, "result": canonical(result), "id": change_id},
        )
        audit(conn, actor, row["kind"] + ".applied", change_id, result)
        conn.execute(
            text(
                "INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload) VALUES(:id,:org,:kind,:change,CAST(:result AS jsonb))"
            ),
            {
                "id": uuid4(),
                "org": actor.organization_id,
                "kind": row["kind"] + ".applied",
                "change": change_id,
                "result": canonical(result),
            },
        )
        return result


def discard(
    engine: Engine, actor: Principal, change_id: UUID, actor_kind: str = "operator"
) -> None:
    if actor_kind not in {"operator", "agent"}:
        raise ValueError("Invalid discard actor kind")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "inventory_manager", "product_editor")
        changed = conn.execute(
            text(
                "UPDATE change_request SET status='discarded',discarded_by=:actor,discarded_at=now(),discarded_by_kind=:kind WHERE id=:id AND status='staged' RETURNING kind"
            ),
            {"id": change_id, "actor": actor.user_id, "kind": actor_kind},
        ).scalar_one_or_none()
        if not changed:
            raise Conflict("变更不存在、无权限或已结束")
        audit(conn, actor, changed + ".discarded", change_id, {"actor_kind": actor_kind})
