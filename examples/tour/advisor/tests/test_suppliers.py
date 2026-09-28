"""Advisors see whose route it is, filter by supplier and keep their own supplier notes;
customers never read a supplier's name."""

from uuid import UUID

import pytest

from tour.advisor.model import Draft, ModelUnavailable, Understanding
from tour.advisor.need import Need
from tour.advisor.tests.test_review import ready, run_turn
from tour.advisor.tests.test_story import CITY, SLOW, Scripted
from tour.advisor.turns import Turns


def test_health_is_available_without_a_session(env):
    client, _, _ = env
    client.cookies.clear()
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/today").status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [True, False])
async def test_supplier_name_is_private_in_fallback_drafts(unavailable):
    class FailedDraft:
        async def call(self, name, payload):
            if unavailable:
                raise ModelUnavailable("draft unavailable")
            return Draft(to_customer="谢谢")

    result = await Turns(None, FailedDraft()).draft(
        None,
        {},
        Need(),
        {"memory": {}, "visible": [{"supplier": {"name": "ACME 城游"}}]},
        "找线",
        None,
        [],
        [],
        {"routes": ["ACME 城游精品线路"]},
        False,
    )
    assert "ACME 城游" not in result["text"]
    assert result["text"] and result["simplified"]


def test_search_shows_each_supplier_and_the_same_route_from_the_other(env):
    client, _, _ = env
    deal = ready(client)
    found = client.post(f"/api/deals/{deal}/search").json()["body"]
    cards = {c["product_id"]: c for c in found["cards"]}
    assert cards[SLOW]["supplier"]["name"] == "ACME 环线"
    assert [a["product_id"] for a in cards[SLOW]["also"]] == [CITY]
    assert {(s["name"], s["count"]) for s in found["suppliers"]} == {
        ("ACME 环线", 1),
        ("ACME 城游", 1),
    }
    only = client.post(f"/api/deals/{deal}/search", json={"suppliers": ["S-A"]}).json()["body"]
    assert [c["product_id"] for c in only["cards"]] == [SLOW]
    assert only["steps"][-1]["label"] == "只看 ACME 环线"


def test_an_advisors_supplier_note_follows_every_card(env):
    client, _, _ = env
    saved = client.put(
        "/api/suppliers/S-B",
        json={"name": "ACME 城游", "stance": "cautious", "note": "改行程要提前三天说"},
    ).json()
    assert saved["stance_text"] == "慎用"
    listed = client.get("/api/routes").json()
    city = next(i for i in listed["items"] if i["product_id"] == CITY)
    assert city["supplier"]["note"] == "改行程要提前三天说"
    assert client.get(f"/api/routes/{CITY}").json()["supplier"]["stance"] == "cautious"
    assert client.put("/api/suppliers/S-B", json={"stance": "best"}).status_code == 422


@pytest.mark.parametrize("prior_search", [False, True])
def test_a_supplier_name_never_reaches_the_customer(env, prior_search):
    client, engine, owner = env
    deal = ready(client)
    if prior_search:
        client.post(f"/api/deals/{deal}/search").raise_for_status()

    class Leaky(Scripted):
        async def call(self, name, payload):
            if name == "understand":
                return Understanding.model_validate({"kinds": ["chitchat"]})
            if name == "draft":
                return Draft(to_customer="您好，这条是 ACME 城游 家的线路，行程很轻松。收到啦。")
            return await super().call(name, payload)

    result = run_turn(engine, owner, UUID(deal), Leaky(), "帮我重新找线")
    assert "ACME 城游" not in result["draft"]["text"]
    assert any("ACME 城游" in r for r in result["draft"]["removed"])


def test_supplier_filter_keeps_alternatives_and_relaxations_in_scope(env):
    client, _, _ = env
    deal = ready(client)
    client.put(f"/api/deals/{deal}/need/depart_city", json={"value": "上海"}).raise_for_status()
    response = client.post(f"/api/deals/{deal}/search", json={"suppliers": ["S-B"]})
    response.raise_for_status()
    body = response.json()["body"]
    assert body["supplier_filter"] == ["S-B"] and body["alternatives_only"]
    assert [c["product_id"] for c in body["cards"]] == [CITY]
    assert next(r for r in body["relax"] if r["field"] == "depart_city")["count"] == 1
    empty = client.post(f"/api/deals/{deal}/search", json={"suppliers": ["S-unknown"]})
    empty.raise_for_status()
    assert empty.json()["body"]["cards"] == []
    assert empty.json()["body"]["relax"] == []
