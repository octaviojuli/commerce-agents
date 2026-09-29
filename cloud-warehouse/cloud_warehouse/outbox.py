"""Database-only search consumer: atomic effects/receipts and bounded durable retries."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import search
from .changes import audit
from .persistence import require_role, transaction

CONSUMER = "product_search_v1"
MAX_ATTEMPTS = 5
REFRESH = {
    "catalog.published",
    "catalog.applied",
    "excel_import.applied",
    "merchant.applied",
    "product_display.applied",
    "document.applied",
    "route_content.applied",
    "route_tags.applied",
}
# These events do not change indexed text. Prices, inventory and access remain live reads.
IGNORE = {
    "offer.applied",
    "price_book.applied",
    "departure_sales.applied",
    "inventory.applied",
    "pricing.applied",
    "source.created",
    "source.changed",
    "distribution_grant.changed",
    "invitation.created",
    "invitation.requested",
    "invitation.approve",
    "invitation.revoke",
}


def _receipt(conn, row, status, error=None):
    attempts = (row["attempts"] or 0) + 1
    if status == "waiting" and attempts >= (row["max_attempts"] or MAX_ATTEMPTS):
        status = "failed"
    conn.execute(
        text("""INSERT INTO outbox_receipt(event_id,consumer,organization_id,status,attempts,max_attempts,next_attempt_at,last_error,completed_at)
        VALUES(:id,:consumer,warehouse_org_id(),:status,:attempts,:max_attempts,now()+(:delay*interval '1 second'),:error,
          CASE WHEN :status IN ('done','ignored') THEN now() END)
        ON CONFLICT(consumer,event_id) DO UPDATE SET status=excluded.status,attempts=excluded.attempts,
          next_attempt_at=excluded.next_attempt_at,last_error=excluded.last_error,completed_at=excluded.completed_at"""),
        {
            "id": row["id"],
            "consumer": CONSUMER,
            "status": status,
            "attempts": attempts,
            "max_attempts": row["max_attempts"] or MAX_ATTEMPTS,
            "delay": min(300, 5 * 2 ** min(attempts - 1, 6)),
            "error": error,
        },
    )
    return status


def run_once(engine, actor):
    # No network or external side effect occurs under this lock. PostgreSQL releases it
    # on worker death, so a separate lease/heartbeat renewer would weaken atomicity.
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        conn.execute(text("SET LOCAL statement_timeout='30s'"))
        if not conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": CONSUMER + str(actor.organization_id)},
        ):
            return {"busy": True}
        conn.execute(
            text("""INSERT INTO search_worker_state(organization_id) VALUES(warehouse_org_id())
          ON CONFLICT(organization_id) DO UPDATE SET heartbeat_at=now()""")
        )
        rows = (
            conn.execute(
                text("""SELECT e.id,e.kind,r.attempts,r.max_attempts FROM outbox_event e
          LEFT JOIN outbox_receipt r ON r.event_id=e.id AND r.consumer=:consumer
          WHERE e.organization_id=warehouse_org_id() AND (r.event_id IS NULL OR
            (r.status='waiting' AND r.attempts<r.max_attempts AND r.next_attempt_at<=now()))
          ORDER BY e.created_at,e.id LIMIT 50 FOR UPDATE OF e SKIP LOCKED"""),
                {"consumer": CONSUMER},
            )
            .mappings()
            .all()
        )
        changed, failure = 0, None
        if any(row["kind"] in REFRESH for row in rows):
            try:
                with conn.begin_nested():
                    changed = search.rebuild(conn)
            except SQLAlchemyError:
                # Never persist driver messages: they can contain source data or credentials.
                failure = "SEARCH_DATABASE_ERROR"
        counts = {"done": 0, "ignored": 0, "waiting": 0, "failed": 0}
        for row in rows:
            if row["kind"] in REFRESH:
                status = _receipt(conn, row, "waiting" if failure else "done", failure)
            elif row["kind"] in IGNORE:
                status = _receipt(conn, row, "ignored")
            else:
                status = _receipt(conn, row, "failed", "UNSUPPORTED_EVENT_KIND")
            counts[status] += 1
        error = failure or ("UNSUPPORTED_EVENT_KIND" if counts["failed"] else None)
        conn.execute(
            text("""UPDATE search_worker_state SET last_error=:error,
          last_success_at=CASE WHEN :success THEN now() ELSE last_success_at END
          WHERE organization_id=warehouse_org_id()"""),
            {"error": error, "success": bool(rows) and not error},
        )
        return {"consumed": len(rows), "changed_products": changed, **counts}


def rebuild(engine, actor):
    """Operator recovery from a missing/corrupt projection, without changing receipts."""
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        conn.execute(text("SET LOCAL statement_timeout='30s'"))
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": CONSUMER + str(actor.organization_id)},
        )
        return search.rebuild(conn)


def retry(engine, actor, event_id):
    """Retry the same consumer/event identity; retained attempts preserve its history."""
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        # Event lock also serializes against the normal consumer.
        identifier = conn.scalar(
            text("SELECT id FROM outbox_event WHERE id=:id FOR UPDATE"), {"id": event_id}
        )
        if identifier is None:
            return False
        retried = conn.execute(
            text("""UPDATE outbox_receipt SET status='waiting',next_attempt_at=now(),
          max_attempts=attempts+:max WHERE event_id=:id AND consumer=:consumer AND status='failed'"""),
            {"id": event_id, "consumer": CONSUMER, "max": MAX_ATTEMPTS},
        ).rowcount
        if retried:
            audit(conn, actor, "outbox.retried", event_id, {"consumer": CONSUMER})
        return bool(retried)


def status(engine, actor):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "auditor", "sync_worker")
        totals = dict(
            conn.execute(
                text("""SELECT
          count(*) FILTER(WHERE r.event_id IS NULL OR r.status='waiting') AS pending,
          count(*) FILTER(WHERE r.status='failed') AS failed,
          count(*) FILTER(WHERE r.status='done') AS done,
          count(*) FILTER(WHERE r.status='ignored') AS ignored,
          min(e.created_at) FILTER(WHERE r.event_id IS NULL OR r.status IN ('waiting','failed')) AS oldest_pending_at
          FROM outbox_event e LEFT JOIN outbox_receipt r ON r.event_id=e.id AND r.consumer=:consumer
          WHERE e.organization_id=warehouse_org_id()"""),
                {"consumer": CONSUMER},
            )
            .mappings()
            .one()
        )
        worker = (
            conn.execute(
                text(
                    "SELECT heartbeat_at,last_success_at,last_error,heartbeat_at<now()-interval '30 seconds' AS stale FROM search_worker_state WHERE organization_id=warehouse_org_id()"
                )
            )
            .mappings()
            .first()
        )
        failures = [
            dict(row)
            for row in conn.execute(
                text("""SELECT event_id,attempts,last_error,next_attempt_at FROM outbox_receipt
          WHERE organization_id=warehouse_org_id() AND consumer=:consumer AND status='failed'
          ORDER BY event_id LIMIT 100"""),
                {"consumer": CONSUMER},
            ).mappings()
        ]
        projection = dict(
            conn.execute(
                text(f"""SELECT count(*) AS products,
          count(*) FILTER(WHERE {search.CURRENT}) AS current,
          count(*) FILTER(WHERE ({search.CURRENT}) IS NOT TRUE) AS stale_or_missing
          FROM supplier_product p LEFT JOIN product_search s ON s.product_id=p.id
          WHERE p.supplier_org_id=warehouse_org_id()""")
            )
            .mappings()
            .one()
        )
        return {
            "consumer": CONSUMER,
            **totals,
            "worker": dict(worker) if worker else None,
            "failures": failures,
            "projection": projection,
        }
