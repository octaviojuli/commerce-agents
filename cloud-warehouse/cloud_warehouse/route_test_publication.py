"""Operator-scoped development publication, with deterministic evidence gates."""

import os
from uuid import UUID

from sqlalchemy import text

from . import route_kit_content as kit
from .changes import Conflict, audit
from .persistence import Forbidden, require_role, transaction

POLICY = "test-direct-evidence-85-v1"
# These report removed/neutralized attributes or retained source text, not failed days.
SOFT = {
    "CONTENT_FACT_REVIEW",
    "NO_SHOPPING_CONFIRM",
    "AUTO_ATTACHED",
    "IMAGE_TEXT_TRANSCRIBED",
    "ATTRIBUTE_UNSUPPORTED",
    "TYPE_CHECK",
    "TEXT_NOT_IN_SOURCE",
    "NUMBER_NOT_IN_SOURCE",
    "NAME_NOT_IN_SOURCE",
    "NAME_APPROXIMATE",
    "TYPE_FROM_SECTION",
    "EXTENSION_UNSUPPORTED",
}


def enabled(actor, connection_id):
    return (
        os.getenv("WAREHOUSE_DEPLOYMENT_MODE") == "development-test"
        and os.getenv("WAREHOUSE_ROUTE_TEST_AUTO_PUBLISH") == "true"
        and os.getenv("WAREHOUSE_ROUTE_TEST_ORGANIZATION_ID") == str(actor.organization_id)
        and os.getenv("WAREHOUSE_ROUTE_TEST_USER_ID") == str(actor.user_id)
        and os.getenv("WAREHOUSE_ROUTE_TEST_CONNECTION_ID") == str(connection_id)
    )


def require_enabled(actor, connection_id):
    if not enabled(actor, connection_id):
        raise Forbidden("测试自动发布未启用，或不在指定组织、来源与操作员范围内")


def assess(content):
    doc = kit.parse_content(content)
    if not kit.is_kit(doc):
        return {
            "policy": POLICY,
            "eligible": False,
            "coverage_percent": 0,
            "blockers": [{"code": "NEW_PARSE_REQUIRED", "message": "须先使用新版附件解析"}],
        }
    q = doc.quality
    blockers = [x for x in kit.issues(doc) if x["code"] not in SOFT]

    def block(code, message):
        if not any(x["code"] == code for x in blockers):
            blockers.append({"code": code, "message": message})

    # Counters come only from the immutable parser output; apply() rejects edited bodies.
    valid = q.units == len(doc.units) and q.units > 0 and 0 <= q.direct_units <= q.units
    if not valid:
        block("COVERAGE_INVALID", "来源覆盖统计缺失或无效")
    if q.days_extracted != len(doc.days) or any(d.status != "extracted" for d in doc.days):
        block("DAY_NOT_EXTRACTED", "存在未成功整理的行程日")
    if not q.days_expected or q.days_expected != doc.days_count:
        block("DAYS_DIFFER_FROM_LISTING", kit.listing_mismatch_message(doc))
    if any(u.get("risky") for u in q.unmapped):
        block("RISKY_UNMAPPED", "仍有费用、限制等高风险原文未归入")
    if doc.shopping_status == "none" and not doc.shopping_cite:
        block("NO_SHOPPING_EVIDENCE", "无购物声明缺少明确原文引用")
    if valid and q.direct_units * 100 < q.units * 85:
        block("COVERAGE_BELOW_THRESHOLD", "有直接来源依据的内容覆盖率低于 85%")
    return {
        "policy": POLICY,
        "threshold_percent": 85,
        "coverage_percent": round(q.direct_units * 100 / q.units, 2) if valid else 0,
        "direct_units": q.direct_units,
        "total_units": q.units,
        "auto_attached_units": q.auto_attached,
        "inferred_units": q.inferred_units,
        "days_expected": q.days_expected,
        "days_found": doc.days_count,
        "days_extracted": q.days_extracted,
        "eligible": not blockers,
        "blockers": blockers,
        "definition": "直接引用原文的单元比例；推断续句与原样挂入不计入，不代表事实准确率",
    }


def validate(actor, product, parsed, body):
    require_enabled(actor, product["connection_id"])
    if (
        parsed is None
        or not kit.is_kit(body)
        or kit.basis(body) != kit.basis(kit.prepare(parsed["body"]))
    ):
        raise Conflict("测试自动发布只接受当前解析原稿，人工修改须走正常审核")
    metrics = assess(body)
    if not metrics["eligible"]:
        raise Conflict(metrics["blockers"][0]["message"])
    return metrics


def run_once(engine, actor, connection_id):
    """Process one new parse; unchanged/failed assessments never create repeat publications."""
    require_enabled(actor, connection_id)
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        row = (
            conn.execute(
                text("""SELECT p.id AS product_id,p.route_draft_version,r.id AS parse_id,r.body,r.created_at,
          revision.created_at AS draft_created_at
          FROM supplier_product p
          JOIN LATERAL (SELECT a.* FROM document_asset a WHERE a.product_id=p.id
            ORDER BY a.created_at DESC,a.id DESC LIMIT 1) a ON a.status='parsed'
          JOIN document_parse r ON r.asset_id=a.id AND r.generation=a.parse_generation
          LEFT JOIN route_content_revision revision ON revision.id=p.latest_route_revision_id
          WHERE p.supplier_org_id=warehouse_org_id() AND p.connection_id=:connection
          AND p.status='published' AND r.body->>'schema'='route-kit/1'
          AND NOT EXISTS (SELECT 1 FROM audit_event e WHERE e.organization_id=warehouse_org_id()
            AND e.action='route_content.test_assessed' AND e.resource_id=r.id
            AND e.details->>'policy'=:policy)
          ORDER BY r.created_at,r.id LIMIT 1"""),
                {"connection": connection_id, "policy": POLICY},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        row = dict(row)
        metrics = assess(row["body"])
        if row["draft_created_at"] and row["draft_created_at"] > row["created_at"]:
            # Resume our saved draft after a crash, but never replace an operator edit.
            revision = (
                conn.execute(
                    text("""SELECT source_note,body_hash FROM route_content_revision
                WHERE id=(SELECT latest_route_revision_id FROM supplier_product WHERE id=:id)"""),
                    {"id": row["product_id"]},
                )
                .mappings()
                .one()
            )
            if revision["source_note"] != POLICY or revision["body_hash"] != kit.basis(
                kit.prepare(row["body"])
            ):
                metrics["eligible"] = False
                metrics["blockers"].append(
                    {"code": "NEWER_DRAFT", "message": "解析后已有人工修订，保留草稿"}
                )
    result = {"product_id": str(row["product_id"]), "parse_id": str(row["parse_id"]), **metrics}
    if metrics["eligible"]:
        try:
            result["publication_id"] = _publish(engine, actor, row)
            result["status"] = "test_published"
        except Conflict as error:
            result["eligible"] = False
            result["status"] = "draft"
            result["blockers"].append({"code": "SOURCE_OR_DRAFT_CHANGED", "message": str(error)})
    else:
        result["status"] = "draft"
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin")
        audit(conn, actor, "route_content.test_assessed", row["parse_id"], result)
    return result


def _publish(engine, actor, row):
    from . import changes, route_editor

    current = route_editor.get(engine, actor, row["product_id"])
    if current["revision"] != row["route_draft_version"]:
        raise Conflict("解析评估后草稿已更新，保留最新修订")
    if kit.basis(kit.parse_content(current["candidate"])) != kit.basis(kit.prepare(row["body"])):
        raise Conflict("附件解析已更新，等待下一轮重新评估")
    with transaction(engine, actor) as conn:
        existing = conn.execute(
            text("""SELECT id FROM document_publication WHERE product_id=:p
            AND parse_id=:r AND review_mode='test_auto' AND body_hash=:h LIMIT 1"""),
            {
                "p": row["product_id"],
                "r": row["parse_id"],
                "h": kit.basis(kit.prepare(row["body"])),
            },
        ).scalar_one_or_none()
    if existing:
        return str(existing)
    else:
        saved = route_editor.save(
            engine,
            actor,
            row["product_id"],
            route_editor.SaveDraft(
                expected_revision=current["revision"],
                expected_source_hash=current["source_hash"],
                content=current["candidate"],
                source_note=POLICY,
            ),
        )
        staged = route_editor.propose(
            engine,
            actor,
            route_editor.PublishDraft(
                target_id=row["product_id"],
                revision_id=saved["revision_id"],
                review_mode="test_auto",
                note="开发测试自动发布；未人工审核；" + POLICY,
            ),
        )
        change_id = UUID(staged["id"])
        changes.approve(engine, actor, change_id, staged["payload_hash"])
        applied = changes.apply(engine, actor, change_id)
        return applied["document_id"]
