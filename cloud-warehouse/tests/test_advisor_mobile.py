"""Mobile advisor boundaries use fictional ACME requirements and references."""

from cloud_warehouse import advisor_stages, trip_brief


def test_text_suggestions_keep_requirements_and_check_stage_and_references():
    brief = trip_brief.TripBrief()
    suggestions = [
        "给 02-03 这团询价",
        "2 大 2 小，儿童 8 岁和 11 岁",
        "看 WP-unseen 线路",
        "发给客人",
        "立即占位",
        {"action": "quote"},
    ]
    assert advisor_stages.public_suggestions(brief, suggestions, {}) == [
        "2 大 2 小，儿童 8 岁和 11 岁"
    ]
    assert advisor_stages.public_suggestions(
        brief, ["调整 WP-acme 的天数", "调整 WD-missing 的天数"], {"WP-acme": object()}
    ) == ["调整 WP-acme 的天数"]


def test_model_preserves_advisor_fields_and_reports_partial_conflicts(database, tenant):
    from uuid import UUID

    from cloud_warehouse import conversations

    runtime = database[1]
    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    saved = trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(
            expected_version=0, fields={"rooms": {"doubles": 2, "child_bed": False}}
        ),
    )
    work = conversations.begin(
        runtime, tenant.buyer, identifier, "ACME-conflict", "四个人去ACME海湾"
    )
    result = trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(
            expected_version=saved["version"],
            fields={
                "rooms": {
                    "value": {"doubles": 2, "child_bed": True},
                    "source": "inferred",
                    "hint": "四人两间双人房，待确认",
                },
                "party_total": {"value": 4, "source": "said", "evidence": "四个人"},
            },
        ),
        model=True,
    )
    assert result["body"]["rooms"]["value"]["child_bed"] is False
    assert result["body"]["rooms"]["source"] == "advisor"
    assert result["body"]["party_total"]["value"] == 4
    assert result["conflicts"][0]["field"] == "rooms"
    assert result["conflicts"][0]["proposed"]["child_bed"] is True
    conversations.interrupt(runtime, tenant.buyer, work)
    changed = trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(
            expected_version=result["version"], fields={"rooms": result["conflicts"][0]["proposed"]}
        ),
    )
    assert changed["body"]["rooms"]["value"]["child_bed"] is True
    assert changed["body"]["rooms"]["source"] == "advisor"


async def test_examples_rank_without_becoming_required_countries(database, tenant):
    import json

    import pytest

    from cloud_warehouse.advisor import WarehouseAdvisorBackend
    from cloud_warehouse.advisor_actions import search_parameters
    from cloud_warehouse.catalog import synchronize
    from cloud_warehouse.integrations import CatalogBatch
    from shopping_agent import NotOffered, ShoppingSessionContext

    from .test_sync import Connector

    _, runtime = database
    await synchronize(
        runtime,
        tenant.worker,
        tenant.connection_id,
        Connector(
            CatalogBatch(
                [
                    {
                        "routeId": 1,
                        "routeCode": "ACME-ONE",
                        "routeName": "ACME 西班牙葡萄牙",
                        "days": 12,
                    },
                    {"routeId": 2, "routeCode": "ACME-TWO", "routeName": "ACME 意大利", "days": 12},
                ],
                [],
            )
        ),
    )
    brief = trip_brief.TripBrief(
        destinations={"value": ["欧洲"]},
        destination_examples={"value": ["西班牙", "葡萄牙", "法国", "德国"]},
    )
    query, filters = search_parameters(brief)
    assert query == "欧洲"
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    ctx = ShoppingSessionContext(session_id="http", user_id=str(tenant.buyer.user_id))
    first = backend.catalog_page(ctx, query=query, filters=filters, limit=1)
    assert first["items"][0].title == "ACME 西班牙葡萄牙"
    reasons = json.loads(first["items"][0].attributes["match_reasons"])
    assert [r["verdict"] for r in reasons] == ["ok", "ok", "unknown", "unknown"]
    second = backend.catalog_page(
        ctx, query=query, filters=filters, limit=1, after=first["next_cursor"]
    )
    assert second["items"][0].title == "ACME 意大利"
    brief.destination_examples.value = ["意大利"]
    _, changed = search_parameters(brief)
    with pytest.raises(NotOffered):
        backend.catalog_page(ctx, query=query, filters=changed, after=first["next_cursor"])


async def test_departures_date_keyset_inside_first_and_scope_bound(database, tenant):
    from datetime import timedelta

    import pytest

    from cloud_warehouse.advisor import WarehouseAdvisorBackend
    from cloud_warehouse.catalog import synchronize
    from cloud_warehouse.integrations import CatalogBatch
    from shopping_agent import NotOffered, ShoppingSessionContext

    from .test_quotes import FUTURE
    from .test_sync import Connector

    _, runtime = database
    await synchronize(
        runtime,
        tenant.worker,
        tenant.connection_id,
        Connector(
            CatalogBatch(
                [{"routeId": 1, "routeCode": "ACME-R", "routeName": "ACME 环线", "days": 2}],
                [
                    {
                        "periodId": n + 1,
                        "routeId": 1,
                        "periodCode": f"ACME-D{n}",
                        "departDate": str(FUTURE + timedelta(days=n)),
                        "returnDate": str(FUTURE + timedelta(days=n + 1)),
                        "availableSeats": 8,
                    }
                    for n in range(30)
                ],
            )
        ),
    )
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    ctx = ShoppingSessionContext(session_id="http", user_id=str(tenant.buyer.user_id))
    route = backend.catalog_page(ctx)["items"][0].product_id
    args = dict(
        start=FUTURE + timedelta(days=10),
        end=FUTURE + timedelta(days=12),
        include_out_of_window=True,
        party_total=4,
        limit=3,
    )
    first = backend.departures_page(ctx, route, **args)
    assert first["out_of_window_total"] == 27
    assert all(p.attributes["capacity_match"] == "ok" for p in first["items"])
    page = first
    items = page["items"][:]
    while page["next_cursor"]:
        page = backend.departures_page(ctx, route, after=page["next_cursor"], **args)
        assert page["out_of_window_total"] == 27
        items.extend(page["items"])
    assert len(items) == len({p.product_id for p in items}) == 30
    expected = [10, 11, 12] + list(range(10)) + list(range(13, 30))
    assert [p.attributes["depart_date"] for p in items] == [
        str(FUTURE + timedelta(days=n)) for n in expected
    ]
    for change in (
        {"end": FUTURE + timedelta(days=13)},
        {"party_total": 3},
        {"include_out_of_window": False},
    ):
        with pytest.raises(NotOffered):
            backend.departures_page(ctx, route, after=first["next_cursor"], **{**args, **change})


def test_conversation_search_uses_owned_brief_titles(database, tenant):
    from uuid import UUID

    from cloud_warehouse import conversations

    _, runtime = database
    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    trip_brief.patch(
        runtime,
        tenant.buyer,
        identifier,
        trip_brief.BriefPatch(
            expected_version=0, fields={"destinations": ["ACME海湾"], "party_total": 4}
        ),
    )
    page = conversations.list_page(runtime, tenant.buyer, query="ACME海湾")
    assert [str(r["id"]) for r in page["items"]] == [str(identifier)]
    assert page["items"][0]["stage"] == "need"
    assert conversations.list_page(runtime, tenant.buyer, query="ACME雪山")["items"] == []
    import pytest

    from cloud_warehouse.persistence import Forbidden

    with pytest.raises(Forbidden):
        conversations.list_page(runtime, tenant.supplier, query="ACME海湾")
