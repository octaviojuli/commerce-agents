from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import changes, documents, route_editor, route_tags
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, transaction
from cloud_warehouse.route_content import candidate, customer_projection
from cloud_warehouse.route_doc import Day, Meal, Meals, RouteDoc

from . import test_documents
from .test_documents import complete_fixture_review, parsed

source = test_documents.source


def commit(runtime, tenant, staged):
    identifier = UUID(staged["id"])
    changes.approve(runtime, tenant.supplier, identifier, staged["payload_hash"])
    return changes.apply(runtime, tenant.supplier, identifier)


def prepare(runtime, tenant, source):
    store, product, asset, _ = source
    documents.run_once(runtime, tenant.worker, store, parsed)
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    return product, current, complete_fixture_review(current["content"])


def save(runtime, tenant, product, current, content, **kwargs):
    return route_editor.save(
        runtime,
        tenant.supplier,
        product["id"],
        route_editor.SaveDraft(
            expected_revision=current["revision"],
            expected_source_hash=current["source_hash"],
            source_kind=current["source_kind"],
            source_note="ACME 按原文逐日整理",
            content=content,
            **kwargs,
        ),
    )


def test_route_draft_is_private_immutable_and_survives_unrelated_sync(database, tenant, source):
    admin, runtime = database
    product, current, content = prepare(runtime, tenant, source)
    content.days[0].summary = "ACME 人工简述"
    saved = save(runtime, tenant, product, current, content)
    assert not saved["issues"]
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    with pytest.raises(Forbidden):
        route_editor.get(runtime, tenant.buyer, product["id"])
    with pytest.raises(Conflict, match="其他人"):
        save(runtime, tenant, product, current, content)
    with (
        transaction(runtime, tenant.supplier) as conn,
        pytest.raises(DBAPIError),
        conn.begin_nested(),
    ):
        conn.execute(text("UPDATE route_content_revision SET source_note='changed'"))
    command = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME 核对原文、三餐和住宿"
    )
    staged = route_editor.propose(runtime, tenant.supplier, command)
    result = commit(runtime, tenant, staged)
    assert result["content_version"] == 1
    before = route_editor.preview(runtime, tenant.supplier, product["id"])
    assert before["content"]["days"][0]["summary"] == "ACME 人工简述"
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_product SET version=version+1,source=jsonb_set(source,'{fromPrice}','999') WHERE id=:id"
            ),
            {"id": product["id"]},
        )
    assert route_editor.preview(runtime, tenant.supplier, product["id"]) == before
    next_current = route_editor.get(runtime, tenant.supplier, product["id"])
    assert not next_current["source_changed"]
    content.days[0].summary = "ACME 第二稿"
    save(runtime, tenant, product, next_current, content)
    assert route_editor.preview(runtime, tenant.supplier, product["id"]) == before


def test_changed_source_or_new_parse_fences_saved_draft(database, tenant, source):
    admin, runtime = database
    product, current, content = prepare(runtime, tenant, source)
    saved = save(runtime, tenant, product, current, content)
    command = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME 审核"
    )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET name='ACME new source' WHERE id=:id"),
            {"id": product["id"]},
        )
    assert route_editor.get(runtime, tenant.supplier, product["id"])["source_changed"]
    with pytest.raises(Conflict, match="来源内容"):
        route_editor.propose(runtime, tenant.supplier, command)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET name=:name WHERE id=:id"),
            {"id": product["id"], "name": product["name"]},
        )
    detail = documents.get(runtime, tenant.supplier, source[2])
    documents.reparse(
        runtime,
        tenant.supplier,
        source[2],
        documents.ReparseRequest(expected_parse_id=detail["parse"]["id"], note="ACME 新规则"),
    )
    with pytest.raises(Conflict):
        route_editor.propose(runtime, tenant.supplier, command)


def test_day_edit_requires_explicit_summary_recheck_and_missing_day_cannot_publish(
    database, tenant, source
):
    _, runtime = database
    product, current, content = prepare(runtime, tenant, source)
    content.days[0].blocks[0].paragraphs = ["ACME 修改后的事实"]
    saved = save(runtime, tenant, product, current, content)
    assert any(x["code"] == "SUMMARY_RECHECK" for x in saved["issues"])
    command = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME 审核"
    )
    with pytest.raises(Conflict, match="核对简述"):
        route_editor.propose(runtime, tenant.supplier, command)
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    fixed = save(
        runtime, tenant, product, current, content, checked_summaries=[content.days[0].day_id]
    )
    assert not fixed["issues"]
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    content = RouteDoc.model_validate(fixed["content"])
    content.days.pop()
    incomplete = save(runtime, tenant, product, current, content)
    assert any(x["code"] == "DAYS_INCOMPLETE" for x in incomplete["issues"])


def test_manual_source_has_no_fabricated_attachment_and_customer_projection_is_allowlisted(
    database, tenant, source
):
    _, runtime = database
    product = source[1]
    # No successful parse: operator authored content has a separate, explicit origin.
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    assert current["source_kind"] == "manual" and current["asset_id"] is None
    doc = RouteDoc.model_validate(current["content"])
    doc.days = [
        Day(
            day=n,
            title=f"ACME {n}",
            text="ACME 原文",
            overnight="home",
            meals=Meals(
                **{
                    key: Meal(text="自理", included=False)
                    for key in ("breakfast", "lunch", "dinner")
                }
            ),
        )
        for n in range(1, 4)
    ]
    doc = candidate(doc.model_dump(mode="json"))
    doc.inclusions = ["ACME 包含"]
    doc.exclusions = ["ACME 不含"]
    result = save(runtime, tenant, product, current, doc)
    published = commit(
        runtime,
        tenant,
        route_editor.propose(
            runtime,
            tenant.supplier,
            route_editor.PublishDraft(
                target_id=product["id"], revision_id=result["revision_id"], note="ACME 人工稿复核"
            ),
        ),
    )
    original = documents.published(runtime, tenant.supplier, UUID(published["document_id"]))
    assert (
        original["asset_id"] is None
        and original["parse_id"] is None
        and original["source_kind"] == "manual"
    )
    public = customer_projection(
        original["body"],
        tags=[
            {"label": "ACME private", "state": "confirmed", "customer_visible": False},
            {"label": "ACME public", "state": "confirmed", "customer_visible": True},
        ],
    )
    assert public["tags"] == ["ACME public"]
    assert set(public) == {
        "title",
        "summary",
        "highlights",
        "tags",
        "days",
        "inclusions",
        "exclusions",
        "shopping",
        "optional",
        "policies",
        "notices",
        "applicability",
        "formation",
        "service_fee",
        "single_room_supplement",
        "cancellation_tiers",
        "traveler_requirements",
        "meeting",
        "shopping_total",
    }
    assert not any(
        key in public["days"][0]
        for key in (
            "source",
            "day_id",
            "summary_basis_hash",
            "quality",
            "available_seats",
            "settlement_prices",
        )
    )


def test_approved_tags_search_and_exclusions_persist_across_sync(database, tenant, source):
    admin, runtime = database
    product = source[1]
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_product SET source=jsonb_set(source,'{itineraryTags}','[\"ACME upstream\",\"ACME removed\"]') WHERE id=:id"
            ),
            {"id": product["id"]},
        )
    current = route_tags.get(runtime, tenant.supplier, product["id"])
    tags = [
        route_tags.Tag(**{**tag, "state": "confirmed" if index == 0 else "excluded"})
        for index, tag in enumerate(current["candidates"])
    ]
    staged = route_tags.propose(
        runtime,
        tenant.supplier,
        route_tags.Command(
            target_id=product["id"], expected_display_version=0, tags=tags, note="ACME 分类复核"
        ),
    )
    commit(runtime, tenant, staged)
    assert route_tags.get(runtime, tenant.supplier, product["id"])["candidates"] == []
    # Query-only search rechecks live buyer authorization, including its fallback.
    from cloud_warehouse import search

    with transaction(runtime, tenant.buyer) as conn:
        rows = (
            conn.execute(
                text(f"SELECT p.id FROM product_listing p {search.JOIN} WHERE {search.MATCH}"),
                search.parameters("ACME upstream"),
            )
            .scalars()
            .all()
        )
        assert product["id"] in rows
        absent = (
            conn.execute(
                text(f"SELECT p.id FROM product_listing p {search.JOIN} WHERE {search.MATCH}"),
                search.parameters("ACME removed"),
            )
            .scalars()
            .all()
        )
        assert product["id"] not in absent
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET version=version+1 WHERE id=:id"),
            {"id": product["id"]},
        )
    assert route_tags.get(runtime, tenant.supplier, product["id"])["candidates"] == []


async def test_changed_attachment_url_cannot_rebase_old_file(database, tenant, tmp_path):
    from cloud_warehouse import document_fetches
    from cloud_warehouse.assets import LocalObjectStore
    from cloud_warehouse.catalog import list_products, synchronize
    from cloud_warehouse.remote_documents import Download

    from .test_document_fetches import source_data
    from .test_documents import docx
    from .test_sync import Connector

    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(source_data()))
    store = LocalObjectStore(tmp_path / "source-objects")
    work = document_fetches.claim(runtime, tenant.worker, tenant.connection_id)
    document_fetches.finish(
        runtime, tenant.worker, store, work, "ACME.docx", Download(docx(), "", "")
    )
    documents.run_once(runtime, tenant.worker, store, parsed)
    product = list_products(runtime, tenant.supplier)[0]
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_product SET source=jsonb_set(source,'{routeAttachmentUrl}', CAST(:url AS jsonb)) WHERE id=:id"
            ),
            {"id": product["id"], "url": '"https://files.acme.example/new.docx"'},
        )
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    with pytest.raises(Conflict, match="附件地址"):
        save(runtime, tenant, product, current, complete_fixture_review(current["content"]))


def test_editor_preserves_prior_approved_copy_and_source_is_optional(database, tenant, source):
    _, runtime = database
    product, current, content = prepare(runtime, tenant, source)
    # The pre-editor document workflow may already hold human-authored copy.
    detail = documents.get(runtime, tenant.supplier, source[2])
    content = RouteDoc.model_validate(detail["parse"]["body"])
    content.cover.highlights = ["ACME 已核对的人工亮点"]
    staged = documents.stage(
        runtime,
        tenant.supplier,
        documents.DocumentReview(
            target_id=product["id"],
            expected_version=detail["product_version"],
            parse_id=detail["parse"]["id"],
            content=content,
            note="ACME 旧内容复核",
        ),
    )
    commit(runtime, tenant, staged)
    current = route_editor.get(runtime, tenant.supplier, product["id"], include_source=False)
    assert current["content"]["cover"]["highlights"] == ["ACME 已核对的人工亮点"]
    assert current["source_body"] is None and current["candidate"] is None
    assert not current["content"]["quality"]["reviewed_by"]
