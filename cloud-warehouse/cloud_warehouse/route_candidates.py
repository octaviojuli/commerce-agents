"""Supplier-scoped daily editing jobs; never publish or overwrite a human revision."""

from uuid import uuid4

from sqlalchemy import text

from . import route_content, route_editor
from .changes import Conflict, audit
from .integrations import canonical
from .persistence import Forbidden, require_role, transaction
from .route_doc import RouteDoc
from .route_editing import POLICY, rewrite_day


def latest(conn, product, parsed):
    if not parsed:
        return None
    return (
        conn.execute(
            text("""SELECT * FROM route_content_candidate WHERE parse_id=:parse
      AND policy=:policy AND source_hash=:hash ORDER BY created_at DESC LIMIT 1"""),
            {
                "parse": parsed["id"],
                "policy": POLICY,
                "hash": route_editor._source_hash(product, parsed),
            },
        )
        .mappings()
        .one_or_none()
    )


def seed(engine, actor, model):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        products = (
            conn.execute(
                text("""SELECT p.* FROM supplier_product p
          JOIN supplier_connection c ON c.id=p.connection_id
          WHERE p.supplier_org_id=warehouse_org_id() AND p.status='published' AND c.active
          ORDER BY p.id""")
            )
            .mappings()
            .all()
        )
        added = 0
        for product in products:
            parsed = route_editor._parse(conn, product)
            if (
                not parsed
                or parsed["body"].get("schema") == "route-kit/1"
                or latest(conn, product, parsed)
            ):
                continue
            try:
                original = route_editor._original(product, parsed, "document")
            except Conflict:
                continue
            doc = route_content.candidate(original.model_dump(mode="json"), normalize_titles=True)
            valid = [d.day for d in doc.days] == list(range(1, doc.summary.days + 1)) and bool(
                doc.days
            )
            reports = [] if valid else [{"status": "retained", "code": "DAYS_INCOMPLETE"}]
            conn.execute(
                text("""INSERT INTO route_content_candidate
              (id,supplier_org_id,connection_id,product_id,asset_id,parse_id,policy,source_hash,model,status,body,reports,completed_at)
              VALUES(:id,:org,:connection,:product,:asset,:parse,:policy,:hash,:model,:status,CAST(:body AS jsonb),CAST(:reports AS jsonb),
              CASE WHEN :status='complete' THEN now() ELSE NULL END) ON CONFLICT DO NOTHING"""),
                {
                    "id": uuid4(),
                    "org": actor.organization_id,
                    "connection": product["connection_id"],
                    "product": product["id"],
                    "asset": parsed["asset_id"],
                    "parse": parsed["id"],
                    "policy": POLICY,
                    "hash": route_editor._source_hash(product, parsed),
                    "model": model,
                    "status": "pending" if valid else "complete",
                    "body": canonical(doc.model_dump(mode="json")),
                    "reports": canonical(reports),
                },
            )
            added += 1
        return added


def claim(engine, actor):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        row = (
            conn.execute(
                text("""SELECT j.* FROM route_content_candidate j
          JOIN supplier_connection c ON c.id=j.connection_id
          WHERE j.supplier_org_id=warehouse_org_id() AND j.policy=:policy AND c.active
            AND (j.status='pending' OR (j.status='working' AND j.lease_until<=now()))
          ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1"""),
                {"policy": POLICY},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        lease = uuid4()
        conn.execute(
            text("""UPDATE route_content_candidate SET status='working',lease_id=:lease,
          lease_until=now()+interval '3 minutes',attempts=attempts+1 WHERE id=:id"""),
            {"id": row["id"], "lease": lease},
        )
        return dict(row) | {"lease_id": lease}


def finish(engine, actor, work, day, report):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        try:
            product = route_editor._product(conn, work["product_id"], lock=True)
            parsed = route_editor._parse(conn, product)
            current = (
                product["status"] == "published"
                and parsed is not None
                and parsed["id"] == work["parse_id"]
                and route_editor._source_hash(product, parsed) == work["source_hash"]
            )
        except Forbidden:
            current = False
        owned = conn.execute(
            text("""SELECT id FROM route_content_candidate WHERE id=:id
          AND lease_id=:lease AND lease_until>now() AND status='working' FOR UPDATE"""),
            {"id": work["id"], "lease": work["lease_id"]},
        ).scalar_one_or_none()
        if owned is None:
            raise Conflict("整理任务租约已失效")
        body = RouteDoc.model_validate(work["body"])
        if current:
            body.days[work["next_day"]] = day
        reports = work["reports"] + [report]
        index = work["next_day"] + 1
        status = "obsolete" if not current else "complete" if index == len(body.days) else "pending"
        conn.execute(
            text("""UPDATE route_content_candidate SET body=CAST(:body AS jsonb),reports=CAST(:reports AS jsonb),
          next_day=:index,status=:status,attempts=0,lease_id=NULL,lease_until=NULL,
          completed_at=CASE WHEN :status IN ('complete','obsolete') THEN now() ELSE NULL END WHERE id=:id"""),
            {
                "id": work["id"],
                "body": canonical(body.model_dump(mode="json")),
                "reports": canonical(reports),
                "index": index,
                "status": status,
            },
        )
        if status in {"complete", "obsolete"}:
            audit(
                conn,
                actor,
                "route_content.candidate_completed",
                work["id"],
                {"status": status, "policy": POLICY, "parse_id": str(work["parse_id"])},
            )
        return status


def run_once(engine, actor, ask):
    work = claim(engine, actor)
    if work is None:
        return None
    day = RouteDoc.model_validate(work["body"]).days[work["next_day"]]
    if work["attempts"] >= 3:
        result, report = day, {"status": "retained", "code": "WORKER_INTERRUPTED"}
    else:
        result, report = rewrite_day(day, ask)
    report["day"] = day.day
    return finish(engine, actor, work, result, report)
