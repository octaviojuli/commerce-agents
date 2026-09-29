"""Real database assertions for additive parsing and stable route publication ownership."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from cloud_warehouse import documents, reparse_diff, route_editor
from cloud_warehouse.persistence import Forbidden, transaction
from cloud_warehouse.route_doc import Applicability

from . import test_documents
from .test_documents import parsed
from .test_route_editor import commit, prepare, save

source = test_documents.source


def test_reparse_keeps_read_only_previous_itinerary_without_reusing_it_for_writes(
    database, tenant, source
):
    _, runtime = database
    store, _, asset, _ = source
    product, before, content = prepare(runtime, tenant, source)
    parse_id = documents.get(runtime, tenant.supplier, asset)["parse"]["id"]
    documents.reparse(
        runtime,
        tenant.supplier,
        asset,
        documents.ReparseRequest(expected_parse_id=parse_id, note="ACME parser upgrade"),
    )
    pending = route_editor.get(runtime, tenant.supplier, product["id"])
    assert pending["parsing"]["showing_previous"] and pending["parsing"]["status"] == "queued"
    assert pending["content"] == before["content"]
    with pytest.raises(documents.Conflict):
        save(runtime, tenant, product, pending, content)
    with pytest.raises(Forbidden):
        route_editor.get(runtime, tenant.buyer, product["id"])
    documents.run_once(runtime, tenant.worker, store, parsed)
    assert route_editor.get(runtime, tenant.supplier, product["id"])["parsing"] is None


def test_reparse_diff_is_read_only_then_explicitly_appends_generation(database, tenant, source):
    admin, runtime = database
    store, product, asset, _ = source
    documents.run_once(runtime, tenant.worker, store, parsed)
    before = documents.get(runtime, tenant.supplier, asset)

    def new_parser(work, body):
        doc = parsed(work, body)
        doc.source.parser = "ACME new rules"
        doc.days[0].text += " ACME extra"
        return doc

    report = reparse_diff.run(runtime, tenant.supplier, store, new_parser)
    assert report["items"][0]["differences"] and not report["items"][0]["queued"]
    after = documents.get(runtime, tenant.supplier, asset)
    assert (
        after["parse"] == before["parse"]
        and after["parse_generation"] == before["parse_generation"]
    )
    with pytest.raises(Forbidden):
        reparse_diff.run(runtime, tenant.buyer, store, new_parser)
    report = reparse_diff.run(runtime, tenant.supplier, store, new_parser, apply=True)
    assert report["items"][0]["queued"]
    documents.run_once(runtime, tenant.worker, store, new_parser)
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["parse_generation"] == before["parse_generation"] + 1
    with admin.connect() as conn:
        old = (
            conn.execute(
                text("SELECT body_hash,body FROM document_parse WHERE id=:id"),
                {"id": before["parse"]["id"]},
            )
            .mappings()
            .one()
        )
    assert (
        old["body"] == before["parse"]["body"] and old["body_hash"] == before["parse"]["body_hash"]
    )


def test_model_field_provenance_persists_only_in_supplier_parse(database, tenant, source):
    _, runtime = database
    store, _, asset, _ = source

    def model_candidate(work, body):
        doc = parsed(work, body)
        return documents.ParsedDocument(
            doc,
            field_sources={
                "/days/0/title": {
                    "method": "model_extract",
                    "source_lines": [1],
                    "source_hash": "a" * 64,
                }
            },
        )

    documents.run_once(runtime, tenant.worker, store, model_candidate)
    detail = documents.get(runtime, tenant.supplier, asset)
    assert detail["parse"]["field_sources"]["/days/0/title"]["method"] == "model_extract"
    with pytest.raises(Forbidden):
        documents.get(runtime, tenant.buyer, asset)


def test_current_route_publication_ignores_descriptive_scope_and_preserves_history(
    database, tenant, source
):
    admin, runtime = database
    product, current, content = prepare(runtime, tenant, source)
    with admin.connect() as conn:
        departure = (
            conn.execute(
                text("SELECT * FROM departure WHERE product_id=:id ORDER BY id LIMIT 1"),
                {"id": product["id"]},
            )
            .mappings()
            .one()
        )
    start = departure["depart_date"]
    end = start + timedelta(days=5)
    content.applicability = Applicability(start=start, end=end, version_label="ACME A")
    saved = save(runtime, tenant, product, current, content)
    command = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME scope A reviewed"
    )
    first = commit(runtime, tenant, route_editor.propose(runtime, tenant.supplier, command))
    original = documents.published(runtime, tenant.supplier, UUID(first["document_id"]))
    assert documents.current(runtime, tenant.buyer, product["id"])["id"] == UUID(
        first["document_id"]
    )
    preview = documents.current(runtime, tenant.buyer, product["id"], route_preview=True)
    assert preview["id"] == UUID(first["document_id"])
    assert "publication_notice" not in preview["body"]
    assert "source" not in preview["body"] and "quality" not in preview["body"]
    selected = documents.current(runtime, tenant.buyer, product["id"], departure_id=departure["id"])
    assert selected["id"] == UUID(first["document_id"]) and "source" not in selected["body"]
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    content.applicability = Applicability(
        start=end + timedelta(days=1),
        end=end + timedelta(days=30),
        version_label="ACME B",
        departure_cities=["ACME 文中城市"],
    )
    content.name = "ACME 校对后的标题"
    content.summary.days -= 1
    content.days.pop()
    saved = save(runtime, tenant, product, current, content)
    command = route_editor.PublishDraft(
        target_id=product["id"], revision_id=saved["revision_id"], note="ACME scope B reviewed"
    )
    second = commit(runtime, tenant, route_editor.propose(runtime, tenant.supplier, command))
    preview = documents.current(runtime, tenant.buyer, product["id"], route_preview=True)
    assert preview["body"]["applicability"]["version_label"] == "ACME B"
    selected_preview = documents.current(
        runtime, tenant.buyer, product["id"], departure_id=departure["id"], route_preview=True
    )
    assert selected_preview["id"] == UUID(second["document_id"])
    assert selected_preview["body"]["title"] == content.name
    assert "publication_notice" not in selected_preview["body"]
    assert documents.current(runtime, tenant.buyer, product["id"], departure_id=departure["id"])[
        "id"
    ] == UUID(second["document_id"])
    assert (
        documents.published(runtime, tenant.supplier, UUID(first["document_id"]))["body"]
        == original["body"]
    )
    with transaction(runtime, tenant.supplier) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM document_publication WHERE product_id=:id"),
                {"id": product["id"]},
            )
            == 2
        )
