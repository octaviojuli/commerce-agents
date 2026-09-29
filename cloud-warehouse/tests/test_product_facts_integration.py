"""Publication decisions retain scoped supplier names and only cover matching content."""

from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from cloud_warehouse import (
    copilot_explore,
    copilot_inquiries,
    documents,
    product_facts,
    route_applicability,
    route_editor,
)
from cloud_warehouse import route_kit_content as kit
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.catalog import list_departures
from cloud_warehouse.merchant import WarehouseMerchantBackend
from cloud_warehouse.persistence import transaction
from shopping_agent import SearchFilters

from . import test_documents
from .test_advisor import session
from .test_route_editor import commit
from .test_route_kit_content import content

source = test_documents.source


@pytest.mark.parametrize(
    "span,content_days,origin,accepted",
    [
        (6, 7, "warehouse_decision", True),
        (7, 7, "warehouse_decision", True),
        (10, 7, "warehouse_decision", False),
        (6, 9, "warehouse_decision", False),
        (6, 7, "source", False),
    ],
)
def test_decision_never_adopts_an_unrelated_historical_itinerary(
    span, content_days, origin, accepted
):
    from datetime import timedelta

    start = date(2030, 10, 1)
    publication = {
        "id": "ACME-publication",
        "product_id": "ACME-product",
        "source_content_hash": "ACME-source",
        "body": content(
            {
                "file_hash": "a" * 64,
                "file_name": "ACME.docx",
                "product_snapshot": {"code": "ACME", "name": "ACME", "days": content_days},
            }
        ).model_dump(mode="json", by_alias=True),
    }
    connection = MagicMock()
    publications, departure = MagicMock(), MagicMock()
    publications.mappings.return_value = [publication]
    departure.mappings.return_value.one_or_none.return_value = {
        "depart_date": start,
        "return_date": start + timedelta(days=span - 1),
        "gateway": "ACME",
        "days_origin": origin,
        "upstream_days": 6,
        "effective_days": 7,
    }
    connection.execute.side_effect = [publications, departure]
    result = route_applicability.select_publication(connection, publication, "ACME-departure")
    assert (result is not None) == accepted


async def test_published_facts_reach_advisor_and_mixed_departures_stay_blocked(
    database, tenant, source, monkeypatch
):
    admin, runtime = database
    store, product, _, _ = source
    documents.run_once(runtime, tenant.worker, store, lambda work, body: content(work))
    initial = route_editor.get(runtime, tenant.supplier, product["id"])
    doc = kit.parse_content(initial["content"])
    extra = doc.days[-1].model_copy(deep=True)
    extra.day, extra.day_id = doc.days_count + 1, ""
    for node in extra.items:
        node.node_id = ""
        for child in node.children:
            child.node_id = ""
    doc.days.append(extra)
    doc.days_count += 1
    doc.depart_city = "ACME 核定口岸"
    doc = kit.prepare(doc)
    decisions = [
        product_facts.FactDecision(
            field=field,
            upstream=initial["listing"][field]["upstream"],
            value=value,
            basis=[{"source": "ACME 附件复核", "value": str(value)}],
        )
        for field, value in [("days", doc.days_count), ("gateway", doc.depart_city)]
    ]
    command = route_editor.SaveDraft(
        expected_revision=initial["revision"],
        expected_source_hash=initial["source_hash"],
        source_note="ACME 原文复核",
        content=doc,
        fact_decisions=decisions,
    )
    issues = route_editor.validate(runtime, tenant.supplier, product["id"], command)["issues"]
    assert all(i["acknowledgeable"] for i in issues)
    command.review_resolutions = [
        {"code": i["code"], "path": i["path"], "basis_hash": i["basis_hash"], "note": "ACME 核对"}
        for i in issues
    ]
    saved = route_editor.save(runtime, tenant.supplier, product["id"], command)
    assert not saved["issues"]
    detail = route_editor.revision_detail(runtime, tenant.supplier, saved["revision_id"])
    assert len(detail["fact_decisions"]) == 2 and detail["listing"]["days"]["state"] == "pending"
    assert detail["body"]["schema"] == "route-kit/1" and not detail["issues"]
    commit(
        runtime,
        tenant,
        route_editor.propose(
            runtime,
            tenant.supplier,
            route_editor.PublishDraft(
                target_id=product["id"], revision_id=saved["revision_id"], note="ACME 确认发布"
            ),
        ),
    )
    published = route_editor.get(runtime, tenant.supplier, product["id"])
    assert published["listing"]["days"]["state"] == "active"
    assert published["fact_decisions"] == []
    backend, context = WarehouseAdvisorBackend(runtime, tenant.buyer), session(tenant.buyer)
    products = await backend.search_products(
        context,
        "",
        SearchFilters(attributes={"days": str(doc.days_count), "depart_city": doc.depart_city}),
    )
    assert len(products) == 1
    read = await backend.get_product_details(context, products[0].product_id)
    assert read.attributes["days"] == str(doc.days_count)
    assert read.attributes["depart_city"] == doc.depart_city
    assert read.attributes["supplier_name"] == "ACME Supplier"
    with transaction(runtime, tenant.supplier) as conn:
        merchant = WarehouseMerchantBackend(runtime, tenant.supplier)._catalog(
            conn, [product["id"]]
        )[0]
        assert merchant.attributes["天数"] == str(doc.days_count)
        assert merchant.attributes["出发地"] == doc.depart_city
    with transaction(runtime, tenant.buyer) as conn:
        facts = copilot_inquiries.source(conn, product["id"])
        assert facts["days"] == doc.days_count and facts["gateway"] == doc.depart_city
        monkeypatch.setattr(copilot_explore, "direction_label", lambda _: "ACME 方向")
        directions = copilot_explore._directions(conn)
        assert directions[0]["days_min"] == directions[0]["days_max"] == doc.days_count
    departures = list_departures(runtime, tenant.buyer)
    departure = next(d for d in departures if str(d["product_id"]) == str(product["id"]))
    assert (
        documents.current(runtime, tenant.buyer, product["id"], departure_id=departure["id"])
        is not None
    )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE departure SET return_date=depart_date+9 WHERE id=:id"),
            {"id": departure["id"]},
        )
    assert (
        documents.current(runtime, tenant.buyer, product["id"], departure_id=departure["id"])
        is None
    )
    changed = route_editor.revision_detail(runtime, tenant.supplier, saved["revision_id"])
    assert any(
        i["code"] == "DEPARTURE_DURATION_MISMATCH" and not i["acknowledgeable"]
        for i in changed["issues"]
    )
    with admin.connect() as conn:
        row = conn.execute(
            text("SELECT days,gateway FROM supplier_product WHERE id=:id"), {"id": product["id"]}
        ).one()
        assert row.days == initial["listing"]["days"]["upstream"]
        assert row.gateway == initial["listing"]["gateway"]["upstream"]


def test_pending_gateway_decision_checks_departures_in_its_future_scope(database, tenant, source):
    admin, runtime = database
    store, product, _, _ = source
    documents.run_once(runtime, tenant.worker, store, lambda work, body: content(work))
    initial = route_editor.get(runtime, tenant.supplier, product["id"])
    doc = kit.parse_content(initial["content"])
    doc.depart_city = "ACME 核定口岸"
    doc.applicability.departure_cities = [doc.depart_city]
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE departure SET return_date=depart_date+9 WHERE product_id=:id"),
            {"id": product["id"]},
        )
    command = route_editor.SaveDraft(
        expected_revision=initial["revision"],
        expected_source_hash=initial["source_hash"],
        source_note="ACME 口岸核定",
        content=doc,
        fact_decisions=[
            product_facts.FactDecision(
                field="gateway",
                upstream=initial["listing"]["gateway"]["upstream"],
                value=doc.depart_city,
            )
        ],
    )
    issues = route_editor.validate(runtime, tenant.supplier, product["id"], command)["issues"]
    assert any(
        i["code"] == "DEPARTURE_DURATION_MISMATCH" and not i["acknowledgeable"] for i in issues
    )
