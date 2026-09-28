"""Fictional tests for the opt-in publication boundary and threshold edge cases."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import changes, documents, route_editor
from cloud_warehouse import route_test_publication as testpub
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal

from . import test_route_kit_content
from .test_route_kit_content import content

source = test_route_kit_content.source


def complete(work=None, body=None):
    doc = content(work)
    doc.quality.units = len(doc.units)
    doc.quality.direct_units = len(doc.units)
    doc.quality.days_extracted = len(doc.days)
    for day in doc.days:
        day.status = "extracted"
    return doc


def enable(monkeypatch, actor, connection):
    for key, value in {
        "WAREHOUSE_DEPLOYMENT_MODE": "development-test",
        "WAREHOUSE_ROUTE_TEST_AUTO_PUBLISH": "true",
        "WAREHOUSE_ROUTE_TEST_ORGANIZATION_ID": str(actor.organization_id),
        "WAREHOUSE_ROUTE_TEST_USER_ID": str(actor.user_id),
        "WAREHOUSE_ROUTE_TEST_CONNECTION_ID": str(connection),
    }.items():
        monkeypatch.setenv(key, value)


def test_threshold_does_not_round_up_or_count_raw_fallback():
    doc = complete()
    doc.units = [{"id": i, "text": "ACME evidence"} for i in range(1, 1001)]
    doc.quality.units = 1000
    doc.quality.direct_units = 849
    doc.quality.auto_attached = 151
    doc.quality.mapped_units = 1000
    assert not testpub.assess(doc)["eligible"]
    doc.quality.direct_units = 850
    assert testpub.assess(doc)["eligible"]


@pytest.mark.parametrize(
    "failure",
    ["source_only", "missing_day", "listing_mismatch", "risky", "unread_image", "unknown_issue"],
)
def test_full_coverage_does_not_override_missing_or_failed_content(failure):
    doc = complete()
    if failure == "source_only":
        doc.days[0].status = "source_only"
    elif failure == "missing_day":
        doc.days.pop()
    elif failure == "listing_mismatch":
        doc.quality.days_expected += 1
    elif failure == "risky":
        doc.quality.unmapped = [{"unit": 1, "risky": True}]
    else:
        doc.quality.issues = [
            {"code": "PICTURE_NOT_READ" if failure == "unread_image" else "NEW_UNKNOWN_FAILURE"}
        ]
    assert not testpub.assess(doc)["eligible"]


def test_policy_is_default_off_actor_and_connection_scoped(monkeypatch):
    actor = Principal(uuid4(), uuid4())
    connection = uuid4()
    assert not testpub.enabled(actor, connection)
    enable(monkeypatch, actor, connection)
    assert testpub.enabled(actor, connection)
    assert not testpub.enabled(Principal(uuid4(), actor.organization_id), connection)
    assert not testpub.enabled(actor, uuid4())
    monkeypatch.setenv("WAREHOUSE_DEPLOYMENT_MODE", "production")
    assert not testpub.enabled(actor, connection)


def test_auto_publish_preserves_provenance_scope_and_idempotence(
    database, tenant, source, monkeypatch
):
    admin, runtime = database
    store, product, _, _ = source
    assert isinstance(documents.run_once(runtime, tenant.worker, store, complete), str)
    with pytest.raises(Forbidden):
        testpub.run_once(runtime, tenant.supplier, tenant.connection_id)
    enable(monkeypatch, tenant.supplier, tenant.connection_id)
    result = testpub.run_once(runtime, tenant.supplier, tenant.connection_id)
    assert result["status"] == "test_published" and result["coverage_percent"] == 100
    assert testpub.run_once(runtime, tenant.supplier, tenant.connection_id) is None
    public = documents.current(runtime, tenant.buyer, product["id"])
    assert "测试自动发布" in public["body"]["publication_notice"]
    assert "units" not in public["body"] and "quality" not in public["body"]
    with admin.connect() as conn:
        pub = (
            conn.execute(
                text("SELECT review_mode,quality_metrics FROM document_publication WHERE id=:id"),
                {"id": UUID(result["publication_id"])},
            )
            .mappings()
            .one()
        )
    assert pub["review_mode"] == "test_auto" and pub["quality_metrics"]["policy"] == testpub.POLICY


def test_scoped_test_publication_has_reference_preview_before_departure_selection(
    database, tenant, source, monkeypatch
):
    _, runtime = database
    store, product, _, _ = source

    def scoped(work, body):
        doc = complete(work, body)
        doc.applicability.version_label = "ACME scoped version"
        return doc

    documents.run_once(runtime, tenant.worker, store, scoped)
    enable(monkeypatch, tenant.supplier, tenant.connection_id)
    assert (
        testpub.run_once(runtime, tenant.supplier, tenant.connection_id)["status"]
        == "test_published"
    )
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    preview = documents.current(runtime, tenant.buyer, product["id"], route_preview=True)
    assert "测试自动发布" in preview["body"]["publication_notice"]
    assert "尚未选择团期" in preview["body"]["publication_notice"]
    assert preview["body"]["applicability"]["version_label"] == "ACME scoped version"
    assert "source" not in preview["body"] and "quality" not in preview["body"]


def test_manual_edits_cannot_use_test_auto_and_policy_rechecked_at_apply(
    database, tenant, source, monkeypatch
):
    _, runtime = database
    store, product, _, _ = source
    assert isinstance(documents.run_once(runtime, tenant.worker, store, complete), str)
    enable(monkeypatch, tenant.supplier, tenant.connection_id)
    current = route_editor.get(runtime, tenant.supplier, product["id"])
    command = route_editor.SaveDraft(
        expected_revision=current["revision"],
        expected_source_hash=current["source_hash"],
        content=current["candidate"],
        source_note="ACME",
    )
    command.content.days[0].summary = "ACME manually changed"
    saved = route_editor.save(runtime, tenant.supplier, product["id"], command)
    proposal = route_editor.PublishDraft(
        target_id=product["id"],
        revision_id=saved["revision_id"],
        review_mode="test_auto",
        note="ACME test",
    )
    with pytest.raises(Conflict, match="当前解析原稿"):
        route_editor.propose(runtime, tenant.supplier, proposal)
    command.expected_revision = saved["revision"]
    command.content = test_route_kit_content.kit.parse_content(current["candidate"])
    saved = route_editor.save(runtime, tenant.supplier, product["id"], command)
    proposal.revision_id = saved["revision_id"]
    staged = route_editor.propose(runtime, tenant.supplier, proposal)
    changes.approve(runtime, tenant.supplier, UUID(staged["id"]), staged["payload_hash"])
    monkeypatch.setenv("WAREHOUSE_ROUTE_TEST_AUTO_PUBLISH", "false")
    with pytest.raises(Forbidden):
        changes.apply(runtime, tenant.supplier, UUID(staged["id"]))
