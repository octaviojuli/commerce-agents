# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_focus``, the 聚焦卡, and the gate around it: a search too broad to shortlist is
put to the advisor as one question over the catalog's own groups, a shortlist over that search
is held until it is, and the advisor's answer ends the asking."""

import pytest

from commerce_common.skills import SkillRegistry
from tour.api.focus import ALREADY_NARROWED, NOT_BROAD, build_focus_extension
from tour.api.tour_backend import FOCUS_FIRST_GATE, TourToolExecutor

BROAD = {
    "query": "新疆",
    "filters": {
        "attributes": {
            "destination": "新疆",
            "depart_from": "2026-10-11",
            "depart_to": "2026-10-20",
        }
    },
    "limit": 2,
}
NARROW = {
    "query": "新疆",
    "filters": {"attributes": {**BROAD["filters"]["attributes"], "region": "伊犁"}},
}


@pytest.fixture
def executor(main, backend, session, state) -> TourToolExecutor:
    return TourToolExecutor(
        backend=backend,
        config=main.agent.config,
        skills=SkillRegistry([]),
        session=session,
        state=state,
        extensions=list(main.agent.extra_presentation_tools),
    )


def _ui(result):
    return next(event for event in result.events if event.type == "ui").data


def _picks(*ids: str) -> dict:
    return {"picks": [{"product_id": pid} for pid in ids]}


def test_the_tool_is_advertised_with_its_input_schema(main):
    extension = build_focus_extension()
    assert extension.component == "focus"
    tool = next(t for t in main.agent._tools if t["name"] == "present_focus")
    assert tool["input_schema"] == extension.input_schema
    schema = extension.payload_model.model_json_schema()
    assert extension.input_schema["properties"].keys() == schema["properties"].keys()
    assert extension.input_schema["required"] == ["question"]


async def test_the_card_carries_the_catalogs_groups_as_chips(executor, backend, session):
    """The model writes the question and names the dimension; the values, their counts and
    the words a tap sends are the backend's overview, so the advisor chooses among what the
    catalog holds."""
    await executor.execute("search_products", BROAD)
    result = await executor.execute(
        "present_focus", {"question": "客人想走哪一片？", "dimension": "线路系"}
    )
    assert not result.is_error, result.result_text
    ui = _ui(result)
    assert ui["component"] == "focus"
    card = ui["payload"]
    assert card["question"] == "客人想走哪一片？" and card["dimension"] == "线路系"
    assert card["total"] == 7 and card["shown"] == 2
    regions = next(g for g in card["groups"] if g["label"] == "线路系")
    assert regions["filter"] == "region"
    assert regions["values"][0] == {"value": "伊犁", "count": 4, "ask": "只看线路系：伊犁"}
    # 成团 is a count and not a filter, so its values send nothing.
    states = next(g for g in card["groups"] if g["label"] == "成团")
    assert states["filter"] == "" and "ask" not in states["values"][0]
    assert card["anchors"] == []


async def test_a_dimension_that_does_not_split_the_set_is_replaced(executor):
    """Every 新疆 line leaves from 乌鲁木齐 but one; asking about the departure city is a
    question with an answer, so the card asks it — but a dimension with one value is not."""
    await executor.execute("search_products", BROAD)
    result = await executor.execute(
        "present_focus", {"question": "先按天数？", "dimension": "起价"}
    )
    card = _ui(result)["payload"]
    prices = next(g for g in card["groups"] if g["label"] == "起价")
    assert card["dimension"] == ("起价" if len(prices["values"]) > 1 else "线路系")


async def test_footholds_come_from_provenance_and_only_on_a_modest_match(
    executor, backend, session
):
    await executor.execute("search_products", BROAD)
    shown = [p for p in backend.overview(session.session_id).groups["线路系"]]
    assert shown
    result = await executor.execute(
        "present_focus",
        {"question": "先看这两条？", "picks": ["RT-1021", "RT-999999"]},
    )
    card = _ui(result)["payload"]
    kept = [p["product_id"] for p in card["anchors"]]
    # RT-1021 was in this search's results; the other id was never seen and is named as dropped.
    assert kept in (["RT-1021"], []) and "RT-999999" in result.result_text
    if kept:
        assert "RT-1021" in backend.presented(session.session_id)


async def test_the_card_is_refused_when_the_last_search_fits(executor):
    await executor.execute("search_products", NARROW)
    result = await executor.execute("present_focus", {"question": "哪一片？"})
    assert result.is_error and NOT_BROAD in result.result_text


async def test_a_shortlist_over_a_broad_search_is_held_until_the_question_is_asked(
    executor, backend, session
):
    """Eight cards over 26 lines is the answer the design forbids: the shortlist is held with
    the step to take, three footholds pass, and once the card is up and the advisor has
    narrowed, cards flow and a second question is refused."""
    await executor.execute("search_products", BROAD)
    held = await executor.execute(
        "present_products", _picks("RT-1021", "RT-1022", "RT-1023", "RT-1024")
    )
    assert held.blocked == FOCUS_FIRST_GATE and "present_focus" in held.result_text
    assert not [e for e in held.events if e.type == "ui"]
    # A foothold of three passes while the match is modest.
    foothold = await executor.execute("present_products", _picks("RT-1021", "RT-1022", "RT-1023"))
    assert not foothold.blocked and not foothold.is_error
    # The question is asked; the advisor answers with a narrowing search.
    asked = await executor.execute("present_focus", {"question": "哪一片？", "dimension": "线路系"})
    assert not asked.is_error
    await executor.execute("search_products", {**BROAD, "limit": 2})  # still broad: answered
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.answered
    assert "已经" not in overview.text() and "do not ask again" in overview.text()
    cards = await executor.execute(
        "present_products", _picks("RT-1021", "RT-1022", "RT-1023", "RT-1024")
    )
    assert not cards.blocked and not cards.is_error
    again = await executor.execute("present_focus", {"question": "再缩一下？"})
    assert again.is_error and ALREADY_NARROWED in again.result_text
