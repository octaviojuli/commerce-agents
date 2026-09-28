"""F6–F10 and privacy regressions, using isolated ACME customer and supplier fixtures."""

import base64
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from cloud_warehouse import (
    conversations,
    copilot_engine,
    copilot_plans,
    copilot_privacy,
    quote_shares,
    quotes,
    travel_requirements,
    trip_brief,
)
from cloud_warehouse import copilot_records as records
from cloud_warehouse.advisor_actions import search_parameters
from cloud_warehouse.persistence import Forbidden
from cloud_warehouse.pricing import PriceSchedule
from tour.api.copilot_model import Extraction

from .test_copilot import new_deal, priced
from .test_imports import excel_source as excel_source
from .test_quotes import managed_offer as managed_offer


def test_direction_gates_and_example_ranking():
    for key in ("themes", "destination_regions", "destination_examples"):
        brief = trip_brief.TripBrief.model_validate(
            {
                key: {"value": ["欧洲"]},
                "window": {"value": {"start": "2030-01-01", "end": "2030-02-01"}},
            }
        )
        assert trip_brief.readiness(brief)["search"]["ready"]
        query, attrs = search_parameters(brief)
        assert query == ("欧洲" if key == "destination_regions" else "")
        if key != "destination_regions":
            assert "欧洲" in attrs.attributes["destination_examples"]


def test_operator_holiday_approximate_days_and_adult_only_inference():
    result = travel_requirements.normalize(
        {
            "window": {"value": {"start": "2027-01-01", "end": "2027-01-03"}},
            "days": {"value": {"min": 7, "max": 7}},
            "adults": {"value": 2},
        },
        "2个成人，元旦前后，一周",
        trip_brief.TripBrief(),
        date(2026, 9, 27),
    )
    assert result["window"]["value"] == {"start": "2026-12-26", "end": "2027-01-03"}
    assert result["window"]["source"] == "inferred"
    assert result["days"]["value"] == {"min": 6, "max": 8}
    assert result["children"]["value"] == 0
    assert result["children"]["source"] == "inferred"


def test_soft_fields_preserve_choices_and_no_spurious_share_invalidation():
    brief = trip_brief.TripBrief(
        route_id="WP-ACME",
        departure_id="WD-ACME",
        share_token="ACME",
        offline_hold_note="ACME 备注",
    )
    updated = copilot_engine.merged(
        brief,
        {
            "preferences": {
                "value": [{"key": "slow_pace", "label": "慢节奏"}],
                "source": "advisor",
            },
            "budget": {"value": {"max_per_person": "15000", "currency": "CNY"}},
        },
        4,
    )
    assert updated.route_id == brief.route_id
    assert updated.departure_id == brief.departure_id
    assert updated.share_token == brief.share_token
    assert copilot_engine.material(updated) == copilot_engine.material(brief)


def test_unconfigured_future_lunar_holiday_cannot_use_model_invented_dates():
    result = travel_requirements.normalize(
        {"window": {"value": {"start": "2035-01-01", "end": "2035-01-07"}}},
        "2035年春节出发",
        trip_brief.TripBrief(),
        date(2034, 9, 27),
    )
    assert result["window"]["value"] is None
    assert "尚未配置" in result["window"]["hint"]


def test_partial_adoption_and_partial_extraction(database, tenant):
    _, runtime = database
    identifier, _ = new_deal(runtime, tenant.buyer)
    trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=0, fields={"adults": 2, "children": 0}),
    )
    turn = conversations.begin(
        runtime, tenant.buyer, identifier, "ACME-change", "改成3位成人，预算15000"
    )
    proposal = copilot_engine.propose(
        runtime,
        tenant.buyer,
        identifier,
        {
            "adults": {"value": 3, "source": "said", "evidence": "3位成人"},
            "budget": {
                "value": {"max_per_person": "15000"},
                "source": "said",
                "evidence": "预算15000",
            },
            "child_ages": {"value": [99], "source": "said", "evidence": "99岁"},
        },
        "改成3位成人，预算15000",
        turn["turn_id"],
    )
    assert proposal["status"] == "pending" and "child_ages" in proposal["rejected"]
    conversations.finish(runtime, tenant.buyer, turn, {}, turn["messages"], [])
    for version, field, accept in ((1, "adults", True), (2, "budget", False)):
        copilot_engine.adopt(
            runtime,
            tenant.buyer,
            identifier,
            UUID(proposal["proposal_id"]),
            copilot_engine.Adoption(
                request_id=uuid4(), expected_version=version, fields=[field], accept=accept
            ),
        )
    detail = records.detail(runtime, tenant.buyer, identifier)
    assert not detail["pending_fields"]
    assert detail["brief"]["body"]["adults"]["value"] == 3
    assert detail["brief"]["body"]["budget"]["value"] is None
    extracted = Extraction.model_validate(
        {
            "changes": [
                {"field": "adults", "value": 2, "source": "said", "evidence": "2大"},
                {"field": "rooms", "value": {"twins": -1}, "source": "said"},
            ]
        }
    )
    assert len(extracted.changes) == 1 and extracted.rejected_fields == ["rooms"]


def test_customer_document_encrypted_masked_and_owner_reveal(database, tenant, monkeypatch):
    admin, runtime = database
    monkeypatch.setenv("WAREHOUSE_ADVISOR_MATERIAL_KEY", base64.b64encode(b"a" * 32).decode())
    number = "ACME-PASSPORT-1234"
    command = records.CustomerWrite(
        request_id=uuid4(),
        customer=records.Customer(
            name="ACME 家庭", travelers=[records.Traveler(name="ACME 旅客", document_number=number)]
        ),
    )
    customer = records.write_customer(runtime, tenant.buyer, command)
    assert customer["body"]["travelers"][0]["document_number"].endswith("1234")
    assert number not in str(customer)
    with admin.connect() as conn:
        stored = conn.execute(
            text("SELECT body FROM advisor_customer WHERE id=:id"), {"id": customer["id"]}
        ).scalar_one()
        assert number not in str(stored) and stored["travelers"][0]["document_cipher"]
    assert (
        copilot_privacy.reveal(runtime, tenant.buyer, customer["id"], 0)["document_number"]
        == number
    )
    with pytest.raises(Forbidden):
        copilot_privacy.reveal(runtime, tenant.supplier, customer["id"], 0)
    updated = records.write_customer(
        runtime,
        tenant.buyer,
        records.CustomerWrite(
            request_id=uuid4(),
            expected_version=1,
            customer=records.Customer.model_validate(customer["body"]),
        ),
        customer["id"],
    )
    assert updated["version"] == 2
    assert (
        copilot_privacy.reveal(runtime, tenant.buyer, customer["id"], 0)["document_number"]
        == number
    )


def test_mixed_room_and_child_bed_prices_require_explicit_rules():
    schedule = PriceSchedule(
        currency="CNY",
        market={"adult": 1000, "child": 700, "single_room": 200},
        settlement={"adult": 800, "child": 500, "single_room": 150},
        fees_complete=True,
        child_occupies_seat=True,
        child_age_min=0,
        child_age_max=12,
    )
    party = quotes.Party(
        adults=2,
        children=1,
        child_ages=[8],
        rooms={"total": 2, "doubles": 1, "twins": 1, "child_bed": False},
        room_type="混合房型待供应商确认",
    )
    assert not quotes.calculate(schedule, party)["complete"]
    schedule = schedule.model_copy(
        update={
            "room_supplements": {
                "doubles": {"market": 0, "settlement": 0},
                "twins": {"market": 100, "settlement": 80},
            },
            "child_bed_prices": {"unoccupied": {"market": 600, "settlement": 400}},
        }
    )
    schedule = PriceSchedule.model_validate(schedule.model_dump())
    quote = quotes.calculate(schedule, party)
    assert (
        quote["complete"]
        and quote["market_total"] == "2700.00"
        and quote["settlement_total"] == "2080.00"
    )


async def test_database_share_policy_accepts_price_older_than_five_minutes(
    database, tenant, managed_offer
):
    admin, runtime = database
    _, quote = await priced(runtime, tenant, managed_offer)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE quote_snapshot SET fresh_until=now()-interval '30 minutes' WHERE id=:id"),
            {"id": UUID(quote["quote_id"])},
        )
    result = quote_shares.create(
        runtime, tenant.buyer, UUID(quote["quote_id"]), quote_shares.Create(request_id=uuid4())
    )
    assert result["token"]


async def test_plan_share_current_versions_and_customer_signal(
    database, tenant, managed_offer, authentication
):
    _, runtime = database
    identifier, _ = await priced(runtime, tenant, managed_offer)
    brief = trip_brief.get(runtime, tenant.buyer, identifier)
    plan = copilot_plans.build(
        runtime,
        tenant.buyer,
        identifier,
        copilot_plans.Build(
            request_id=uuid4(),
            expected_version=brief["version"],
            product_ids=[brief["body"]["route_id"]],
        ),
    )
    shared = copilot_plans.share(
        runtime,
        tenant.buyer,
        identifier,
        plan["id"],
        records.Command(request_id=uuid4(), expected_version=brief["version"]),
    )
    public = copilot_plans.public_read(runtime, authentication, shared["token"], signal="select")
    assert public and all(k not in str(public) for k in ("settlement", "profit", "margin", "WP-"))
    detail = records.detail(runtime, tenant.buyer, identifier)
    assert any(r["body"].get("category") == "customer_signal" for r in detail["records"])
    trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(expected_version=brief["version"], fields={"adults": 3}),
    )
    assert copilot_plans.public_read(runtime, authentication, shared["token"]) is None
