"""Human choices, not a model search result, move the advisor workflow forward."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cloud_warehouse import advisor_actions, advisor_flow, conversations, trip_brief
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.persistence import transaction
from shopping_agent import ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState

from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE
from .test_quotes import managed_offer as managed_offer

ROUTES = [
    {"product_id": "WP-ACME-A", "title": "ACME 海岸轻奢 8 天"},
    {"product_id": "WP-ACME-B", "title": "ACME 海岸臻奢 8 天"},
]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("有四个客人，11月份去 ACME 海岸旅游，8天左右", None),
        ("第一条", "WP-ACME-A"),
        ("选第2条，看看团期", "WP-ACME-B"),
        ("选轻奢那条", "WP-ACME-A"),
        ("看看 ACME 的团期", None),
        ("对比第一条和第二条", None),
        ("不要第二条", None),
        ("第一条和第二条都看看", None),
        ("第三条", None),
    ],
)
def test_unique_choice(message, expected):
    assert advisor_flow.choice(message, ROUTES) == expected


async def test_initial_search_cannot_select_or_advance_even_with_all_quote_fields(
    database, tenant, managed_offer
):
    _, runtime = database
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    cid = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    context = ShoppingSessionContext(session_id=str(cid), user_id=str(tenant.buyer.user_id))
    state = ShoppingSessionState()
    b = trip_brief.patch(
        runtime,
        tenant.buyer,
        cid,
        trip_brief.BriefPatch(
            expected_version=0,
            fields={
                "destinations": ["ACME"],
                "window": {"start": str(FUTURE), "end": str(FUTURE)},
                "adults": 4,
                "children": 0,
                "party_total": 4,
                "rooms": {"doubles": 2},
            },
        ),
    )
    work = conversations.begin(
        runtime,
        tenant.buyer,
        cid,
        "initial",
        "四位成人，ACME 海岸，11月出发，8天左右，两间双人房，找线路",
    )
    found = await advisor_actions.perform(
        backend,
        context,
        state,
        advisor_actions.Action(action="search_routes", expected_version=b["version"], limit=3),
    )
    route = found["payload"]["items"][0]["product_id"]
    for operation in ["departures", "offers", "quote"]:
        with pytest.raises(ValueError, match="顾问尚未选定"):
            advisor_flow.require_choice(runtime, tenant.buyer, cid, operation, route, b)
    assert trip_brief.get(runtime, tenant.buyer, cid)["body"]["route_id"] is None
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [{"type": "ui", "data": {"component": "warehouse_routes", "payload": found["payload"]}}],
    )
    # No route identified: the model cannot choose the first one on the user's behalf.
    work = conversations.begin(runtime, tenant.buyer, cid, "ambiguous", "看看团期")
    with pytest.raises(ValueError, match="顾问尚未选定"):
        advisor_flow.require_choice(runtime, tenant.buyer, cid, "departures", route, b)
    conversations.finish(
        runtime, tenant.buyer, work, state.model_dump(mode="json"), work["messages"], []
    )
    work = conversations.begin(runtime, tenant.buyer, cid, "choose", "选择第一条，看看团期")
    advisor_flow.require_choice(runtime, tenant.buyer, cid, "departures", route, b)
    dep = await advisor_actions.perform(
        backend,
        context,
        state,
        advisor_actions.Action(
            action="departures", product_id=route, expected_version=b["version"]
        ),
    )
    # Choosing a route does not also authorize an arbitrary departure/quote.
    with pytest.raises(ValueError, match="顾问尚未选定"):
        advisor_flow.require_choice(
            runtime,
            tenant.buyer,
            cid,
            "quote",
            dep["payload"]["items"][0]["product_id"],
            dep["brief"],
        )
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [{"type": "ui", "data": {"component": "warehouse_departures", "payload": dep["payload"]}}],
    )
    work = conversations.begin(runtime, tenant.buyer, cid, "details", "介绍第一条线路的行程")
    with pytest.raises(ValueError, match="顾问尚未选定"):
        advisor_flow.require_choice(runtime, tenant.buyer, cid, "departures", route, dep["brief"])
    conversations.finish(
        runtime, tenant.buyer, work, state.model_dump(mode="json"), work["messages"], []
    )
    work = conversations.begin(runtime, tenant.buyer, cid, "quote", "第一期，请询价")
    advisor_flow.require_choice(
        runtime, tenant.buyer, cid, "quote", dep["payload"]["items"][0]["product_id"], dep["brief"]
    )
    conversations.finish(
        runtime, tenant.buyer, work, state.model_dump(mode="json"), work["messages"], []
    )
    updated = trip_brief.patch(
        runtime,
        tenant.buyer,
        cid,
        trip_brief.BriefPatch(
            expected_version=dep["brief"]["version"], fields={"destinations": ["ACME 山脉"]}
        ),
    )
    assert updated["body"]["route_id"] is None and updated["body"]["departure_id"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"destination_examples": ["ACME 城市"]},
        {"preferences": [{"key": "no_shopping", "label": "不进购物店"}]},
    ],
)
def test_changed_search_cannot_keep_a_fresh_quote_or_old_selection(database, tenant, fields):
    _, runtime = database
    cid = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    with transaction(runtime, tenant.buyer) as conn:
        body = trip_brief.TripBrief(
            destinations={"value": ["ACME"]},
            window={"value": {"start": FUTURE, "end": FUTURE}},
            route_id="WP-ACME",
            departure_id="WD-ACME",
            quote_id=uuid4(),
            quote_brief_version=1,
            share_token="ACME-share-receipt",
            quote={
                "fresh_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "complete": True,
            },
        )
        trip_brief.save(conn, tenant.buyer, cid, body, 1)
    before = trip_brief.get(runtime, tenant.buyer, cid)
    assert not before["quote_stale"] and "share_quote" in before["allowed_actions"]
    after = trip_brief.patch(
        runtime, tenant.buyer, cid, trip_brief.BriefPatch(expected_version=1, fields=fields)
    )
    assert after["quote_stale"] and after["stage"] == "select"
    assert "share_quote" not in after["allowed_actions"]
    assert all(after["body"][key] is None for key in ("route_id", "departure_id", "share_token"))


def test_ordinals_follow_appended_pages_and_new_search_resets_them():
    first = {"items": ROUTES, "page_scope": "ACME-scope", "after": None, "next_cursor": "ACME-next"}
    second = {
        "items": [{"product_id": "WP-ACME-C", "title": "ACME 第三条"}],
        "page_scope": "ACME-scope",
        "after": "ACME-next",
        "next_cursor": None,
    }
    items = advisor_flow.displayed_items([second, first])
    assert advisor_flow.choice("选择第一条，看看团期", items) == "WP-ACME-A"
    assert advisor_flow.choice("选择第三条，看看团期", items) == "WP-ACME-C"
    assert not advisor_flow.displayed_items([second])
    assert not advisor_flow.displayed_items([second, {**first, "page_scope": "ACME-other"}])
    assert advisor_flow.displayed_items([{**second, "after": None}, first]) == second["items"]
