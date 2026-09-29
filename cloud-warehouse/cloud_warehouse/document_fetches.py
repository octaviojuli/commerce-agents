"""Durable retrieval of source-bound attachments; fetching never approves a document."""

import hashlib
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from sqlalchemy import text

from . import documents
from .changes import Conflict, audit
from .integrations import SourceError
from .persistence import Forbidden, require_role, transaction
from .remote_documents import fetch

RECHECK_SECONDS = 24 * 60 * 60


def enqueue(conn, connection_id):
    """Publish only the queue entry with the catalog; all network work is separate."""
    rows = (
        conn.execute(
            text("""SELECT p.id,p.supplier_org_id,p.connection_id,p.version,p.source_hash,s.id AS snapshot
        FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id
        JOIN source_snapshot s ON s.sync_run_id=p.sync_run_id AND s.entity_type='route'
          AND s.external_id=p.external_id AND s.body_hash=p.source_hash
        WHERE p.connection_id=:connection AND p.supplier_org_id=warehouse_org_id()
          AND c.active AND c.connector_type='tour_b2b' AND p.status='published'
          AND jsonb_typeof(s.body->'routeAttachmentUrl')='string' AND s.body->>'routeAttachmentUrl'<>''
          AND NOT EXISTS(SELECT 1 FROM document_fetch f WHERE f.product_id=p.id AND f.product_version=p.version)"""),
            {"connection": connection_id},
        )
        .mappings()
        .all()
    )
    inserted = 0
    for row in rows:
        inserted += conn.execute(
            text("""INSERT INTO document_fetch(id,supplier_org_id,connection_id,product_id,product_version,source_snapshot_id,source_hash,next_attempt_at)
            VALUES(:job,:supplier_org_id,:connection_id,:id,:version,:snapshot,:source_hash,
              COALESCE((SELECT max(o.checked_at)+make_interval(secs=>:recheck)
                FROM document_fetch_observation o JOIN document_fetch f ON f.id=o.fetch_id
                WHERE f.product_id=:id AND f.source_hash=:source_hash),now()))
            ON CONFLICT(product_id,product_version) DO NOTHING"""),
            dict(row) | {"job": uuid4(), "recheck": RECHECK_SECONDS},
        ).rowcount
    return inserted


def seed(engine, actor, connection_id):
    from .catalog import _scope

    with transaction(engine, actor) as conn:
        _scope(conn, connection_id)
        return enqueue(conn, connection_id)


def claim(engine, actor, connection_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        conn.execute(
            text("""UPDATE document_fetch f SET status='obsolete',lease_id=NULL,lease_until=NULL,asset_id=NULL,
            last_error='PRODUCT_VERSION_CHANGED',completed_at=now()
            WHERE f.connection_id=:connection AND f.status IN ('queued','fetching','failed','fetched')
              AND EXISTS(SELECT 1 FROM supplier_product p WHERE p.id=f.product_id
                AND (p.version<>f.product_version OR p.source_hash<>f.source_hash))"""),
            {"connection": connection_id},
        )
        conn.execute(
            text("""UPDATE document_fetch SET status='failed',lease_id=NULL,lease_until=NULL,
            last_error='FETCH_ATTEMPTS_EXHAUSTED',completed_at=now()
            WHERE connection_id=:connection AND status='fetching' AND lease_until<=now() AND attempts>=3"""),
            {"connection": connection_id},
        )
        row = (
            conn.execute(
                text("""SELECT f.*,s.body FROM document_fetch f
            JOIN source_snapshot s ON s.id=f.source_snapshot_id
            JOIN supplier_connection c ON c.id=f.connection_id
            WHERE f.connection_id=:connection AND f.supplier_org_id=warehouse_org_id() AND c.active
              AND (f.status='fetched' OR f.attempts<3) AND (((f.status='queued' OR f.status='fetched') AND f.next_attempt_at<=now())
                OR (f.status='fetching' AND f.lease_until<=now()))
            ORDER BY f.next_attempt_at,f.created_at,f.id FOR UPDATE OF f SKIP LOCKED LIMIT 1"""),
                {"connection": connection_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        lease = uuid4()
        conn.execute(
            text("""UPDATE document_fetch SET status='fetching',attempts=CASE WHEN status='fetched' THEN 1 ELSE attempts+1 END,
            asset_id=NULL,
            lease_id=:lease,lease_until=now()+interval '2 minutes',last_error=NULL,completed_at=NULL WHERE id=:id"""),
            {"id": row["id"], "lease": lease},
        )
        return dict(row) | {
            "lease_id": lease,
            "attempts": 0 if row["status"] == "fetched" else row["attempts"],
        }


def finish(engine, actor, store, work, name, result):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        product = documents._product(conn, work["product_id"], lock=True)
        owned = conn.execute(
            text("""SELECT id FROM document_fetch WHERE id=:id AND lease_id=:lease
            AND status='fetching' AND lease_until>now() FOR UPDATE"""),
            {"id": work["id"], "lease": work["lease_id"]},
        ).scalar_one_or_none()
        if owned is None:
            raise Conflict("附件拉取租约已失效")
        current_hash = conn.scalar(
            text("SELECT source_hash FROM supplier_product WHERE id=:id"), {"id": product["id"]}
        )
        if product["version"] != work["product_version"] or current_hash != work["source_hash"]:
            conn.execute(
                text("""UPDATE document_fetch SET status='obsolete',lease_id=NULL,lease_until=NULL,
                last_error='PRODUCT_VERSION_CHANGED',completed_at=now() WHERE id=:id"""),
                {"id": work["id"]},
            )
            return "obsolete"
        previous = (
            conn.execute(
                text("""SELECT o.asset_id,a.file_hash,f.source_hash FROM document_fetch_observation o
            JOIN document_asset a ON a.id=o.asset_id JOIN document_fetch f ON f.id=o.fetch_id
            WHERE o.product_id=:product ORDER BY o.checked_at DESC,o.id DESC LIMIT 1"""),
                {"product": work["product_id"]},
            )
            .mappings()
            .one_or_none()
        )
        unchanged = (
            previous is not None
            and previous["file_hash"] == hashlib.sha256(result.body).hexdigest()
            and previous["source_hash"] == work["source_hash"]
        )
        # Human publication raises the product version. Identical source bytes do not
        # need another file/parse/review merely because the approved version changed.
        if unchanged:
            store.read(actor.organization_id, previous["asset_id"], previous["file_hash"])
        asset = (
            {"id": previous["asset_id"]}
            if unchanged
            else documents.save_asset(conn, actor, store, product, name, result.body)
        )
        outcome = "initial" if previous is None else "unchanged" if unchanged else "changed"
        conn.execute(
            text("""INSERT INTO document_fetch_observation(
            id,fetch_id,supplier_org_id,connection_id,product_id,asset_id,etag,last_modified,outcome)
            VALUES(:observation,:id,:org,:connection,:product,:asset,:etag,:modified,:outcome)"""),
            {
                "observation": uuid4(),
                "id": work["id"],
                "org": actor.organization_id,
                "connection": work["connection_id"],
                "product": work["product_id"],
                "asset": UUID(str(asset["id"])),
                "etag": result.etag,
                "modified": result.last_modified,
                "outcome": outcome,
            },
        )
        conn.execute(
            text("""UPDATE document_fetch SET status='fetched',asset_id=:asset,etag=:etag,last_modified=:modified,
            lease_id=NULL,lease_until=NULL,last_error=NULL,completed_at=now(),
            next_attempt_at=now()+make_interval(secs=>:recheck) WHERE id=:id"""),
            {
                "id": work["id"],
                "asset": UUID(str(asset["id"])),
                "etag": result.etag,
                "modified": result.last_modified,
                "recheck": RECHECK_SECONDS,
            },
        )
        audit(
            conn,
            actor,
            "document.fetched",
            work["id"],
            {
                "asset_id": str(asset["id"]),
                "source_snapshot_id": str(work["source_snapshot_id"]),
                "outcome": outcome,
            },
        )
    return "unchanged" if outcome == "unchanged" else "fetched"


def run_once(engine, actor, connection_id, store, allowed_hosts, downloader=fetch):
    if not allowed_hosts:
        raise SourceError("ATTACHMENT_HOSTS_UNCONFIGURED")
    work = claim(engine, actor, connection_id)
    if work is None:
        return None
    try:
        url = work["body"]["routeAttachmentUrl"]
        suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
        name = work["body"].get("routeAttachmentName") or unquote(
            PurePosixPath(urlsplit(url).path).name
        )
        if PurePosixPath(name).suffix.lower() not in (".docx", ".pdf"):
            if suffix not in (".docx", ".pdf"):
                raise SourceError("ATTACHMENT_TYPE_UNSUPPORTED")
            name += suffix
        result = downloader(url, allowed_hosts)
        try:
            documents.file_type(name, result.body)
        except Conflict:
            raise SourceError("ATTACHMENT_FILE_INVALID") from None
        return finish(engine, actor, store, work, name, result)
    except Exception as error:
        code = error.code if isinstance(error, SourceError) else "ATTACHMENT_PROCESS_FAILED"
        permanent = code in {
            "ATTACHMENT_URL_INVALID",
            "ATTACHMENT_DESTINATION_DENIED",
            "ATTACHMENT_ADDRESS_DENIED",
            "ATTACHMENT_REDIRECT_DENIED",
            "ATTACHMENT_ENCODING_DENIED",
            "ATTACHMENT_SIZE_INVALID",
            "ATTACHMENT_TYPE_UNSUPPORTED",
            "ATTACHMENT_FILE_INVALID",
        }
        # Error text can include signed URLs or supplier content; persist only safe codes.
        with transaction(engine, actor) as conn:
            changed = conn.execute(
                text("""UPDATE document_fetch SET status=CASE WHEN attempts<3 AND NOT :permanent THEN 'queued' ELSE 'failed' END,
                last_error=:code,lease_id=NULL,lease_until=NULL,
                next_attempt_at=now()+interval '30 seconds'*power(2,attempts-1),
                completed_at=CASE WHEN attempts>=3 OR :permanent THEN now() ELSE NULL END
                WHERE id=:id AND lease_id=:lease AND lease_until>now() AND status='fetching'"""),
                {"id": work["id"], "lease": work["lease_id"], "code": code, "permanent": permanent},
            ).rowcount
        return (
            "retry_pending"
            if changed and work["attempts"] < 2 and not permanent
            else "failed"
            if changed
            else "lease_lost"
        )


def listing(engine, actor, product_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        documents._product(conn, product_id)
        # There is at most one job for a product version; historical provenance is exposed
        # on its asset. This endpoint describes the current version's retrieval only.
        return [
            dict(row)
            for row in conn.execute(
                text("""SELECT f.id,f.product_version,f.status,f.attempts,f.last_error,f.asset_id,f.created_at,f.completed_at,
            CASE WHEN f.status='fetched' THEN f.next_attempt_at ELSE NULL END AS next_check_at,
            o.checked_at AS last_checked_at,o.outcome AS last_outcome,o.asset_id AS last_successful_asset_id
            FROM document_fetch f JOIN supplier_product p ON p.id=f.product_id
            LEFT JOIN LATERAL (SELECT checked_at,outcome,asset_id FROM document_fetch_observation
              WHERE fetch_id=f.id ORDER BY checked_at DESC,id DESC LIMIT 1) o ON true
            WHERE f.product_id=:product AND f.product_version=p.version"""),
                {"product": product_id},
            ).mappings()
        ]


def retry(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        row = (
            conn.execute(text("SELECT * FROM document_fetch WHERE id=:id"), {"id": identifier})
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("附件任务不存在或无权限")
        product = documents._product(conn, row["product_id"], lock=True)
        if product["version"] != row["product_version"]:
            raise Conflict("产品版本已变化，请读取当前版本任务")
        changed = conn.execute(
            text("""UPDATE document_fetch SET status='queued',attempts=0,last_error=NULL,next_attempt_at=now(),completed_at=NULL
            WHERE id=:id AND status='failed'"""),
            {"id": identifier},
        ).rowcount
        if not changed:
            raise Conflict("只可重试失败的附件任务")
        audit(conn, actor, "document.fetch_retry", identifier, {})
    return {"id": str(identifier), "status": "queued"}
