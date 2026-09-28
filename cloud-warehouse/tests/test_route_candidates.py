import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import documents, route_candidates, route_editor
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, transaction
from cloud_warehouse.route_editing import EditedDay, FactReview

from . import test_documents
from .test_documents import parsed
from .test_route_editor import prepare, save

source = test_documents.source


def ask(prompt, data, schema):
    if schema is FactReview:
        return schema(unsupported_claims=[], omitted_facts_or_conditions=[], changed_meanings=[])
    return EditedDay(
        summary="游览ACME 景点。",
        blocks=[
            {
                "source_ids": [x["id"] for x in data["source_units"]],
                "type": "visit",
                "title": "ACME 游览",
                "paragraphs": ["ACME 景点"],
            }
        ],
    )


def test_candidate_is_persistent_private_and_preserves_human_draft(database, tenant, source):
    _, runtime = database
    product, current, content = prepare(runtime, tenant, source)
    original = documents.get(runtime, tenant.supplier, source[2])["parse"]["body"]
    content.days[0].summary = "ACME 人工内容"
    save(runtime, tenant, product, current, content)
    assert route_candidates.seed(runtime, tenant.worker, "ACME-model") == 1
    assert route_candidates.seed(runtime, tenant.worker, "ACME-model") == 0
    for _ in range(3):
        route_candidates.run_once(runtime, tenant.worker, ask)
    result = route_editor.get(runtime, tenant.supplier, product["id"])
    assert result["content"]["days"][0]["summary"] == "ACME 人工内容"
    assert result["candidate"]["days"][0]["summary"] == "游览ACME 景点。"
    assert result["editing"]["edited_days"] == 3
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    assert documents.get(runtime, tenant.supplier, source[2])["parse"]["body"] == original
    assert route_candidates.run_once(runtime, tenant.worker, ask) is None
    with transaction(runtime, tenant.buyer) as conn:
        assert not conn.execute(text("SELECT * FROM route_content_candidate")).all()
    with pytest.raises(Forbidden):
        route_candidates.seed(runtime, tenant.supplier, "ACME")
    with (
        transaction(runtime, tenant.worker) as conn,
        pytest.raises(DBAPIError),
        conn.begin_nested(),
    ):
        conn.execute(
            text("UPDATE route_content_candidate SET status='pending' WHERE id=:id"),
            {"id": result["editing"]["id"]},
        )


@pytest.mark.parametrize("change", ["source", "parse", "withdraw"])
def test_changed_source_rejects_worker_completion(database, tenant, source, change):
    admin, runtime = database
    product, _, _ = prepare(runtime, tenant, source)
    route_candidates.seed(runtime, tenant.worker, "ACME-model")
    work = route_candidates.claim(runtime, tenant.worker)
    with admin.begin() as conn:
        if change == "source":
            conn.execute(
                text("UPDATE supplier_product SET name='ACME newer' WHERE id=:id"),
                {"id": product["id"]},
            )
        elif change == "parse":
            conn.execute(
                text(
                    "UPDATE document_asset SET parse_generation=parse_generation+1,status='queued' WHERE id=:id"
                ),
                {"id": source[2]},
            )
        else:
            conn.execute(
                text("UPDATE supplier_product SET status='paused' WHERE id=:id"),
                {"id": product["id"]},
            )
    from cloud_warehouse.route_doc import RouteDoc

    day = RouteDoc.model_validate(work["body"]).days[0]
    assert (
        route_candidates.finish(runtime, tenant.worker, work, day, {"status": "retained"})
        == "obsolete"
    )


def test_expired_lease_cannot_finish_and_new_worker_resumes(database, tenant, source):
    admin, runtime = database
    prepare(runtime, tenant, source)
    route_candidates.seed(runtime, tenant.worker, "ACME-model")
    work = route_candidates.claim(runtime, tenant.worker)
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE route_content_candidate SET lease_until=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": work["id"]},
        )
    from cloud_warehouse.route_doc import RouteDoc

    with pytest.raises(Conflict):
        route_candidates.finish(
            runtime, tenant.worker, work, RouteDoc.model_validate(work["body"]).days[0], {}
        )
    assert route_candidates.run_once(runtime, tenant.worker, ask) == "pending"


def test_incomplete_source_never_calls_model(database, tenant, source):
    _, runtime = database

    def incomplete(work, body):
        doc = parsed(work, body)
        doc.days.pop()
        return doc

    documents.run_once(runtime, tenant.worker, source[0], incomplete)
    route_candidates.seed(runtime, tenant.worker, "ACME-model")
    assert (
        route_candidates.run_once(
            runtime, tenant.worker, lambda *_: pytest.fail("must not call model")
        )
        is None
    )
    result = route_editor.get(runtime, tenant.supplier, source[1]["id"])
    assert result["editing"]["reports"][0]["code"] == "DAYS_INCOMPLETE"
