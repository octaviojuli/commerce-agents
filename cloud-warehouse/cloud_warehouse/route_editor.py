"""Supplier route drafts and one immutable publication stream, without source writes."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from . import changes, documents, product_facts, route_content
from . import route_kit_content as kit
from .changes import Conflict, audit
from .integrations import canonical, fingerprint
from .persistence import Forbidden, require_role, transaction
from .route_applicability import departure_durations
from .route_doc import Quality, RouteDoc, Source, Summary


class SaveDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    expected_source_hash: str
    source_kind: Literal["document", "manual"] = "document"
    source_note: str = Field(min_length=1, max_length=2000)
    content: kit.Content
    review_resolutions: list[dict] = Field(default_factory=list, max_length=500)
    fact_decisions: list[product_facts.FactDecision] = Field(default_factory=list, max_length=2)
    checked_summaries: list[str] = Field(default_factory=list, max_length=100)


class PublishDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: UUID
    revision_id: UUID
    note: str = Field(min_length=1, max_length=2000)
    review_mode: Literal["human", "test_auto"] = "human"


def _product(conn, product_id, *, lock=False):
    row = (
        conn.execute(
            text(
                """SELECT p.* FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id
      WHERE p.id=:id AND p.supplier_org_id=warehouse_org_id() AND c.active"""
                + (" FOR UPDATE OF p" if lock else "")
            ),
            {"id": product_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Forbidden("线路不存在或无内容维护权限")
    return row


def _issues(conn, product, doc, resolutions, decisions=()):
    return route_content.issues(
        doc,
        departure_durations(conn, product["id"], doc),
        resolutions,
        product_facts.listing(product, decisions) if kit.is_kit(doc) else None,
    )


def _parse(conn, product):
    return (
        conn.execute(
            text("""SELECT r.*,a.file_hash,a.file_name,a.product_snapshot,a.parse_generation,a.status,
      a.product_version,origin.attachment_url FROM (SELECT * FROM document_asset WHERE product_id=:id ORDER BY created_at DESC,id DESC LIMIT 1) a
      JOIN document_parse r ON r.asset_id=a.id AND r.generation=a.parse_generation
      LEFT JOIN LATERAL (SELECT s.body->>'routeAttachmentUrl' AS attachment_url
        FROM document_fetch_observation o JOIN document_fetch f ON f.id=o.fetch_id
        JOIN source_snapshot s ON s.id=f.source_snapshot_id WHERE o.asset_id=a.id
        ORDER BY o.checked_at DESC,o.id DESC LIMIT 1) origin ON true
      WHERE a.status='parsed'"""),
            {"id": product["id"]},
        )
        .mappings()
        .one_or_none()
    )


def _source_hash(product, parsed):
    raw = product["source"] or {}
    return fingerprint(
        {
            **{
                k: product[k]
                for k in ("external_id", "code", "name", "days", "gateway", "description")
            },
            "attachment_url": raw.get("routeAttachmentUrl"),
            "attachment_name": raw.get("routeAttachmentName"),
            "features": raw.get("features"),
            "file_hash": parsed["file_hash"] if parsed else None,
        }
    )


def _manual(product):
    return RouteDoc(
        schema_version="3.0",
        route_id=int(product["external_id"]) if product["external_id"].isdigit() else 0,
        route_code=product["code"],
        name=product["name"],
        department="",
        summary=Summary(days=product["days"] or 0, depart_city=product["gateway"] or ""),
        source=Source(
            attachment_name="", attachment_url="", bytes=0, parsed_at=None, parser="manual"
        ),
        quality=Quality(completeness=0),
    )


def _original(product, parsed, kind):
    if kind == "manual":
        return _manual(product)
    if kind != "document" or parsed is None:
        raise Conflict("请等待附件解析完成，或选择人工整理并说明来源")
    for key in ("external_id", "code", "name", "days", "gateway"):
        if parsed["product_snapshot"].get(key) != product[key]:
            raise Conflict("附件绑定的线路内容已变化，请根据最新资料重新上传或解析")
    if parsed["attachment_url"] is not None and parsed["attachment_url"] != (
        product["source"] or {}
    ).get("routeAttachmentUrl"):
        raise Conflict("上游附件地址已变化，请等待新文件获取并解析后再保存")
    return kit.parse_content(parsed["body"])


def _body(product, parsed, command):
    doc = command.content.model_copy(deep=True)
    original = _original(product, parsed, command.source_kind)
    if kit.is_kit(doc):
        kit.guard(doc, original)
        if not command.source_note.strip():
            raise Conflict("请填写本次整理及资料来源说明")
        if len(canonical(kit.dump(doc)).encode()) > documents.MAX_CONTENT_BYTES:
            raise Conflict("行程内容超过大小上限")
        return doc
    if kit.is_kit(original):
        raise Conflict("解析结构已升级，请对照原稿后采用新候选稿")
    if any(
        getattr(doc, k) != getattr(original, k)
        for k in ("route_id", "route_code", "name", "department", "source", "twin_of")
    ):
        raise Conflict("请使用当前候选稿，不可改写原文来源或产品身份")
    if (
        doc.schema_version not in {"2.0", "3.0"}
        or doc.quality.reviewed_by
        or doc.quality.reviewed_at
    ):
        raise Conflict("草稿不能携带审批身份，须使用当前结构")
    if not command.source_note.strip():
        raise Conflict("请填写本次整理及资料来源说明")
    if (
        len(canonical(doc.model_dump(mode="json", by_alias=True)).encode())
        > documents.MAX_CONTENT_BYTES
    ):
        raise Conflict("行程内容超过大小上限")
    for day in doc.days:
        if day.day_id in command.checked_summaries:
            day.summary_basis_hash = route_content.day_basis(day)
    return doc


def get(engine, actor, product_id, *, include_source=True):

    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        product = _product(conn, product_id)
        parsed = _parse(conn, product)
        parsing = None
        if parsed is None:
            asset = (
                conn.execute(
                    text("""SELECT id,status,parse_generation,last_error FROM document_asset
                WHERE product_id=:id ORDER BY created_at DESC,id DESC LIMIT 1"""),
                    {"id": product_id},
                )
                .mappings()
                .one_or_none()
            )
            if asset:
                parsing = dict(asset)
                previous = (
                    conn.execute(
                        text("""SELECT r.*,a.file_hash,a.file_name,a.product_version
                    FROM document_parse r JOIN document_asset a ON a.id=r.asset_id
                    WHERE a.id=:id ORDER BY r.generation DESC LIMIT 1"""),
                        {"id": asset["id"]},
                    )
                    .mappings()
                    .one_or_none()
                )
                # Read-only continuity. Save and approval still use _parse(), which
                # requires the current completed generation and rejects this preview.
                parsed = previous
                parsing["showing_previous"] = previous is not None
        draft = (
            conn.execute(
                text("SELECT * FROM route_content_revision WHERE id=:id AND product_id=:product"),
                {"id": product["latest_route_revision_id"], "product": product_id},
            )
            .mappings()
            .one_or_none()
        )
        original = kit.parse_content(parsed["body"]) if parsed else _manual(product)
        prepared = route_content.candidate(
            original.model_dump(mode="json", by_alias=True), normalize_titles=True
        )
        generated = None
        if not kit.is_kit(original):
            from .route_candidates import latest

            generated = latest(conn, product, parsed)
        if generated and generated["status"] == "complete":
            prepared = kit.parse_content(generated["body"])
        publication = documents.current_in_transaction(conn, product_id)
        # Keep approved human copy when upgrading an existing route to the editor.
        effective = (
            kit.parse_content(draft["body"])
            if draft
            else route_content.candidate(publication["body"])
            if publication and publication["parse_id"] == (parsed["id"] if parsed else None)
            else prepared
        )
        digest = _source_hash(
            product, parsed if not draft or draft["source_kind"] == "document" else None
        )
        from .route_test_publication import assess, enabled

        return {
            "product_id": product_id,
            "parsing": parsing,
            "revision": product["route_draft_version"],
            "content_version": product["content_version"],
            "published_review_mode": publication.get("review_mode", "human")
            if publication
            else None,
            "test_publication": {
                "enabled": enabled(actor, product["connection_id"]),
                **assess(prepared, departure_durations(conn, product_id, prepared)),
            }
            if kit.is_kit(prepared)
            else None,
            "revision_id": draft["id"] if draft else None,
            "base_publication_id": product["published_document_id"],
            "source_kind": draft["source_kind"] if draft else ("document" if parsed else "manual"),
            "source_note": draft["source_note"] if draft else "",
            "source_hash": digest,
            "source_changed": bool(
                draft
                and (
                    draft["source_content_hash"] != digest
                    or (
                        draft["source_kind"] == "document"
                        and (not parsed or parsed["id"] != draft["source_parse_id"])
                    )
                )
            ),
            "content": effective.model_dump(mode="json", by_alias=True),
            "candidate": prepared.model_dump(mode="json", by_alias=True)
            if include_source
            else None,
            "source_body": original.model_dump(mode="json", by_alias=True)
            if include_source
            else None,
            "asset_id": parsed["asset_id"] if parsed else None,
            "asset_product_version": parsed["product_version"] if parsed else None,
            "issues": _issues(
                conn,
                product,
                effective,
                draft["review_resolutions"] if draft else (),
                draft["fact_decisions"] if draft else (),
            ),
            "listing": product_facts.listing(product, draft["fact_decisions"] if draft else ()),
            "departure_days": [
                dict(row)
                for row in conn.execute(
                    text("""SELECT (d.return_date-d.depart_date+1) AS days,count(*) AS count
              FROM departure d WHERE d.product_id=:id AND d.status='published' AND d.return_date IS NOT NULL
              GROUP BY 1 ORDER BY 2 DESC,1 LIMIT 5"""),
                    {"id": product_id},
                ).mappings()
            ],
            "fact_decisions": draft["fact_decisions"] if draft else [],
            "saved_at": draft["created_at"] if draft else None,
            "review_resolutions": draft["review_resolutions"] if draft else [],
            "editing": {
                "id": generated["id"],
                "status": generated["status"],
                "completed_days": generated["next_day"],
                "total_days": len(prepared.days),
                "model": generated["model"],
                "policy": generated["policy"],
                "edited_days": sum(r.get("status") == "edited" for r in generated["reports"]),
                "retained_days": sum(r.get("status") == "retained" for r in generated["reports"]),
                "reports": generated["reports"] if include_source else None,
            }
            if generated
            else None,
        }


def save(engine, actor, product_id, command):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        product = _product(conn, product_id, lock=True)
        parsed = _parse(conn, product)
        digest = _source_hash(product, parsed if command.source_kind == "document" else None)
        if command.expected_revision != product["route_draft_version"]:
            raise Conflict("草稿已被其他人更新，请重新读取后合并修改")
        if command.expected_source_hash != digest:
            raise Conflict("资料来源已变化，请重新读取并核对差异")
        doc = _body(product, parsed, command)
        product_facts.check(product, command.fact_decisions)
        identifier, revision = uuid4(), product["route_draft_version"] + 1
        body = doc.model_dump(mode="json", by_alias=True)
        conn.execute(
            text("""INSERT INTO route_content_revision(id,supplier_org_id,connection_id,product_id,revision,parent_id,
          base_publication_id,source_kind,source_parse_id,source_content_hash,source_note,body,body_hash,created_by,review_resolutions,fact_decisions)
          VALUES(:id,:org,:connection,:product,:revision,:parent,:publication,:kind,:parse,:source_hash,:note,CAST(:body AS jsonb),:hash,:actor,CAST(:resolutions AS jsonb),CAST(:facts AS jsonb))"""),
            {
                "id": identifier,
                "org": actor.organization_id,
                "connection": product["connection_id"],
                "product": product_id,
                "revision": revision,
                "parent": product["latest_route_revision_id"],
                "publication": product["published_document_id"],
                "kind": command.source_kind,
                "parse": parsed["id"] if command.source_kind == "document" else None,
                "source_hash": digest,
                "note": command.source_note.strip(),
                "body": canonical(body),
                "hash": fingerprint(body),
                "actor": actor.user_id,
                "resolutions": canonical(command.review_resolutions),
                "facts": canonical([d.model_dump(mode="json") for d in command.fact_decisions]),
            },
        )
        conn.execute(
            text(
                "UPDATE supplier_product SET route_draft_version=:revision,latest_route_revision_id=:draft WHERE id=:id"
            ),
            {"id": product_id, "revision": revision, "draft": identifier},
        )
        audit(
            conn,
            actor,
            "route_content.draft_saved",
            identifier,
            {"product_id": str(product_id), "revision": revision, "body_hash": fingerprint(body)},
        )
        return {
            "revision_id": identifier,
            "revision": revision,
            "content": body,
            "issues": _issues(
                conn, product, doc, command.review_resolutions, command.fact_decisions
            ),
        }


def _review(conn, command, *, lock=False, actor=None):
    product = _product(conn, command.target_id, lock=lock)
    draft = (
        conn.execute(
            text("SELECT * FROM route_content_revision WHERE id=:id AND product_id=:product"),
            {"id": command.revision_id, "product": command.target_id},
        )
        .mappings()
        .one_or_none()
    )
    if not draft or product["latest_route_revision_id"] != draft["id"]:
        raise Conflict("草稿已更新，请提交最新保存版本")
    if draft["base_publication_id"] != product["published_document_id"]:
        raise Conflict("已发布内容已更新，请重新保存并核对草稿")
    parsed = _parse(conn, product) if draft["source_kind"] == "document" else None
    if draft["source_content_hash"] != _source_hash(product, parsed):
        raise Conflict("来源内容已变化，需重新整理并复核")
    if draft["source_kind"] == "document" and (
        not parsed or parsed["id"] != draft["source_parse_id"]
    ):
        raise Conflict("原文件已有新解析稿，请重新复核")
    _original(product, parsed, draft["source_kind"])
    body = kit.parse_content(draft["body"])
    if (
        product["days"] is not None
        and kit.day_count(body) != product["days"]
        and not kit.is_kit(body)
    ):
        raise Conflict("文档天数与线路不一致")
    durations = departure_durations(conn, product["id"], body)
    product_facts.check(product, draft["fact_decisions"])
    if command.review_mode == "test_auto":
        from .route_test_publication import validate

        if actor is None:
            raise Forbidden("测试发布缺少操作员")
        validate(actor, product, parsed, body, durations)
    elif found := _issues(
        conn, product, body, draft["review_resolutions"], draft["fact_decisions"]
    ):
        raise Conflict(found[0]["message"])
    if not command.note.strip():
        raise Conflict("请填写发布复核说明")
    return product, draft, parsed, body


def validate_change(conn, actor, payload, *, lock=False):
    require_role(conn, "supplier_admin", "product_editor")
    command = PublishDraft.model_validate(payload)
    _, draft, _, _ = _review(conn, command, lock=lock, actor=actor)
    return command.target_id, draft["revision"]


def propose(engine, actor, command):
    return changes.stage(
        engine, actor, "route_content", command.model_dump(mode="json", by_alias=True)
    )


def apply_command(conn, actor, change_id, payload):
    require_role(conn, "supplier_admin")
    command = PublishDraft.model_validate(payload)
    product, draft, parsed, doc = _review(conn, command, lock=True, actor=actor)
    product_facts.apply(conn, actor, product, draft["fact_decisions"], draft["id"])
    metrics = {}
    if command.review_mode == "test_auto":
        from .route_test_publication import assess

        metrics = assess(doc, departure_durations(conn, product["id"], doc))
    if not kit.is_kit(doc):
        doc.quality.reviewed_by = str(actor.user_id)
        doc.quality.reviewed_at = datetime.now(UTC)
    body = doc.model_dump(mode="json", by_alias=True)
    original = dict(documents._leaves(parsed["body"])) if parsed else {}
    normalized = (
        dict(
            documents._leaves(
                route_content.candidate(parsed["body"]).model_dump(mode="json", by_alias=True)
            )
        )
        if parsed
        else {}
    )
    fields = {
        path: (
            parsed["field_sources"].get(path, {})
            if parsed and original.get(path) == value
            else {"method": "normalization", "parse_id": str(parsed["id"])}
            if parsed and normalized.get(path) == value
            else {
                "method": "test_auto" if command.review_mode == "test_auto" else "human_review",
                "revision_id": str(draft["id"]),
                "reviewer": str(actor.user_id),
            }
        )
        for path, value in documents._leaves(body)
    }
    identifier = uuid4()
    conn.execute(
        text("""INSERT INTO document_publication(id,supplier_org_id,connection_id,product_id,product_version,
      asset_id,parse_id,body,body_hash,field_sources,reviewed_by,review_note,change_id,source_kind,source_note,content_version,source_content_hash,revision_id,review_mode,quality_metrics)
      VALUES(:id,:org,:connection,:product,:version,:asset,:parse,CAST(:body AS jsonb),:hash,CAST(:fields AS jsonb),:actor,:note,:change,:kind,:source_note,:content_version,:source_hash,:revision,:review_mode,CAST(:metrics AS jsonb))"""),
        {
            "id": identifier,
            "org": actor.organization_id,
            "connection": product["connection_id"],
            "product": product["id"],
            "version": product["version"] + 1,
            "asset": parsed["asset_id"] if parsed else None,
            "parse": parsed["id"] if parsed else None,
            "body": canonical(body),
            "hash": fingerprint(body),
            "fields": canonical(fields),
            "actor": actor.user_id,
            "note": command.note,
            "change": change_id,
            "kind": draft["source_kind"],
            "source_note": draft["source_note"],
            "content_version": product["content_version"] + 1,
            "source_hash": draft["source_content_hash"],
            "revision": draft["id"],
            "review_mode": command.review_mode,
            "metrics": canonical(metrics),
        },
    )
    conn.execute(
        text(
            "UPDATE supplier_product SET published_document_id=:publication,version=version+1,content_version=content_version+1 WHERE id=:id"
        ),
        {"publication": identifier, "id": product["id"]},
    )
    return {
        "document_id": str(identifier),
        "product_id": str(product["id"]),
        "content_version": product["content_version"] + 1,
        "revision_id": str(draft["id"]),
    }


def preview(engine, actor, product_id):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        product = _product(conn, product_id)
        published = documents.current_in_transaction(conn, product_id)
        if not published:
            return {"published": False, "content": None}
        return {
            "published": True,
            "content_version": published["content_version"],
            "content": route_content.customer_projection(
                published["body"],
                title=product["name_override"] or product["name"],
                tags=product["approved_tags"],
                review_mode=published.get("review_mode", "human"),
            ),
        }


def revision_detail(engine, actor, identifier):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        revision = (
            conn.execute(
                text("SELECT * FROM route_content_revision WHERE id=:id"), {"id": identifier}
            )
            .mappings()
            .one_or_none()
        )
        if revision is None:
            raise Forbidden("内容修订不存在或无权查看")
        product = _product(conn, revision["product_id"])
        parsed = (
            conn.execute(
                text("""SELECT p.*,a.file_hash,a.file_name,a.product_version FROM document_parse p
          JOIN document_asset a ON a.id=p.asset_id WHERE p.id=:id AND p.product_id=:product"""),
                {"id": revision["source_parse_id"], "product": revision["product_id"]},
            )
            .mappings()
            .one_or_none()
            if revision["source_parse_id"]
            else None
        )
        latest = _parse(conn, product) if parsed else None
        current = (
            product["latest_route_revision_id"] == identifier
            and product["published_document_id"] == revision["base_publication_id"]
            and revision["source_content_hash"] == _source_hash(product, latest)
            and (not parsed or bool(latest and latest["id"] == parsed["id"]))
        )
        return {
            "id": identifier,
            "product_id": revision["product_id"],
            "revision": revision["revision"],
            "body": revision["body"],
            "source_body": parsed["body"] if parsed else None,
            "source_note": revision["source_note"],
            "review_resolutions": revision["review_resolutions"],
            "current": current,
            "asset": {
                "id": parsed["asset_id"],
                "name": parsed["file_name"],
                "hash": parsed["file_hash"],
                "version": parsed["product_version"],
            }
            if parsed
            else None,
            "fact_decisions": revision["fact_decisions"],
            "listing": product_facts.listing(product, revision["fact_decisions"]),
            "issues": route_content.issues(
                kit.parse_content(revision["body"]),
                resolutions=revision["review_resolutions"],
                listing=product_facts.listing(product, revision["fact_decisions"]),
            ),
        }


def validate(engine, actor, product_id, command):
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor")
        product = _product(conn, product_id)
        parsed = _parse(conn, product)
        if command.expected_revision != product[
            "route_draft_version"
        ] or command.expected_source_hash != _source_hash(
            product, parsed if command.source_kind == "document" else None
        ):
            raise Conflict("草稿或来源已变化，请重新核对")
        doc = _body(product, parsed, command)
        product_facts.check(product, command.fact_decisions)
        return {
            "issues": _issues(
                conn, product, doc, command.review_resolutions, command.fact_decisions
            )
        }
