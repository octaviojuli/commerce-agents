"""Independent review regressions for c172c66; fictional inputs only."""

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from tour.advisor import db, grounding, privacy
from tour.advisor import need as needs
from tour.advisor.interpret import changes
from tour.advisor.need import Need
from tour.advisor.tests.test_review import ready, run_turn
from tour.advisor.tests.test_story import Scripted


def party_change(saved, message, value):
    understanding = SimpleNamespace(
        rejected=[],
        changes=[
            SimpleNamespace(field="party", value=value, source="said", evidence=message, hint="")
        ],
    )
    fills, proposals, rejected = changes(saved, understanding, message, date(2026, 9, 28))
    assert not rejected
    return needs.parse("party", fills["party"]["new"] if fills else proposals[0]["new"])


def test_partial_adult_correction_preserves_other_travelers():
    saved = needs.set_field(
        Need(),
        "party",
        {"adults": 3, "children": [{"age": 8, "bed": True}], "seniors": [{"age": 70}]},
        "said",
    )
    changed = party_change(saved, "成人改成两个大人，其他不变", {"adults": 2})
    assert changed.adults == 2
    assert changed.children == saved.get("party").children
    assert changed.seniors == saved.get("party").seniors


def test_couple_with_friends_does_not_reduce_four_adults_to_two():
    party = party_change(
        Need(),
        "我和老婆，还有两位朋友一起去",
        {"adults": 4, "children": [], "seniors": []},
    )
    assert party.adults == 4


@pytest.mark.parametrize(
    "said,claim",
    [
        ("我希望行程含早餐和午餐。", "行程含早餐和午餐。"),
        ("我希望签证费用包含。", "签证费用包含。"),
        ("听别人说这条线包含签证费。", "这条线包含签证费。"),
    ],
)
def test_customer_preferences_and_hearsay_do_not_prove_supplier_facts(said, claim):
    checked = grounding.check(claim, [], said=[said])
    assert checked["removed"], checked
    assert not checked["text"]
    judged = grounding.judged(checked, [("safe", 0.99)], [], said=[said])
    assert judged["removed"] and not judged["text"]


@pytest.mark.parametrize(
    "reply",
    [
        "您希望行程含早餐和午餐。",
        "您的需求是含早餐和午餐。",
        "您希望行程含早餐和午餐，这些我都记下了。",
    ],
)
def test_explicit_customer_requirements_can_still_be_repeated(reply):
    assert not grounding.check(reply, [], said=["我希望行程含早餐和午餐。"])["removed"]


def test_actual_supplier_evidence_can_confirm_a_customer_preference():
    claim = "行程含早餐和午餐。"
    facts = [{"fact_id": "meal", "text": claim, "section": "行程"}]
    result = grounding.check(claim, facts, said=["我希望行程含早餐和午餐。"])
    assert result["text"] == claim and result["claims"][0]["fact_id"] == "meal"


def test_saved_customer_evidence_is_not_treated_as_supplier_publication():
    claim = "行程含早餐和午餐。"
    facts = [
        {"fact_id": "said:themes", "text": "我希望行程含早餐和午餐", "section": "客人原话"},
        {"fact_id": "need:themes:0", "text": "主题：行程含早餐和午餐", "section": "需求"},
    ]
    result = grounding.check(claim, facts)
    assert result["reasons"] == ["customer_claim"]
    assert not grounding.judged(result, [("safe", 1.0)], facts)["text"]


@pytest.mark.parametrize(
    "pasted",
    [
        "138 1234 5678",
        "手机：138-1234-5678",
        "电话 +86 138 1234 5678",
        "护照号 e12345678",
        "护照：e-1234-5678",
        "passport no: e 1234 5678",
    ],
)
def test_pasted_number_variants_are_hidden(pasted):
    masked, found = privacy.mask(pasted)
    assert found and "1234" not in masked and "5678" not in masked


@pytest.mark.parametrize(
    "text",
    [
        "ACME 12 天，每人 15800 元，出团编号 HU11",
        "产品编号 E-12345678，2026-12-01 出发",
        "项目单价 138 元、1234 元、5678 元",
        "价格 138\n1234\n5678 元",
    ],
)
def test_product_codes_prices_and_dates_are_preserved(text):
    assert privacy.mask(text) == (text, [])


def test_common_personal_number_formats_are_masked_before_model_and_storage(env):
    client, engine, owner = env
    deal = ready(client)
    seen = []

    class Watching(Scripted):
        async def call(self, name, payload):
            seen.append(str(payload))
            return await super().call(name, payload)

    pasted = "护照号e12345678，手机138 1234 5678"
    run_turn(engine, owner, UUID(deal), Watching(), pasted)
    with engine.connect() as conn:
        stored = conn.execute(db.turns.select().where(db.turns.c.deal_id == UUID(deal))).all()
    assert all("e12345678" not in item and "138 1234 5678" not in item for item in seen)
    assert "e12345678" not in str(stored) and "138 1234 5678" not in str(stored)
