"""Private advisor materials share the immutable object lock, backup and collection roots."""

import hashlib
from pathlib import PurePath
from uuid import uuid4

from sqlalchemy import text

from . import copilot_crypto, trip_brief
from . import copilot_records as records
from .advisor import parse_id
from .assets import MAX_BYTES, OBJECT_WRITE_LOCK
from .changes import Conflict, audit
from .persistence import Forbidden, transaction


def upload(engine, actor, store, identifier, request_id, filename, body):
    if store is None:
        raise Conflict("私有材料存储尚未配置")
    media = (
        "application/pdf"
        if body.startswith(b"%PDF-")
        else "image/png"
        if body.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if body.startswith(b"\xff\xd8\xff")
        else None
    )
    if not media or not 0 < len(body) <= MAX_BYTES - 64:
        raise ValueError("请上传 20 MB 以内的 PDF、PNG 或 JPEG 材料")
    name = PurePath(filename).name[:150].replace("\n", " ").replace("\r", " ")
    with transaction(engine, actor) as conn:
        records.deal(conn, identifier, lock=True)
        previous = (
            conn.execute(
                text("SELECT id,deal_id,file_hash FROM advisor_asset WHERE request_id=:id"),
                {"id": request_id},
            )
            .mappings()
            .one_or_none()
        )
        if previous:
            if (
                previous["deal_id"] != identifier
                or copilot_crypto.decrypt(
                    store.read(actor.organization_id, previous["id"], previous["file_hash"]),
                    previous["id"],
                )
                != body
            ):
                raise Conflict("上传编号已用于其他文件")
            return {"id": str(previous["id"])}
        brief, _ = trip_brief.load(conn, identifier)
        if not brief.departure_id:
            raise Conflict("请先选定团期，再保存证件材料，以确定材料保留期限")
        expires = conn.scalar(
            text(
                "SELECT (return_date + 91)::timestamp AT TIME ZONE 'Asia/Shanghai' FROM departure WHERE id=:id"
            ),
            {"id": parse_id(brief.departure_id, "WD-")},
        )
        if expires is None:
            raise Conflict("团期不可用，请先重新核对")
        conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": OBJECT_WRITE_LOCK})
        asset = uuid4()
        encrypted = copilot_crypto.encrypt(body, asset)
        digest = hashlib.sha256(encrypted).hexdigest()
        store.put(actor.organization_id, asset, encrypted)
        conn.execute(
            text("""INSERT INTO advisor_asset(id,deal_id,organization_id,user_id,filename,media_type,file_hash,byte_size,request_id,retain_until)
          VALUES(:id,:deal,:org,:user,:name,:media,:hash,:size,:request,:expires)"""),
            {
                "id": asset,
                "deal": identifier,
                "org": actor.organization_id,
                "user": actor.user_id,
                "name": name,
                "media": media,
                "hash": digest,
                "size": len(encrypted),
                "request": request_id,
                "expires": expires,
            },
        )
        audit(conn, actor, "advisor.material_uploaded", asset, {"deal_id": str(identifier)})
        return {"id": str(asset), "filename": name, "media_type": media, "verified": False}


def download(engine, actor, store, identifier):
    with transaction(engine, actor) as conn:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM advisor_asset WHERE id=:id AND purged_at IS NULL AND retain_until>now()"
                ),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise Forbidden("材料不存在或不属于当前顾问")
        if store is None:
            raise Conflict("私有材料存储不可用")
        body = store.read(row["organization_id"], identifier, row["file_hash"])
        body = copilot_crypto.decrypt(body, identifier)
        audit(
            conn, actor, "advisor.material_downloaded", identifier, {"deal_id": str(row["deal_id"])}
        )
        return {"body": body, "media_type": row["media_type"], "filename": row["filename"]}
