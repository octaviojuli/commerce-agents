"""Destination intent, recall and evidence use fictional ACME products only."""

import json
import re
from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from cloud_warehouse import (
    advisor_actions,
    conversations,
    destination_catalog,
    destinations,
    outbox,
    search,
    travel_requirements,
    travel_search,
    trip_brief,
)
from cloud_warehouse.advisor import WarehouseAdvisorBackend
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.integrations import CatalogBatch
from cloud_warehouse.persistence import transaction

from .test_advisor import session
from .test_quotes import FUTURE
from .test_sync import Connector

FOUR = {"德国", "法国", "意大利", "瑞士"}


@pytest.mark.parametrize(
    "message",
    [
        "有四位客人，想12月份去欧洲，去西欧，德法意瑞多国，看看有什么线路可以报名",
        "12月想看德法瑞意线路",
        "欧洲德国法国意大利瑞士四国",
        "西欧，德国、法国、意大利、瑞士都去",
        "德意法瑞",
        "法瑞意德",
        "去Germany法国意大利Switzerland旅游",
    ],
)
def test_explicit_countries_remain_required(message):
    values = travel_requirements.normalize(
        {"destinations": {}}, message, trip_brief.TripBrief(), date(2026, 9, 27)
    )
    assert set(values["destinations"]["value"]) == FOUR
    assert values["destination_examples"]["value"] == []


@pytest.mark.parametrize(
    "message",
    [
        "欧洲，比如德国法国意大利瑞士",
        "欧洲，德法意瑞，不一定四国都去",
        "欧洲，德国法国意大利瑞士之类",
        "西欧，例如德国、法国、意大利、瑞士等国家",
    ],
)
def test_explicit_examples_do_not_become_hard_requirements(message):
    values = travel_requirements.normalize(
        {"destinations": {}}, message, trip_brief.TripBrief(), date(2026, 9, 27)
    )
    assert values["destinations"]["value"] in (["欧洲"], ["西欧"])
    assert set(values["destination_examples"]["value"]) == FOUR


@pytest.mark.parametrize(
    "message,required,excluded",
    [
        ("西欧，不去德国", ["西欧"], ["德国"]),
        ("欧洲，避开法国和德国", ["欧洲"], ["法国", "德国"]),
        ("不去德国，但瑞士必须去", ["瑞士"], ["德国"]),
        ("法国不去，想去瑞士", ["瑞士"], ["法国"]),
    ],
)
def test_exclusions_do_not_turn_into_requirements(message, required, excluded):
    values = travel_requirements.normalize(
        {"destinations": {}}, message, trip_brief.TripBrief(), date(2026, 9, 27)
    )
    assert values["destinations"]["value"] == required
    assert values["excluded_destinations"]["value"] == excluded


@pytest.mark.parametrize(
    "message,today,start,end,source",
    [
        ("12月份去欧洲", date(2026, 9, 27), "2026-12-01", "2026-12-31", "inferred"),
        ("一月份去欧洲", date(2026, 12, 29), "2027-01-01", "2027-01-31", "inferred"),
        ("2028年2月去欧洲", date(2026, 9, 27), "2028-02-01", "2028-02-29", "said"),
    ],
)
def test_months_preserve_year_provenance(message, today, start, end, source):
    values = travel_requirements.normalize(
        {
            "window": {
                "value": {"start": "2020-01-01", "end": "2020-01-30"},
                "source": "said",
                "evidence": message,
            }
        },
        message,
        trip_brief.TripBrief(),
        today,
    )
    assert values["window"]["value"] == {"start": start, "end": end}
    assert values["window"]["source"] == source


def test_qualified_dates_are_not_expanded():
    value = {
        "value": {"start": "2026-12-11", "end": "2026-12-20"},
        "source": "inferred",
        "hint": "中旬待确认",
    }
    assert (
        travel_requirements.normalize(
            {"window": value}, "12月中旬", trip_brief.TripBrief(), date(2026, 9, 27)
        )["window"]["value"]
        == value["value"]
    )


def test_aliases_are_and_countries_or_names_and_escape_unknown_terms():
    patterns = travel_search.parameters("西欧 德法意瑞")["travel_patterns"]
    assert len(patterns) == 4
    assert all(re.search(p, "acme 卢德比法意瑞") for p in patterns)
    assert not all(re.search(p, "acme 荷比意瑞") for p in patterns)
    assert not all(re.search(p, "acme 西欧") for p in patterns)
    assert destinations.names("Thailand and island") == ["泰国"]
    assert travel_search.parameters("ACME .* ")["travel_patterns"][-1] == r"\.\*"


def test_metadata_keeps_evidence_and_exclusions():
    row = {
        "version": 1,
        "display_version": 3,
        "content_version": 1,
        "published_document_id": None,
        "effective_name": "ACME 德法意瑞",
        "source": {"countries": ["德国"], "private_note": "英国"},
        "destination_document": {"days": [{"cities": ["巴黎"], "countries": ["意大利"]}]},
        "approved_tags": [
            {
                "category": "destination",
                "label": "瑞士",
                "state": "confirmed",
                "search_enabled": True,
            }
        ],
    }
    facts = destination_catalog.facts(row)
    countries = {c["code"]: c for c in facts["countries"]}
    assert {k: v["origin"] for k, v in countries.items()} == {
        "DE": "supplier_field",
        "FR": "published_itinerary",
        "IT": "published_itinerary",
        "CH": "merchant_tag",
    }
    row["approved_tags"].append({"category": "destination", "label": "法国", "state": "excluded"})
    assert "FR" not in {c["code"] for c in destination_catalog.facts(row)["countries"]}
    assert destination_catalog.facts(row)["cities"] == []


def test_normalizer_cannot_launder_false_evidence_or_advisor_authority(database, tenant):
    _, runtime = database
    cid = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    work = conversations.begin(runtime, tenant.buyer, cid, "ACME-geo", "12月去西欧德法意瑞")
    for source, evidence in [("advisor", ""), ("said", "未在原话出现")]:
        with pytest.raises(ValueError):
            trip_brief.patch(
                runtime,
                tenant.buyer,
                cid,
                trip_brief.BriefPatch(
                    expected_version=0,
                    fields={
                        "destinations": {"value": ["西欧"], "source": source, "evidence": evidence}
                    },
                ),
                model=True,
            )
    conversations.interrupt(runtime, tenant.buyer, work)


async def test_full_recall_pagination_exclusions_date_and_stale_projection(database, tenant):
    admin, runtime = database
    titles = [
        "ACME 德法意瑞",
        "ACME 德法瑞意",
        "ACME 德国法国意大利瑞士",
        "ACME 卢德比法意瑞",
        "ACME 西欧",
        "ACME 西葡",
        "ACME 德法意瑞旧日期",
    ]
    await synchronize(
        runtime,
        tenant.worker,
        tenant.connection_id,
        Connector(
            CatalogBatch(
                [
                    {"routeId": i, "routeCode": f"ACME-{i}", "routeName": title, "days": 12}
                    for i, title in enumerate(titles, 1)
                ],
                [
                    {
                        "periodId": i,
                        "routeId": i,
                        "periodCode": f"ACME-D{i}",
                        "departDate": str(FUTURE if i < 7 else FUTURE + timedelta(days=40)),
                        "returnDate": str(FUTURE + timedelta(days=52)),
                        "availableSeats": 8,
                    }
                    for i in range(1, 8)
                ],
            )
        ),
    )
    brief = trip_brief.TripBrief(
        destinations={"value": ["西欧", *sorted(FOUR)]},
        window={"value": {"start": FUTURE, "end": FUTURE}},
        party_total={"value": 4},
    )
    query, filters = advisor_actions.search_parameters(brief)
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    ctx = session(tenant.buyer)
    outbox.run_once(runtime, tenant.worker)
    first = backend.catalog_page(ctx, query=query, filters=filters, limit=3)
    second = backend.catalog_page(
        ctx, query=query, filters=filters, limit=3, after=first["next_cursor"]
    )
    assert len(first["items"]) == 3 and len(second["items"]) == 1 and not second["next_cursor"]
    assert {x.title for x in [*first["items"], *second["items"]]} == set(titles[:4])
    for item in first["items"]:
        reasons = json.loads(item.attributes["match_reasons"])
        assert reasons[0]["verdict"] == "unknown"
        assert "待核对" in reasons[0]["text"] and "待核实" in reasons[1]["text"]
    with transaction(runtime, tenant.buyer) as conn:
        ids = [UUID(x.product_id[3:]) for x in first["items"]]
        assert set(search.destination_facts(conn, ids)) == set(ids)
    brief.destinations.value = ["欧洲"]
    brief.excluded_destinations.value = ["德国"]
    query, filters = advisor_actions.search_parameters(brief)
    found = backend.catalog_page(ctx, query=query, filters=filters)
    assert {x.title for x in found["items"]} == set(titles[4:6])
    # A current source version invalidates both text and destination evidence.
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE supplier_product SET name='ACME 日本',version=version+1 WHERE code='ACME-1' AND connection_id=:id"
            ),
            {"id": tenant.connection_id},
        )
    result = backend.catalog_page(ctx, query="德国 法国 意大利 瑞士", filters=filters)
    assert not result["items"]  # Explicit exclusion still applies, even after a fallback.
    brief.excluded_destinations.value = []
    brief.destinations.value = sorted(FOUR)
    query, filters = advisor_actions.search_parameters(brief)
    assert len(backend.catalog_page(ctx, query=query, filters=filters)["items"]) == 3
    outbox.rebuild(runtime, tenant.worker)
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE distribution_grant SET active=false WHERE connection_id=:id"),
            {"id": tenant.connection_id},
        )
    with transaction(runtime, tenant.buyer) as conn:
        assert search.destination_facts(conn, ids) == {}


@pytest.mark.parametrize("scoped", [False, True])
async def test_only_global_reviewed_itinerary_supplies_country_evidence(
    database, tenant, tmp_path, scoped
):
    from cloud_warehouse import documents
    from shopping_agent import SearchFilters

    from .test_documents import parsed, proposal, source
    from .test_quotes import commit

    _, runtime = database
    store, product, asset, _ = await source.__wrapped__(database, tenant, tmp_path)

    def parser(work, body):
        doc = parsed(work, body)
        doc.days[0].cities = ["巴黎"]
        if scoped:
            doc.applicability.start = FUTURE
        return doc

    documents.run_once(runtime, tenant.worker, store, parser)
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    args = dict(query="法国", filters=SearchFilters(attributes={"ranked": "true"}))
    outbox.run_once(runtime, tenant.worker)
    assert backend.catalog_page(session(tenant.buyer), **args)["items"] == []
    commit(runtime, tenant.supplier, proposal(runtime, tenant, asset))
    for rebuild in (False, True):
        if rebuild:
            outbox.run_once(runtime, tenant.worker)
        items = backend.catalog_page(session(tenant.buyer), **args)["items"]
        if scoped:
            assert items == []
        else:
            assert len(items) == 1
            assert json.loads(items[0].attributes["match_reasons"])[0]["verdict"] == "ok"
            assert (
                json.loads(items[0].attributes["destination_facts"])["countries"][0]["origin"]
                == "published_itinerary"
            )


async def test_gateway_is_not_visited_country(database, tenant):
    from shopping_agent import SearchFilters

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
                        "routeCode": "ACME-GATE",
                        "routeName": "ACME 日本",
                        "days": 8,
                        "departCityName": "巴黎",
                    }
                ],
                [],
            )
        ),
    )
    backend = WarehouseAdvisorBackend(runtime, tenant.buyer)
    for rebuilt in (False, True):
        if rebuilt:
            outbox.run_once(runtime, tenant.worker)
        assert not backend.catalog_page(
            session(tenant.buyer),
            query="法国",
            filters=SearchFilters(attributes={"ranked": "true"}),
        )["items"]
