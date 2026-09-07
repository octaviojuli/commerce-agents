# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``present_shortlist`` and the share link its card carries: what the model may put on the
card, what the server joins onto it, and the one route the customer's page calls back."""

from commerce_common.presentation import enrich_partial, partial_ui_tool_names
from demo_common import SESSION_HEADER
from tour.api.shortlist import build_shortlist_extension

ROUTE = "RT-1022"
FIRST = "DP-1022-20260916"
SECOND = "DP-1022-20261014"
UNSEEN = "DP-1022-20991231"


async def _seen_shortlist(executor):
    """Search the 线路 and open it, so the session has both 团期 and their route."""
    await executor.execute("search_products", {"query": "伊犁", "limit": 8})
    await executor.execute("get_product_details", {"product_id": ROUTE})


def _ui_payload(result):
    return next(event for event in result.events if event.type == "ui").data


def test_the_tool_is_advertised_with_its_input_schema(main):
    """The shared contract's own check needs a merchant portal this example has no fixture
    for (``test_contract.py`` says so), so the rule it states is checked here."""
    extension = build_shortlist_extension()
    assert extension.component == "shortlist"
    tool = next(t for t in main.agent._tools if t["name"] == "present_shortlist")
    assert tool["input_schema"] == extension.input_schema
    assert tool["description"] == extension.description

    # What the model is shown is the shape the executor enforces.
    schema = extension.payload_model.model_json_schema()
    advertised = extension.input_schema["properties"]
    assert extension.input_schema["additionalProperties"] is False
    assert advertised.keys() == schema["properties"].keys() == {"title", "departure_ids", "note"}
    assert sorted(extension.input_schema["required"]) == sorted(schema["required"])
    assert (
        advertised["departure_ids"]["maxItems"] == schema["properties"]["departure_ids"]["maxItems"]
    )


async def test_a_shortlist_joins_each_departure_to_its_route_and_carries_a_share_link(executor):
    await _seen_shortlist(executor)
    result = await executor.execute(
        "present_shortlist",
        {
            "title": "10 月两个可选团期",
            "departure_ids": [FIRST, SECOND],
            "note": "两个团都还能坐下 2 大 2 小。",
        },
    )
    assert not result.is_error
    ui = _ui_payload(result)
    assert ui["component"] == "shortlist"
    payload = ui["payload"]
    assert payload["title"] == "10 月两个可选团期"
    assert payload["note"] == "两个团都还能坐下 2 大 2 小。"
    assert "/s/" in payload["share_url"]
    # The ids the model wrote are gone; the records behind them are what renders.
    assert "departure_ids" not in payload

    first, second = payload["items"]
    assert [item["departure"]["product_id"] for item in payload["items"]] == [FIRST, SECOND]
    assert first["route"]["product_id"] == second["route"]["product_id"] == ROUTE
    assert first["route"]["title"] == "伊犁·喀拉峻草原深度 10 日"
    assert first["departure"]["price"] > 0
    # The route is joined as the catalog record, not the details read it came from.
    assert "variants" not in first["route"] and "long_description" not in first["route"]


async def test_a_route_never_searched_is_fetched_for_the_card(executor):
    # Opening a 团期 by id puts the departure in provenance and not its 线路.
    await executor.execute("get_product_details", {"product_id": "DP-1021-20261017"})
    result = await executor.execute(
        "present_shortlist",
        {"title": "客人指定的团期", "departure_ids": ["DP-1021-20261017"]},
    )
    assert not result.is_error
    item = _ui_payload(result)["payload"]["items"][0]
    assert item["route"]["product_id"] == "RT-1021"
    assert item["route"]["title"] == "伊犁北疆环线 8 日纯玩小团"


async def test_an_unseen_departure_is_dropped_and_named_to_the_model(executor):
    await _seen_shortlist(executor)
    result = await executor.execute(
        "present_shortlist",
        {"title": "可选团期", "departure_ids": [FIRST, UNSEEN]},
    )
    assert not result.is_error
    assert [
        item["departure"]["product_id"] for item in _ui_payload(result)["payload"]["items"]
    ] == [FIRST]
    assert UNSEEN in result.result_text


async def test_a_shortlist_of_only_unseen_departures_is_refused(executor):
    result = await executor.execute(
        "present_shortlist", {"title": "凭空的清单", "departure_ids": [UNSEEN]}
    )
    assert result.is_error
    assert not [event for event in result.events if event.type == "ui"]
    assert "本次会话" in result.result_text


async def test_create_share_link_records_the_token_behind_it(backend):
    url = await backend.create_share_link("s-1", "demo-user", [FIRST, SECOND])
    token = url.rsplit("/s/", 1)[1]
    assert url.endswith(f"/s/{token}") and token
    shared = backend.share_record(token)
    assert shared is not None
    assert (shared.session_id, shared.advisor_id) == ("s-1", "demo-user")
    assert shared.departure_ids == [FIRST, SECOND]
    assert backend.share_record("not-a-token") is None


async def test_the_customers_choice_reaches_the_advisors_conversation(main, client, shopper):
    headers = shopper()
    session_id = headers[SESSION_HEADER]
    url = await main.backend.create_share_link(session_id, "demo-user", [FIRST, SECOND])
    token = url.rsplit("/s/", 1)[1]

    response = client.post(f"/api/share/{token}/choose", json={"departure_id": SECOND})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    events = main.host.sessions.require(session_id).pending_app_events
    assert events == [f"客人已在分享页选定团期 {SECOND}（分享 {token}）"]

    assert client.post("/api/share/nope/choose", json={"departure_id": SECOND}).status_code == 404
    assert (
        client.post(f"/api/share/{token}/choose", json={"departure_id": UNSEEN}).status_code == 404
    )


async def test_a_choice_falls_back_to_the_advisors_live_sessions(main, client, shopper):
    """The conversation that sent the link can be over before the customer taps."""
    advisor = "advisor-of-the-share-fallback"
    gone = shopper(user_id=advisor)[SESSION_HEADER]
    url = await main.backend.create_share_link(gone, advisor, [FIRST])
    token = url.rsplit("/s/", 1)[1]
    main.host.sessions.delete(gone)

    assert (
        client.post(f"/api/share/{token}/choose", json={"departure_id": FIRST}).status_code == 410
    )

    live = shopper(user_id=advisor)[SESSION_HEADER]
    assert (
        client.post(f"/api/share/{token}/choose", json={"departure_id": FIRST}).status_code == 200
    )
    assert main.host.sessions.require(live).pending_app_events == [
        f"客人已在分享页选定团期 {FIRST}（分享 {token}）"
    ]


async def test_the_streaming_prefix_renders_seen_departures_without_a_link(executor, state):
    extension = build_shortlist_extension()
    assert "present_shortlist" in partial_ui_tool_names({}, [extension])

    # Nothing has provenance yet, so there is no frame to render.
    assert (
        enrich_partial(extension, {"title": "10 月团期", "departure_ids": [FIRST]}, state) is None
    )

    await _seen_shortlist(executor)
    enriched = enrich_partial(
        extension,
        {"title": "10 月团期", "departure_ids": [FIRST, UNSEEN], "note": "还在写"},
        state,
    )
    assert enriched is not None
    component, payload, _signature = enriched
    assert component == "shortlist"
    assert payload["title"] == "10 月团期"
    assert payload["note"] == "还在写"
    assert [item["departure"]["product_id"] for item in payload["items"]] == [FIRST]
    assert payload["items"][0]["route"]["product_id"] == ROUTE
    assert "share_url" not in payload
