"""ACME multi-turn advisor/merchant acceptance with real PostgreSQL and denied cross-owner reads."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cloud_warehouse import conversations, quote_shares, quotes, trip_brief
from cloud_warehouse import copilot_engine as engine
from cloud_warehouse import copilot_inquiries as inquiries
from cloud_warehouse import copilot_records as records
from cloud_warehouse import copilot_sales as sales
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.catalog import list_departures
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from commerce_common.testing import FakeCreateClient, tool_use_message
from shopping_agent import ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState
from tour.api.warehouse_copilot import CopilotAgent

from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, add_buyer, commit, contract
from .test_quotes import managed_offer as managed_offer


def new_deal(runtime, actor):
    customer = records.write_customer(
        runtime,
        actor,
        records.CustomerWrite(
            request_id=uuid4(),
            customer=records.Customer(name="ACME 客人", contact="ACME 私有联系方式"),
        ),
    )
    item = records.create_deal(
        runtime,
        actor,
        records.DealCreate(request_id=uuid4(), customer_id=customer["id"], title="ACME 家庭假期"),
    )
    return item["id"], customer


def fields():
    return {
        "destinations": ["ACME"],
        "window": {"start": str(FUTURE), "end": str(FUTURE)},
        "adults": 2,
        "children": 0,
        "child_ages": [],
        "party_total": 2,
        "rooms": {"doubles": 1, "twins": 0, "singles": 0, "child_bed": None, "raw": "一间双人房"},
    }


async def priced(runtime, tenant, offer):
    commit(runtime, tenant.supplier, contract(runtime, tenant, offer))
    identifier, _ = new_deal(runtime, tenant.buyer)
    result = trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=0, fields=fields()),
    )
    departure = list_departures(runtime, tenant.buyer)[0]
    quote = await quotes.create(
        runtime,
        tenant.buyer,
        quotes.QuoteRequest(
            offer_id=offer,
            departure_date=FUTURE,
            party=trip_brief.to_party(trip_brief.TripBrief.model_validate(result["body"])),
        ),
        str(uuid4()),
    )
    with transaction(runtime, tenant.buyer) as conn:
        brief, version = trip_brief.load(conn, identifier)
        brief.route_id = "WP-" + str(departure["product_id"])
        brief.departure_id = "WD-" + str(departure["id"])
        brief.offer_id = offer
        brief.quote_id = UUID(quote["quote_id"])
        brief.quote = quote
        brief.quote_brief_version = version
        trip_brief.save(conn, tenant.buyer, identifier, brief, version + 1)
    return identifier, quote


def test_customers_deals_and_records_are_private_even_in_same_org(database, tenant):
    admin, runtime = database
    identifier, customer = new_deal(runtime, tenant.buyer)
    other = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
            {"id": other, "email": f"{other}@acme.example"},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,ARRAY['advisor'])"
            ),
            {"id": other, "org": tenant.buyer.organization_id},
        )
    for actor in (Principal(other, tenant.buyer.organization_id), tenant.supplier):
        with transaction(runtime, actor) as conn:
            for table in ("advisor_customer", "advisor_deal", "advisor_record"):
                assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        with pytest.raises(Forbidden):
            records.detail(runtime, actor, identifier)
    with pytest.raises(Forbidden):
        records.create_deal(
            runtime,
            Principal(other, tenant.buyer.organization_id),
            records.DealCreate(request_id=uuid4(), customer_id=customer["id"]),
        )
    command = records.Note(
        request_id=uuid4(), expected_version=0, kind="note", text="ACME 私有备注"
    )
    first = records.note(runtime, tenant.buyer, identifier, command)
    assert records.note(runtime, tenant.buyer, identifier, command)["id"] == first["id"]
    with transaction(runtime, tenant.buyer) as conn, pytest.raises(DBAPIError):
        conn.execute(text("UPDATE advisor_record SET body='{}' WHERE id=:id"), {"id": first["id"]})


def test_changes_require_acceptance_and_keep_versions(database, tenant):
    _, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    work = conversations.begin(runtime, tenant.buyer, identifier, "first", "想去 ACME")
    proposal = engine.propose(
        runtime,
        tenant.buyer,
        identifier,
        {"destinations": {"value": ["ACME"], "source": "said", "evidence": "ACME"}},
        "想去 ACME",
        work["turn_id"],
    )
    assert proposal["status"] == "adopted"
    conversations.finish(runtime, tenant.buyer, work, {}, work["messages"], [])
    work = conversations.begin(runtime, tenant.buyer, identifier, "second", "改去 ACME 海湾")
    proposal = engine.propose(
        runtime,
        tenant.buyer,
        identifier,
        {"destinations": {"value": ["ACME 海湾"], "source": "said", "evidence": "ACME 海湾"}},
        "改去 ACME 海湾",
        work["turn_id"],
    )
    assert proposal["status"] == "pending"
    assert trip_brief.get(runtime, tenant.buyer, identifier)["body"]["destinations"]["value"] == [
        "ACME"
    ]
    with pytest.raises(ValueError, match="原话"):
        engine.propose(
            runtime,
            tenant.buyer,
            identifier,
            {"children": {"value": 0, "source": "said", "evidence": "没有儿童"}},
            "改去 ACME 海湾",
            work["turn_id"],
        )
    conversations.finish(runtime, tenant.buyer, work, {}, work["messages"], [])
    command = engine.Adoption(request_id=uuid4(), expected_version=1, accept=True)
    result = engine.adopt(runtime, tenant.buyer, identifier, UUID(proposal["proposal_id"]), command)
    assert (
        engine.adopt(runtime, tenant.buyer, identifier, UUID(proposal["proposal_id"]), command)[
            "id"
        ]
        == result["id"]
    )
    detail = records.detail(runtime, tenant.buyer, identifier)
    assert detail["brief"]["version"] == 2
    assert len([r for r in detail["records"] if r["kind"] == "state"]) == 2
    state = next(r for r in detail["records"] if r["kind"] == "state" and r["brief_version"] == 1)
    engine.revert(
        runtime,
        tenant.buyer,
        identifier,
        state["id"],
        records.Command(request_id=uuid4(), expected_version=2),
    )
    assert trip_brief.get(runtime, tenant.buyer, identifier)["body"]["route_id"] is None


def test_supplier_roundtrip_scopes_and_stale_adoption(database, tenant, managed_offer):
    admin, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    departure = list_departures(runtime, tenant.buyer)[0]
    command = inquiries.Submit(
        request_id=uuid4(),
        expected_version=0,
        product_id="WP-" + str(departure["product_id"]),
        question="ACME 儿童餐食如何安排？",
    )
    item = inquiries.submit(runtime, tenant.buyer, identifier, command)
    assert inquiries.submit(runtime, tenant.buyer, identifier, command)["id"] == item["id"]
    inbox = inquiries.page(runtime, tenant.supplier)["items"]
    assert item["id"] in {r["id"] for r in inbox}
    assert "客户" not in str(inbox)
    with pytest.raises(Forbidden):
        inquiries.reply(
            runtime,
            tenant.buyer,
            item["id"],
            inquiries.Reply(request_id=uuid4(), answer="伪造商户回复"),
        )
    reply = inquiries.reply(
        runtime,
        tenant.supplier,
        item["id"],
        inquiries.Reply(request_id=uuid4(), answer="仅本团提供 ACME 儿童餐，需要提前两天登记。"),
    )
    saved = inquiries.adopt(
        runtime,
        tenant.buyer,
        identifier,
        reply["id"],
        records.Command(request_id=uuid4(), expected_version=0),
    )
    assert saved["body"]["reply_id"] == str(reply["id"])
    other = add_buyer(admin, tenant, item["connection_id"])
    assert inquiries.page(runtime, other)["items"] == []
    trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=0, fields={"adults": 3}),
    )
    with pytest.raises(Conflict, match="需求已变化"):
        inquiries.adopt(
            runtime,
            tenant.buyer,
            identifier,
            reply["id"],
            records.Command(request_id=uuid4(), expected_version=1),
        )


async def test_retail_quote_share_margin_manual_receipts_and_invalidation(
    database, authentication, tenant, managed_offer
):
    admin, runtime = database
    identifier, quote = await priced(runtime, tenant, managed_offer)
    version = 2
    with pytest.raises(Conflict, match="确认单"):
        sales.retail(
            runtime,
            tenant.buyer,
            identifier,
            sales.Retail(request_id=uuid4(), expected_version=version),
        )
    engine.confirm(
        runtime,
        tenant.buyer,
        identifier,
        engine.Confirmation(
            request_id=uuid4(),
            expected_version=version,
            evidence="ACME 客人：以上五项均已核对确认",
            confirmed_items=list(engine.CONFIRM_ITEMS),
        ),
    )
    with pytest.raises(Conflict, match="亏损"):
        sales.retail(
            runtime,
            tenant.buyer,
            identifier,
            sales.Retail(request_id=uuid4(), expected_version=version, sales_total="10"),
        )
    retail = sales.retail(
        runtime,
        tenant.buyer,
        identifier,
        sales.Retail(request_id=uuid4(), expected_version=version, sales_total="260"),
    )
    assert Decimal(retail["body"]["profit"]) == Decimal("260") - Decimal(quote["settlement_total"])
    share = sales.share(
        runtime,
        tenant.buyer,
        identifier,
        retail["id"],
        records.Command(request_id=uuid4(), expected_version=version),
    )
    public = quote_shares.read(runtime, authentication, share["token"])
    assert public["market_total"] == "260.00"
    assert "settlement" not in str(public) and "profit" not in str(public)
    assert public["market_lines"][0]["total"] == "260.00"
    with admin.connect() as conn:
        inventory = conn.execute(
            text("SELECT id,total,sold,blocked FROM inventory_pool ORDER BY id")
        ).all()
    sale = sales.sale(
        runtime,
        tenant.buyer,
        identifier,
        sales.Sale(
            request_id=uuid4(),
            expected_version=version,
            retail_quote_id=retail["id"],
            supplier_order_number="ACME-OFFLINE-1",
        ),
    )
    receipt = sales.Receipt(
        request_id=uuid4(),
        expected_version=version,
        sale_id=sale["id"],
        amount="60",
        category="deposit",
        received_on=str(FUTURE),
        reference="ACME 线下收据",
    )
    assert (
        sales.receipt(runtime, tenant.buyer, identifier, receipt)["body"]["platform_collected"]
        is False
    )
    sales.receipt(runtime, tenant.buyer, identifier, receipt)
    with pytest.raises(Conflict, match="累计"):
        sales.receipt(
            runtime,
            tenant.buyer,
            identifier,
            receipt.model_copy(update={"request_id": uuid4(), "amount": Decimal("300")}),
        )
    trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=version, fields={"adults": 3, "party_total": 3}),
    )
    assert quote_shares.read(runtime, authentication, share["token"]) is None
    assert any(
        r["kind"] == "sale" for r in records.detail(runtime, tenant.buyer, identifier)["records"]
    )
    with admin.connect() as conn:
        assert (
            conn.execute(text("SELECT id,total,sold,blocked FROM inventory_pool ORDER BY id")).all()
            == inventory
        )


async def run_turn(runtime, actor, identifier, client, message):
    work = conversations.begin(runtime, actor, identifier, str(uuid4()), message)
    state = ShoppingSessionState.model_validate(work["state"])
    agent = CopilotAgent(WarehouseAdvisorBackend(runtime, actor), client=client)
    events = [
        e
        async for e in agent.stream_turn(
            work["messages"],
            ShoppingSessionContext(session_id=str(identifier), user_id=str(actor.user_id)),
            state,
        )
    ]
    assert events[-1].type == "turn_complete"
    assert any(e.type == "text_delta" and e.data["text"] for e in events)
    conversations.finish(
        runtime,
        actor,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [e.model_dump(mode="json") for e in events],
    )
    return events


async def test_typed_copilot_multiturn_retains_all_countries_and_does_not_advance(
    database, tenant, managed_offer
):
    _, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)

    def client(changes, intent="new_need", ids=None):
        return FakeCreateClient(
            [
                tool_use_message("extract", {"changes": changes}),
                tool_use_message(
                    "decide", {"intent": intent, "target_ids": ids or [], "confidence": 0.99}
                ),
                tool_use_message("draft", {"fact_ids": []}),
            ]
        )

    await run_turn(
        runtime,
        tenant.buyer,
        identifier,
        client(
            [
                {
                    "field": "destinations",
                    "value": ["德国", "法国", "意大利", "瑞士"],
                    "source": "said",
                    "evidence": "德法意瑞",
                }
            ]
        ),
        "德法意瑞都要去",
    )
    assert trip_brief.get(runtime, tenant.buyer, identifier)["body"]["destinations"]["value"] == [
        "德国",
        "法国",
        "意大利",
        "瑞士",
    ]
    events = await run_turn(
        runtime,
        tenant.buyer,
        identifier,
        client(
            [{"field": "destinations", "value": ["ACME"], "source": "said", "evidence": "ACME"}]
        ),
        "改去 ACME",
    )
    proposal = next(
        e.data["payload"]
        for e in events
        if e.type == "ui" and e.data["component"] == "copilot_proposal"
    )
    engine.adopt(
        runtime,
        tenant.buyer,
        identifier,
        UUID(proposal["proposal_id"]),
        engine.Adoption(request_id=uuid4(), expected_version=1, accept=True),
    )
    events = await run_turn(
        runtime,
        tenant.buyer,
        identifier,
        client(
            [
                {
                    "field": "window",
                    "value": {"start": str(FUTURE), "end": str(FUTURE)},
                    "source": "said",
                    "evidence": str(FUTURE),
                }
            ]
        ),
        f"{FUTURE} 出发",
    )
    assert any(e.type == "ui" and e.data["component"] == "warehouse_routes" for e in events)
    assert not any(e.type == "ui" and e.data["component"] == "warehouse_departures" for e in events)
    events = await run_turn(
        runtime, tenant.buyer, identifier, client([], "quote"), "先不选线路，能保证退费吗？"
    )
    assert not any(e.type == "ui" and e.data["component"] == "warehouse_quote" for e in events)


async def test_model_failure_still_replies_and_preserves_state(database, tenant):
    _, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    events = await run_turn(
        runtime, tenant.buyer, identifier, FakeCreateClient([]), "你好，我还没想好去哪里"
    )
    reply = next(
        e.data["payload"]
        for e in events
        if e.type == "ui" and e.data["component"] == "copilot_reply"
    )
    assert reply["to_customer"] and reply["degraded"]
    assert trip_brief.get(runtime, tenant.buyer, identifier)["version"] == 0


async def test_explicit_change_cannot_disappear_as_an_empty_extraction(database, tenant):
    _, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    empty = tool_use_message("extract", {"changes": []})
    change = tool_use_message("decide", {"intent": "change", "confidence": 0.99})
    draft = tool_use_message("draft", {"fact_ids": []})
    await run_turn(
        runtime,
        tenant.buyer,
        identifier,
        FakeCreateClient(
            [
                empty,
                change,
                tool_use_message(
                    "extract",
                    {
                        "changes": [
                            {
                                "field": "child_ages",
                                "value": [7],
                                "source": "said",
                                "evidence": "孩子7岁",
                            }
                        ]
                    },
                ),
                draft,
            ]
        ),
        "补充一下，孩子7岁",
    )
    assert trip_brief.get(runtime, tenant.buyer, identifier)["body"]["child_ages"]["value"] == [7]
    events = await run_turn(
        runtime,
        tenant.buyer,
        identifier,
        FakeCreateClient([empty, change, empty, draft]),
        "改成8岁，其他不变",
    )
    reply = next(
        e.data["payload"]
        for e in events
        if e.type == "ui" and e.data["component"] == "copilot_reply"
    )
    assert reply["degraded"]
    assert trip_brief.get(runtime, tenant.buyer, identifier)["body"]["child_ages"]["value"] == [7]


def test_evidence_restores_only_original_punctuation_and_not_changed_facts():
    assert (
        engine.original_evidence("孩子7岁，需要1间双床房，孩子不占床。", "需要1间双床房,孩子不占床")
        == "需要1间双床房，孩子不占床"
    )
    assert not engine.original_evidence("孩子7岁", "孩子8岁")
    assert not engine.original_evidence("孩子不占床", "孩子占床")
    assert not engine.original_evidence("一间双床房", "1间双床房")


def test_material_candidates_are_unconfirmed_and_large_images_are_bounded():
    from io import BytesIO

    from PIL import Image

    from cloud_warehouse import copilot_ocr

    assert copilot_ocr.candidates("没有可识别的证件") == []
    # Large decompressed dimensions are rejected before any OCR subprocess starts.
    payload = BytesIO()
    Image.new("1", (10001, 2)).save(payload, format="PNG")
    assert copilot_ocr.recognize_bytes(payload.getvalue(), "image/png") == []


def test_retired_supplier_context_does_not_hide_private_customer_records(database, tenant):
    admin, runtime = database
    identifier, customer = new_deal(runtime, tenant.buyer)
    records.note(
        runtime,
        tenant.buyer,
        identifier,
        records.Note(
            request_id=uuid4(), expected_version=0, kind="note", text="ACME 客户自己的备忘"
        ),
    )
    with transaction(runtime, tenant.buyer) as conn:
        records.append(
            conn, tenant.buyer, identifier, "plan", {"text": "ACME 供应商历史安排"}, 0, str(uuid4())
        )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE agent_conversation SET authorization_hash=:hash WHERE id=:id"),
            {"id": identifier, "hash": "0" * 64},
        )
    result = records.detail(runtime, tenant.buyer, identifier)
    assert result["conversation_available"] is False
    assert records.list_deals(runtime, tenant.buyer)["items"][0]["brief"] is None
    assert result["customer"]["id"] == customer["id"]
    assert [r["kind"] for r in result["records"]] == ["note"]
    assert [r["kind"] for r in records.history(runtime, tenant.buyer, identifier)["items"]] == [
        "note"
    ]
    records.note(
        runtime,
        tenant.buyer,
        identifier,
        records.Note(request_id=uuid4(), expected_version=0, kind="task", text="ACME 行前提醒"),
    )
    with pytest.raises(Conflict):
        conversations.get(runtime, tenant.buyer, identifier)


def test_private_materials_deny_other_advisors_and_support_idempotent_upload(
    database, tenant, tmp_path, monkeypatch, managed_offer
):
    import base64
    import os

    monkeypatch.setenv("WAREHOUSE_ADVISOR_MATERIAL_KEY", base64.b64encode(os.urandom(32)).decode())
    from cloud_warehouse import copilot_assets
    from cloud_warehouse.assets import LocalObjectStore

    admin, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    departure = list_departures(runtime, tenant.buyer)[0]
    with transaction(runtime, tenant.buyer) as conn:
        brief, version = trip_brief.load(conn, identifier)
        brief.departure_id = "WD-" + str(departure["id"])
        trip_brief.save(conn, tenant.buyer, identifier, brief, version + 1)
    store = LocalObjectStore(tmp_path / "materials")
    request_id = uuid4()
    body = b"%PDF-1.4 ACME fictional attachment"
    material = copilot_assets.upload(
        runtime, tenant.buyer, store, identifier, request_id, "ACME.pdf", body
    )
    assert (
        copilot_assets.upload(
            runtime, tenant.buyer, store, identifier, request_id, "ACME.pdf", body
        )["id"]
        == material["id"]
    )
    assert (
        copilot_assets.download(runtime, tenant.buyer, store, UUID(material["id"]))["body"] == body
    )
    with pytest.raises(Forbidden):
        copilot_assets.download(runtime, tenant.supplier, store, UUID(material["id"]))

    from cloud_warehouse import copilot_retention

    with admin.begin() as conn:
        conn.execute(
            text("UPDATE advisor_asset SET retain_until=now()-interval '1 day' WHERE id=:id"),
            {"id": material["id"]},
        )
        database_name = conn.scalar(text("SELECT current_database()"))
    assert (
        body not in (store.root / str(tenant.buyer.organization_id) / material["id"]).read_bytes()
    )
    with pytest.raises(Forbidden):
        copilot_assets.download(runtime, tenant.buyer, store, UUID(material["id"]))
    assert copilot_retention.purge(admin, store.root, database_name)["retired_materials"] >= 1
    assert not (store.root / str(tenant.buyer.organization_id) / material["id"]).exists()
    assert copilot_retention.purge(admin, store.root, database_name)["retired_materials"] == 0


def test_copilot_prompt_artifact_matches_runtime():
    from pathlib import Path

    from tour.api.copilot_model import SYSTEM

    assert Path("docs/cloud-warehouse/advisor/copilot-system.md").read_text() == SYSTEM + "\n"
