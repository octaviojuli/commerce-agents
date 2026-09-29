"""Upload, parse, review and publish separate immutable document evidence."""

import hashlib
import io
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID, uuid4

from defusedxml import ElementTree
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import changes
from . import route_kit_content as kit
from .assets import MAX_BYTES, OBJECT_WRITE_LOCK, ObjectStore
from .changes import Conflict, audit
from .integrations import SourceError, canonical, fingerprint
from .persistence import Forbidden, require_role, transaction
from .route_doc import RouteDoc

MAX_CONTENT_BYTES = 2_000_000


class DocumentParseError(ValueError):
    """Only fixed, non-sensitive error codes may cross the parser/job boundary."""

    CODES = frozenset(
        {
            "DOCUMENT_TEXT_MISSING",
            "DOCUMENT_TEXT_INSUFFICIENT",
            "DOCUMENT_PDF_READ_FAILED",
            "DOCUMENT_PARSER_UNAVAILABLE",
            "DOCUMENT_PARSE_TIMEOUT",
            "DOCUMENT_PARSE_OUTPUT_LIMIT",
            "DOCUMENT_PARSE_FAILED",
            "DOCUMENT_EXTRACTION_RETIRED",
            "DOCUMENT_PARSE_OPEN_FAILED",
            "DOCUMENT_PARSE_SEGMENT_FAILED",
            "DOCUMENT_PARSE_DAYS_FAILED",
            "DOCUMENT_PARSE_TERMS_FAILED",
            "DOCUMENT_PARSE_VALIDATION_FAILED",
        }
    )

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "DOCUMENT_PARSE_FAILED"
        super().__init__(self.code)


@dataclass(frozen=True)
class ParsedDocument:
    document: kit.Content
    extraction: dict | None = None
    field_sources: dict = field(default_factory=dict)
    media: dict[str, bytes] = field(default_factory=dict)


class DocumentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    parse_id: UUID
    content: RouteDoc
    note: str = Field(min_length=1, max_length=2000)


class ReparseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_parse_id: UUID
    note: str = Field(min_length=1, max_length=2000)


def file_type(name, body):
    if not 0 < len(body) <= MAX_BYTES:
        raise Conflict("文档不能为空或超过 20 MB")
    suffix = PurePath(name).suffix.lower()
    if suffix == ".pdf" and body.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix != ".docx" or not body.startswith(b"PK"):
        raise Conflict("仅支持内容与扩展名一致的 DOCX 或 PDF 文件")
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 2000 or len(set(names)) != len(names):
                raise ValueError("archive entries")
            if sum(item.file_size for item in entries) > 60_000_000:
                raise ValueError("archive expansion")
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ValueError("document missing")
            for item in entries:
                if item.flag_bits & 1 or "vbaproject" in item.filename.lower():
                    raise ValueError("encrypted or macro document")
                if item.filename.endswith((".xml", ".rels")):
                    ElementTree.fromstring(archive.read(item))
    except Exception as error:
        raise Conflict("DOCX 压缩包或 XML 校验失败") from error
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _product(conn, product_id, *, lock=False):
    row = (
        conn.execute(
            text(
                "SELECT p.id,p.supplier_org_id,p.connection_id,p.version,p.external_id,p.code,p.name,p.days,p.gateway "
                "FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id "
                "WHERE p.id=:id AND p.supplier_org_id=warehouse_org_id() AND c.active "
                + ("FOR UPDATE OF p" if lock else "")
            ),
            {"id": product_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("线路不存在或无维护权限")
    return row


def upload(engine, actor, store: ObjectStore, product_id, name, body):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        _product(conn, product_id)
    file_type(name, body)
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        product = _product(conn, product_id, lock=True)
        return save_asset(conn, actor, store, product, name, body)


def save_asset(conn, actor, store, product, name, body):
    """Called inside an authorized transaction with the product row locked."""
    media = file_type(name, body)
    product_id = product["id"]
    file_hash, identifier = hashlib.sha256(body).hexdigest(), uuid4()
    name = PurePath(name.replace("\\", "/")).name[:200]
    if any(ord(character) < 32 for character in name):
        raise Conflict("文件名不能包含控制字符")
    existing = (
        conn.execute(
            text(
                "SELECT id,status FROM document_asset WHERE product_id=:id AND product_version=:version AND file_hash=:hash"
            ),
            {"id": product_id, "version": product["version"], "hash": file_hash},
        )
        .mappings()
        .one_or_none()
    )
    if existing:
        return {**dict(existing), "duplicate": True}
    # Write before metadata commit; an interrupted transaction can leave an inaccessible
    # orphan object, never a published pointer to a partially written file.
    # Keep the collector out until metadata commits (or rolls back). A file may
    # exist before INSERT, so locking document_asset alone cannot protect it.
    conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": OBJECT_WRITE_LOCK})
    store.put(actor.organization_id, identifier, body)
    snapshot = {
        key: str(value) if isinstance(value, UUID) else value for key, value in product.items()
    }
    conn.execute(
        text("""INSERT INTO document_asset(id,supplier_org_id,connection_id,product_id,
        product_version,product_snapshot,file_name,media_type,file_hash,byte_size,created_by)
        VALUES(:id,:org,:connection,:product,:version,CAST(:snapshot AS jsonb),:name,:media,:hash,:size,:actor)"""),
        {
            "id": identifier,
            "org": actor.organization_id,
            "connection": product["connection_id"],
            "product": product_id,
            "version": product["version"],
            "snapshot": canonical(snapshot),
            "name": name,
            "media": media,
            "hash": file_hash,
            "size": len(body),
            "actor": actor.user_id,
        },
    )
    audit(
        conn,
        actor,
        "document.uploaded",
        identifier,
        {"product_id": str(product_id), "hash": file_hash},
    )
    return {"id": str(identifier), "status": "queued", "duplicate": False}


def _asset(conn, identifier):
    row = (
        conn.execute(text("SELECT * FROM document_asset WHERE id=:id"), {"id": identifier})
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("文档不存在或无权限")
    return row


def get(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        row = _asset(conn, identifier)
        if row["supplier_org_id"] != actor.organization_id:
            raise Forbidden("文档不存在或无维护权限")
        parsed = (
            conn.execute(
                text(
                    "SELECT id,generation,parser_version,body,body_hash,field_sources,created_at "
                    "FROM document_parse WHERE asset_id=:id AND generation=:generation"
                ),
                {"id": identifier, "generation": row["parse_generation"]},
            )
            .mappings()
            .one_or_none()
        )
        origin = (
            conn.execute(
                text(
                    """SELECT f.id,f.source_snapshot_id,f.source_hash,o.etag,o.last_modified,
                    o.checked_at AS completed_at FROM document_fetch_observation o
                    JOIN document_fetch f ON f.id=o.fetch_id WHERE o.asset_id=:id
                    ORDER BY o.checked_at DESC,o.id DESC LIMIT 1"""
                ),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        return {
            key: row[key]
            for key in (
                "id",
                "product_id",
                "product_version",
                "file_name",
                "file_hash",
                "byte_size",
                "status",
                "attempts",
                "last_error",
                "parse_generation",
            )
        } | {
            "parse": dict(parsed) if parsed else None,
            "origin": dict(origin) if origin else None,
            "current_product_version": conn.scalar(
                text("SELECT version FROM supplier_product WHERE id=:id"), {"id": row["product_id"]}
            ),
            "publications": [
                dict(item)
                for item in conn.execute(
                    text(
                        "SELECT id,product_version,created_at FROM document_publication WHERE asset_id=:id ORDER BY created_at,id"
                    ),
                    {"id": identifier},
                ).mappings()
            ],
        }


def parse_history(engine, actor, identifier, *, before=None, limit=10):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        asset = _asset(conn, identifier)
        if asset["supplier_org_id"] != actor.organization_id:
            raise Forbidden("文档不存在或无维护权限")
        anchor = None
        if before:
            anchor = conn.scalar(
                text("SELECT generation FROM document_parse WHERE id=:id AND asset_id=:asset"),
                {"id": before, "asset": identifier},
            )
            if anchor is None:
                raise Forbidden("分页游标不属于当前文件")
        rows = (
            conn.execute(
                text(
                    "SELECT id,generation,parser_version,created_at FROM document_parse WHERE asset_id=:asset "
                    + ("AND generation<:anchor " if anchor is not None else "")
                    + "ORDER BY generation DESC LIMIT :limit"
                ),
                {"asset": identifier, "anchor": anchor, "limit": limit + 1},
            )
            .mappings()
            .all()
        )
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


def parse_detail(engine, actor, identifier, parse_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        asset = _asset(conn, identifier)
        if asset["supplier_org_id"] != actor.organization_id:
            raise Forbidden("文档不存在或无维护权限")
        row = (
            conn.execute(
                text(
                    "SELECT id,generation,parser_version,body,body_hash,field_sources,created_at "
                    "FROM document_parse WHERE id=:id AND asset_id=:asset"
                ),
                {"id": parse_id, "asset": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("解析版本不存在或不属于当前文件")
        return dict(row)


def extraction_detail(engine, actor, identifier, parse_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        asset = _asset(conn, identifier)
        if asset["supplier_org_id"] != actor.organization_id:
            raise Forbidden("文档不存在或无维护权限")
        row = (
            conn.execute(
                text("SELECT extraction FROM document_parse WHERE id=:id AND asset_id=:asset"),
                {"id": parse_id, "asset": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("解析版本不存在或不属于当前文件")
        return {"available": row["extraction"] is not None, "evidence": row["extraction"]}


def reparse(engine, actor, identifier, command: ReparseRequest):
    if not command.note.strip():
        raise Conflict("须填写重新解析原因")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        asset = _asset(conn, identifier)
        # Serialize against publication, which also locks this product first.
        _product(conn, asset["product_id"], lock=True)
        generation = conn.scalar(
            text("""UPDATE document_asset a SET parse_generation=parse_generation+1,
              status='queued',attempts=0,last_error=NULL
              WHERE a.id=:id AND a.status='parsed' AND EXISTS(
                SELECT 1 FROM document_parse p WHERE p.id=:parse AND p.asset_id=a.id
                AND p.generation=a.parse_generation)
              RETURNING parse_generation"""),
            {"id": identifier, "parse": command.expected_parse_id},
        )
        if generation is None:
            raise Conflict("解析状态或版本已变化，请刷新后重试")
        audit(
            conn,
            actor,
            "document.reparse_requested",
            identifier,
            {
                "previous_parse_id": str(command.expected_parse_id),
                "generation": generation,
                "note": command.note.strip(),
            },
        )
    return {"id": str(identifier), "status": "queued", "parse_generation": generation}


def listing(engine, actor, product_id, *, before=None, limit=25):
    if not 1 <= limit <= 100:
        raise Conflict("分页大小须为 1 至 100")
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        _product(conn, product_id)
        anchor = None
        if before:
            anchor = _asset(conn, before)
            if anchor["product_id"] != product_id:
                raise Forbidden("分页游标不属于当前线路")
        rows = (
            conn.execute(
                text(
                    "SELECT id,file_name,status,product_version,created_at FROM document_asset WHERE product_id=:product "
                    + ("AND (created_at,id)<(:created,:before) " if anchor else "")
                    + "ORDER BY created_at DESC,id DESC LIMIT :limit"
                ),
                {
                    "product": product_id,
                    "created": anchor["created_at"] if anchor else None,
                    "before": before,
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


def retry(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        row = _asset(conn, identifier)
        _product(conn, row["product_id"])
        changed = conn.execute(
            text("""UPDATE document_asset SET status='queued',attempts=0,last_error=NULL,
                parse_generation=parse_generation+CASE
                  WHEN last_error='DOCUMENT_EXTRACTION_RETIRED' THEN 1 ELSE 0 END
                WHERE id=:id AND status='failed'"""),
            {"id": identifier},
        ).rowcount
        if not changed:
            raise Conflict("只可重新排队失败的解析任务")
        audit(conn, actor, "document.retry", identifier, {})
    return {"id": str(identifier), "status": "queued"}


def claim(engine, actor):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        conn.execute(
            text("""UPDATE document_asset SET status='failed',lease_id=NULL,lease_until=NULL,last_error='PARSER_ATTEMPTS_EXHAUSTED'
            WHERE supplier_org_id=warehouse_org_id() AND status='parsing' AND lease_until<=now() AND attempts>=3""")
        )
        row = (
            conn.execute(
                text("""SELECT a.*
            FROM document_asset a JOIN supplier_connection c ON c.id=a.connection_id
            WHERE a.supplier_org_id=warehouse_org_id() AND c.active AND a.attempts<3
              AND (a.status='queued' OR (a.status='parsing' AND a.lease_until<=now()))
            ORDER BY a.created_at,a.id FOR UPDATE OF a SKIP LOCKED LIMIT 1""")
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            return None
        lease = uuid4()
        conn.execute(
            text(
                "UPDATE document_asset SET status='parsing',attempts=attempts+1,lease_id=:lease,lease_until=now()+interval '3 minutes' WHERE id=:id"
            ),
            {"lease": lease, "id": row["id"]},
        )
        return dict(row) | {"lease_id": lease}


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, prefix + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, prefix + "/" + str(index))
    else:
        yield prefix, value


def finish_parse(engine, actor, work, parsed: RouteDoc | ParsedDocument, *, store=None):
    media = parsed.media if isinstance(parsed, ParsedDocument) else {}
    extraction = None
    field_sources = {}
    if isinstance(parsed, ParsedDocument):
        field_sources = parsed.field_sources
        extraction, parsed = parsed.extraction, parsed.document
        if extraction is not None:
            if len(canonical(extraction).encode()) > 8_100_000:
                raise Conflict("解析证据超过大小上限")
            evidence = extraction.get("extraction", {})
            if (
                evidence.get("input_sha256") != work["file_hash"]
                or fingerprint(evidence) != parsed.source.extraction_hash
            ):
                raise Conflict("解析证据与原文件或候选稿不一致")
    parsed = kit.parse_content(parsed.model_dump(mode="json", by_alias=True))
    if kit.is_kit(parsed):
        parsed = kit.prepare(parsed)
        if parsed.source.sha256 != work["file_hash"]:
            raise Conflict("解析来源与原文件不一致")
        parsed.source.file_name = work["file_name"]
        return _finish_kit_parse(engine, actor, work, parsed, media, store)
    parsed.quality.reviewed_by = ""
    parsed.quality.reviewed_at = None
    parsed.twin_of = None
    parsed.source.etag = work["file_hash"]
    parsed.source.attachment_name = work["file_name"]
    parsed.source.attachment_url = ""
    parsed.source.bytes = work["byte_size"]
    if not parsed.source.parser.strip():
        raise Conflict("解析器版本不能为空")
    body = parsed.model_dump(mode="json")
    if len(canonical(body).encode()) > MAX_CONTENT_BYTES:
        raise Conflict("解析结果超过大小上限")
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        owned = conn.execute(
            text("""UPDATE document_asset SET status='parsed',lease_id=NULL,lease_until=NULL,last_error=NULL
            WHERE id=:id AND lease_id=:lease AND lease_until>now() AND status='parsing'
              AND parse_generation=:generation RETURNING id"""),
            {"id": work["id"], "lease": work["lease_id"], "generation": work["parse_generation"]},
        ).scalar_one_or_none()
        if owned is None:
            raise Conflict("文档解析租约已失效")
        _product(conn, work["product_id"])
        identifier = uuid4()
        fields = {
            path: {
                "file_hash": work["file_hash"],
                "parser": parsed.source.parser,
                "method": "product_snapshot"
                if path
                in {
                    "/route_id",
                    "/route_code",
                    "/name",
                    "/department",
                    "/summary/days",
                    "/summary/depart_city",
                }
                else "parser_metadata"
                if path.startswith(("/source/", "/quality/"))
                else "rule_parse",
                "product_version": work["product_version"],
                **(
                    {"extraction_hash": parsed.source.extraction_hash}
                    if parsed.source.extraction_hash
                    else {}
                ),
            }
            for path, _ in _leaves(body)
        }
        for path, provenance in field_sources.items():
            if path in fields and provenance.get("method") == "model_extract":
                fields[path].update(provenance)
        conn.execute(
            text("""INSERT INTO document_parse(id,asset_id,supplier_org_id,connection_id,product_id,generation,parser_version,body,body_hash,field_sources,extraction)
            VALUES(:id,:asset,:org,:connection,:product,:generation,:parser,CAST(:body AS jsonb),:hash,CAST(:fields AS jsonb),CAST(:extraction AS jsonb))"""),
            {
                "id": identifier,
                "asset": work["id"],
                "org": actor.organization_id,
                "connection": work["connection_id"],
                "product": work["product_id"],
                "parser": parsed.source.parser,
                "generation": work["parse_generation"],
                "body": canonical(body),
                "hash": fingerprint(body),
                "fields": canonical(fields),
                "extraction": canonical(extraction) if extraction is not None else None,
            },
        )
        audit(
            conn,
            actor,
            "document.parsed",
            identifier,
            {"asset_id": str(work["id"]), "hash": fingerprint(body)},
        )
        return str(identifier)


def run_once(engine, actor, store: ObjectStore, parser: Callable):
    work = claim(engine, actor)
    if not work:
        return None
    try:
        body = store.read(actor.organization_id, work["id"], work["file_hash"])
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(45):
                try:
                    if not renew(engine, actor, work):
                        break
                except Exception:
                    break  # finish_parse rechecks the lease before any publication.

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            parsed = parser(work, body)
        finally:
            stop.set()
            thread.join(timeout=5)
        return finish_parse(engine, actor, work, parsed, store=store)
    except Exception as error:
        code = error.code if isinstance(error, DocumentParseError) else "DOCUMENT_PARSE_FAILED"
        if code not in DocumentParseError.CODES:
            code = "DOCUMENT_PARSE_FAILED"
        with transaction(engine, actor) as conn:
            require_role(conn, "sync_worker")
            changed = conn.execute(
                text("""UPDATE document_asset SET status='failed',lease_id=NULL,lease_until=NULL,last_error=:error
                WHERE id=:id AND lease_id=:lease AND lease_until>now()"""),
                {"id": work["id"], "lease": work["lease_id"], "error": code},
            ).rowcount
        return {
            "id": str(work["id"]),
            "status": "failed" if changed else "lease_lost",
            "error": code,
        }


def _review(conn, payload, *, lock=False):
    command = DocumentReview.model_validate_json(canonical(payload))
    product = _product(conn, command.target_id, lock=lock)
    parsed = (
        conn.execute(
            text("""SELECT p.*,a.file_hash,a.product_version,a.status,a.parse_generation FROM document_parse p
        JOIN document_asset a ON a.id=p.asset_id WHERE p.id=:id AND p.supplier_org_id=warehouse_org_id()"""),
            {"id": command.parse_id},
        )
        .mappings()
        .one_or_none()
    )
    if parsed is None or parsed["product_id"] != product["id"]:
        raise Forbidden("解析结果不属于当前供应商线路")
    if (
        product["version"] != command.expected_version
        or parsed["product_version"] != product["version"]
    ):
        raise Conflict("线路已更新，请上传绑定当前版本的文档后重新复核")
    if parsed["status"] != "parsed":
        raise Conflict("文档尚未成功解析")
    if parsed["generation"] != parsed["parse_generation"]:
        raise Conflict("已有新解析版本，请根据最新解析稿重新复核")
    if kit.is_kit(parsed["body"]):
        raise Conflict("请在线路内容工作台整理并提交此版本")
    doc = command.content
    original = RouteDoc.model_validate(parsed["body"])
    if any(
        getattr(doc, field) != getattr(original, field)
        for field in (
            "route_id",
            "route_code",
            "department",
            "source",
            "schema_version",
            "twin_of",
        )
    ):
        raise Conflict("不可改写文档来源及产品身份")
    if doc.quality.reviewed_by or doc.quality.reviewed_at or not command.note.strip():
        raise Conflict("复核身份由当前审批人确定，须填写复核说明")
    if not doc.days or [day.day for day in doc.days] != list(range(1, doc.summary.days + 1)):
        raise Conflict("逐日行程必须完整、连续并与声明天数一致")
    if any(not day.title.strip() or not day.text.strip() for day in doc.days):
        raise Conflict("每天行程必须有标题和内容")
    if doc.schema_version in {"2.0", "3.0"}:
        from .route_content import issues

        if found := issues(doc):
            raise Conflict(found[0]["message"])
    if len(canonical(doc.model_dump(mode="json")).encode()) > MAX_CONTENT_BYTES:
        raise Conflict("复核文档超过大小上限")
    return command, product, parsed


def validate_change(conn, actor, payload, *, lock=False):
    require_role(conn, "supplier_admin", "product_editor")
    command, _, _ = _review(conn, payload, lock=lock)
    return command.target_id, command.expected_version


def stage(engine, actor, command: DocumentReview):
    return changes.stage(engine, actor, "document", command.model_dump(mode="json"))


def apply_command(conn, actor, change_id, payload):
    require_role(conn, "supplier_admin")
    command, product, parsed = _review(conn, payload, lock=True)
    identifier = uuid4()
    doc = command.content
    doc.quality.reviewed_by = str(actor.user_id)
    doc.quality.reviewed_at = datetime.now(UTC)
    body = doc.model_dump(mode="json")
    before = dict(_leaves(parsed["body"]))
    fields = {
        path: (
            {
                "file_hash": parsed["file_hash"],
                "method": "human_review",
                "reviewer": str(actor.user_id),
            }
            if path not in before or before[path] != value
            else parsed["field_sources"].get(path, {})
        )
        for path, value in _leaves(body)
    }
    conn.execute(
        text("""INSERT INTO document_publication(id,supplier_org_id,connection_id,product_id,product_version,
        asset_id,parse_id,body,body_hash,field_sources,reviewed_by,review_note,change_id)
        VALUES(:id,:org,:connection,:product,:version,:asset,:parse,CAST(:body AS jsonb),:hash,CAST(:fields AS jsonb),:actor,:note,:change)"""),
        {
            "id": identifier,
            "org": actor.organization_id,
            "connection": product["connection_id"],
            "product": product["id"],
            "version": product["version"] + 1,
            "asset": parsed["asset_id"],
            "parse": parsed["id"],
            "body": canonical(body),
            "hash": fingerprint(body),
            "fields": canonical(fields),
            "actor": actor.user_id,
            "note": command.note,
            "change": change_id,
        },
    )
    conn.execute(
        text(
            "UPDATE supplier_product SET published_document_id=:document,version=version+1 WHERE id=:id"
        ),
        {"document": identifier, "id": product["id"]},
    )
    return {
        "document_id": str(identifier),
        "product_id": str(product["id"]),
        "product_version": product["version"] + 1,
    }


def published(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin", "supplier_admin", "product_editor", "auditor")
        row = (
            conn.execute(
                text("""SELECT d.*,(CASE WHEN d.content_version>0 THEN p.published_document_id IS DISTINCT FROM d.id ELSE p.version<>d.product_version END) AS historical FROM document_publication d
            JOIN supplier_product p ON p.id=d.product_id WHERE d.id=:id"""),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("文档版本不存在或未获授权")
        result = dict(row)
        if row["supplier_org_id"] != actor.organization_id:
            return _sales_document(result)
        if row["supplier_org_id"] != actor.organization_id:
            result.pop("review_note", None)
            result.pop("change_id", None)
        return result


def download(engine, actor, store: ObjectStore, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin", "supplier_admin", "product_editor", "auditor")
        row = _asset(conn, identifier)
        try:
            body = store.read(row["supplier_org_id"], row["id"], row["file_hash"])
        except (OSError, ValueError) as error:
            raise SourceError("DOCUMENT_OBJECT_UNAVAILABLE") from error
        audit(conn, actor, "document.downloaded", identifier, {"hash": row["file_hash"]})
        return {"name": row["file_name"], "media_type": row["media_type"], "body": body}


def current_in_transaction(conn, product_id, *, departure_id=None, sales=False):
    row = (
        conn.execute(
            text("""SELECT d.* FROM supplier_product p JOIN document_publication d
        ON d.id=p.published_document_id AND d.product_id=p.id AND (d.content_version>0 OR d.product_version=p.version)
        WHERE p.id=:id AND p.status='published'"""),
            {"id": product_id},
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        return None
    if sales:
        from .route_applicability import select_publication

        return select_publication(conn, dict(row), departure_id)
    return dict(row)


def current(engine, actor, product_id, *, departure_id=None, route_preview=False):
    with transaction(engine, actor) as conn:
        require_role(conn, "advisor", "buyer_admin", "supplier_admin", "product_editor", "auditor")
        # Keep route_preview as a compatible argument; all route reads use the
        # current publication, whether or not a departure has been selected.
        result = current_in_transaction(conn, product_id, departure_id=departure_id, sales=True)
        if result and (route_preview or result["supplier_org_id"] != actor.organization_id):
            return _sales_document(result)
        return result


def _sales_document(document):
    from .route_content import customer_projection

    return {
        **{
            key: document[key]
            for key in (
                "id",
                "product_id",
                "product_version",
                "content_version",
                "created_at",
                "historical",
            )
            if key in document
        },
        "body": customer_projection(
            document["body"], review_mode=document.get("review_mode", "human")
        ),
    }


def _finish_kit_parse(engine, actor, work, parsed, media, store):
    cover = media.get("cover")
    media_id = uuid4() if cover else None
    if cover:
        if store is None or not cover.startswith(b"\xff\xd8\xff") or len(cover) > 4_000_000:
            raise Conflict("封面图片无效")
        parsed.source.cover_image = str(media_id)
    body = kit.dump(parsed)
    if len(canonical(body).encode()) > MAX_CONTENT_BYTES:
        raise Conflict("解析结果超过大小上限")
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        owned = conn.execute(
            text("""UPDATE document_asset SET status='parsed',lease_id=NULL,
          lease_until=NULL,last_error=NULL WHERE id=:id AND lease_id=:lease AND lease_until>now()
          AND status='parsing' AND parse_generation=:generation RETURNING id"""),
            dict(id=work["id"], lease=work["lease_id"], generation=work["parse_generation"]),
        ).scalar_one_or_none()
        if owned is None:
            raise Conflict("文档解析租约已失效")
        _product(conn, work["product_id"])
        identifier = uuid4()
        fields = {
            path: {
                "file_hash": work["file_hash"],
                "parser": parsed.source.parser,
                "method": "route_kit_candidate",
                "product_version": work["product_version"],
            }
            for path, _ in _leaves(body)
        }
        conn.execute(
            text("""INSERT INTO document_parse(id,asset_id,supplier_org_id,connection_id,
          product_id,generation,parser_version,body,body_hash,field_sources)
          VALUES(:id,:asset,:org,:connection,:product,:generation,:parser,CAST(:body AS jsonb),
          :hash,CAST(:fields AS jsonb))"""),
            dict(
                id=identifier,
                asset=work["id"],
                org=actor.organization_id,
                connection=work["connection_id"],
                product=work["product_id"],
                generation=work["parse_generation"],
                parser=parsed.source.parser,
                body=canonical(body),
                hash=fingerprint(body),
                fields=canonical(fields),
            ),
        )
        if cover:
            conn.execute(
                text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": OBJECT_WRITE_LOCK}
            )
            store.put(actor.organization_id, media_id, cover)
            conn.execute(
                text("""INSERT INTO document_media(id,parse_id,asset_id,supplier_org_id,
              connection_id,product_id,kind,file_hash,byte_size) VALUES(:id,:parse,:asset,:org,
              :connection,:product,'cover',:hash,:size)"""),
                dict(
                    id=media_id,
                    parse=identifier,
                    asset=work["id"],
                    org=actor.organization_id,
                    connection=work["connection_id"],
                    product=work["product_id"],
                    hash=hashlib.sha256(cover).hexdigest(),
                    size=len(cover),
                ),
            )
        audit(
            conn,
            actor,
            "document.parsed",
            identifier,
            {"asset_id": str(work["id"]), "hash": fingerprint(body)},
        )
        return str(identifier)


def renew(engine, actor, work):
    with transaction(engine, actor) as conn:
        require_role(conn, "sync_worker")
        return bool(
            conn.execute(
                text("""UPDATE document_asset SET lease_until=now()+interval '3 minutes'
          WHERE id=:id AND lease_id=:lease AND lease_until>now() AND status='parsing'
          AND parse_generation=:generation RETURNING id"""),
                dict(id=work["id"], lease=work["lease_id"], generation=work["parse_generation"]),
            ).scalar_one_or_none()
        )
