"""What a run on a real supplier's catalog showed, pinned down with ACME material."""

from datetime import date
from types import SimpleNamespace
from uuid import UUID

from tour.advisor import db, grounding, privacy
from tour.advisor import need as needs
from tour.advisor.facts import Route
from tour.advisor.interpret import changes
from tour.advisor.model import Understanding
from tour.advisor.need import Need
from tour.advisor.pricing import missing_text
from tour.advisor.tests.test_review import quoted, ready, run_turn
from tour.advisor.tests.test_story import CITY, SLOW, Scripted
from tour.advisor.turns import pending_line

TODAY = date(2026, 9, 28)


def reading(*items):
    return SimpleNamespace(
        changes=[SimpleNamespace(**{"source": "said", "hint": "", **i}) for i in items], rejected=[]
    )


def party_from(message, value):
    fills, _, _ = changes(
        Need(), reading({"field": "party", "value": value, "evidence": message}), message, TODAY
    )
    return needs.parse("party", fills["party"]["new"])


def test_personal_numbers_never_leave_the_paste():
    text, found = privacy.mask("我手机13812345678，护照号E12345678，身份证110101199001011234")
    assert found == ["手机号", "身份证号", "护照号"]
    assert "13812345678" not in text and "E12345678" not in text and "1101011990" not in text
    assert privacy.mask("ACME 12 天，每人 15800 元，出团编号 HU11")[1] == []


def test_a_couple_is_two_people_and_an_older_couple_is_two_seniors():
    party = party_from("两个大人，住一间双床房", {"adults": 2, "children": None})
    assert (party.adults, party.children, party.seniors) == (2, [], [])
    older = party_from("我和老伴明年3月想去", {"adults": 2, "children": None, "seniors": [{}, {}]})
    assert (older.adults, older.children, len(older.seniors)) == (0, [], 2)
    family = party_from("两个大人两个小孩", {"adults": 2, "children": [{"age": 8}]})
    assert family.children and family.children[0].age == 8


def test_holding_seats_is_a_promise_and_comparing_prices_is_a_claim():
    assert grounding.check("我这边帮您锁定安排。", [])["reasons"] == ["promise"]
    assert grounding.check("两种住法价格会差一些。", [])["removed"]


def test_a_list_of_what_was_asked_and_a_recap_of_what_was_said_are_kept():
    said = ["想去西班牙葡萄牙，10到12天，预算每人2万左右。不想进购物店"]
    for kept in [
        "您问的自费项目、含餐和签证这三项，资料里都没写明。",
        "西葡、10–12天、每人2万左右、不进购物店，这些我都记下了。",
    ]:
        assert not grounding.check(kept, [], said=said)["removed"], kept
    for cut in [
        "行程里没有购物店。",
        "含早餐和午餐这两项我去确认，另外全程无自费。",
        "这三项都含在团费里，我去确认一下。",
    ]:
        assert grounding.check(cut, [], said=said)["removed"], cut
    assert grounding.check("这条线含早餐。", [], said=["这条线含早餐吗？"])["removed"]


def test_meals_are_counted_without_the_empty_slots_and_summarised():
    def meals(b, lunch, d):
        return {"breakfast": {"text": b}, "lunch": {"text": lunch}, "dinner": {"text": d}}

    days = [
        {"day": 1, "title": "ACME 出发", "meals": meals("×", "×", "×")},
        {"day": 2, "title": "ACME 小镇", "meals": meals("酒店内", "自理", "5 菜 1 汤")},
    ]
    route = Route("P", "ACME", {"days": days})
    assert any(
        f["text"] == "餐食安排：行程含 2 餐（早餐 1、午餐 0、晚餐 1）" for f in route.facts()
    )
    assert next(g for g in route.grid() if g["label"] == "含餐")["value"] == "2 餐"


def test_what_is_being_checked_is_said_in_the_customers_terms():
    assert (
        pending_line([{"q": "帮我跟她说一下这个团期的价格"}])
        == "您问的这个团期的价格，现有资料尚未写明，仍待核实。"
    )
    assert (
        pending_line([{"q": "小林问：有购物店吗？"}, {"q": "有购物店吗？"}])
        == "您问的有购物店，现有资料尚未写明，仍待核实。"
    )
    assert missing_text("market.child.occupied") == "门市价缺儿童占床价"


def test_a_comparison_question_is_answered_on_each_route_shown(env):
    client, engine, owner = env
    deal = ready(client)
    client.post(f"/api/deals/{deal}/search").raise_for_status()

    class Asks(Scripted):
        async def call(self, name, payload):
            if name == "understand":
                return Understanding.model_validate(
                    {"kinds": ["ask"], "questions": [{"text": "哪条吃得好一点？", "topic": "餐食"}]}
                )
            return await super().call(name, payload)

    result = run_turn(engine, owner, UUID(deal), Asks(), "她更在意吃，哪条吃得好一点？")
    answers = next(c for c in result["cards"] if c["type"] == "answers")["items"]
    assert {a["product_id"] for a in answers} == {SLOW, CITY}


def test_a_sales_price_waits_for_a_price_that_holds(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.put(
        f"/api/deals/{deal}/need/party",
        json={"value": {"adults": 3, "children": [], "seniors": []}},
    ).raise_for_status()
    refused = client.put(f"/api/deals/{deal}/quotes/{quote['id']}", json={"sales_total": 1000})
    assert refused.status_code == 409, refused.json()


def test_a_pasted_passport_number_is_neither_kept_nor_sent(env):
    client, engine, owner = env
    deal = ready(client)
    seen = []

    class Watching(Scripted):
        async def call(self, name, payload):
            seen.append(str(payload))
            return await super().call(name, payload)

    result = run_turn(engine, owner, UUID(deal), Watching(), "我护照号E12345678，手机13812345678")
    assert any("已隐藏" in n for n in result["notes"])
    assert not any("E12345678" in s or "13812345678" in s for s in seen)
    with engine.connect() as conn:
        stored = conn.execute(db.turns.select().where(db.turns.c.deal_id == UUID(deal))).all()
    assert not any("E12345678" in str(r) for r in stored)


def test_asking_for_documents_separately_is_kept_and_claiming_them_is_not():
    ask = "另外麻烦您之后把出行人的证件信息单独发我一下，方便登记。"
    assert not grounding.check(ask, [])["removed"]
    assert grounding.check("护照号已帮您登记。", [])["reasons"] == ["private"]


def test_an_unknown_label_does_not_lose_the_customers_changes():
    from tour.advisor.model import Understanding

    reading = Understanding.model_validate(
        {
            "action": "change_need",
            "kinds": ["change", "complaint"],
            "changes": [{"field": "budget", "value": {"per_person": 15000}, "evidence": "1.5万"}],
        }
    )
    assert reading.action == "auto" and reading.kinds == ["change"]
    assert reading.changes[0].field == "budget"


def test_a_senior_is_not_counted_again_as_an_adult():
    reading = {
        "total_count": 5,
        "adults": 3,
        "seniors": [{"age": 70}],
        "children": [{"age": 8, "bed": True}, {"age": 5, "bed": False}],
    }
    party = party_from("2个大人2个小孩，我妈也去，一共5人", reading)
    assert (party.adults, len(party.seniors), len(party.children), party.total) == (2, 1, 2, 5)


def test_an_added_adult_is_kept_and_an_old_total_gives_way():
    need = needs.set_field(
        Need(),
        "party",
        {"total_count": 3, "adults": 2, "children": [{"age": 8, "bed": True}], "seniors": []},
        "said",
    )
    message = "再加一位成人，成人改成3位"
    fills, proposals, _ = changes(
        need,
        reading({"field": "party", "value": {"adults": 3}, "evidence": message}),
        message,
        TODAY,
    )
    new = needs.parse("party", proposals[0]["new"])
    assert (new.adults, new.total_count, new.total) == (3, None, 4)


def test_an_unpublished_route_says_which_day_counts_disagree():
    from tour.advisor import routes

    product = {"title": "ACME 海岛7天5晚", "attributes": {"days": "6"}}
    odd = [
        routes.day_check(
            {"calendar_days": "10", "duration_check": "mismatch_requires_confirmation"}
        )
    ]
    fine = [routes.day_check({"calendar_days": "6", "duration_check": "matched"})]
    assert routes.unpublished_reason(product, fine) == (
        "行程待供应商核对天数（线路名写 7 天，供应商登记 6 天），暂未发布"
    )
    assert "部分团期为 10 天" in routes.unpublished_reason(product, odd)
    agreed = {"title": "ACME 海岛6天", "attributes": {"days": "6"}}
    assert routes.unpublished_reason(agreed, fine) == "这条线路尚未发布行程"
