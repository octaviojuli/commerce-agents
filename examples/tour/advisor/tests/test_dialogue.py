"""Conversation acceptance against real HTTP handlers and disposable advisor persistence."""

import base64
import secrets
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from tour.advisor import db, grounding, interpret, pricing, queries, store, turns
from tour.advisor import need as needs
from tour.advisor.api import Settings, create_app
from tour.advisor.judge import Judge
from tour.advisor.model import Draft, Understanding
from tour.advisor.tests.conftest import database_url
from tour.advisor.tests.test_review import quoted, ready
from tour.advisor.tests.test_story import SLOW, warehouse

TITLE = "ACME 斯里兰卡9天7晚轻奢"


class DialogueModel:
    def __init__(self):
        self.calls = []

    async def call(self, name, data):
        self.calls.append((name, data))
        if name == "draft":
            # An overeager model still must not ask quote questions during route search.
            return Draft(to_customer="收到。有没有孩子同行？", to_advisor="已整理")
        text = data["message"]
        if "三个客人" in text:
            return Understanding.model_validate(
                {
                    "action": "search",
                    # The real model may omit redundant intent tags.
                    "kinds": [],
                    "changes": [
                        {
                            "field": "party",
                            "value": {"adults": 3, "children": []},
                            "evidence": "三个客人",
                        },
                        {
                            "field": "window",
                            "value": {"start": "2026-10-30", "end": "2026-10-30"},
                            "evidence": "10月30日去",
                        },
                        {"field": "days", "value": {"min": 7, "max": 9}, "evidence": "8天左右"},
                    ],
                }
            )
        if "没有孩子" in text:
            return Understanding.model_validate(
                {
                    "action": "departures",
                    "changes": [
                        {"field": "party", "value": {"children": []}, "evidence": "没有孩子"},
                    ],
                }
            )
        if "团期" in text:
            # A date inquiry is not permission to change a stored requirement.
            return Understanding.model_validate(
                {
                    "action": "departures",
                    "changes": [
                        {
                            "field": "window",
                            "value": {"start": "2026-10-30", "end": "2026-11-10"},
                            "evidence": text,
                        },
                    ],
                }
            )
        # A model's suggestion is deliberately overconfident: program authority must win.
        return Understanding.model_validate({"selection": {"route": TITLE}, "kinds": ["decide"]})


@pytest.fixture
def dialogue(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_MATERIAL_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setattr(turns, "today", lambda: date(2026, 9, 29))
    state = {
        "dates": ["2026-10-25", "2026-11-05", "2026-11-06"],
        "published": False,
        "error": False,
        "calls": [],
    }

    def upstream(request):
        path, params = request.url.path, request.url.params
        state["calls"].append((request.method, path, dict(params)))
        if path == "/v1/advisor/products":
            fits = not params.get("start") or any(
                params["start"] <= d <= params["end"] for d in state["dates"]
            )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "product_id": SLOW,
                            "title": state.get("supplier_name", "") + TITLE,
                            "attributes": {
                                "days": "9",
                                "depart_city": "上海",
                                "supplier_id": "ACME-source",
                                "supplier_name": state.get("supplier_name", ""),
                            },
                        }
                    ]
                    if fits
                    else []
                },
            )
        if path.endswith("/document"):
            if state.get("doc_error"):
                return httpx.Response(503, json={"message": "unavailable"})
            if not state["published"]:
                return httpx.Response(404, json={"message": "not published"})
        if path == "/v1/advisor/departures":
            if state["error"]:
                return httpx.Response(503, json={"message": "unavailable"})
            days = [
                d
                for d in state["dates"]
                if not params.get("start") or params["start"] <= d <= params["end"]
            ]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "product_id": "WD-" + d,
                            "attributes": {"depart_date": d, "availability": "available"},
                        }
                        for d in days
                    ]
                },
            )
        return warehouse(request)

    model = DialogueModel()
    app = create_app(
        Settings(
            database_url=database_url(),
            warehouse_url="http://warehouse.test",
            material_dir=tmp_path,
        ),
        model=model,
        transport=httpx.MockTransport(upstream),
        judge=Judge(mode="off"),
    )
    with TestClient(app) as client:
        client.post(
            "/api/login", json={"email": secrets.token_hex(8) + "@acme.example", "password": "x"}
        ).raise_for_status()
        deal = client.post("/api/deals", json={"title": "ACME 多轮验收"}).json()["id"]
        yield client, deal, state, model


def say(client, deal, text, kind="advisor"):
    result = client.post(f"/api/deals/{deal}/turns", json={"text": text, "kind": kind})
    result.raise_for_status()
    return result.json()


def detail(client, deal):
    return client.get(f"/api/deals/{deal}").json()


def value(detail, key):
    return next(f["value"] for f in detail["need"]["fields"] if f["field"] == key)


def first(client, deal):
    return say(
        client, deal, "有三个客人想去斯里兰卡玩，玩8天左右，10月30日去。推荐一条线路。", "customer"
    )


def test_nine_turns_keep_source_requirement_and_selection_separate_without_jev(dialogue):
    client, deal, state, model = dialogue
    result = first(client, deal)
    saved = detail(client, deal)
    assert value(saved, "party") == {
        "total_count": 3,
        "adults": None,
        "children": None,
        "seniors": [],
    }
    assert value(saved, "window")["start"] == value(saved, "window")["end"] == "2026-10-30"
    assert "10月25日" in result["draft"]["text"] and "有没有孩子" not in result["draft"]["text"]
    assert saved["route"] is None
    result = say(client, deal, "出发时间前后放宽10天")
    assert "draft" not in result
    saved = detail(client, deal)
    assert saved["query"] and saved["version"] == 1 and not saved["pending"]
    query_id = saved["query"]["id"]
    for text in (
        "10月30日有团期吗？",
        "那10月30日附件哪天可以发团？",
        "10月30日前后有哪几个团期可选？",
    ):
        result = say(client, deal, text)
        readings = next(c for c in result["cards"] if c["type"] == "route_reads")["items"]
        assert readings[0]["dates"]["status"] == "nearby"
        assert readings[0]["itinerary"]["status"] == "unpublished"
        assert "draft" not in result
        assert detail(client, deal)["query"]["id"] == query_id
    say(
        client,
        deal,
        "没有孩子，你先查团期。为什么就一定要问清楚孩子，会影响什么问题吗？",
        "customer",
    )
    saved = detail(client, deal)
    assert value(saved, "party")["children"] == [] and value(saved, "party")["adults"] is None
    assert saved["query"]  # An unrelated party fill does not discard the date exploration.
    result = say(client, deal, "找线路给我啊")
    assert len(next(c for c in result["cards"] if c["type"] == "routes")["cards"]) == 1
    say(client, deal, "斯里兰卡9天7晚轻奢")
    assert detail(client, deal)["route"] is None
    result = say(client, deal, "我想看这条线路")
    inspected = next(c for c in result["cards"] if c["type"] == "route_reads")["items"][0]
    assert inspected["dates"]["status"] == "available"  # Uses the lasting trial window.
    assert detail(client, deal)["route"] is None
    say(client, deal, "就选这条")
    assert detail(client, deal)["route"]["product_id"] == SLOW
    assert not [c for c in state["calls"] if c[0] != "GET" and c[1] != "/v1/auth/login"]
    assert {data["input_kind"] for name, data in model.calls if name == "understand"} == {
        "customer",
        "advisor",
    }


@pytest.mark.parametrize(
    "status,days,published,error",
    [
        ("available", ["2026-10-30"], True, False),
        ("available", ["2026-10-30"], False, False),
        ("nearby", ["2026-11-05"], False, False),
        ("none", [], True, False),
        ("error", [], True, True),
    ],
)
def test_departure_and_itinerary_states_are_independent(dialogue, status, days, published, error):
    client, deal, state, _ = dialogue
    first(client, deal)
    state.update(dates=days, published=published, error=error)
    result = say(client, deal, "10月30日有团期吗？")
    item = next(c for c in result["cards"] if c["type"] == "route_reads")["items"][0]
    assert item["dates"]["status"] == status
    assert item["itinerary"]["status"] == ("published" if published else "unpublished")
    assert detail(client, deal)["route"] is None


def test_trial_adoption_and_undo_are_versioned_and_owner_scoped(dialogue):
    client, deal, _, _ = dialogue
    first(client, deal)
    endpoint = f"/api/deals/{deal}"
    client.post(
        endpoint + "/query/relax", json={"field": "window", "amount": 10}
    ).raise_for_status()
    before = detail(client, deal)
    old_id = before["query"]["id"]
    client.post(
        endpoint + "/query/resolve", json={"query_id": old_id, "adopt": False}
    ).raise_for_status()
    after = detail(client, deal)
    assert (
        after["query"] is None
        and after["need"] == before["need"]
        and after["version"] == before["version"]
    )
    assert (
        client.post(
            endpoint + "/query/resolve", json={"query_id": old_id, "adopt": True}
        ).status_code
        == 409
    )
    client.post(
        endpoint + "/query/relax", json={"field": "window", "amount": 10}
    ).raise_for_status()
    current = detail(client, deal)
    assert (
        client.post(
            endpoint + "/query/resolve", json={"query_id": old_id, "adopt": True}
        ).status_code
        == 409
    )
    client.post(
        endpoint + "/query/resolve", json={"query_id": current["query"]["id"], "adopt": True}
    ).raise_for_status()
    adopted = detail(client, deal)
    assert value(adopted, "window")["start"] == "2026-10-20"
    assert value(adopted, "window")["label"] == ""
    assert adopted["version"] == before["version"] + 1 and adopted["query"] is None
    client.post(
        "/api/login", json={"email": secrets.token_hex(8) + "@acme.example", "password": "x"}
    ).raise_for_status()
    assert client.post(endpoint + "/query/relax", json={"field": "window"}).status_code == 404


def test_unadopted_query_never_invalidates_quote_or_customer_selection(env):
    client, engine, owner = env
    deal = ready(client)
    quoted(client, deal)
    before = detail(client, deal)
    client.post(f"/api/deals/{deal}/query/relax", json={"field": "window"}).raise_for_status()
    after = detail(client, deal)
    assert after["route"] == before["route"] and after["departure"] == before["departure"]
    assert after["quote"] == before["quote"] and after["confirmation"] == before["confirmation"]
    assert after["need"] == before["need"]
    client.post(
        f"/api/deals/{deal}/query/resolve", json={"query_id": after["query"]["id"], "adopt": True}
    ).raise_for_status()
    adopted = detail(client, deal)
    assert (
        adopted["route"] is None and adopted["departure"] is None and not adopted["quote"]["valid"]
    )
    with engine.connect() as conn:
        assert not conn.execute(
            db.ledger.select().where(owner.where(db.ledger), db.ledger.c.deal_id == deal)
        ).all()


@pytest.mark.parametrize(
    "text", ["斯里兰卡9天7晚轻奢", "这条能选吗？", "先不选这条", "如果就选这条会怎样？"]
)
def test_mention_question_and_hypothesis_do_not_select(dialogue, text):
    client, deal, _, _ = dialogue
    first(client, deal)
    say(client, deal, text)
    assert detail(client, deal)["route"] is None


@pytest.mark.parametrize("text", ["您的护照号码已经保存了。", "您的手机号码已经登记了。"])
def test_false_personal_retention_claim(text):
    assert grounding.check(text, [])["reasons"] == ["private"]


def test_sold_deal_cannot_release_or_replace_its_route(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.post(
        f"/api/deals/{deal}/sale", json={"quote_id": quote["id"], "deposit": 10000}
    ).raise_for_status()
    before = detail(client, deal)
    assert client.delete(f"/api/deals/{deal}/route").status_code == 409
    assert detail(client, deal)["route"] == before["route"]
    assert detail(client, deal)["quote"] == before["quote"]


def test_exact_date_does_not_accept_model_expansion():
    result, _, _ = interpret.window(
        "10月30日去", {"start": "2026-10-25", "end": "2026-11-05"}, date(2026, 9, 29)
    )
    assert result["start"] == result["end"] == "2026-10-30"


def test_nearby_route_reply_removes_supplier_name_even_without_exact_candidates(dialogue):
    client, deal, state, _ = dialogue
    state["supplier_name"] = "ACME 日行"
    result = first(client, deal)
    assert "ACME 日行" not in result["draft"]["text"]
    assert "10月25日" in result["draft"]["text"]
    assert any(c["type"] == "routes" and not c["cards"] for c in result["cards"])


def test_viewing_route_does_not_take_its_duration_as_a_new_requirement(dialogue):
    client, deal, _, model = dialogue
    first(client, deal)
    before = detail(client, deal)
    original = model.call

    async def read(name, data):
        if name == "understand":
            return Understanding.model_validate(
                {
                    "action": "view",
                    "changes": [
                        {"field": "days", "value": {"min": 9, "max": 9}, "evidence": TITLE}
                    ],
                }
            )
        return await original(name, data)

    model.call = read
    say(client, deal, TITLE, "customer")
    after = detail(client, deal)
    assert after["need"] == before["need"] and not after["pending"] and after["route"] is None


def test_search_results_cannot_cross_requirement_or_query_versions():
    deal = {
        "need_version": 2,
        "query_context": {"id": "new", "base_version": 2, "fields": {"depart_city": {"new": None}}},
    }
    assert not queries.matches_search(deal, {"need_version": 1, "body": {}})
    assert not queries.matches_search(deal, {"need_version": 2, "body": {}})
    assert not queries.matches_search(
        deal, {"need_version": 2, "body": {"query_context": {"id": "old"}}}
    )
    assert queries.matches_search(
        deal, {"need_version": 2, "body": {"query_context": {"id": "new"}}}
    )


def test_legacy_quote_survives_optional_total_field_and_blocks_inconsistent_total(env):
    client, engine, owner = env
    deal_id = ready(client)
    quote = quoted(client, deal_id)
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
        row = store.rows(conn, owner, db.quotes, deal_id)[0]
    legacy = dict(row["snapshot"]["terms"]["party"])
    assert legacy == {"adults": 2, "children": [], "seniors": []}
    need = needs.Need.model_validate(deal["need"])
    assert pricing.validity(row, deal, need)[0] and quote["valid"]
    need = needs.set_field(need, "party", {**legacy, "total_count": 2}, "advisor")
    assert pricing.validity(row, deal, need)[0]
    need = needs.set_field(need, "party", {**legacy, "total_count": 3}, "advisor")
    assert not pricing.validity(row, deal, need)[0]


def test_viewing_and_reselecting_current_route_preserve_valid_quote(env):
    client, _, _ = env
    deal = ready(client)
    quoted(client, deal)
    before = detail(client, deal)
    viewed = say(client, deal, "我想看这条线路", "customer")
    assert any(c["type"] == "route_reads" for c in viewed["cards"])
    say(client, deal, "就选这条", "advisor")
    assert detail(client, deal)["quote"] == before["quote"]


def test_query_adoption_respects_sale_ledger_even_if_status_was_changed(env):
    client, engine, owner = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.post(
        f"/api/deals/{deal}/sale", json={"quote_id": quote["id"], "deposit": 10000}
    ).raise_for_status()
    with engine.begin() as conn:
        store.update_deal(conn, owner, deal, status="open")
    client.post(f"/api/deals/{deal}/query/relax", json={"field": "window"}).raise_for_status()
    before = detail(client, deal)
    response = client.post(
        f"/api/deals/{deal}/query/resolve", json={"query_id": before["query"]["id"], "adopt": True}
    )
    assert response.status_code == 409
    assert detail(client, deal)["need"] == before["need"]


def test_schema_three_upgrade_keeps_existing_need_and_quote(env):
    client, engine, owner = env
    deal = ready(client)
    quoted(client, deal)
    before = detail(client, deal)
    with engine.begin() as conn:
        conn.execute(sql_text("ALTER TABLE advisor.deal DROP COLUMN query_context"))
        conn.execute(db.versions.delete().where(db.versions.c.version == 4))
        conn.execute(
            sql_text(
                "INSERT INTO advisor.schema_version(version) VALUES (3) ON CONFLICT DO NOTHING"
            )
        )
    try:
        db.migrate(engine)
        db.migrate(engine)
        after = detail(client, deal)
        assert after["query"] is None
        assert after["need"] == before["need"]
        assert after["quote"] == before["quote"]
        with engine.connect() as conn:
            assert conn.execute(db.versions.select().where(db.versions.c.version == 4)).one()
    finally:
        db.migrate(engine)


# Reproductions from the review of 53f55b9.
def test_customer_route_view_preserves_published_itinerary(dialogue):
    client, deal, state, model = dialogue
    first(client, deal)
    state["published"] = True
    result = say(client, deal, "我想看这条线路", "customer")
    read = next(c for c in result["cards"] if c["type"] == "route_reads")["items"][0]
    assert read["itinerary"]["status"] == "published" and read["itinerary"]["summary"]
    assert "行程概要" in result["draft"]["text"], result["draft"]["text"]


def test_customer_date_error_is_not_just_acknowledged(dialogue):
    client, deal, state, model = dialogue
    first(client, deal)
    state["error"] = True
    result = say(client, deal, "10月30日有没有团期", "customer")
    read = next(c for c in result["cards"] if c["type"] == "route_reads")["items"][0]
    assert read["dates"]["status"] == "error"
    assert "查询失败" in result["draft"]["text"] or "重试" in result["draft"]["text"], result[
        "draft"
    ]["text"]


def test_visible_trial_departure_does_not_claim_wrong_route(dialogue):
    client, deal, state, model = dialogue
    first(client, deal)
    state["dates"] = ["2026-10-20"]
    for field, value in {
        "party": {"adults": 3, "children": [], "seniors": []},
        "rooms": {"twins": 2},
    }.items():
        client.put(f"/api/deals/{deal}/need/{field}", json={"value": value}).raise_for_status()
    client.post(
        f"/api/deals/{deal}/route", json={"product_id": SLOW, "title": TITLE}
    ).raise_for_status()
    client.post(
        f"/api/deals/{deal}/query/relax", json={"field": "window", "amount": 10}
    ).raise_for_status()
    dates = client.get(f"/api/deals/{deal}/dates").json()
    assert dates["items"]
    dep = dates["items"][0]["departure_id"]
    price = client.post(f"/api/deals/{deal}/dates/{dep}/price")
    assert "不属于当前线路" not in price.text, price.json()


def test_a_failed_departure_lookup_does_not_cost_the_route_page(dialogue):
    client, deal, state, model = dialogue
    state["error"] = True
    page = client.get(f"/api/routes/{SLOW}", params={"deal": deal})
    assert page.status_code == 200, page.text
    assert page.json()["published"] is False and page.json()["unpublished_reason"]


# Room counts and head counts: the rule corrects only a plain statement, and a reply
# states only the head counts the customer gave.
ROOM_CASES = [
    ("不要两间大床房，就一间双床房", {"doubles": 0, "twins": 1}),
    ("原来两间大床房，改成一间大床房", {"doubles": 1}),
]


@pytest.mark.parametrize("message,expected", ROOM_CASES, ids=["negated", "corrected"])
def test_rule_preserves_a_correct_model_room_count(message, expected):
    reading = Understanding.model_validate(
        {"changes": [{"field": "rooms", "value": expected, "evidence": message}]}
    )
    fills, proposals, rejected = interpret.changes(
        needs.Need(), reading, message, date(2026, 9, 29)
    )
    assert fills["rooms"]["new"] == needs.Rooms.model_validate(expected).model_dump(), (
        fills,
        proposals,
        rejected,
    )


@pytest.mark.parametrize(
    "message", ["一间大床房多少钱？", "如果住两间标间会贵多少？"], ids=["question", "hypothetical"]
)
def test_room_inquiry_does_not_fill_a_confirmed_requirement(message):
    fills, proposals, rejected = interpret.changes(
        needs.Need(), Understanding(), message, date(2026, 9, 29)
    )
    assert "rooms" not in fills and not any(p["field"] == "rooms" for p in proposals), (
        fills,
        proposals,
        rejected,
    )


@pytest.mark.parametrize("message,expected", ROOM_CASES, ids=["negated", "corrected"])
def test_http_persists_current_room_count_only(dialogue, monkeypatch, message, expected):
    client, deal, state, model = dialogue

    async def correct_reading(name, data):
        if name == "understand":
            return Understanding.model_validate(
                {"changes": [{"field": "rooms", "value": expected, "evidence": message}]}
            )
        if name == "draft":
            return Draft(to_customer="好的，已收到您的需求。", to_advisor="已整理")
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", correct_reading)
    say(client, deal, message, "customer")
    assert value(detail(client, deal), "rooms") == needs.Rooms.model_validate(expected).model_dump()


UNSUPPORTED = [
    "按3位成人来安排，酒店需要再核实。",
    "按三名成人来安排。",
    "按二大一小来安排。",
]


@pytest.mark.parametrize(
    "draft", UNSUPPORTED, ids=["unrelated-uncertainty", "chinese-counter", "chinese-shorthand"]
)
def test_draft_does_not_invent_a_known_party_composition(draft):
    checked = grounding.check(
        draft, [], said=["三个客人"], counts=grounding.known_counts(needs.Party(total_count=3))
    )
    assert not checked["text"] and "party" in checked["reasons"], checked


@pytest.mark.parametrize(
    "draft", UNSUPPORTED, ids=["unrelated-uncertainty", "chinese-counter", "chinese-shorthand"]
)
def test_http_does_not_return_invented_party_composition(dialogue, monkeypatch, draft):
    client, deal, state, model = dialogue
    first(client, deal)
    assert value(detail(client, deal), "party")["adults"] is None

    async def wrong_draft(name, data):
        if name == "understand":
            return Understanding()
        if name == "draft":
            return Draft(to_customer=draft, to_advisor="已整理")
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", wrong_draft)
    result = say(client, deal, "收到，谢谢。", "customer")
    assert draft not in result["draft"]["text"], result["draft"]


# A room swap keeps the model's reading; a count of one is not "one of them" by itself.
ROOM_SWAPS = ["两间大床房改一间双床房", "两间大床房换一间双床房", "大床房两间，调整为一间双床房"]
SWAPPED = {"doubles": 0, "twins": 1, "singles": 0, "note": ""}


@pytest.mark.parametrize("message", ROOM_SWAPS, ids=["change", "swap", "adjust"])
def test_room_correction_does_not_restore_the_old_count(message):
    reading = Understanding.model_validate(
        {"changes": [{"field": "rooms", "value": SWAPPED, "evidence": message}]}
    )
    fills, proposals, rejected = interpret.changes(
        needs.Need(), reading, message, date(2026, 9, 29)
    )
    assert fills["rooms"]["new"] == SWAPPED, (fills, proposals, rejected)


def test_http_room_correction_preserves_correct_model_reading(dialogue, monkeypatch):
    client, deal, state, model = dialogue
    message = ROOM_SWAPS[0]

    async def correct_model(name, data):
        if name == "understand":
            return Understanding.model_validate(
                {"changes": [{"field": "rooms", "value": SWAPPED, "evidence": message}]}
            )
        if name == "draft":
            return Draft(to_customer="好的，已收到您的需求。", to_advisor="已整理")
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", correct_model)
    say(client, deal, message, "customer")
    assert value(detail(client, deal), "rooms") == SWAPPED


FAMILY = {"adults": 2, "children": [{"age": 8}, {"age": 5}], "seniors": []}
FEWER = ["按一位成人、一个孩子来安排。", "只有一个孩子同行。"]


@pytest.mark.parametrize("draft", FEWER, ids=["one-and-one", "only-one-child"])
def test_one_person_exception_must_not_change_total_composition(draft):
    checked = grounding.check(
        draft,
        [],
        said=["两个大人和两个小孩"],
        counts=grounding.known_counts(needs.Party.model_validate(FAMILY)),
    )
    assert not checked["text"] and "party" in checked["reasons"], checked


@pytest.mark.parametrize("draft", FEWER, ids=["one-and-one", "only-one-child"])
def test_http_does_not_reduce_known_party_counts(dialogue, monkeypatch, draft):
    client, deal, state, model = dialogue
    client.put(f"/api/deals/{deal}/need/party", json={"value": FAMILY}).raise_for_status()
    saved = value(detail(client, deal), "party")
    assert saved["adults"] == 2 and len(saved["children"]) == 2

    async def wrong_draft(name, data):
        if name == "understand":
            return Understanding()
        if name == "draft":
            return Draft(to_customer=draft, to_advisor="已整理")
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", wrong_draft)
    result = say(client, deal, "收到，谢谢。", "customer")
    assert draft not in result["draft"]["text"], result["draft"]


# A room count that is not a number costs only the rooms; a part of the party needs
# that many people of its kind.
def room_reading(message, count):
    return Understanding.model_validate(
        {
            "changes": [
                {"field": "rooms", "value": {"doubles": count}, "evidence": "一间大床房"},
                {"field": "budget", "value": {"per_person": 15000}, "evidence": "每人15000元"},
            ]
        }
    )


@pytest.mark.parametrize("count", ["一", "一间"], ids=["chinese-number", "with-unit"])
def test_invalid_room_value_does_not_discard_valid_budget(count):
    message = "一间大床房，预算每人15000元"
    fills, proposals, rejected = interpret.changes(
        needs.Need(), room_reading(message, count), message, date(2026, 9, 29)
    )
    assert str(fills["budget"]["new"]["per_person"]) == "15000", (fills, proposals, rejected)


@pytest.mark.parametrize("count", ["一", "一间"], ids=["chinese-number", "with-unit"])
def test_http_survives_a_non_numeric_model_room_value(dialogue, monkeypatch, count):
    client, deal, state, model = dialogue
    message = "一间大床房，预算每人15000元"

    async def response(name, data):
        if name == "understand":
            return room_reading(message, count)
        if name == "draft":
            return Draft(to_customer="好的，已收到您的需求。")
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", response)
    monkeypatch.setattr(client._transport, "raise_server_exceptions", False)
    result = client.post(f"/api/deals/{deal}/turns", json={"text": message, "kind": "customer"})
    budget = value(detail(client, deal), "budget")
    assert result.status_code == 200, (result.status_code, result.text, {"saved_budget": budget})
    assert str(budget["per_person"]) == "15000"


SUBSET_CASES = [
    ({"total_count": 3}, "三个客人", "其中三位成人同行。"),
    ({"adults": 3, "children": []}, "三位大人，没有孩子", "其中一个孩子同行。"),
    ({"adults": 2, "children": [{"age": 8}]}, "两个大人一个孩子", "其中两个孩子同行。"),
]


@pytest.mark.parametrize(
    "party,said,draft",
    SUBSET_CASES,
    ids=["unknown-composition", "no-children", "too-many-children"],
)
def test_a_subset_cannot_invent_people(party, said, draft):
    result = grounding.check(
        draft, [], said=[said], counts=grounding.known_counts(needs.Party.model_validate(party))
    )
    assert not result["text"] and "party" in result["reasons"], result


@pytest.mark.parametrize(
    "party,said,draft",
    SUBSET_CASES,
    ids=["unknown-composition", "no-children", "too-many-children"],
)
def test_http_does_not_return_an_unfounded_subset(dialogue, monkeypatch, party, said, draft):
    client, deal, state, model = dialogue
    client.put(f"/api/deals/{deal}/need/party", json={"value": party}).raise_for_status()

    async def response(name, data):
        if name == "understand":
            return Understanding()
        if name == "draft":
            return Draft(to_customer=draft)
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", response)
    result = say(client, deal, "收到，谢谢。", "customer")
    assert draft not in result["draft"]["text"], result["draft"]


def test_disputed_room_counts_remain_pending_in_http(dialogue, monkeypatch):
    client, deal, state, model = dialogue
    message = "一间大床房，预算每人15000元"

    async def response(name, data):
        if name == "understand":
            return room_reading(message, 2)
        if name == "draft":
            return Draft(to_customer="好的，已收到您的需求。")
        raise AssertionError(name)

    monkeypatch.setattr(model, "call", response)
    result = say(client, deal, message, "customer")
    assert value(detail(client, deal), "rooms") is None
    change = next(c for c in result["cards"] if c["type"] == "change")
    item = next(i for i in change["items"] if i["field"] == "rooms")
    assert item["status"] == "pending" and item["new"]["doubles"] == 1
    assert "大床1间" in item["hint"] and "大床2间" in item["hint"]
