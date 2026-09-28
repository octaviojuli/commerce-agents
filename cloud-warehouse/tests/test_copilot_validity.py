"""F2/F3: requirement revisions and quote validity survive routine actions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import (
    advisor_actions,
    copilot_engine,
    copilot_facts,
    copilot_inquiries,
    quote_shares,
    quotes,
    trip_brief,
)
from cloud_warehouse import copilot_records as records
from cloud_warehouse import copilot_sales as sales
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import transaction
from shopping_agent import ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState

from .test_copilot import new_deal, priced
from .test_imports import excel_source as excel_source
from .test_quotes import managed_offer as managed_offer


async def test_requote_and_departure_reads_keep_revision_confirmation_and_reply(
    database, tenant, managed_offer
):
    _, runtime = database
    identifier, quote = await priced(runtime, tenant, managed_offer)
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    session = ShoppingSessionContext(session_id=str(identifier), user_id=str(tenant.buyer.user_id))
    state = ShoppingSessionState()
    saved = trip_brief.get(runtime, tenant.buyer, identifier)
    brief = trip_brief.TripBrief.model_validate(saved["body"])
    route = await backend.get_product_details(session, brief.route_id)
    departure = await backend.get_product_details(session, brief.departure_id)
    state.remember_products([route, departure])
    confirmation = copilot_engine.confirm(
        runtime,
        tenant.buyer,
        identifier,
        copilot_engine.Confirmation(
            request_id=uuid4(),
            expected_version=saved["version"],
            evidence="ACME 全部核对确认",
            confirmed_items=list(copilot_engine.CONFIRM_ITEMS),
        ),
    )
    inquiry = copilot_inquiries.submit(
        runtime,
        tenant.buyer,
        identifier,
        copilot_inquiries.Submit(
            request_id=uuid4(),
            expected_version=saved["version"],
            product_id=brief.route_id,
            question="ACME 相邻房如何安排？",
        ),
    )
    result = await advisor_actions.perform(
        backend,
        session,
        state,
        advisor_actions.Action(
            action="departures", expected_version=saved["version"], product_id=brief.route_id
        ),
    )
    assert result["brief"]["version"] == saved["version"]
    reply = copilot_inquiries.reply(
        runtime,
        tenant.supplier,
        inquiry["id"],
        copilot_inquiries.Reply(request_id=uuid4(), answer="ACME 需酒店确认，不能保证相邻。"),
    )
    copilot_inquiries.adopt(
        runtime,
        tenant.buyer,
        identifier,
        reply["id"],
        records.Command(request_id=uuid4(), expected_version=saved["version"]),
    )
    # A new snapshot ID with exactly the same commercial terms is an event.
    result = await advisor_actions.perform(
        backend,
        session,
        state,
        advisor_actions.Action(
            action="quote", expected_version=saved["version"], product_id=brief.departure_id
        ),
    )
    assert result["payload"]["quote_id"] != quote["quote_id"]
    detail = records.detail(runtime, tenant.buyer, identifier)
    assert detail["brief"]["version"] == saved["version"]
    assert not next(r for r in detail["records"] if r["id"] == confirmation["id"])["stale"]
    assert copilot_facts.adopted(runtime, tenant.buyer, identifier, saved["version"])
    current = trip_brief.TripBrief.model_validate(detail["brief"]["body"])
    current.quote = {**current.quote, "market_total": "123456.00"}
    assert copilot_engine.confirmation_reason(confirmation, current, saved["version"])


@pytest.mark.parametrize("minutes,valid", [(30, True), (1500, False)])
async def test_quote_document_lifetime_is_independent_of_price_freshness(
    database, tenant, managed_offer, monkeypatch, minutes, valid
):
    _, runtime = database
    identifier, quote = await priced(runtime, tenant, managed_offer)
    saved = trip_brief.get(runtime, tenant.buyer, identifier)
    now = datetime.now(UTC) + timedelta(minutes=minutes)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(quotes, "datetime", Clock)
    monkeypatch.setattr(sales, "datetime", Clock)
    monkeypatch.setattr(quote_shares, "datetime", Clock)
    displayed = quotes.get(runtime, tenant.buyer, UUID(quote["quote_id"]))
    assert displayed["price_stale"]
    assert displayed["quote_expired"] is not valid
    if not valid:
        with pytest.raises(Conflict):
            copilot_engine.confirm(
                runtime,
                tenant.buyer,
                identifier,
                copilot_engine.Confirmation(
                    request_id=uuid4(),
                    expected_version=saved["version"],
                    evidence="ACME 确认行程和费用",
                    confirmed_items=list(copilot_engine.CONFIRM_ITEMS),
                ),
            )
        with pytest.raises(Conflict):
            sales.retail(
                runtime,
                tenant.buyer,
                identifier,
                sales.Retail(request_id=uuid4(), expected_version=saved["version"]),
            )
        return
    copilot_engine.confirm(
        runtime,
        tenant.buyer,
        identifier,
        copilot_engine.Confirmation(
            request_id=uuid4(),
            expected_version=saved["version"],
            evidence="ACME 确认行程和费用",
            confirmed_items=list(copilot_engine.CONFIRM_ITEMS),
        ),
    )
    retail = sales.retail(
        runtime,
        tenant.buyer,
        identifier,
        sales.Retail(request_id=uuid4(), expected_version=saved["version"]),
    )
    shared = sales.share(
        runtime,
        tenant.buyer,
        identifier,
        retail["id"],
        records.Command(request_id=uuid4(), expected_version=saved["version"]),
    )
    assert shared["token"]
    sales.sale(
        runtime,
        tenant.buyer,
        identifier,
        sales.Sale(
            request_id=uuid4(), expected_version=saved["version"], retail_quote_id=retail["id"]
        ),
    )


def test_unchanged_edit_does_not_create_revision(database, tenant):
    _, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    saved = trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=0, fields={"adults": 2}),
    )
    repeated = trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=1, fields={"adults": 2}),
    )
    assert saved["version"] == repeated["version"] == 1
    with transaction(runtime, tenant.buyer) as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM advisor_record WHERE deal_id=:id AND kind='state'"),
                {"id": identifier},
            )
            == 1
        )
