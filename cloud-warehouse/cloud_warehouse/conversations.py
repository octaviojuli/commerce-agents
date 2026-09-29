"""Durable leases and idempotent turns; conversations never establish identity."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from . import platform_sales
from .changes import Conflict
from .integrations import canonical, fingerprint
from .persistence import Forbidden, Principal, require_role, transaction

MAX_SAVED_BYTES = 2_000_000


def _authorization(conn, role):
    if role == "advisor":
        require_role(conn, "advisor", "buyer_admin")
        policy = platform_sales.version(conn)
        if policy:
            sources = conn.execute(
                text(
                    "SELECT id,version FROM supplier_connection WHERE id IN (SELECT warehouse_buyer_connections()) ORDER BY id"
                )
            ).mappings()
            return fingerprint(
                {
                    "inventory_policy": "availability-only-v1",
                    "sales_policy": policy,
                    "sources": [
                        {"id": str(row["id"]), "version": row["version"]} for row in sources
                    ],
                }
            )
        # A revoked/changed grant prevents old source text in a transcript from being
        # sent back to a model. A new conversation starts with current authorizations.
        rows = (
            conn.execute(
                text(
                    "SELECT g.id,g.version,c.active,warehouse_org_active(g.supplier_org_id) AS supplier_active FROM distribution_grant g JOIN supplier_connection c ON c.id=g.connection_id WHERE g.buyer_org_id=warehouse_org_id() AND warehouse_live_grant(g.id) ORDER BY g.id"
                )
            )
            .mappings()
            .all()
        )
    elif role == "merchant":
        require_role(conn, "supplier_admin", "product_editor", "inventory_manager", "auditor")
        rows = (
            conn.execute(
                text(
                    "SELECT id,version,(active AND warehouse_live_grant(id) AND "
                    "warehouse_org_active(buyer_org_id)) AS active,expires_at "
                    "FROM distribution_grant WHERE supplier_org_id=warehouse_org_id() ORDER BY id"
                )
            )
            .mappings()
            .all()
        )
    else:
        raise ValueError("Unknown conversation role")
    grants = [
        {
            key: str(value) if isinstance(value, (UUID, datetime)) else value
            for key, value in row.items()
        }
        for row in rows
    ]
    # Old advisor transcripts/model state may contain exact inventory in free text.
    # Preserve the archive but never replay it across this disclosure boundary.
    return fingerprint(
        {"inventory_policy": "availability-only-v1", "grants": grants}
        if role == "advisor"
        else grants
    )


def create(engine: Engine, actor: Principal, role: str, buyer_context: UUID | None = None):
    identifier = uuid4()
    with transaction(engine, actor) as conn:
        authorization = _authorization(conn, role)
        if buyer_context is not None and (
            role != "merchant"
            or not conn.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM distribution_grant WHERE supplier_org_id=warehouse_org_id() AND buyer_org_id=:buyer AND warehouse_live_grant(id) AND warehouse_org_active(buyer_org_id))"
                ),
                {"buyer": buyer_context},
            )
        ):
            raise Forbidden("采购价格上下文未获授权")
        conn.execute(
            text(
                "INSERT INTO agent_conversation(id,organization_id,user_id,role,buyer_context,authorization_hash) VALUES(:id,:org,:user,:role,:buyer,:authorization)"
            ),
            {
                "id": identifier,
                "org": actor.organization_id,
                "user": actor.user_id,
                "role": role,
                "buyer": buyer_context,
                "authorization": authorization,
            },
        )
    return {
        "id": str(identifier),
        "role": role,
        "buyer_context": str(buyer_context) if buyer_context else None,
    }


def _load(conn, identifier, *, lock=False, check_authorization=True):
    row = (
        conn.execute(
            text(
                "SELECT *,lease_until>now() AS busy FROM agent_conversation WHERE id=:id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": identifier},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        raise Forbidden("会话不存在或不属于当前用户和组织")
    authorization = _authorization(conn, row["role"])
    if check_authorization and authorization != row["authorization_hash"]:
        raise Conflict("供采授权或库存展示规则已变化，历史上下文不能继续使用，请新建会话")
    return row


def check_access(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        _load(conn, identifier)


def list_page(engine, actor, *, role="advisor", before=None, limit=25, query=""):
    if len(query) > 120:
        raise ValueError("会话搜索限 120 字")
    if not 1 <= limit <= 100:
        raise ValueError("会话分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        authorization = _authorization(conn, role)
        anchor = None
        if before is not None:
            anchor = (
                conn.execute(
                    text(
                        "SELECT created_at,id FROM agent_conversation WHERE id=:id AND role=:role"
                    ),
                    {"id": before, "role": role},
                )
                .mappings()
                .one_or_none()
            )
            if anchor is None:
                raise Forbidden("无效的会话分页游标")
        rows = (
            conn.execute(
                text(
                    "SELECT c.id,c.role,c.created_at,c.updated_at,"
                    "c.authorization_hash=:authorization AS resumable,"
                    "CASE WHEN c.authorization_hash=:authorization THEN "
                    "(SELECT left(t.message,80) FROM agent_turn t WHERE t.conversation_id=c.id "
                    "ORDER BY t.started_at,t.id LIMIT 1) ELSE NULL END AS title "
                    "FROM agent_conversation c WHERE c.role=:role "
                    + (
                        "AND c.authorization_hash=:authorization AND EXISTS (SELECT 1 FROM conversation_brief b WHERE b.conversation_id=c.id AND position(lower(:query) in lower(concat_ws(' · ', b.body#>>'{party_total,value}', (SELECT string_agg(v,'·') FROM jsonb_array_elements_text(COALESCE(NULLIF(b.body#>'{destinations,value}','null'::jsonb),'[]'::jsonb)) v), b.body#>>'{window,evidence}', b.body#>>'{window,value,start}'))) > 0) "
                        if query
                        else ""
                    )
                    + ("AND (c.created_at,c.id)<(:created,:before) " if anchor else "")
                    + "ORDER BY c.created_at DESC,c.id DESC LIMIT :limit"
                ),
                {
                    "role": role,
                    "query": query.strip(),
                    "authorization": authorization,
                    "limit": limit + 1,
                    "created": anchor["created_at"] if anchor else None,
                    "before": before,
                },
            )
            .mappings()
            .all()
        )
        return {
            "items": [_summary(conn, row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def get(engine, actor, identifier, *, before=None, limit=10):
    """Newest window in chronological order; raw model state stays server-side."""
    if not 1 <= limit <= 25:
        raise ValueError("对话分页大小须为 1 至 25")
    with transaction(engine, actor) as conn:
        row = _load(conn, identifier)
        anchor = None
        if before is not None:
            anchor = (
                conn.execute(
                    text(
                        "SELECT started_at,id FROM agent_turn WHERE id=:before AND conversation_id=:id"
                    ),
                    {"before": before, "id": identifier},
                )
                .mappings()
                .one_or_none()
            )
            if anchor is None:
                raise Forbidden("无效的对话分页游标")
        # Select only metadata first; never materialize many large event bodies at once.
        turns = (
            conn.execute(
                text(
                    "SELECT id,status,message,started_at,completed_at FROM agent_turn WHERE conversation_id=:id "
                    + ("AND (started_at,id)<(:started,:before) " if anchor else "")
                    + "ORDER BY started_at DESC,id DESC LIMIT :limit"
                ),
                {
                    "id": identifier,
                    "limit": limit + 1,
                    "started": anchor["started_at"] if anchor else None,
                    "before": before,
                },
            )
            .mappings()
            .all()
        )
        selected, size = [], 0
        for turn in turns[:limit]:
            events = conn.scalar(
                text("SELECT events FROM agent_turn WHERE id=:id"), {"id": turn["id"]}
            )
            event_bytes = len(canonical(events).encode())
            if selected and size + event_bytes > MAX_SAVED_BYTES:
                break
            size += event_bytes
            selected.append({**dict(turn), "events": events})
        return {
            "id": str(identifier),
            "role": row["role"],
            "buyer_context": row["buyer_context"],
            "version": row["version"],
            "busy": bool(row["busy"]),
            "turns": list(reversed(selected)),
            "next_cursor": str(selected[-1]["id"]) if len(turns) > len(selected) else None,
        }


def begin(engine, actor, identifier, request_key, message, *, request_payload=None):
    if not 1 <= len(request_key) <= 128 or not 1 <= len(message.strip()) <= 4000:
        raise ValueError("会话请求编号或消息长度无效")
    digest, turn_id = (
        fingerprint(
            {"message": message, **({"action": request_payload} if request_payload else {})}
        ),
        uuid4(),
    )
    with transaction(engine, actor) as conn:
        row = _load(conn, identifier, lock=True)
        existing = (
            conn.execute(
                text("SELECT * FROM agent_turn WHERE conversation_id=:id AND request_key=:key"),
                {"id": identifier, "key": request_key},
            )
            .mappings()
            .one_or_none()
        )
        if existing:
            if existing["request_hash"] != digest:
                raise Conflict("相同请求编号不能用于不同消息")
            if existing["status"] == "complete":
                return {"replay": existing["events"]}
            raise Conflict("该请求仍在处理或曾被中断，请读取会话状态后重新发起新请求")
        if row["busy"]:
            raise Conflict("该会话正在处理另一条消息")
        conn.execute(
            text(
                "UPDATE agent_turn SET status='interrupted',completed_at=now() WHERE conversation_id=:id AND status='running'"
            ),
            {"id": identifier},
        )
        conn.execute(
            text(
                "INSERT INTO agent_turn(id,conversation_id,organization_id,user_id,request_key,request_hash,message,status) VALUES(:id,:conversation,:org,:user,:key,:hash,:message,'running')"
            ),
            {
                "id": turn_id,
                "conversation": identifier,
                "org": actor.organization_id,
                "user": actor.user_id,
                "key": request_key,
                "hash": digest,
                "message": message,
            },
        )
        conn.execute(
            text(
                "UPDATE agent_conversation SET lease_id=:turn,lease_until=now()+interval '5 minutes' WHERE id=:id"
            ),
            {"turn": turn_id, "id": identifier},
        )
        return {
            "turn_id": turn_id,
            "conversation_id": identifier,
            "role": row["role"],
            "buyer_context": row["buyer_context"],
            "state": row["state"],
            "messages": [*row["messages"], {"role": "user", "content": message}],
        }


def checkpoint(engine, actor, identifier, turn_id):
    with transaction(engine, actor) as conn:
        row = _load(conn, identifier)
        if row["lease_id"] != turn_id or not row["busy"]:
            raise Conflict("当前会话处理租约已失效")


def approved_changes(engine, actor):
    with transaction(engine, actor) as conn:
        return {
            str(identifier)
            for identifier in conn.execute(
                text(
                    "SELECT c.id FROM change_request c JOIN change_approval a ON a.change_id=c.id WHERE c.kind='merchant' AND c.status='staged' AND a.approved_by=warehouse_user_id() AND a.expires_at>now() AND a.payload_hash=c.payload_hash AND a.expected_version=c.expected_version"
                )
            ).scalars()
        }


def finish(engine, actor, work, state, messages, events):
    body = {"state": state, "messages": messages, "events": events}
    if len(canonical(body).encode()) > MAX_SAVED_BYTES:
        raise Conflict("会话达到保存上限，请新建会话")
    with transaction(engine, actor) as conn:
        row = _load(conn, work["conversation_id"], lock=True)
        if row["lease_id"] != work["turn_id"] or not row["busy"]:
            raise Conflict("当前会话处理租约已失效")
        conn.execute(
            text(
                "UPDATE agent_conversation SET state=CAST(:state AS jsonb),messages=CAST(:messages AS jsonb),version=version+1,lease_id=NULL,lease_until=NULL,updated_at=now() WHERE id=:id"
            ),
            {
                "id": work["conversation_id"],
                "state": canonical(state),
                "messages": canonical(messages),
            },
        )
        conn.execute(
            text(
                "UPDATE agent_turn SET status='complete',events=CAST(:events AS jsonb),completed_at=now() WHERE id=:id"
            ),
            {"id": work["turn_id"], "events": canonical(events)},
        )


def interrupt(engine, actor, work):
    with transaction(engine, actor) as conn:
        # Revoked membership cannot write. A later valid request expires the lease and
        # marks the abandoned turn interrupted; do not use an admin bypass for cleanup.
        row = _load(conn, work["conversation_id"], lock=True, check_authorization=False)
        if row["lease_id"] == work["turn_id"]:
            conn.execute(
                text("UPDATE agent_conversation SET lease_id=NULL,lease_until=NULL WHERE id=:id"),
                {"id": work["conversation_id"]},
            )
            conn.execute(
                text(
                    "UPDATE agent_turn SET status='interrupted',completed_at=now() WHERE id=:id AND status='running'"
                ),
                {"id": work["turn_id"]},
            )


def finish_action(engine, actor, work, state, label, events):
    # Finish and label the turn atomically under the existing durable lease.
    if len(canonical({"state": state, "events": events}).encode()) > MAX_SAVED_BYTES:
        raise Conflict("会话达到保存上限，请新建会话")
    with transaction(engine, actor) as conn:
        row = _load(conn, work["conversation_id"], lock=True)
        if row["lease_id"] != work["turn_id"] or not row["busy"]:
            raise Conflict("当前操作租约已失效")
        conn.execute(
            text(
                "UPDATE agent_turn SET message=:label,status='complete',events=CAST(:events AS jsonb),completed_at=now() WHERE id=:id"
            ),
            {"label": label[:1000], "events": canonical(events), "id": work["turn_id"]},
        )
        # Current selections are injected after the breakpoint on the next model turn.
        conn.execute(
            text(
                "UPDATE agent_conversation SET state=CAST(:state AS jsonb),version=version+1,lease_id=NULL,lease_until=NULL,updated_at=now() WHERE id=:id"
            ),
            {"state": canonical(state), "id": work["conversation_id"]},
        )


def _summary(conn, row):
    from .advisor_stages import derive
    from .trip_brief import TripBrief, title

    result = dict(row)
    if row["role"] == "advisor" and row["resumable"]:
        body = conn.scalar(
            text("SELECT body FROM conversation_brief WHERE conversation_id=:id"), {"id": row["id"]}
        )
        if body:
            brief = TripBrief.model_validate(body)
            result.update(
                title=title(brief),
                stage=derive(brief)["stage"],
                offline_status=brief.offline_status,
                shared=bool(brief.share_token),
            )
    return result
