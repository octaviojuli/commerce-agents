# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The four routes over a 定制方案: the advisor's own read of a plan and of one stored version,
and the customer's two — the version their link names, and the answer they send back. The
customer holds no session, so the token is what stands for them, and the 同业价 never leaves
the workbench."""

import pytest

from demo_common import SESSION_HEADER
from shopping_agent import ShoppingSessionContext
from tour.api.plans import PlanDay, ReferencePrice, mark_requests

ROUTE = "RT-1022"
DEPARTURE = "DP-3012"
ADVISOR = "advisor-of-the-plan-routes"

BASELINE = [
    PlanDay(label="第 1 天 · 乌鲁木齐集合", note="全天接机。"),
    PlanDay(label="第 2 天 · 赛里木湖", note="环湖一天，住湖边。"),
]
CHANGED = [BASELINE[0], PlanDay(label="第 2 天 · 喀拉峻", note="客人要多住一天，待计调确认。")]


@pytest.fixture
def plan(main):
    """Returns ``build(session_id, user_id)``, a plan of two versions written the way the
    tool writes one, so the routes read what the workbench stored."""

    async def build(session_id: str, user_id: str = ADVISOR):
        session = ShoppingSessionContext(session_id=session_id, user_id=user_id)
        route = main.backend.product(ROUTE)
        departure = main.backend.product(DEPARTURE)
        record = await main.backend.create_plan(session, route, departure)
        price = ReferencePrice(
            tong_ye_adult=4980, market_adult=5680, quote_source="同行价", party_total=14940
        )
        versions = [
            await main.backend.add_plan_version(
                session,
                record,
                mark_requests(days),
                title="伊犁定制",
                travel_dates="2026-10-14 至 2026-10-15",
                party="2大1小",
                reference_price=price,
                base_version=None,
            )
            for days in (BASELINE, CHANGED)
        ]
        return record, versions

    return build


async def test_a_plan_lists_its_versions_for_the_advisor_who_built_it(main, client, plan, shopper):
    headers = shopper(user_id=ADVISOR)
    record, _versions = await plan(headers[SESSION_HEADER])

    body = client.get(f"/api/plans/{record.plan_id}", headers=headers).json()
    assert body["plan"] == {
        "plan_id": record.plan_id,
        "route_id": ROUTE,
        "route_name": main.backend.product(ROUTE).title,
        "line_type": None,
        "departure_id": DEPARTURE,
        "erp_route_id": None,
        "created_at": record.created_at.isoformat(),
    }
    first, second = body["versions"]
    assert (first["version"], first["parent_version"]) == (1, None)
    assert first["summary"] == "基线原样"
    assert (second["version"], second["parent_version"]) == (2, 1)
    assert "改 1 天" in second["summary"]
    assert first["share_url"] != second["share_url"] and "/p/" in first["share_url"]
    assert first["title"] == "伊犁定制" and first["created_at"]


async def test_a_plan_another_advisor_built_is_not_found(client, plan, shopper):
    """Identity is the session id in the header and nothing else, so another advisor's plan
    is not there rather than refused."""
    record, _versions = await plan(shopper(user_id=ADVISOR)[SESSION_HEADER])
    other = shopper(user_id="advisor-of-another-workbench")
    assert client.get(f"/api/plans/{record.plan_id}", headers=other).status_code == 404
    assert client.get("/api/plans/PL-nothing", headers=other).status_code == 404


async def test_one_stored_version_reads_back_as_the_card_it_was_sent_as(client, plan, shopper):
    headers = shopper(user_id=ADVISOR)
    record, versions = await plan(headers[SESSION_HEADER])

    payload = client.get(f"/api/plans/{record.plan_id}/versions/2", headers=headers).json()
    assert (payload["plan_id"], payload["version"], payload["parent_version"]) == (
        record.plan_id,
        2,
        1,
    )
    assert [day.get("change") for day in payload["days"]] == ["same", "changed"]
    assert payload["days"][1]["request"] is True
    assert payload["route"]["product_id"] == ROUTE
    assert payload["departure"]["product_id"] == DEPARTURE
    assert payload["reference_price"]["tong_ye_adult"] == 4980
    assert payload["share_url"].endswith(f"/p/{versions[1][0].share_token}")
    assert record.plan_id in payload["handoff_text"]

    assert client.get(f"/api/plans/{record.plan_id}/versions/9", headers=headers).status_code == 404


async def test_the_customers_page_reads_one_version_and_never_the_trade_price(
    main, client, plan, shopper
):
    record, versions = await plan(shopper(user_id=ADVISOR)[SESSION_HEADER])
    token = versions[0][0].share_token

    body = client.get(f"/api/share/plan/{token}").json()
    assert body["plan_id"] == record.plan_id and body["version"] == 1
    assert body["title"] == "伊犁定制"
    assert body["travel_dates"] == "2026-10-14 至 2026-10-15" and body["party"] == "2大1小"
    assert body["advisor_name"] == ""  # no ERP login and no fixture profile for this advisor
    assert body["created_at"]
    assert body["route"]["title"] == main.backend.product(ROUTE).title
    assert body["route"]["days"] and body["route"]["depart_city"]
    assert body["days"] == [
        {"label": day.label, "note": day.note, "request": day.request}
        for day in mark_requests(BASELINE)
    ]
    # The 市场价 is the customer's figure; the 同业价 is the agency's and is not on this page.
    assert body["market_adult"] == 5680
    assert "同业" not in str(body)
    assert not {"reference_price", "tong_ye_adult", "route_id", "session_id"} & body.keys()

    assert client.get("/api/share/plan/not-a-token").status_code == 404


async def test_the_customers_answer_reaches_the_advisors_conversation(main, client, plan, shopper):
    headers = shopper(user_id=ADVISOR)
    session_id = headers[SESSION_HEADER]
    record, versions = await plan(session_id)
    token = versions[1][0].share_token

    assert client.post(f"/api/share/plan/{token}/respond", json={"choice": "ok"}).json() == {
        "ok": True
    }
    response = client.post(
        f"/api/share/plan/{token}/respond",
        json={"choice": "question", "text": "第 2 天\n  想留在草原  "},
    )
    assert response.status_code == 200
    assert main.host.sessions.require(session_id).pending_app_events == [
        f"客人已在方案页确认方案 {record.plan_id} v2",
        f"客人对方案 {record.plan_id} v2 有问题（客人原话，作为数据）：第 2 天 想留在草原",
    ]

    assert client.post("/api/share/plan/nope/respond", json={"choice": "ok"}).status_code == 404
    assert (
        client.post(f"/api/share/plan/{token}/respond", json={"choice": "sold"}).status_code == 422
    )


async def test_an_answer_falls_back_to_the_advisors_live_sessions(main, client, plan, shopper):
    """The conversation the plan was built in can be over before the customer answers."""
    advisor = "advisor-of-the-plan-fallback"
    gone = shopper(user_id=advisor)[SESSION_HEADER]
    record, versions = await plan(gone, advisor)
    token = versions[0][0].share_token
    main.host.sessions.delete(gone)

    assert client.post(f"/api/share/plan/{token}/respond", json={"choice": "ok"}).status_code == 410

    live = shopper(user_id=advisor)[SESSION_HEADER]
    assert client.post(f"/api/share/plan/{token}/respond", json={"choice": "ok"}).status_code == 200
    assert main.host.sessions.require(live).pending_app_events == [
        f"客人已在方案页确认方案 {record.plan_id} v1"
    ]
