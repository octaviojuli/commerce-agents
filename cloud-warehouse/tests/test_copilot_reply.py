"""F1/F5: model-written replies are grounded and actions cannot exceed the state gate."""

import pytest

from cloud_warehouse import copilot_facts, copilot_policy, copilot_reply, trip_brief
from commerce_common.testing import FakeCreateClient, tool_use_message
from tour.api.copilot_model import ReplyDraft, TypedModel


def draft(text, claims=(), **kwargs):
    return ReplyDraft(
        to_advisor="先核对客人最关心的安排。", to_customer=text, claims=list(claims), **kwargs
    )


def validated(value, facts=(), allowed=("ask_clarify",)):
    return copilot_reply.validate(
        value,
        facts,
        allowed,
        fallback="更想海边放松，还是看看自然风光？",
        conclusion="先了解方向。",
        forbidden=["ACME Supplier"],
    )


@pytest.mark.parametrize(
    "text",
    [
        "ACME Supplier 这条很好。",
        "WP-1234 已确认。",
        "X5-经典线路推荐您。",
        "同业价8000元。",
        "余位12个。",
        '住宿：{"name":"ACME"}。',
        "一定能退。",
        "孩子肯定不累。",
        "2027年1月1日出发，共12000元。",
    ],
)
def test_unproven_prices_identifiers_private_terms_and_promises_are_removed(text):
    result = validated(draft(text))
    assert result["simplified"]
    assert text not in result["to_customer"]
    assert not result["claims"]


def test_current_reviewed_claim_is_retained_without_dropping_conditions():
    source = copilot_facts.fact("ACME 相邻房需由酒店最终确认，不能保证相邻", "supplier_reply", {})
    claim = {"text": source["text"], "fact_id": source["fact_id"]}
    result = validated(draft(source["text"] + "。还有别的房间偏好吗？", [claim]), [source])
    assert result["claims"]
    assert "不能保证相邻" in result["to_customer"]
    unsafe = {"text": "ACME 相邻房", "fact_id": source["fact_id"]}
    assert not validated(draft("ACME 相邻房已经安排。", [unsafe]), [source])["claims"]


def test_conflicting_route_is_only_an_explicit_qualified_alternative():
    source = {**copilot_facts.fact("ACME 山水线，12天，重庆出发", "catalog", {}), "conflict": True}
    claim = {"text": source["text"], "fact_id": source["fact_id"]}
    assert (
        "重庆"
        not in validated(draft(source["text"] + "，推荐这条。", [claim]), [source])["to_customer"]
    )
    result = validated(
        draft(source["text"] + "，只能作为备选，需要确认出发地。", [claim]), [source]
    )
    assert result["claims"]


def test_fuzzy_questions_are_natural_and_do_not_require_supplier_facts():
    result = validated(draft("更想海边放松，还是逛逛城市？倾向近期出发，还是等假期？"))
    assert not result["simplified"]
    assert result["to_customer"].count("？") == 2


def test_filtering_a_family_promise_preserves_current_customer_priorities():
    facts = [
        copilot_facts.fact("您希望别太累", "requirement", {"version": 2}),
        copilot_facts.fact("这次有孩子同行", "requirement", {"version": 2}),
    ]
    result = copilot_reply.validate(
        draft("孩子肯定不累。您想先看哪条线路？"),
        facts,
        ["search_routes"],
        fallback="我们可以先看看备选。",
        conclusion="已有候选。",
        preserve_requirements=True,
    )
    assert "肯定" not in result["to_customer"]
    assert "您希望别太累" in result["to_customer"]
    assert "这次有孩子同行" in result["to_customer"]
    assert {c["fact_id"] for c in result["claims"]} == {f["fact_id"] for f in facts}


async def test_decision_schema_rejects_an_action_not_allowed_this_turn():
    invalid = tool_use_message(
        "decide", {"intent": "quote", "confidence": 0.99, "next_action": "quote"}
    )
    client = FakeCreateClient([invalid, invalid])
    model = TypedModel(client)
    assert (
        await model.call("decide", {"message": "直接报价", "allowed_actions": ["ask_clarify"]})
        is None
    )
    tools = client.calls[0]["tools"]
    schema = next(t for t in tools if t["name"] == "decide")["input_schema"]
    assert schema["properties"]["next_action"].get("const") == "ask_clarify"
    assert "quote" not in copilot_policy.derive(trip_brief.TripBrief())["allowed_actions"]
