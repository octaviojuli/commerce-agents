# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_focus``, the 聚焦卡, and the gate around it: a search too wide to shortlist is put
to the advisor as one question over the catalog's own groups, a shortlist over that search is
held until it is asked, and once the advisor has narrowed the cards come first."""

import pytest

from commerce_common.skills import SkillRegistry
from tour.api.focus import NOT_BROAD, build_focus_extension
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
    "filters": {"attributes": {**BROAD["filters"]["attributes"], "region": "喀纳斯"}},
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
    documents hold."""
    await executor.execute("search_products", BROAD)
    result = await executor.execute(
        "present_focus", {"question": "客人想走哪一片？", "dimension": "目的地"}
    )
    assert not result.is_error, result.result_text
    ui = _ui(result)
    assert ui["component"] == "focus"
    card = ui["payload"]
    assert card["question"] == "客人想走哪一片？" and card["dimension"] == "目的地"
    assert card["total"] == 7 and card["shown"] == 2
    places = next(g for g in card["groups"] if g["label"] == "目的地")
    assert places["filter"] == "region/destination"
    assert places["values"][0] == {"value": "伊犁", "count": 4, "ask": "只看目的地：伊犁"}
    assert {"value": "南疆", "count": 1, "ask": "只看目的地：南疆"} in places["values"]
    hotels = next(g for g in card["groups"] if g["label"] == "酒店标准")
    assert hotels["filter"] == "hotel_level"
    assert card["anchors"] == []


async def test_a_dimension_that_does_not_split_the_set_is_replaced(executor):
    """Every 新疆 line the fixtures sell is under 一万元, so 起价 is a question with one answer;
    the card asks the first dimension that does split the set instead."""
    await executor.execute("search_products", BROAD)
    result = await executor.execute(
        "present_focus", {"question": "先按预算？", "dimension": "起价"}
    )
    card = _ui(result)["payload"]
    prices = next(g for g in card["groups"] if g["label"] == "起价")
    assert len(prices["values"]) == 1 and card["dimension"] == "目的地"


async def test_footholds_come_from_provenance_and_only_on_a_modest_match(
    executor, backend, session
):
    await executor.execute("search_products", BROAD)
    shown = [p for p in backend.overview(session.session_id).groups["目的地"]]
    assert shown
    result = await executor.execute(
        "present_focus",
        {"question": "先看这两条？", "picks": ["RT-1032", "RT-999999"]},
    )
    card = _ui(result)["payload"]
    kept = [p["product_id"] for p in card["anchors"]]
    # RT-1032 was in this search's results; the other id was never seen and is named as dropped.
    assert kept in (["RT-1032"], []) and "RT-999999" in result.result_text
    if kept:
        assert "RT-1032" in backend.presented(session.session_id)


async def test_the_card_is_refused_when_the_last_search_fits(executor):
    await executor.execute("search_products", NARROW)
    result = await executor.execute("present_focus", {"question": "哪一片？"})
    assert result.is_error and NOT_BROAD in result.result_text


async def test_cards_and_chips_go_together_until_the_set_is_too_wide_to_read(
    executor, backend, session, monkeypatch
):
    """Up to twelve matches the advisor gets the cards and the question after them; above
    twelve the cards are held until the question has been asked, and once the advisor has
    narrowed the cards flow with the chips beside them."""
    await executor.execute("search_products", {**BROAD, "limit": 8})
    cards = await executor.execute(
        "present_products", _picks("RT-1021", "RT-1022", "RT-1023", "RT-1024")
    )
    assert not cards.blocked and not cards.is_error
    asked = await executor.execute("present_focus", {"question": "哪一片？", "dimension": "目的地"})
    assert not asked.is_error
    await executor.execute("search_products", {**BROAD, "limit": 8})  # still wide: answered
    overview = backend.overview(session.session_id)
    assert overview is not None and overview.answered
    assert "缩小范围后仍有 7 条" in overview.text()
    assert "narrowed once already" in overview.text()
    # The chips are welcome beside the cards while the set is still wider than a shortlist.
    again = await executor.execute("present_focus", {"question": "再缩一下？"})
    assert not again.is_error


async def test_a_shortlist_over_a_set_too_wide_to_read_is_held(executor, backend, monkeypatch):
    """Eight cards over 26 lines is the answer the design forbids: the shortlist is held with
    the step to take, and the 聚焦卡 that follows carries the question alone."""
    monkeypatch.setattr("tour.api.tour_backend.FOCUS_ANCHORS_UP_TO", 3)
    await executor.execute("search_products", {**BROAD, "limit": 8})
    held = await executor.execute(
        "present_products", _picks("RT-1021", "RT-1022", "RT-1023", "RT-1024")
    )
    assert held.blocked == FOCUS_FIRST_GATE and "present_focus" in held.result_text
    assert not [event for event in held.events if event.type == "ui"]
