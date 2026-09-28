"""Advisors see whose route it is, filter by supplier and keep their own supplier notes;
customers never read a supplier's name."""

from uuid import UUID

from tour.advisor.model import Draft, Understanding
from tour.advisor.tests.test_review import ready, run_turn
from tour.advisor.tests.test_story import CITY, SLOW, Scripted


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


def test_a_supplier_name_never_reaches_the_customer(env):
    client, engine, owner = env
    deal = ready(client)
    client.post(f"/api/deals/{deal}/search").raise_for_status()

    class Leaky(Scripted):
        async def call(self, name, payload):
            if name == "understand":
                return Understanding.model_validate({"kinds": ["chitchat"]})
            if name == "draft":
                return Draft(to_customer="您好，这条是 ACME 城游 家的线路，行程很轻松。收到啦。")
            return await super().call(name, payload)

    result = run_turn(engine, owner, UUID(deal), Leaky(), "好的")
    assert "ACME 城游" not in result["draft"]["text"]
    assert any("ACME 城游" in r for r in result["draft"]["removed"])
