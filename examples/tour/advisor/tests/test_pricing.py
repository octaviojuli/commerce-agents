"""Per-bed child prices and the one rule for whether a stored price still holds."""

from datetime import UTC, datetime, timedelta

from tour.advisor import need as needs
from tour.advisor import pricing
from tour.advisor.need import Need


def need(beds=(True, False)):
    n = needs.set_field(
        Need(),
        "party",
        {
            "adults": 2,
            "children": [{"age": 8, "bed": beds[0]}, {"age": 5, "bed": beds[1]}],
            "seniors": [{"age": 70}],
        },
        "said",
    )
    return needs.set_field(n, "rooms", {"doubles": 1, "twins": 1, "singles": 1}, "said")


def quote(child, complete=True):
    lines = [
        {"code": "adult", "quantity": 2, "unit_amount": "14800.00", "total": "29600.00"},
        {
            "code": child[0],
            "quantity": 2,
            "unit_amount": child[1],
            "total": str(float(child[1]) * 2),
        },
        {"code": "senior", "quantity": 1, "unit_amount": "14800.00", "total": "14800.00"},
    ]
    return {
        "complete": complete,
        "market_lines": lines,
        "settlement_lines": lines,
        "quote_valid_until": "2030-01-01T00:00:00+00:00",
    }


def test_each_child_is_priced_by_their_own_bed_on_any_warehouse():
    occupied = quote(("child", "12800.00"))
    unoccupied = quote(("child.unoccupied", "11800.00"))
    out = pricing.compose(occupied, unoccupied, need().get("party"))
    lines = {line["code"]: line for line in out["market_lines"]}
    assert lines["child.occupied"]["total"] == "12800.00"
    assert lines["child.unoccupied"]["total"] == "11800.00"
    assert out["market_total"] == "69000.00" and out["complete"]


def test_a_price_holds_only_for_its_party_rooms_departure_and_time():
    n = need()
    row = {
        "status": "active",
        "departure_id": "WD-1",
        "snapshot": {"terms": pricing.terms(n), "complete": True},
        "valid_until": datetime.now(UTC) + timedelta(hours=1),
    }
    deal = {"departure": {"departure_id": "WD-1"}}
    assert pricing.validity(row, deal, n) == (True, "")
    assert not pricing.validity(row, {"departure": {"departure_id": "WD-2"}}, n)[0]
    assert not pricing.validity(row, deal, need(beds=(True, True)))[0]
    assert not pricing.validity(
        {**row, "valid_until": datetime.now(UTC) - timedelta(minutes=1)}, deal, n
    )[0]
    assert not pricing.validity(
        {**row, "snapshot": {**row["snapshot"], "complete": False}}, deal, n
    )[0]


def test_gaps_are_named_in_words_and_a_zero_single_room_is_not_a_price():
    snapshot = {
        "complete": False,
        "missing_items": ["child_seat_policy_unknown", "market.charge.visa"],
        "market_lines": [
            {"code": "adult", "quantity": 2, "unit_amount": "8999.00", "total": "17998.00"},
            {"code": "single_room", "quantity": 1, "unit_amount": "0.00", "total": "0.00"},
        ],
    }
    assert pricing.missing(snapshot) == [
        "儿童占床规则未写明",
        "附加费未报价（visa）",
        "单房差未报价",
    ]
    single = next(x for x in pricing.lines(snapshot) if x["label"] == "单房差")
    assert single["unit"] is None and single["total"] is None
