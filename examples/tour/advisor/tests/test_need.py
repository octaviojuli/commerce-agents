"""Gates, clarity and the spoken summary of a need."""

from tour.advisor import need as needs
from tour.advisor.need import Need


def test_search_needs_a_direction_and_a_time_and_quotes_need_every_child():
    n = needs.set_field(Need(), "destinations", {"must": ["日本"]}, "said")
    assert needs.gates(n)["search"]["missing"] == ["window"]
    n = needs.set_field(
        n, "window", {"start": "2026-12-26", "end": "2027-01-03", "label": "元旦前后"}, "inferred"
    )
    assert needs.gates(n)["search"]["ready"]
    n = needs.set_field(n, "party", {"adults": 2, "children": [{"age": 5, "bed": None}]}, "said")
    n = needs.set_field(n, "rooms", {"doubles": 1}, "said")
    assert needs.gates(n)["quote"]["missing"] == ["child_beds"]
    n = needs.set_field(n, "party", {"adults": 2, "children": [{"age": 5, "bed": False}]}, "said")
    assert needs.gates(n)["quote"]["ready"]


def test_the_need_is_said_back_in_plain_words():
    n = needs.set_field(
        Need(),
        "window",
        {"start": "2026-12-26", "end": "2027-01-03", "label": "元旦前后"},
        "inferred",
    )
    n = needs.set_field(
        n,
        "party",
        {"adults": 2, "children": [{"age": 8}, {"age": 5}], "seniors": [{"age": 70}]},
        "said",
    )
    n = needs.set_field(n, "budget", {"per_person": 15000}, "said")
    n = needs.set_field(n, "preferences", ["slow_pace"], "said")
    assert needs.spoken(n) == "元旦前后出发、2大2小 + 1位长辈、每人1.5万以内、别太累"
