"""Fictional business acceptance scenarios with explicit expected decisions."""

import pytest

from cloud_warehouse.route_doc import Day, ItineraryBlock, Sight
from cloud_warehouse.route_editing import EditedDay, FactReview, rewrite_day, validate_edit
from cloud_warehouse.route_facts import evidence

SCENARIOS = [
    (
        "same_block_duration_swap",
        "ACME 宫外观不少于1小时；ACME 园外观不少于2小时。",
        "ACME 宫外观不少于2小时；ACME 园外观不少于1小时。",
        False,
    ),
    (
        "same_sentence_duration_swap",
        "ACME 宫外观1小时，ACME 园外观2小时。",
        "ACME 宫外观2小时，ACME 园外观1小时。",
        False,
    ),
    ("visit_mode_swap", "ACME 宫外观；ACME 园入内。", "ACME 宫入内；ACME 园外观。", False),
    ("ticket_object_swap", "ACME 宫含门票，不含讲解。", "ACME 宫含讲解，不含门票。", False),
    ("optional_gift_swap", "ACME 宫自费；ACME 园赠送。", "ACME 宫赠送；ACME 园自费。", False),
    (
        "condition_subject_swap",
        "如遇闭馆，ACME 宫取消；ACME 园外观。",
        "ACME 宫外观；如遇闭馆，ACME 园取消。",
        False,
    ),
    (
        "amount_subject_swap",
        "ACME 宫自费100元；ACME 园自费200元。",
        "ACME 宫自费200元；ACME 园自费100元。",
        False,
    ),
    ("quantity_repeated_loss", "ACME 宫外观1小时，再次外观1小时。", "ACME 宫外观1小时。", False),
    ("bound_weakened", "ACME 宫外观不少于1小时。", "ACME 宫外观约1小时。", False),
    ("hotel_qualification_lost", "入住ACME 酒店或同级。", "入住ACME 酒店。", False),
    (
        "flight_next_day_lost",
        "参考航班AC101，次日06:00+1抵达。",
        "参考航班AC101，06:00抵达。",
        False,
    ),
    ("unresolved_price_invented", "儿童价格待确认。", "儿童价格0元。", False),
    ("valid_lower_bound", "ACME 宫外观不少于1小时。", "ACME 宫安排外观至少1小时。", True),
    ("valid_upper_bound", "ACME 宫外观不超过2小时。", "ACME 宫安排外观至多2小时。", True),
    ("valid_fee_wording", "ACME 宫包含门票，不包含讲解。", "ACME 宫含门票，不含讲解。", True),
    (
        "valid_narrative",
        "ACME 宫外观，古老建筑诉说历史。",
        "ACME 宫安排外观，了解这座古老建筑的历史。",
        True,
    ),
    ("valid_unknown", "酒店及儿童价格待确认。", "酒店安排和儿童价格尚待确认。", True),
    (
        "valid_conditional",
        "如遇闭馆，取消ACME 宫，替代游览ACME 园。",
        "如遇闭馆，取消ACME 宫，替代游览ACME 园。",
        True,
    ),
]


def source(body):
    return Day(
        day=2,
        title="ACME 宫 → ACME 园",
        sights=[Sight(name="ACME 宫"), Sight(name="ACME 园")],
        text=body,
        blocks=[
            ItineraryBlock(
                block_id="source-2-1",
                type="visit",
                paragraphs=[body],
                visit_mode="outside",
                ticket_status="excluded",
            )
        ],
    )


def proposal(body):
    return EditedDay(
        summary="当天安排。",
        blocks=[dict(source_ids=[1], type="visit", title="当地游览", paragraphs=[body])],
    )


@pytest.mark.parametrize("name,before,after,accepted", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_business_scenarios(name, before, after, accepted):
    if accepted:
        validate_edit(source(before), proposal(after))
    else:
        with pytest.raises(ValueError, match="^EDITOR_"):
            validate_edit(source(before), proposal(after))


def test_failure_has_source_and_proposal_and_never_reaches_semantic_approval():
    original = source(SCENARIOS[0][1])
    calls = []

    def ask(prompt, data, schema):
        calls.append(schema)
        return proposal(SCENARIOS[0][2])

    result, report = rewrite_day(original, ask)
    assert result == original and calls == [EditedDay]
    assert report["code"] == "EDITOR_FACT_ASSOCIATION"
    issue = report["fact_checks"]["issues"][0]
    assert issue["day"] == 2 and issue["subject"].startswith("ACME")
    assert issue["source_block_ids"] == ["source-2-1"]
    assert issue["source_text"] and issue["proposed_text"]
    assert report["fact_checks"]["source_claims"] == evidence(original)


def test_preserves_explicit_block_facts_and_still_runs_semantic_review():
    day = source("ACME 宫外观不少于1小时，不含门票。")

    def ask(prompt, data, schema):
        if schema is FactReview:
            return schema(
                unsupported_claims=[], omitted_facts_or_conditions=[], changed_meanings=[]
            )
        return proposal("ACME 宫安排外观至少1小时，不含门票。")

    result, report = rewrite_day(day, ask)
    assert report["status"] == "edited" and report["fact_checks"]["status"] == "passed"
    assert result.blocks[0].visit_mode == "outside" and result.blocks[0].ticket_status == "excluded"


def test_no_structured_names_still_checks_explicit_source_subjects():
    day = source(SCENARIOS[0][1])
    day.sights = []
    with pytest.raises(ValueError, match="EDITOR_FACT_ASSOCIATION"):
        validate_edit(day, proposal(SCENARIOS[0][2]))


def test_facts_cannot_be_moved_only_into_heading():
    day = source("ACME 宫外观不少于1小时。")
    edit = proposal("ACME 宫游览。")
    edit.blocks[0].title = day.text
    with pytest.raises(ValueError, match="EDITOR_FACT_ASSOCIATION|EDITOR_NUMERIC_FACTS"):
        validate_edit(day, edit)


def test_repunctuation_spaces_and_flight_dates_keep_the_same_facts():
    day = source(
        "参考航班AC101 12:40-17:45，日期5/23 6/6 7/4。ACME 宫市区观光不少于1小时，建于1860年。"
    )
    day.sights = [Sight(name="ACME 宫 市区观光")]
    validate_edit(
        day,
        proposal(
            "参考航班AC101 12:40-17:45。日期5/23、6/6、7/4。ACME宫市区观光至少1小时。建于1860年。"
        ),
    )


def test_flight_carrier_change_is_not_hidden_by_identical_digits():
    day = source("参考航班AC101 12:40-17:45。")
    with pytest.raises(ValueError, match="EDITOR_FACT_ASSOCIATION"):
        validate_edit(day, proposal("参考航班BC101 12:40-17:45。"))


def test_excessive_fact_input_is_retained_without_model_call():
    day = source("ACME 宫外观1小时。" * 250)

    def ask(*args):
        raise AssertionError("Bounded source must not reach a model")

    result, report = rewrite_day(day, ask)
    assert result == day and report["code"] == "EDITOR_FACT_BOUND"


def test_single_letter_subject_durations_cannot_swap():
    day = source("A 外观不少于1小时；B 外观不少于2小时。")
    edit = proposal("A 外观不少于2小时；B 外观不少于1小时。")
    with pytest.raises(ValueError, match="EDITOR_FACT_ASSOCIATION"):
        validate_edit(day, edit)


def test_duplicated_number_is_not_hidden_by_set_comparison():
    day = source("ACME 宫外观约1小时，ACME 馆外观约1小时。")
    edit = proposal("ACME 宫和 ACME 馆外观约1小时。")
    with pytest.raises(ValueError, match="EDITOR_NUMERIC_FACTS"):
        validate_edit(day, edit)


def test_added_condition_is_rejected():
    day = source("ACME 宫外观。")
    edit = proposal("ACME 宫外观，赠送门票。")
    with pytest.raises(ValueError, match="EDITOR_CONDITION_ADDED"):
        validate_edit(day, edit)
