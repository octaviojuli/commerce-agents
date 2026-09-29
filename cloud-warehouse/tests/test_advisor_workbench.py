"""Scripted ACME turns exercise real persistence, ranking, quote and final actions."""

import json
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from cloud_warehouse import advisor_actions as actions
from cloud_warehouse import advisor_stages, conversations
from cloud_warehouse import trip_brief as tb
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.catalog import list_departures
from cloud_warehouse.persistence import transaction
from commerce_common.testing import FakeClient, text_block, text_message, tool_use_message
from shopping_agent import ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState
from tour.api.warehouse_agent import build_agent, configuration

from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer


def said(value, evidence):
    return {"value": value, "source": "said", "evidence": evidence}


def test_stages_and_suggestion_authority():
    brief = tb.TripBrief()
    assert advisor_stages.derive(brief)["stage"] == "need"
    assert (
        advisor_stages.public_suggestions(
            brief,
            [
                "直接询价",
                {"action": "quote"},
                {"action": "update_trip_brief", "product_id": "unseen"},
            ],
            {},
        )
        == []
    )
    assert len(configuration().domain_search_notes.split(". ")) - 1 <= 10


async def test_clean_suggestions_end_after_one_summary(database, tenant):
    _, runtime = database
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    cid = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    work = conversations.begin(runtime, tenant.buyer, cid, "summary", "找 ACME 线路")
    response = tool_use_message("present_suggestions", {"suggestions": ["去 ACME 海湾"]})
    response.content.insert(0, text_block("请提供出发时间。"))
    fake = FakeClient([response, text_message("请提供出发时间，重复总结不应出现。")])
    events = [
        e
        async for e in build_agent(backend, client=fake).stream_turn(
            work["messages"],
            ShoppingSessionContext(session_id=str(cid), user_id=str(tenant.buyer.user_id)),
            ShoppingSessionState(),
        )
    ]
    assert len(fake.calls) == 1
    assert "".join(e.data["text"] for e in events if e.type == "text_delta") == "请提供出发时间。"
    assert events[-1].type == "turn_complete"


async def test_scripted_brief_to_quote_and_offline_invariants(database, tenant, managed_offer):
    admin, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    departure = list_departures(runtime, tenant.buyer)[0]
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_product SET name='ACME 西班牙葡萄牙12日',days=12,gateway='上海' WHERE id=:id"
            ),
            {"id": departure["product_id"]},
        )
    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    session = ShoppingSessionContext(session_id=str(identifier), user_id=str(tenant.buyer.user_id))
    state = ShoppingSessionState()
    first = "一家4口，春节前后，西葡，12天左右，上海出发，不进购物店"
    fields = {
        "party_total": said(4, "一家4口"),
        "destinations": said(["西班牙", "葡萄牙"], "西葡"),
        "days": {
            "value": {"min": 11, "max": 13},
            "source": "inferred",
            "hint": "12天左右按11–13天",
        },
        "depart_city": said("上海", "上海出发"),
        "window": {
            "value": {"start": str(FUTURE), "end": str(FUTURE + timedelta(days=3))},
            "source": "inferred",
            "hint": "ACME脚本测试窗口，需顾问确认",
        },
        "preferences": said([{"key": "no_shopping", "label": "不进购物店"}], "不进购物店"),
    }
    route_id, dep_id = "WP-" + str(departure["product_id"]), "WD-" + str(departure["id"])
    work = conversations.begin(runtime, tenant.buyer, identifier, "first", first)
    fake = FakeClient(
        [
            tool_use_message("update_trip_brief", {"expected_version": 0, "fields": fields}),
            tool_use_message("search_routes", {}),
            tool_use_message("present_warehouse_departures", {"product_id": route_id}),
            tool_use_message("present_warehouse_quote", {"departure_id": dep_id}),
            text_message("已检索，日期为推断待确认。请补充成人儿童构成。"),
        ]
    )
    agent = build_agent(backend, client=fake)
    events = [e async for e in agent.stream_turn(work["messages"], session, state)]
    assert not [e for e in events if e.type == "error"]
    assert [e for e in events if e.type == "ui" and e.data["component"] == "warehouse_routes"]
    assert not [e for e in events if e.type == "ui" and e.data["component"] == "warehouse_quote"]
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [e.model_dump(mode="json") for e in events],
    )
    saved = tb.get(runtime, tenant.buyer, identifier)
    assert saved["body"]["adults"]["value"] is None
    assert saved["body"]["window"]["source"] == "inferred"
    assert saved["body"]["route_id"] is None
    assert not [
        e for e in events if e.type == "ui" and e.data["component"] == "warehouse_departures"
    ]
    work = conversations.begin(runtime, tenant.buyer, identifier, "choose", "选择第一条，看看团期")
    fake = FakeClient(
        [
            tool_use_message("present_warehouse_departures", {"product_id": route_id}),
            text_message("已查看您选择的线路团期，请选择一个团期。"),
        ]
    )
    chosen = [
        e
        async for e in build_agent(backend, client=fake).stream_turn(
            work["messages"], session, state
        )
    ]
    assert [e for e in chosen if e.type == "ui" and e.data["component"] == "warehouse_departures"]
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [e.model_dump(mode="json") for e in chosen],
    )
    saved = tb.get(runtime, tenant.buyer, identifier)
    second = "第一期，2大2小，8岁和11岁，两间双人房，请询价"
    work = conversations.begin(runtime, tenant.buyer, identifier, "second", second)
    fields = {
        "adults": said(2, "2大"),
        "children": said(2, "2小"),
        "child_ages": said([8, 11], "8岁和11岁"),
        "rooms": {
            "value": {"doubles": 2, "child_bed": True, "raw": "两间双人房"},
            "source": "inferred",
            "hint": "四人入住两间双人房，推断儿童占床，待确认",
            "evidence": "两间双人房",
        },
    }
    fake = FakeClient(
        [
            tool_use_message(
                "update_trip_brief", {"expected_version": saved["version"], "fields": fields}
            ),
            tool_use_message("present_warehouse_quote", {"departure_id": dep_id}),
            text_message("报价已生成，儿童占床为推断，请确认。"),
        ]
    )
    events = [
        e
        async for e in build_agent(backend, client=fake).stream_turn(
            work["messages"], session, state
        )
    ]
    quoted = next(
        e.data["payload"]
        for e in events
        if e.type == "ui" and e.data["component"] == "warehouse_quote"
    )
    assert quoted["party"]["adults"] == quoted["party"]["children"] == 2
    assert quoted["party"]["rooms"]["doubles"] == 2
    assert quoted["market_total"] == "360.00" and quoted["complete"]
    assert "rooms" in quoted["inferred"]
    saved = tb.get(runtime, tenant.buyer, identifier)
    assert "WO-" + str(quoted["offer_id"]) in state.seen_products
    repeated = await actions.perform(
        backend,
        session,
        state,
        actions.Action(
            action="quote",
            expected_version=saved["version"],
            product_id=dep_id,
            offer_id=quoted["offer_id"],
        ),
    )
    assert repeated["payload"]["complete"]
    assert repeated["payload"]["offer_id"] == quoted["offer_id"]
    conversations.finish(
        runtime,
        tenant.buyer,
        work,
        state.model_dump(mode="json"),
        work["messages"],
        [e.model_dump(mode="json") for e in events],
    )

    def stocks():
        with admin.connect() as conn:
            return [
                list(
                    conn.execute(
                        text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id")
                    ).scalars()
                )
                for table in ("inventory_pool", "inventory_movement")
            ]

    before = stocks()
    for name in ("share_quote", "customer_confirmed", "offline_hold_recorded"):
        saved = tb.get(runtime, tenant.buyer, identifier)
        result = await actions.perform(
            backend,
            session,
            state,
            actions.Action(
                action=name, expected_version=saved["version"], note="ACME计调已收到需求，仅记录"
            ),
        )
        assert stocks() == before
    assert result["brief"]["body"]["offline_status"] == "offline_hold_recorded"
    assert result["payload"]["reservation_created"] is False
    saved = tb.patch(
        runtime,
        tenant.buyer,
        identifier,
        tb.BriefPatch(
            expected_version=result["brief"]["version"],
            fields={"rooms": {"doubles": 1, "twins": 1, "child_bed": True}},
        ),
    )
    assert saved["quote_stale"] and saved["stage"] == "departure"
    assert saved["body"]["share_token"] is None
    with pytest.raises(ValueError):
        await actions.perform(
            backend,
            session,
            state,
            actions.Action(action="share_quote", expected_version=saved["version"]),
        )


async def test_ranking_cursor_soft_city_and_capacity(database, tenant, managed_offer):
    admin, runtime = database
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = ShoppingSessionContext(session_id="http", user_id=str(tenant.buyer.user_id))
    brief = tb.TripBrief(
        destinations={"value": ["ACME"]},
        window={"value": {"start": FUTURE, "end": FUTURE}},
        days={"value": {"min": 2, "max": 4}},
        depart_city={"value": "上海"},
        preferences={"value": [{"key": "no_shopping", "label": "无购物"}]},
    )
    query, filters = actions.search_parameters(brief)
    result = backend.catalog_page(context, query=query, filters=filters, limit=1)
    route = result["items"][0]
    reasons = json.loads(route.attributes["match_reasons"])
    assert next(r for r in reasons if r["criterion"] == "depart_city")["verdict"] == "conflict"
    assert next(r for r in reasons if r["criterion"] == "no_shopping")["verdict"] == "unknown"
    page = backend.departures_page(
        context, route.product_id, start=FUTURE, end=FUTURE, party_total=11
    )
    assert page["items"][0].attributes["capacity_match"] == "short"
    page = backend.departures_page(
        context,
        route.product_id,
        start=FUTURE + timedelta(days=1),
        party_total=4,
        include_out_of_window=True,
    )
    assert page["items"][0].attributes["capacity_match"] == "out_of_window"
    with transaction(runtime, tenant.buyer) as conn:
        from cloud_warehouse.advisor_matching import query_sql

        sql, params = query_sql(query, filters.attributes, None)
        # Force index alternatives, as the production capacity plan probe does.
        conn.execute(text("SET LOCAL enable_seqscan=off"))
        plan = conn.execute(
            text("EXPLAIN (FORMAT JSON) " + str(sql)), {**params, "limit": 2}
        ).scalar()

        def walks(node):
            yield node
            for child in node.get("Plans", []):
                yield from walks(child)

        assert not [
            node
            for node in walks(plan[0]["Plan"])
            if node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name")
            in {"supplier_product", "departure", "document_publication", "product_search"}
        ]


@pytest.mark.parametrize("shopping_kind", ["none", "list", "sight", "block"])
async def test_only_reviewed_publications_prove_shopping(database, tenant, tmp_path, shopping_kind):
    from cloud_warehouse import documents
    from cloud_warehouse.route_doc import ItineraryBlock, ShoppingStop, Sight

    from .test_documents import parsed, proposal, source

    _, runtime = database
    store, product, asset, _ = await source.__wrapped__(database, tenant, tmp_path)

    def parser(work, body):
        doc = parsed(work, body)
        if shopping_kind == "list":
            doc.shopping = [ShoppingStop(name="ACME 购物店")]
        elif shopping_kind == "sight":
            doc.days[0].sights = [Sight(name="ACME 购物店", kind="购物")]
        elif shopping_kind == "block":
            doc.days[0].blocks = [
                ItineraryBlock(block_id="acme-shop", type="shopping", title="ACME 购物")
            ]
        return doc

    documents.run_once(runtime, tenant.worker, store, parser)
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = ShoppingSessionContext(session_id="http", user_id=str(tenant.buyer.user_id))
    from shopping_agent import SearchFilters

    filters = SearchFilters(attributes={"no_shopping": "true"})

    def verdict():
        page = backend.catalog_page(context, query="ACME", filters=filters)
        return json.loads(page["items"][0].attributes["match_reasons"])[0]["verdict"]

    assert verdict() == "unknown"
    commit(runtime, tenant.supplier, proposal(runtime, tenant, asset))
    assert verdict() == ("ok" if shopping_kind == "none" else "conflict")


async def test_ranked_keyset_pages_keep_global_order_and_survive_deleted_anchor(database, tenant):
    from cloud_warehouse.catalog import synchronize
    from cloud_warehouse.integrations import CatalogBatch
    from shopping_agent import SearchFilters

    from .test_sync import Connector

    admin, runtime = database
    routes = [
        {
            "routeId": n,
            "routeCode": f"ACME-R{n}",
            "routeName": f"ACME 环线{n}",
            "days": 12,
            "departCityName": "上海" if n % 2 else "北京",
        }
        for n in range(1, 8)
    ]
    deps = [
        {
            "periodId": n,
            "routeId": n,
            "periodCode": f"ACME-D{n}",
            "departDate": str(FUTURE + timedelta(days=n)),
            "returnDate": str(FUTURE + timedelta(days=n + 11)),
            "availableSeats": 3,
        }
        for n in range(1, 8)
    ]
    await synchronize(
        runtime, tenant.worker, tenant.connection_id, Connector(CatalogBatch(routes, deps))
    )
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    context = ShoppingSessionContext(session_id="http", user_id=str(tenant.buyer.user_id))
    filters = SearchFilters(
        attributes={
            "ranked": "true",
            "depart_city": "上海",
            "days_min": "11",
            "days_max": "13",
            "depart_from": str(FUTURE),
            "depart_to": str(FUTURE + timedelta(days=9)),
        }
    )
    full = backend.catalog_page(context, query="ACME", filters=filters)["items"]
    assert [p.title for p in full] == [f"ACME 环线{n}" for n in [1, 3, 5, 7, 2, 4, 6]]
    page = backend.catalog_page(context, query="ACME", filters=filters, limit=2)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_product SET status='paused' WHERE id=:id"),
            {"id": UUID(page["items"][-1].product_id[3:])},
        )
    seen = page["items"][:]
    while page["next_cursor"]:
        page = backend.catalog_page(
            context, query="ACME", filters=filters, limit=2, after=page["next_cursor"]
        )
        seen.extend(page["items"])
    assert [p.product_id for p in seen] == [p.product_id for p in full]
    # Stale upstream inventory never becomes a definitive capacity verdict.
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE departure SET availability_expires_at=now()-interval '1 minute' WHERE connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    capacity = backend.departures_page(context, full[0].product_id, party_total=4)["items"][0]
    assert capacity.attributes["capacity_match"] == "unknown"
    assert "available_seats" not in capacity.attributes


def test_http_actions_are_idempotent_compact_private_and_transaction_free(
    database, authentication, tenant, managed_offer, monkeypatch
):
    from uuid import uuid4

    import httpx
    from fastapi.testclient import TestClient

    from cloud_warehouse import auth
    from cloud_warehouse.api import create_app

    admin, runtime = database
    calls = []

    async def upstream(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Offline actions must not call upstream")

    monkeypatch.setattr(httpx.AsyncClient, "request", upstream)
    email = f"{uuid4()}@acme.example"
    auth.create_user(
        admin, email, "ACME-acceptance-password", {tenant.buyer.organization_id: ["advisor"]}
    )
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    from commerce_common.testing import FakeClient, text_message, tool_use_message
    from tour.api.warehouse_agent import build_agent

    def agent_factory(role, actor, buyer):
        return build_agent(
            WarehouseAdvisorBackend(runtime, actor),
            client=FakeClient(
                [tool_use_message("share_quote", {}), text_message("已生成对客链接。")]
            ),
        )

    with TestClient(create_app(runtime, authentication, agent_factory=agent_factory)) as client:
        token = client.post(
            "/v1/auth/login", json={"email": email, "password": "ACME-acceptance-password"}
        ).json()["access_token"]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        cid = client.post("/v1/conversations", json={"role": "advisor"}, headers=headers).json()[
            "id"
        ]
        base = f"/v1/conversations/{cid}"
        initial = client.patch(
            base + "/brief",
            headers=headers,
            json={
                "expected_version": 0,
                "fields": {
                    "destinations": ["ACME"],
                    "window": {"start": str(FUTURE), "end": str(FUTURE)},
                    "adults": 2,
                    "children": 0,
                    "party_total": 2,
                    "rooms": {"doubles": 1},
                },
            },
        )
        assert initial.status_code == 200

        def action(name, **args):
            version = client.get(base + "/brief", headers=headers).json()["version"]
            body = {"action": name, "expected_version": version, "request_id": str(uuid4()), **args}
            response = client.post(base + "/actions", headers=headers, json=body)
            assert response.status_code == 200, response.text
            assert "x-request-id" in response.headers
            return response.json(), body

        found, search_body = action("search_routes")
        route = found["payload"]["items"][0]["product_id"]
        dep, _ = action("departures", product_id=route)
        quote, body = action("quote", product_id=dep["payload"]["items"][0]["product_id"])
        with admin.connect() as conn:
            inventory_before = list(
                conn.execute(
                    text("SELECT row_to_json(i)::text FROM inventory_pool i ORDER BY id")
                ).scalars()
            )
            movements = conn.scalar(text("SELECT count(*) FROM inventory_movement"))
            quotes_before = conn.scalar(text("SELECT count(*) FROM quote_snapshot"))
        replay = client.post(base + "/actions", headers=headers, json=body)
        assert replay.status_code == 200
        assert replay.json()["payload"]["quote_id"] == quote["payload"]["quote_id"]
        assert (
            client.post(
                base + "/actions", headers=headers, json={**body, "note": "different command"}
            ).status_code
            == 409
        )
        share, _ = action("share_quote")
        raw_token = share["payload"]["token"]
        action("customer_confirmed")
        action("offline_hold_recorded", note="ACME 线下备注")
        transcript = client.get(base, headers=headers).json()
        assert all("request_id" not in turn["message"] for turn in transcript["turns"])
        assert all(turn["message"].startswith("▸ ") for turn in transcript["turns"])
        assert raw_token not in json.dumps(transcript)
        assert raw_token not in json.dumps(client.get(base + "/brief", headers=headers).json())
        assert calls == []
        with admin.connect() as conn:
            assert (
                list(
                    conn.execute(
                        text("SELECT row_to_json(i)::text FROM inventory_pool i ORDER BY id")
                    ).scalars()
                )
                == inventory_before
            )
            assert conn.scalar(text("SELECT count(*) FROM inventory_movement")) == movements
            assert conn.scalar(text("SELECT count(*) FROM quote_snapshot")) == quotes_before
        stream = client.post(
            base + "/chat",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"message": "请把当前报价发给客人"},
        )
        streamed = [
            json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")
        ]
        assert any(item.get("component") == "warehouse_share" for item in streamed), stream.text
        shared = next(item for item in streamed if item.get("component") == "warehouse_share")
        live_token = shared["payload"]["token"]
        assert live_token and live_token not in json.dumps(client.get(base, headers=headers).json())
        with admin.connect() as conn:
            saved_messages = conn.scalar(
                text("SELECT messages::text FROM agent_conversation WHERE id=:id"), {"id": cid}
            )
        assert live_token not in saved_messages
        denied = client.post(base + "/actions", json=search_body)
        assert denied.status_code == 401
        client.patch(
            base + "/brief",
            headers=headers,
            json={
                "expected_version": client.get(base + "/brief", headers=headers).json()["version"],
                "fields": {"adults": 3},
            },
        )
        version = client.get(base + "/brief", headers=headers).json()["version"]
        rejected = client.post(
            base + "/actions",
            headers=headers,
            json={
                "action": "quote",
                "expected_version": version,
                "product_id": dep["payload"]["items"][0]["product_id"],
            },
        )
        assert rejected.status_code == 422 and "missing" in rejected.json()
