"""Content corrections preserve upstream ownership and live sales permissions."""

from datetime import date
from uuid import uuid4

from sqlalchemy import text

from cloud_warehouse import (
    copilot_explore,
    copilot_inquiries,
    documents,
    product_facts,
    route_editor,
)
from cloud_warehouse import route_kit_content as kit
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.catalog import list_departures, synchronize
from cloud_warehouse.merchant import WarehouseMerchantBackend
from cloud_warehouse.persistence import transaction
from shopping_agent import SearchFilters

from . import test_documents
from .test_advisor import session
from .test_route_editor import commit
from .test_route_kit_content import content
from .test_sync import Connector, batch

source = test_documents.source


def linked_records(admin, product):
    """Compare entire linked records, not merely their IDs, across content publication."""
    with admin.connect() as conn:
        linked = {
            table: conn.execute(
                text(f"SELECT to_jsonb(t) FROM {table} t WHERE {where} ORDER BY id"),
                {"id": product},
            )
            .scalars()
            .all()
            for table, where in {
                "departure": "product_id=:id",
                "document_asset": "product_id=:id",
                "document_parse": "product_id=:id",
                "offer": "departure_id IN (SELECT id FROM departure WHERE product_id=:id)",
                "inventory_pool": "departure_id IN (SELECT id FROM departure WHERE product_id=:id)",
                "contract_price": "offer_id IN (SELECT o.id FROM offer o JOIN departure d ON d.id=o.departure_id WHERE d.product_id=:id)",
            }.items()
        }
        linked["upstream_product"] = dict(
            conn.execute(
                text(
                    "SELECT id,supplier_org_id,connection_id,external_id,code,name,days,gateway,source,source_hash FROM supplier_product WHERE id=:id"
                ),
                {"id": product},
            )
            .mappings()
            .one()
        )
    return linked


async def test_content_corrections_preserve_all_linked_departures_and_records(
    database, tenant, source, monkeypatch
):
    admin, runtime = database
    store, product, _, _ = source
    data = batch()
    data.routes.append({**data.routes[0], "routeId": 9, "routeCode": "ACME-R9"})
    data.departures.extend(
        [
            {
                **data.departures[0],
                "periodId": 3,
                "departDate": "2026-10-25",
                "returnDate": "2026-11-03",
            },
            {
                **data.departures[0],
                "periodId": 4,
                "departDate": "2026-11-05",
                "returnDate": "2026-11-12",
            },
            {**data.departures[0], "periodId": 9, "routeId": 9},
        ]
    )
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector(data))
    documents.run_once(runtime, tenant.worker, store, lambda work, body: content(work))
    before = linked_records(admin, product["id"])
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
    doc.title = "ACME 校对后的线路标题"
    doc.depart_city = "ACME 核定口岸"
    doc.applicability.start = doc.applicability.end = date(2035, 1, 1)
    doc.applicability.departure_cities = ["ACME 文中城市"]
    doc.applicability.version_label = "ACME 文字版本"
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
        assert directions[0]["days_min"] == initial["listing"]["days"]["upstream"]
        assert directions[0]["days_max"] == doc.days_count
    departures = list_departures(runtime, tenant.buyer)
    owned = [d for d in departures if str(d["product_id"]) == str(product["id"])]
    assert len(owned) == 3
    current = documents.current(runtime, tenant.buyer, product["id"])
    assert current["body"]["title"] == doc.title
    assert current["body"]["days_count"] == doc.days_count
    assert "publication_notice" not in current["body"]
    for departure in owned:
        assert (
            documents.current(runtime, tenant.buyer, product["id"], departure_id=departure["id"])
            == current
        )
    assert linked_records(admin, product["id"]) == before
    assert not route_editor.revision_detail(runtime, tenant.supplier, saved["revision_id"])[
        "issues"
    ]
    foreign = next(d for d in departures if str(d["product_id"]) != str(product["id"]))
    for invalid in (foreign["id"], uuid4()):
        assert documents.current(runtime, tenant.buyer, product["id"], departure_id=invalid) is None
    # A paused group and a revoked distribution grant remain inaccessible.
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE departure SET status='paused' WHERE id=:id"), {"id": owned[0]["id"]}
        )
    assert (
        documents.current(runtime, tenant.buyer, product["id"], departure_id=owned[0]["id"]) is None
    )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE connection_id=:id"),
            {"id": tenant.connection_id},
        )
    assert documents.current(runtime, tenant.buyer, product["id"]) is None
    assert (
        documents.current(runtime, tenant.buyer, product["id"], departure_id=owned[1]["id"]) is None
    )


def test_gateway_content_review_is_independent_of_group_dates(database, tenant, source):
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
    assert all(i["acknowledgeable"] for i in issues)
    assert not any(i["code"] == "DEPARTURE_DURATION_MISMATCH" for i in issues)
