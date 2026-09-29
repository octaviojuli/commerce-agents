"""The draft check: meaning-preserving rewording passes, reversed or trimmed facts do not."""

import pytest

from tour.advisor.grounding import check, supported

DAY = "ACME 小镇散步，含早餐；午餐和晚餐自理。每天预留自由活动时间。"


def fact(text):
    return {"fact_id": "f", "text": text}


@pytest.mark.parametrize(
    ("source", "claim", "ok"),
    [
        ("取消须支付1000元手续费", "取消须支付1000元手续费", True),
        ("取消须支付1000元手续费", "取消无须支付1000元手续费", False),
        ("午餐和晚餐自理", "午餐和晚餐自理", True),
        ("午餐和晚餐自理", "午餐和晚餐不需要自理", False),
        ("儿童餐需要提前两天登记", "儿童餐需要提前2天登记", True),
        ("儿童餐需要提前两天登记", "儿童餐需要提前登记", False),
        (DAY, "餐食方面是含早餐", True),
        (DAY, "早餐包含在内", True),
        (DAY, "含午餐", False),
        (DAY, "三餐全含", False),
        ("餐食：仅限6岁以下儿童。早餐免费。其他年龄按成人标准收费。", "早餐免费", False),
        ("全家合计69000元", "全家合计69000元", True),
        ("全家合计69000元", "全家合计7万元", False),
        ("相邻房间不能保证，以酒店最终确认为准", "相邻房间已经安排", False),
        ("5岁那个不占床，8岁的占床", "5岁的小朋友不占床", True),
        ("5岁那个不占床，8岁的占床", "8岁的占床", True),
        ("5岁那个不占床，8岁的占床", "5岁的占床", False),
        ("无须签证", "须签证", False),
        ("早餐免费", "早餐收费", False),
    ],
)
def test_a_claim_keeps_the_fact_meaning(source, claim, ok):
    assert supported(claim, fact(source)) is ok


def test_questions_about_saved_needs_are_removed_even_as_statements():
    result = check(
        "孩子分别几岁？方便的话也告诉我您希望什么时候出发。这次住几间房呢？",
        [],
        known={"child_ages", "window"},
    )
    assert result["text"] == "这次住几间房呢？"
    assert result["reasons"] == ["asks_known", "asks_known"]


def test_a_condition_and_its_number_may_span_two_clauses():
    facts = [fact("儿童不占床每位11800元")]
    assert check("如果孩子不占床，每位11800元。", facts)["removed"] == []


def test_the_customer_words_back_numbers_never_facts():
    said = ["预算压到每人1.5万吧，我妈70了"]
    assert check("预算调整为每人1.5万以内，妈妈70岁也一起去。", [], said=said)["removed"] == []
    assert check("午餐包含在内。", [], said=["听说包含午餐"])["removed"]


def test_private_terms_codes_promises_and_conflicting_routes_are_cut():
    result = check(
        "同业价是60400元。X1-慢游线路很好。保证孩子不累。山湖漫游也不错。山湖漫游只能算备选，需要确认。",
        [fact("同业价是60400元")],
        conflicts=["山湖漫游"],
    )
    assert result["text"] == "山湖漫游只能算备选，需要确认。"
    assert set(result["reasons"]) == {"private", "promise", "conflict"}


def test_holiday_names_are_not_money():
    assert check("时间改到元旦假期，孩子放假。", [])["removed"] == []


def test_saying_what_is_not_known_is_kept_and_a_disguised_claim_is_not():
    facts = [fact("全程3-4星酒店")]
    kept = check("购物店资料里没写明，仍待核实。", facts)
    assert kept["removed"] == []
    assert check("购物店资料里没写明，我去跟供应商确认后回您。", facts)["removed"]
    cut = check("购物店已经安排好了，含3次免费购物。", facts)
    assert cut["removed"]


def test_a_route_without_a_reviewed_document_is_cited_and_marked():
    unreviewed = {"fact_id": "u", "text": "全程3-4星酒店", "product_id": "p", "reviewed": False}
    result = check("全程3-4星酒店。", [unreviewed])
    assert result["removed"] == []
    assert result["claims"][0]["reviewed"] is False
    # A route that has reviewed facts is cited only from those.
    reviewed = {"fact_id": "r", "text": "全程4星酒店", "product_id": "p", "reviewed": True}
    assert check("全程3-4星酒店。", [unreviewed, reviewed])["removed"]
