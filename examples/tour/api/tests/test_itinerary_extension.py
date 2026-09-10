# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_itinerary``: what the model may put on a 定制方案, what the server numbers and
joins onto it, and what it refuses. The plan store is the suite's own file, so a version
written here is read back the way the workbench reads one."""

import pytest

from commerce_common.presentation import enrich_partial, partial_ui_tool_names
from commerce_common.skills import SkillRegistry
from shopping_agent import ShoppingSessionContext
from tour.api.itinerary import build_itinerary_extension
from tour.api.mock_erp import MockErpClient
from tour.api.plans import PlanDay, mark_requests
from tour.api.store import SqliteSessionStore
from tour.api.tour_backend import (
    MAX_PLAN_VERSIONS,
    MAX_PLANS_PER_SESSION,
    TourBackend,
    TourToolExecutor,
)

from .conftest import CONTACT_MOBILE, CUSTOMER_ID, PINNED_TODAY

ROUTE = "RT-1022"
DEPARTURE = "DP-3012"
PRIVATE = "RT-1023"  # a 包团 line, reachable by id and never a search result
OTHER_ROUTE = "RT-1021"
OTHER_DEPARTURE = "DP-3008"

# The three days every plan below is built from, and the day the customer asks for later.
BASELINE = [
    {"label": "第 1 天 · 乌鲁木齐集合", "note": "全天接机，晚上把路况讲一遍。"},
    {"label": "第 2 天 · 乌鲁木齐—赛里木湖", "note": "走果子沟大桥，傍晚到湖边看日落。"},
    {"label": "第 3 天 · 赛里木湖—特克斯", "note": "上午环湖，下午翻山，住喀拉峻山下。"},
]
EXTRA = {"label": "第 3 天 · 喀拉峻牧场一日", "note": "客人要的一天，全天不安排车程，待计调确认。"}


@pytest.fixture
def erp() -> MockErpClient:
    return MockErpClient(today=PINNED_TODAY)


@pytest.fixture
def backend(erp, tmp_path) -> TourBackend:
    """The deployment's backend with a plan store of its own, in place of the shared
    fixture's: a plan outlives the conversation, so the tool has nowhere to write without
    one."""
    return TourBackend(
        erp,
        today=PINNED_TODAY,
        customer_id=CUSTOMER_ID,
        contact_mobile=CONTACT_MOBILE,
        plans=SqliteSessionStore(tmp_path / "plans.sqlite"),
    )


@pytest.fixture
def make_plan_executor(main, backend, state):
    def build(session: ShoppingSessionContext) -> TourToolExecutor:
        return TourToolExecutor(
            backend=backend,
            config=main.agent.config,
            skills=SkillRegistry([]),
            session=session,
            state=state,
            extensions=list(main.agent.extra_presentation_tools),
        )

    return build


@pytest.fixture
def executor(make_plan_executor, session) -> TourToolExecutor:
    return make_plan_executor(session)


async def _shown(executor) -> None:
    """The flow a plan comes after: the 线路 searched, presented, and opened for its 团期."""
    await executor.execute("search_products", {"query": "伊犁", "limit": 8})
    await executor.execute("present_products", {"picks": [{"product_id": ROUTE}]})
    await executor.execute("get_product_details", {"product_id": ROUTE})


def _ui_payload(result):
    return next(event for event in result.events if event.type == "ui").data


async def _plan(executor, days=None, **fields) -> dict:
    result = await executor.execute(
        "present_itinerary",
        {"title": "伊犁定制", "days": days or BASELINE, "route_id": ROUTE, **fields},
    )
    assert not result.is_error, result.result_text
    return _ui_payload(result)["payload"]


def test_the_tool_is_advertised_with_its_input_schema(main):
    """The shared contract's own check needs a merchant portal this example has no fixture
    for (``test_contract.py`` says so), so the rule it states is checked here."""
    extension = build_itinerary_extension()
    assert extension.component == "itinerary"
    tool = next(t for t in main.agent._tools if t["name"] == "present_itinerary")
    assert tool["input_schema"] == extension.input_schema
    assert tool["description"] == extension.description

    # What the model is shown is the shape the executor enforces.
    schema = extension.payload_model.model_json_schema()
    advertised = extension.input_schema["properties"]
    assert extension.input_schema["additionalProperties"] is False
    assert advertised.keys() == schema["properties"].keys()
    assert sorted(extension.input_schema["required"]) == sorted(schema["required"])
    assert advertised["days"]["maxItems"] == schema["properties"]["days"]["maxItems"]


async def test_a_payload_that_is_not_the_tools_shape_is_a_soft_error(executor):
    await _shown(executor)
    result = await executor.execute(
        "present_itinerary", {"title": "伊犁定制", "days": [], "route_id": ROUTE}
    )
    assert result.is_error
    assert not [event for event in result.events if event.type == "ui"]
    assert "Invalid present_itinerary payload" in result.result_text


async def test_a_route_the_advisor_has_not_been_shown_is_refused(executor):
    """A plan is a change to a 线路 the advisor has in front of them, so the card comes first.
    A route the session searched and never showed is held the same way an unseen one is."""
    await executor.execute("search_products", {"query": "伊犁", "limit": 8})
    held = await executor.execute(
        "present_itinerary", {"title": "凭空的方案", "days": BASELINE, "route_id": ROUTE}
    )
    assert held.is_error
    assert not [event for event in held.events if event.type == "ui"]
    assert "present_products" in held.result_text and "RT-" in held.result_text

    await executor.execute("present_products", {"picks": [{"product_id": ROUTE}]})
    assert (await _plan(executor))["version"] == 1


async def test_a_private_line_opened_by_its_id_needs_no_card(executor, erp):
    """A 包团 or 定制 line is never a search result, so it was never a card either: opening it
    by id is what the advisor did, and that is enough to build the customer's plan on."""
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    await executor.execute("get_product_details", {"product_id": PRIVATE})
    result = await executor.execute(
        "present_itinerary", {"title": "王鑫包团改行程", "days": BASELINE, "route_id": PRIVATE}
    )
    assert not result.is_error, result.result_text
    assert _ui_payload(result)["payload"]["route"]["product_id"] == PRIVATE


async def test_the_first_version_is_the_baseline_with_the_advisors_two_texts(executor):
    await _shown(executor)
    payload = await _plan(executor, travel_dates="2026-10-14 至 2026-10-16", party="2大1小")
    assert payload["version"] == 1
    assert payload.get("parent_version") is None
    assert payload["summary"] == "基线原样"
    assert payload["travel_dates"] == "2026-10-14 至 2026-10-16"
    assert payload["party"] == "2大1小"
    assert payload["plan_id"].startswith("PL-")
    # Nothing has changed from anything yet, so no day carries a mark.
    assert all(day.get("change") is None for day in payload["days"])
    assert [day["label"] for day in payload["days"]] == [day["label"] for day in BASELINE]
    assert payload["removed_days"] == []
    assert "/p/" in payload["share_url"]
    assert payload["plan_id"] in payload["handoff_text"]
    # The baseline is the catalog record, not the details read it came from.
    assert payload["route"]["product_id"] == ROUTE
    assert "variants" not in payload["route"] and "specs" not in payload["route"]
    # No 团期 was named, so the plan carries no reference price at all.
    assert "reference_price" not in payload and "departure" not in payload
    assert "erp_route_id" not in payload


async def test_a_second_version_marks_the_day_it_added_and_leaves_the_rest_alone(executor):
    await _shown(executor)
    first = await _plan(executor)
    payload = await _plan(
        executor, days=[*BASELINE[:2], EXTRA, BASELINE[2]], plan_id=first["plan_id"]
    )
    assert (payload["version"], payload["parent_version"]) == (2, 1)
    assert [day.get("change") for day in payload["days"]] == ["same", "same", "added", "same"]
    assert "第 3 天" in payload["summary"] and payload["summary"].startswith("+1 天")
    assert payload["removed_days"] == []
    # The day the route does not carry is the 计调's to answer, and the card says so.
    assert [day["request"] for day in payload["days"]] == [False, False, True, False]
    # A version is its own card, with its own link.
    assert payload["share_url"] != first["share_url"]


async def test_a_day_the_next_version_drops_is_drawn_where_it_sat(executor):
    await _shown(executor)
    first = await _plan(executor)
    payload = await _plan(executor, days=[BASELINE[0], BASELINE[2]], plan_id=first["plan_id"])
    assert [day.get("change") for day in payload["days"]] == ["same", "same"]
    assert payload["removed_days"] == [
        {"index": 1, "label": BASELINE[1]["label"], "note": BASELINE[1]["note"]}
    ]
    assert "删 1 天" in payload["summary"]


async def test_the_reference_price_is_the_baseline_departures_own(executor):
    await _shown(executor)
    payload = await _plan(executor, departure_id=DEPARTURE)
    assert payload["departure"]["product_id"] == DEPARTURE
    price = payload["reference_price"]
    assert price["tong_ye_adult"] > 0 and price["market_adult"] > 0
    assert price["quote_source"] and price["party_total"] > 0
    assert "参考价" in payload["handoff_text"]


async def test_a_departure_of_another_route_is_dropped_and_named_to_the_model(executor):
    """The reference price is the baseline 团期's, so a 团期 of another 线路 is not one. The
    plan stands without it, because the price was only ever a reference."""
    await _shown(executor)
    await executor.execute("get_product_details", {"product_id": OTHER_DEPARTURE})
    result = await executor.execute(
        "present_itinerary",
        {
            "title": "伊犁定制",
            "days": BASELINE,
            "route_id": ROUTE,
            "departure_id": OTHER_DEPARTURE,
        },
    )
    assert not result.is_error
    payload = _ui_payload(result)["payload"]
    assert "departure" not in payload and "reference_price" not in payload
    assert OTHER_DEPARTURE in result.result_text


async def test_a_plan_built_in_another_conversation_is_refused(executor, make_plan_executor):
    await _shown(executor)
    mine = await _plan(executor)
    theirs = make_plan_executor(ShoppingSessionContext(session_id="s-2", user_id="demo-user-2"))
    await theirs.execute("present_products", {"picks": [{"product_id": ROUTE}]})
    result = await theirs.execute(
        "present_itinerary",
        {"title": "别人的方案", "days": BASELINE, "route_id": ROUTE, "plan_id": mine["plan_id"]},
    )
    assert result.is_error
    assert "不属于本会话" in result.result_text


async def test_a_plan_keeps_the_route_it_was_built_on(executor):
    await _shown(executor)
    mine = await _plan(executor)
    await executor.execute("get_product_details", {"product_id": OTHER_DEPARTURE})
    await executor.execute("present_products", {"picks": [{"product_id": OTHER_ROUTE}]})
    result = await executor.execute(
        "present_itinerary",
        {
            "title": "换了条线",
            "days": BASELINE,
            "route_id": OTHER_ROUTE,
            "plan_id": mine["plan_id"],
        },
    )
    assert result.is_error
    assert ROUTE in result.result_text


async def test_a_conversation_builds_at_most_three_plans(executor):
    await _shown(executor)
    for _ in range(MAX_PLANS_PER_SESSION):
        await _plan(executor)
    result = await executor.execute(
        "present_itinerary", {"title": "第四个方案", "days": BASELINE, "route_id": ROUTE}
    )
    assert result.is_error
    assert f"最多 {MAX_PLANS_PER_SESSION} 个定制方案" in result.result_text


async def test_a_plan_carries_at_most_thirty_versions(executor, backend, session):
    await _shown(executor)
    first = await _plan(executor)
    plan = backend.plan_of(first["plan_id"])
    days = mark_requests([PlanDay(label=day["label"], note=day["note"]) for day in BASELINE])
    for _ in range(MAX_PLAN_VERSIONS - 1):
        await backend.add_plan_version(
            session,
            plan,
            days,
            title="再改一版",
            travel_dates=None,
            party=None,
            reference_price=None,
            base_version=None,
        )
    result = await executor.execute(
        "present_itinerary",
        {"title": "第 31 版", "days": BASELINE, "route_id": ROUTE, "plan_id": plan.plan_id},
    )
    assert result.is_error
    assert f"最多 {MAX_PLAN_VERSIONS} 版" in result.result_text


async def test_the_line_the_agency_built_is_linked_without_a_new_version(executor, erp):
    """The 线路 the 计调 built for the plan is the plan's and not a version's, so linking it
    draws the latest version again rather than sending the customer a new one."""
    await _shown(executor)
    first = await _plan(executor)
    erp._routes[1023]["routeName"] = "王鑫包团-伊犁定制行程 9 日"
    await executor.execute("get_product_details", {"product_id": PRIVATE})
    result = await executor.execute(
        "present_itinerary",
        {
            "title": "伊犁定制",
            "days": BASELINE,
            "route_id": ROUTE,
            "plan_id": first["plan_id"],
            "erp_route_id": PRIVATE,
        },
    )
    assert not result.is_error, result.result_text
    payload = _ui_payload(result)["payload"]
    assert payload["erp_route_id"] == PRIVATE
    assert payload["version"] == 1 and payload["share_url"] == first["share_url"]


async def test_a_deployment_with_no_plan_store_says_so(executor, backend):
    await _shown(executor)
    backend.plans = None
    result = await executor.execute(
        "present_itinerary", {"title": "无处可存", "days": BASELINE, "route_id": ROUTE}
    )
    assert result.is_error
    assert "此部署未配置方案库" in result.result_text


async def test_the_streaming_prefix_renders_the_days_without_a_version(executor, state):
    extension = build_itinerary_extension()
    assert "present_itinerary" in partial_ui_tool_names({}, [extension])

    # Nothing is renderable until a day carries a label.
    assert enrich_partial(extension, {"title": "伊犁定制", "days": [{}]}, state) is None

    await _shown(executor)
    enriched = enrich_partial(
        extension,
        {"title": "伊犁定制", "party": "2大1小", "days": [*BASELINE[:2], {"label": "第 3 天"}]},
        state,
    )
    assert enriched is not None
    component, payload, _signature = enriched
    assert component == "itinerary"
    assert payload["title"] == "伊犁定制" and payload["party"] == "2大1小"
    assert [day["label"] for day in payload["days"]] == [
        BASELINE[0]["label"],
        BASELINE[1]["label"],
        "第 3 天",
    ]
    assert "note" not in payload["days"][2]
    assert not {"version", "plan_id", "share_url"} & payload.keys()
