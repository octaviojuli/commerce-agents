"""Fictional facts verify rewrite gates and separation from protected business fields."""

import pytest

from cloud_warehouse.route_content import candidate, customer_projection, day_basis
from cloud_warehouse.route_doc import Day, ItineraryBlock, Quality, RouteDoc, Source, Summary
from cloud_warehouse.route_editing import EditedDay, FactReview, heading, rewrite_day


def document():
    return RouteDoc(
        route_id=1,
        route_code="ACME",
        name="ACME",
        department="",
        summary=Summary(days=1),
        source=Source(
            attachment_name="ACME.pdf", attachment_url="", bytes=1, parsed_at=None, parser="fixture"
        ),
        quality=Quality(completeness=1),
        days=[
            Day(
                day=1,
                title="ACME 港-180KM-ACME 城-220KM-ACME 湾",
                places=["ACME 港", "ACME 城", "ACME 湾"],
                text="ACME 宫外观，不少于1小时。含门票，不含讲解；如遇闭馆，替代游览ACME 广场。",
            )
        ],
    )


def edited(day):
    return EditedDay(
        summary="外观ACME 宫；如遇闭馆，替代游览ACME 广场。",
        blocks=[
            {
                "source_ids": list(range(1, len(day.blocks) + 1)),
                "type": "visit",
                "title": "ACME 宫与广场",
                "paragraphs": [
                    "ACME 宫安排外观，不少于1小时。含门票，不含讲解；如遇闭馆，替代游览ACME 广场。"
                ],
            }
        ],
    )


def test_title_separates_reference_without_reassigning_distances():
    original = document()
    doc = candidate(original.model_dump(mode="json"), normalize_titles=True)
    assert doc.days[0].title == "ACME 港 → ACME 城 → ACME 湾"
    assert doc.days[0].travel_reference == original.days[0].title
    assert original.days[0].title.endswith("220KM-ACME 湾")
    doc.days[0].title = "人工保留主题"
    assert candidate(doc.model_dump(mode="json")).days[0].title == "人工保留主题"
    assert "travel_reference" in customer_projection(doc)["days"][0]
    assert heading(Day(day=1, title="ACME 港-约2小时-ACME 城", places=["ACME 港", "ACME 城"])) == (
        "ACME 港 → ACME 城",
        "ACME 港-约2小时-ACME 城",
    )


def test_edit_preserves_protected_fields_and_basis():
    doc = candidate(document().model_dump(mode="json"), normalize_titles=True)
    day = doc.days[0]

    def ask(prompt, data, schema):
        return (
            edited(day)
            if schema is EditedDay
            else FactReview(
                unsupported_claims=[], omitted_facts_or_conditions=[], changed_meanings=[]
            )
        )

    result, report = rewrite_day(day, ask)
    assert report["status"] == "edited"
    assert result.summary_basis_hash == day_basis(result)
    excluded = {"summary", "blocks", "summary_basis_hash"}
    assert result.model_dump(exclude=excluded) == day.model_dump(exclude=excluded)
    assert day.blocks != result.blocks


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("number", "EDITOR_NUMERIC_FACTS"),
        ("condition", "EDITOR_CONDITION_LOSS"),
        ("coverage", "EDITOR_SOURCE_COVERAGE"),
        ("review", "SEMANTIC_REVIEW"),
        ("unavailable", "EDITOR_MODEL_UNAVAILABLE"),
    ],
)
def test_rejected_changes_retain_original_day(mutation, code):
    day = candidate(document().model_dump(mode="json"), normalize_titles=True).days[0]

    def ask(prompt, data, schema):
        if mutation == "unavailable":
            raise ValueError("EDITOR_MODEL_UNAVAILABLE")
        if schema is FactReview:
            return FactReview(
                unsupported_claims=["ACME 新增承诺"],
                omitted_facts_or_conditions=[],
                changed_meanings=[],
            )
        out = edited(day)
        if mutation == "number":
            out.blocks[0].paragraphs[0] = out.blocks[0].paragraphs[0].replace("1小时", "2小时")
        if mutation == "condition":
            out.blocks[0].paragraphs[0] = out.blocks[0].paragraphs[0].replace("外观", "参观")
        if mutation == "coverage":
            out.blocks[0].source_ids = [99]
        return out

    result, report = rewrite_day(day, ask)
    assert result == day
    assert report["code"] == code and report["status"] == "retained"


def test_another_days_new_number_is_rejected():
    day = document().days[0]
    day.blocks = [
        ItineraryBlock(block_id="ACME-1", paragraphs=["ACME 港外观1小时"]),
        ItineraryBlock(block_id="ACME-2", paragraphs=["ACME 城自费2小时"]),
    ]
    out = EditedDay(
        summary="ACME",
        blocks=[
            {
                "source_ids": [1],
                "type": "visit",
                "title": "ACME 港",
                "paragraphs": ["ACME 港外观2小时"],
            },
            {
                "source_ids": [2],
                "type": "optional",
                "title": "ACME 城",
                "paragraphs": ["ACME 城自费1小时"],
            },
        ],
    )
    _, report = rewrite_day(day, lambda *_: out)
    assert report["code"] == "EDITOR_NUMERIC_FACTS"
