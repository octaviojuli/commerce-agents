"""Reading a message into checked changes: evidence, places, holidays and the party."""

from datetime import date
from types import SimpleNamespace

from tour.advisor import need as needs
from tour.advisor.interpret import changes, grounded, merge_party, window
from tour.advisor.need import Need

TODAY = date(2026, 9, 28)


def reading(*items, **extra):
    base = {"changes": [SimpleNamespace(**i) for i in items], "rejected": []}
    return SimpleNamespace(**{**base, **extra})


def change(field, value, evidence, source="said", hint=""):
    return {"field": field, "value": value, "evidence": evidence, "source": source, "hint": hint}


def family():
    need = Need()
    need = needs.set_field(
        need, "party", {"adults": 2, "children": [{"age": 8}, {"age": 5}]}, "said", "2个大人2个小孩"
    )
    return needs.set_field(need, "destinations", {"must": ["德国"]}, "said", "德国")


def test_evidence_must_be_the_customer_words():
    assert grounded("我们一家四口，孩子8岁", "我们一家四口,孩子8岁") == "我们一家四口，孩子8岁"
    assert grounded("别太累，最怕行程太赶", "别太累、最怕行程太赶") == "最怕行程太赶"
    assert grounded("别太累", "想住五星") == ""


def test_a_partial_bed_answer_keeps_the_other_child_open():
    party = needs.Party.model_validate({"adults": 2, "children": [{"age": 8}, {"age": 5}]})
    merged = needs.Party.model_validate(merge_party(party, {"children": [{"age": 8, "bed": True}]}))
    assert [(c.age, c.bed) for c in merged.children] == [(8, True), (5, None)]
    assert (
        "child_beds"
        in needs.gates(needs.set_field(Need(), "party", merged.model_dump(), "said"))["quote"][
            "missing"
        ]
    )


def test_answering_open_questions_fills_and_adding_a_traveller_is_a_change():
    need = family()
    fills, proposals, _ = changes(
        need,
        reading(
            change(
                "party",
                {"children": [{"age": 8, "bed": True}, {"age": 5, "bed": False}]},
                "5岁不占床，8岁占床",
            )
        ),
        "5岁不占床，8岁占床",
        TODAY,
    )
    assert "party" in fills and not proposals
    fills, proposals, _ = changes(
        need,
        reading(change("party", {"seniors": [{"age": 70}]}, "我妈也想去，她70了")),
        "我妈也想去，她70了",
        TODAY,
    )
    assert not fills and proposals[0]["field"] == "party"


def test_a_question_about_a_shown_route_does_not_change_the_destinations():
    fills, proposals, _ = changes(
        family(),
        reading(change("destinations", {"must": ["德国", "法国"]}, "德法瑞意那条")),
        "德法瑞意那条会不会很赶？",
        TODAY,
    )
    assert not fills and not proposals


def test_holidays_mean_the_days_around_them():
    value, evidence, hint = window("时间改元旦", None, TODAY)
    assert (value["start"], value["end"], evidence) == ("2026-12-26", "2027-01-03", "元旦")
    assert "推断" not in hint or hint


def test_family_travel_is_not_travelling_with_children():
    fills, _, _ = changes(
        Need(), reading(change("preferences", ["family"], "带家里人")), "想带家里人出去玩玩", TODAY
    )
    assert "preferences" not in fills
