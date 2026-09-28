"""Fictional integration cases for the new content, approval and sales boundary."""

from uuid import UUID

import pytest
from route_kit.models import Day, Item, Node, Quality, RouteContent, Source

from cloud_warehouse import documents, route_editor
from cloud_warehouse import route_kit_content as kit
from cloud_warehouse.changes import Conflict

from . import test_documents
from .test_route_editor import commit

source = test_documents.source


def content(work=None):
    work = work or {
        "file_hash": "a" * 64,
        "file_name": "ACME.docx",
        "product_snapshot": {"code": "ACME", "name": "ACME 行程", "days": 2},
    }
    p = work["product_snapshot"]
    count = p.get("days") or 2
    return kit.prepare(
        RouteContent(
            code=p["code"],
            listed_name=p["name"],
            title=p["name"],
            days_count=count,
            source=Source(
                file_name=work["file_name"],
                media="docx",
                sha256=work["file_hash"],
                parser="route-kit-1",
            ),
            quality=Quality(days_expected=count),
            units=[
                {
                    "id": 1,
                    "text": "ACME 游览，含车费，不含个人消费。",
                    "page": None,
                    "origin": "text",
                }
            ],
            days=[
                Day(
                    day=i,
                    title="ACME 城市",
                    summary="ACME 游览安排",
                    units=[1],
                    items=[
                        Node(type="poi", name="ACME 景点", description="ACME 原文安排", cite=[1])
                    ],
                )
                for i in range(1, count + 1)
            ],
            inclusions=[Item(text="包含车费", cite=[1])],
            exclusions=[Item(text="不含个人消费", cite=[1])],
        )
    )


def acknowledgements(doc):
    return [
        {
            "code": x["code"],
            "path": x["path"],
            "basis_hash": x["basis_hash"],
            "note": "ACME 人工逐项核对",
        }
        for x in kit.issues(doc)
        if x["acknowledgeable"]
    ]


def test_stable_identifiers_and_strict_schema():
    doc = content()
    wire = kit.dump(doc)
    assert wire["schema"] == "route-kit/1"
    assert kit.dump(kit.prepare(wire)) == wire
    assert len({d.items[0].node_id for d in doc.days}) == 2
    wire["schema"] = "route-kit/untrusted"
    with pytest.raises(ValueError):
        kit.parse_content(wire)


def test_new_edit_invalidates_review_and_days_cannot_be_acknowledged():
    doc = content()
    resolutions = acknowledgements(doc)
    assert kit.issues(doc, resolutions=resolutions) == []
    doc.days[0].summary = "ACME 变更安排"
    assert any(x["code"] == "CONTENT_FACT_REVIEW" for x in kit.issues(doc, resolutions=resolutions))
    doc.days.pop()
    assert any(x["code"] == "DAYS_INCOMPLETE" and not x["acknowledgeable"] for x in kit.issues(doc))


def test_evidence_cannot_be_rewritten_and_projection_is_allowlisted():
    doc = content()
    original = doc.model_copy(deep=True)
    doc.units[0]["text"] = "ACME changed evidence"
    with pytest.raises(Conflict):
        kit.guard(doc, original)
    projection = kit.customer_projection(original)

    def check(value):
        if isinstance(value, dict):
            assert not set(value).intersection(
                {"cite", "units", "review", "quality", "issues", "auto", "source"}
            )
            for v in value.values():
                check(v)
        elif isinstance(value, list):
            for v in value:
                check(v)

    check(projection)
    assert projection["days"][0]["items"][0]["name"] == "ACME 景点"


def test_conflicting_shopping_and_bad_scope_are_blocked():
    doc = content()
    doc.shopping_status = "none"
    doc.days[0].items[0].type = "shopping"
    assert any(
        x["code"] == "SHOPPING_CONFLICT" and not x["acknowledgeable"] for x in kit.issues(doc)
    )
    doc.days[0].title = "ACME-180KM-ACME"
    assert any(x["code"] == "TITLE_HAS_MEASURE" for x in kit.issues(doc))


def test_kit_draft_publish_and_customer_reads(database, tenant, source):
    _, runtime = database
    store, product, asset, _ = source
    result = documents.run_once(runtime, tenant.worker, store, lambda work, body: content(work))
    assert isinstance(result, str), result
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    doc = kit.parse_content(current["content"])
    assert current["editing"] is None
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    assert documents.current(runtime, tenant.buyer, product["id"], route_preview=True) is None
    cmd = route_editor.SaveDraft(
        expected_revision=current["revision"],
        expected_source_hash=current["source_hash"],
        content=doc,
        source_note="ACME 原文复核",
    )
    saved = route_editor.save(runtime, tenant.supplier, product["id"], cmd)
    pub = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME 发布复核"
    )
    with pytest.raises(Conflict):
        route_editor.propose(runtime, tenant.supplier, pub)
    cmd.expected_revision = saved["revision"]
    cmd.review_resolutions = acknowledgements(doc)
    saved = route_editor.save(runtime, tenant.supplier, product["id"], cmd)
    assert not saved["issues"]
    pub.revision_id = saved["revision_id"]
    result = commit(runtime, tenant, route_editor.propose(runtime, tenant.supplier, pub))
    read = documents.published(runtime, tenant.buyer, UUID(result["document_id"]))
    assert read["body"]["schema"] == "route-kit/1" and "units" not in read["body"]
    assert route_editor.preview(runtime, tenant.supplier, product["id"])["published"]


def test_derived_cover_stays_private_until_publication(database, tenant, source):
    from cloud_warehouse import route_media
    from cloud_warehouse.persistence import Forbidden

    _, runtime = database
    store, product, _, _ = source
    cover = b"\xff\xd8\xffACME fixture image"
    result = documents.run_once(
        runtime,
        tenant.worker,
        store,
        lambda work, body: documents.ParsedDocument(content(work), media={"cover": cover}),
    )
    assert isinstance(result, str), result
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    doc = kit.parse_content(current["content"])
    media = UUID(doc.source.cover_image)
    assert route_media.read(runtime, tenant.supplier, store, media) == cover
    with pytest.raises(Forbidden):
        route_media.read(runtime, tenant.buyer, store, media)
    saved = route_editor.save(
        runtime,
        tenant.supplier,
        product["id"],
        route_editor.SaveDraft(
            expected_revision=current["revision"],
            expected_source_hash=current["source_hash"],
            content=doc,
            source_note="ACME 图片核对",
            review_resolutions=acknowledgements(doc),
        ),
    )
    assert not saved["issues"]
    commit(
        runtime,
        tenant,
        route_editor.propose(
            runtime,
            tenant.supplier,
            route_editor.PublishDraft(
                target_id=product["id"], revision_id=saved["revision_id"], note="ACME 审核"
            ),
        ),
    )
    assert route_media.read(runtime, tenant.buyer, store, media) == cover


def test_reader_boundary_uses_json_without_credentials():
    import hashlib
    import json

    from tour.api.route_kit_worker import _child

    body = test_documents.docx()
    request = {
        "name": "ACME.docx",
        "sha256": hashlib.sha256(body).hexdigest(),
        "product": {"code": "ACME", "name": "ACME", "days": 3},
    }
    wire = json.loads(_child("read", json.dumps(request).encode() + b"\n" + body, {}, 120))
    assert len(wire["document"]["lines"]) >= 3
    assert wire["document"]["file_name"] == "ACME.docx"


def test_search_facts_preserve_unknown_shopping_and_count_explicit_meals():
    from cloud_warehouse.route_search_facts import derive

    doc = content()
    assert derive(None)["shopping"] == "unknown"
    assert derive(kit.dump(doc))["shopping"] == "unknown"
    doc.shopping_status = "none"
    doc.days[0].meals.breakfast.status = "included"
    doc.days[1].meals.breakfast.status = "self"
    facts = derive(kit.dump(doc))
    assert facts["shopping"] == "ok"
    assert facts["meals_included"] == 1
    doc.days[0].items[0].type = "shopping"
    assert derive(kit.dump(doc))["shopping"] == "conflict"


def test_kit_diff_groups_attributes_under_day_and_named_node():
    from cloud_warehouse.reparse_diff import differences

    before = content()
    after = before.model_copy(deep=True)
    after.days[0].items[0].ticket = "included"
    changed = differences(kit.dump(before), kit.dump(after))
    assert len(changed) == 1 and changed[0]["path"].endswith("/nodes/ACME 景点/0/ticket")


def test_itinerary_sections_are_complete_paged_and_public(monkeypatch):
    from cloud_warehouse import itinerary_reads

    doc = content()
    doc.days[0].items = [Node(type="poi", name=f"ACME {i}", cite=[1]) for i in range(20)]
    projection = kit.customer_projection(kit.prepare(doc))
    monkeypatch.setattr(documents, "current", lambda *a, **k: {"id": "ACME", "body": projection})
    pages = []
    offset = 0
    while True:
        row = itinerary_reads.read(
            None,
            None,
            "WP-00000000-0000-0000-0000-000000000001",
            section="day",
            day=1,
            offset=offset,
        )
        pages += row["items"]
        offset = row["next_offset"]
        if offset is None:
            break
    assert len(pages) == 20
    assert all("cite" not in n for n in pages)
    assert [n["name"] for n in pages] == [f"ACME {i}" for i in range(20)]
