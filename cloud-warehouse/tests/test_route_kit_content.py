"""Fictional integration cases for the new content, approval and sales boundary."""

from datetime import date
from uuid import UUID

import pytest
from route_kit.models import Day, Item, Node, Quality, RouteContent, Source
from sqlalchemy import text

from cloud_warehouse import documents, product_facts, route_editor
from cloud_warehouse import route_kit_content as kit
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import transaction

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


@pytest.mark.parametrize("found,listed,nights", [(7, 6, 5), (9, 8, 7)])
def test_duration_messages_distinguish_parse_evidence_from_edited_draft(found, listed, nights):
    doc = content()
    doc.days_count, doc.nights = found, nights
    doc.quality.days_found, doc.quality.days_expected = found, listed
    issue = next(x for x in kit.issues(doc) if x["code"] == "DAYS_DIFFER_FROM_LISTING")
    assert f"当前整理稿 {found} 天（{nights} 晚）" in issue["message"]
    assert f"附件解析识别 {found} 天" in issue["message"]
    assert f"解析时线路登记 {listed} 天" in issue["message"]
    doc.days_count = found + 1
    updated = next(x for x in kit.issues(doc) if x["code"] == issue["code"])
    assert f"当前整理稿 {found + 1} 天" in updated["message"]
    assert f"附件解析识别 {found} 天" in updated["message"]
    assert updated["basis_hash"] != issue["basis_hash"]


def test_departure_mismatch_examples_cover_each_duration_and_remain_bounded():
    doc = content()
    doc.days_count, doc.nights = 9, 7
    rows = [(f"ACME-{i}", 8, date(2026, 11, 5), date(2026, 11, 12)) for i in range(100)]
    rows += [
        ("ACME-long", 10, date(2026, 10, 25), date(2026, 11, 3)),
        ("ACME-matched", 9, date(2026, 10, 1), date(2026, 10, 9)),
        ("ACME-unknown", None, date(2026, 12, 1), None),
    ]
    issue = next(x for x in kit.issues(doc, rows) if x["code"] == "DEPARTURE_DURATION_MISMATCH")
    message = issue["message"]
    assert "当前整理稿 9 天（7 晚）" in message
    assert "8 天，共 100 个团期" in message and "10 天，共 1 个团期" in message
    assert "2026-10-25 至 2026-11-03" in message and "首尾都计入" in message
    assert "2026-10-01" not in message and "2026-12-01" not in message
    assert len(message) < 500 and not issue["acknowledgeable"]
    assert not any(x["code"] == issue["code"] for x in kit.issues(doc, rows[-2:]))
    # Older callers with only a duration remain supported, without invented dates.
    message = next(x for x in kit.issues(doc, [("ACME", 6)]) if x["code"] == issue["code"])[
        "message"
    ]
    assert "6 天，共 1 个团期" in message and "2026" not in message


async def test_review_duration_dates_follow_scope_and_do_not_bypass_publication(
    database, tenant, source
):
    from cloud_warehouse.catalog import synchronize
    from cloud_warehouse.persistence import Forbidden

    from .test_sync import Connector, batch

    _, runtime = database
    store, product, _, _ = source
    data = batch()
    for number, start, end in [
        (3, "2026-10-25", "2026-11-03"),
        (4, "2026-11-05", "2026-11-12"),
    ]:
        data.departures.append(
            {**data.departures[0], "periodId": number, "departDate": start, "returnDate": end}
        )
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))

    def parse(work, _):
        doc = content({**work, "product_snapshot": {**work["product_snapshot"], "days": 9}})
        doc.nights = 7
        doc.quality.days_expected = work["product_snapshot"]["days"]
        doc.quality.days_found = 9
        return doc

    assert isinstance(documents.run_once(runtime, tenant.worker, store, parse), str)
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    issue = next(x for x in current["issues"] if x["code"] == "DEPARTURE_DURATION_MISMATCH")
    assert "2026-10-01 至 2026-10-03" in issue["message"]
    assert "2026-10-25 至 2026-11-03" in issue["message"]
    assert "2026-11-05 至 2026-11-12" in issue["message"]
    assert "2026-11-06" not in issue["message"]
    assert issue in current["test_publication"]["blockers"]
    assert not current["test_publication"]["eligible"]
    cmd = route_editor.SaveDraft(
        expected_revision=current["revision"],
        expected_source_hash=current["source_hash"],
        content=current["content"],
        source_note="ACME 核对天数",
        review_resolutions=acknowledgements(current["content"]),
    )
    assert issue in route_editor.validate(runtime, tenant.supplier, product["id"], cmd)["issues"]
    saved = route_editor.save(runtime, tenant.supplier, product["id"], cmd)
    assert issue in saved["issues"]
    with pytest.raises(Conflict, match="适用团期天数不一致"):
        route_editor.propose(
            runtime,
            tenant.supplier,
            route_editor.PublishDraft(
                target_id=product["id"], revision_id=saved["revision_id"], note="ACME 审核"
            ),
        )
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    with pytest.raises(Forbidden):
        route_editor.get(runtime, tenant.buyer, product["id"])
    cmd.expected_revision = saved["revision"]
    cmd.content.applicability.start = cmd.content.applicability.end = date(2026, 10, 25)
    scoped = route_editor.validate(runtime, tenant.supplier, product["id"], cmd)["issues"]
    message = next(x for x in scoped if x["code"] == issue["code"])["message"]
    assert "2026-10-25 至 2026-11-03" in message
    assert "2026-10-01" not in message and "2026-11-05" not in message
    cmd.content.applicability.departure_cities = ["ACME 其他出发地"]
    assert not any(
        x["code"] == issue["code"]
        for x in route_editor.validate(runtime, tenant.supplier, product["id"], cmd)["issues"]
    )


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


def test_answers_given_in_review_bind_to_the_content_they_edited():
    doc = content()
    doc.quality.issues = [
        {"code": "ATTRIBUTE_UNSUPPORTED", "path": "D1.items[0].ticket", "detail": "included"}
    ]
    assert any(x["code"] == "ATTRIBUTE_UNSUPPORTED" for x in kit.issues(doc))
    doc.days[0].items[0].ticket = "included"  # the answer edits the content, then is recorded
    answers = acknowledgements(doc)
    assert {x["code"] for x in answers} == {"ATTRIBUTE_UNSUPPORTED", "CONTENT_FACT_REVIEW"}
    assert kit.issues(doc, resolutions=answers) == []
    doc.days[0].items[0].ticket = "excluded"  # a later change to the same field needs a new answer
    assert kit.issues(doc, resolutions=answers)


def test_a_days_decision_covers_only_the_overridden_departure_span():
    doc = content()
    decided = {
        "days": {
            "upstream": doc.days_count - 1,
            "effective": doc.days_count,
            "origin": "warehouse_decision",
            "state": "pending",
        },
        "gateway": {"upstream": None, "effective": None, "origin": "source", "state": "none"},
    }
    spans = [("a", doc.days_count - 1), ("b", doc.days_count + 3)]
    found = {
        (x["code"], x["acknowledgeable"])
        for x in kit.issues(doc, spans, listing=decided)
        if x["code"] == "DEPARTURE_DURATION_MISMATCH"
    }
    assert found == {("DEPARTURE_DURATION_MISMATCH", False)}
    covered = kit.issues(doc, spans[:1], listing=decided)
    assert any(x["code"] == "DEPARTURE_DURATION_MISMATCH" and x["acknowledgeable"] for x in covered)


def test_days_conflict_needs_a_decision_that_follows_the_source(database, tenant, source):
    admin, runtime = database
    store, product, _, _ = source
    documents.run_once(runtime, tenant.worker, store, lambda work, body: content(work))
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    upstream = current["listing"]["days"]["upstream"]
    doc = kit.parse_content(current["content"])
    extra = doc.days[-1].model_copy(deep=True)
    extra.day, extra.day_id = doc.days_count + 1, ""
    for node in extra.items:
        node.node_id = ""
        for child in node.children:
            child.node_id = ""
    doc.days.append(extra)
    doc.days_count += 1
    doc = kit.prepare(doc)
    cmd = route_editor.SaveDraft(
        expected_revision=current["revision"],
        expected_source_hash=current["source_hash"],
        content=doc,
        source_note="ACME 原文复核",
    )
    conflict = next(
        i
        for i in route_editor.validate(runtime, tenant.supplier, product["id"], cmd)["issues"]
        if i["code"] == "DAYS_DIFFER_FROM_LISTING"
    )
    assert not conflict["acknowledgeable"], "a note cannot settle a days conflict"

    stale = product_facts.FactDecision(field="days", upstream=upstream + 5, value=doc.days_count)
    with pytest.raises(Conflict, match="上游"):
        route_editor.validate(
            runtime,
            tenant.supplier,
            product["id"],
            cmd.model_copy(update={"fact_decisions": [stale]}),
        )
    cmd.fact_decisions = [
        product_facts.FactDecision(
            field="days",
            upstream=upstream,
            value=doc.days_count,
            basis=[{"source": "附件行程", "value": f"{doc.days_count} 天"}],
        )
    ]
    open_ = route_editor.validate(runtime, tenant.supplier, product["id"], cmd)["issues"]
    assert not any(i["code"] == "DAYS_DIFFER_FROM_LISTING" for i in open_)
    cmd.review_resolutions = [
        {"code": i["code"], "path": i["path"], "basis_hash": i["basis_hash"], "note": "ACME 核对"}
        for i in open_
        if i["acknowledgeable"]
    ]
    saved = route_editor.save(runtime, tenant.supplier, product["id"], cmd)
    assert not saved["issues"]
    pub = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME 发布复核"
    )
    commit(runtime, tenant, route_editor.propose(runtime, tenant.supplier, pub))

    def listed():
        with transaction(runtime, tenant.supplier) as conn:
            return (
                conn.execute(
                    text(
                        "SELECT days,effective_days,days_origin FROM product_listing WHERE id=:id"
                    ),
                    {"id": product["id"]},
                )
                .mappings()
                .one()
            )

    row = listed()
    assert (row["days"], row["effective_days"], row["days_origin"]) == (
        upstream,
        doc.days_count,
        "warehouse_decision",
    )
    assert [c["field"] for c in product_facts.conflicts(runtime, tenant.supplier)] == ["days"]
    with admin.begin() as conn:  # the source moves to a third value: the source wins again
        conn.execute(
            text("UPDATE supplier_product SET days=:d WHERE id=:id"),
            {"d": upstream + 7, "id": product["id"]},
        )
    row = listed()
    assert (row["effective_days"], row["days_origin"]) == (upstream + 7, "source")
    with admin.begin() as conn:  # the source is corrected to the decided value
        conn.execute(
            text("UPDATE supplier_product SET days=:d WHERE id=:id"),
            {"d": doc.days_count, "id": product["id"]},
        )
    assert listed()["days_origin"] == "source"
    assert product_facts.conflicts(runtime, tenant.supplier) == []
