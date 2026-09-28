"""Drafts see the saved need, reword facts without losing limits, and price each child."""

from datetime import date

from cloud_warehouse import copilot_facts, copilot_reply, quotes, travel_requirements, trip_brief
from cloud_warehouse.copilot_engine import original_evidence
from cloud_warehouse.pricing import PriceSchedule
from tour.api.copilot_model import ReplyDraft
from tour.api.warehouse_copilot import grounded_memory, requirement_facts, salute

FAMILY = (
    "我们一家四口，2个大人2个小孩，孩子8岁和5岁，想12月中下旬去德法意瑞，上海走，12天左右，别太累"
)
DAY = copilot_facts.fact(
    "ACME 小镇散步，含早餐；午餐和晚餐自理。每天预留自由活动时间。", "itinerary", {}
)
PRICE = copilot_facts.fact("全家合计70000元", "price", {})


def check(text, facts=(DAY, PRICE), known=()):
    draft = ReplyDraft(to_advisor="已核对。", to_customer=text)
    return copilot_reply.validate(
        draft, list(facts), [], fallback="我先核对一下。", conclusion="已核对。", known=known
    )


def family():
    return trip_brief.TripBrief.model_validate(
        {
            "adults": {"value": 2, "source": "said"},
            "children": {"value": 2, "source": "said"},
            "child_ages": {"value": [8, 5], "source": "said"},
            "window": {"value": {"start": "2026-12-26", "end": "2027-01-03"}, "source": "inferred"},
            "depart_city": {"value": "上海", "source": "said"},
            "preferences": {
                "value": [{"key": "slow_pace", "label": "行程轻松不赶"}],
                "source": "said",
            },
        }
    )


def test_saved_requirements_are_facts_and_are_not_asked_again():
    facts, known, missing = requirement_facts(family(), {"deal_id": "d"})
    texts = [f["text"] for f in facts]
    assert "2位成人、2位儿童（8岁、5岁）" in texts
    assert "12月26日至1月3日之间出发" in texts
    assert {"party", "child_ages", "window", "depart_city"} <= known
    assert "房型与占床" in missing
    result = check("孩子分别几岁？一共几位大人？这次需要安排几间房呢？", facts, known)
    assert result["to_customer"] == "这次需要安排几间房呢？"
    assert result["violations"].count("asks_known") == 2


def test_reworded_facts_are_kept_and_cited():
    result = check("行程每天都留了自由活动时间。早餐包含在内，午餐和晚餐自理。全家合计70000元。")
    assert not result["simplified"]
    assert {c["fact_id"] for c in result["claims"]} == {DAY["fact_id"], PRICE["fact_id"]}


def test_rewording_cannot_add_numbers_or_drop_a_limit():
    for text in ("三餐全含，含午餐和晚餐。", "全家合计7万元。", "全家合计68000元。"):
        assert text.rstrip("。") not in check(text)["to_customer"]


def test_a_question_about_a_shown_route_keeps_the_saved_destinations():
    brief = trip_brief.TripBrief.model_validate(
        {"destination_regions": {"value": ["欧洲"], "source": "explore"}}
    )
    values = travel_requirements.normalize(
        {"destinations": {"value": ["德国"]}},
        "德法瑞意那条会不会很赶？有没有购物店？",
        brief,
        date(2026, 9, 28),
    )
    assert not set(values) & travel_requirements.GEO_FIELDS


def test_destination_evidence_is_the_clause_not_the_message():
    values = travel_requirements.normalize(
        {"destinations": {}}, FAMILY, trip_brief.TripBrief(), date(2026, 9, 28)
    )
    assert values["destinations"]["evidence"] == "想12月中下旬去德法意瑞"
    assert "destination_examples" not in values


def test_joined_evidence_phrases_are_found_separately():
    message = "别太累，孩子第一次出国，最怕行程太赶"
    assert original_evidence(message, "别太累、最怕行程太赶") == "最怕行程太赶"
    assert original_evidence(message, "别太累、想住五星") == ""


def test_concerns_are_remembered_in_the_customer_words():
    kept = grounded_memory(["希望行程轻松不赶"], FAMILY + "。孩子第一次出国，最怕行程太赶。")
    assert "最怕行程太赶" in kept and "别太累" in kept


def test_salutation_is_not_doubled():
    assert salute("[客人称呼]您好！", "") == "您好！"
    assert salute("[客人称呼]，您好！", "王女士") == "王女士，您好！"
    assert salute("您您好～", "") == "您好～"


def test_each_child_is_priced_by_their_own_bed():
    schedule = PriceSchedule(
        currency="CNY",
        market={"adult": 14800, "child": 12800},
        settlement={"adult": 12800, "child": 10800},
        fees_complete=True,
        child_occupies_seat=True,
        child_age_min=2,
        child_age_max=12,
        room_supplements={
            "doubles": {"market": 0, "settlement": 0},
            "twins": {"market": 0, "settlement": 0},
        },
        child_bed_prices={
            "occupied": {"market": 12800, "settlement": 10800},
            "unoccupied": {"market": 11800, "settlement": 9800},
        },
    )
    rooms = trip_brief.Rooms(
        doubles=1, twins=1, child_beds=[{"age": 5, "bed": False}, {"age": 8, "bed": True}]
    )
    assert rooms.child_bed is None
    party = quotes.Party(
        adults=2, children=2, child_ages=[8, 5], rooms=rooms.model_dump(mode="json")
    )
    quote = quotes.calculate(schedule, party)
    lines = {line["code"]: line for line in quote["market_lines"]}
    assert lines["child.occupied"]["total"] == "12800.00"
    assert lines["child.unoccupied"]["total"] == "11800.00"
    assert quote["market_total"] == "54200.00"
