# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``api/plans.py``: what one version of a 定制方案 reads as against the version before it.
A day is compared by what it says, so the numbering the model rewrites when it inserts a day
is not a change; the summary names the days by their own markers; and the handoff is the
plain text the 计调 is sent."""

import re

from tour.api.plans import (
    PLAN_ID,
    DayDiff,
    Plan,
    PlanDay,
    PlanVersion,
    ReferencePrice,
    diff_days,
    handoff_text,
    mark_requests,
    new_plan_id,
    new_share_token,
    summarize,
)

# The published 线路 every plan below is built from, as five days.
BASELINE = [
    PlanDay(label="第1天 乌鲁木齐集合", note="接机后入住四钻酒店"),
    PlanDay(label="第2天 赛里木湖", note="环湖一天，晚上住湖边"),
    PlanDay(label="第3天 那拉提草原", note="空中草原徒步"),
    PlanDay(label="第4天 巴音布鲁克", note="九曲十八弯看日落"),
    PlanDay(label="第5天 返程", note="送机"),
]


def kinds(diff: list[DayDiff]) -> list[str]:
    return [day.kind for day in diff]


def version(days: list[PlanDay], parent: int | None = 1, **fields) -> PlanVersion:
    return PlanVersion(
        plan_id="PL-Ab12Cd34",
        version=2 if parent else 1,
        parent_version=parent,
        title="伊犁八日定制",
        days=mark_requests(days),
        share_token="a-token",
        **fields,
    )


def test_the_first_version_is_the_baseline_and_every_day_of_it_is_unchanged():
    diff = diff_days(None, BASELINE)
    assert kinds(diff) == ["same"] * 5
    assert [day.index for day in diff] == [0, 1, 2, 3, 4]
    assert all(day.fields == [] for day in diff)
    assert [day.label for day in diff] == [day.label for day in BASELINE]
    assert summarize(diff, parent_version=None) == "基线原样"


def test_a_version_that_says_the_same_thing_changed_nothing():
    diff = diff_days(BASELINE, list(BASELINE))
    assert kinds(diff) == ["same"] * 5
    assert summarize(diff, parent_version=1) == "与上一版相同"


def test_renumbering_every_label_is_not_a_change():
    """The model rewrites 第N天 whenever it moves a day, so the marker is not what a day is."""
    renumbered = [
        day.model_copy(update={"label": day.label.replace(f"第{index}天", f"第 {index + 1} 天")})
        for index, day in enumerate(BASELINE, start=1)
    ]
    diff = diff_days(BASELINE, renumbered)
    assert kinds(diff) == ["same"] * 5
    assert summarize(diff, parent_version=2) == "与上一版相同"


def test_one_rewritten_note_is_one_changed_day():
    days = list(BASELINE)
    days[1] = days[1].model_copy(update={"note": "环湖半天，下午赶路，晚上住昭苏"})
    diff = diff_days(BASELINE, days)
    assert kinds(diff) == ["same", "changed", "same", "same", "same"]
    assert diff[1].fields == ["note"]
    assert diff[1].note.startswith("环湖半天")
    assert summarize(diff, parent_version=1) == "改 1 天（第 2 天）"


def test_a_renamed_day_is_a_changed_label():
    days = list(BASELINE)
    days[2] = days[2].model_copy(update={"label": "第3天 那拉提草原（改走恰西）"})
    diff = diff_days(BASELINE, days)
    assert kinds(diff) == ["same", "same", "changed", "same", "same"]
    assert diff[2].fields == ["label"]


def test_a_day_inserted_in_the_middle_shifts_the_rest_without_changing_them():
    days = [
        BASELINE[0],
        BASELINE[1],
        PlanDay(label="第3天 昭苏油菜花", note="加一天，待计调确认能否加住宿"),
        BASELINE[2].model_copy(update={"label": "第4天 那拉提草原"}),
        BASELINE[3].model_copy(update={"label": "第5天 巴音布鲁克"}),
        BASELINE[4].model_copy(update={"label": "第6天 返程"}),
    ]
    diff = diff_days(BASELINE, days)
    assert kinds(diff) == ["same", "same", "added", "same", "same", "same"]
    assert diff[2].index == 2
    assert diff[2].fields == []
    assert summarize(diff, parent_version=1) == "+1 天（第 3 天）"


def test_a_removed_day_sits_where_it_would_have_been():
    days = [
        BASELINE[0],
        BASELINE[2].model_copy(update={"label": "第2天 那拉提草原"}),
        BASELINE[3].model_copy(update={"label": "第3天 巴音布鲁克"}),
        BASELINE[4].model_copy(update={"label": "第4天 返程"}),
    ]
    diff = diff_days(BASELINE, days)
    assert kinds(diff) == ["same", "removed", "same", "same", "same"]
    removed = diff[1]
    # The parent's day, at the place in the new version the card strikes it through.
    assert (removed.index, removed.label) == (1, "第2天 赛里木湖")
    assert summarize(diff, parent_version=1) == "删 1 天"


def test_a_version_that_adds_changes_and_removes_says_all_three():
    days = [
        BASELINE[0],
        PlanDay(label="第2天 天山大峡谷", note="客人想加一天"),
        BASELINE[1].model_copy(update={"label": "第3天 赛里木湖"}),
        BASELINE[2].model_copy(update={"label": "第4天 那拉提草原"}),
        BASELINE[3].model_copy(update={"label": "第5天 巴音布鲁克", "note": "改住景区内酒店"}),
    ]
    diff = diff_days(BASELINE, days)
    assert kinds(diff) == ["same", "added", "same", "same", "changed", "removed"]
    assert diff[4].fields == ["note"]
    assert diff[5].label == "第5天 返程"
    assert summarize(diff, parent_version=2) == "+1 天（第 2 天）；改 1 天（第 5 天）；删 1 天"


def test_a_day_is_named_by_its_own_marker_and_otherwise_by_its_place():
    parent = [
        PlanDay(label="第一天 集合", note="一"),
        PlanDay(label="Day 2 赛里木湖", note="二"),
        PlanDay(label="返程", note="三"),
    ]
    days = [day.model_copy(update={"note": day.note + "，改"}) for day in parent]
    assert summarize(diff_days(parent, days), parent_version=1) == "改 3 天（第 1、2、3 天）"


def test_a_note_that_asks_the_planner_a_question_is_a_request():
    days = mark_requests(
        [
            PlanDay(label="第1天 乌鲁木齐集合", note="接机后入住四钻酒店"),
            PlanDay(label="第2天 赛里木湖", note="客人要换成湖景房，待计调确认差价"),
            PlanDay(label="第3天 返程", note="送机", request=True),
        ]
    )
    assert [day.request for day in days] == [False, True, False]
    # The flag is read off the note on every version, so what the model marked is not it.
    assert mark_requests(days)[2].request is False


def test_the_handoff_is_plain_lines_the_planner_can_read():
    plan = Plan(
        plan_id="PL-Ab12Cd34",
        session_id="session-of-the-plans-suite",
        user_id="advisor-of-the-plans-suite",
        route_id=1021,
        route_name="伊犁北疆环线",
        line_type="散拼",
        departure_id=3008,
    )
    days = [
        BASELINE[0],
        PlanDay(label="第2天 赛里木湖", note="客人要换成湖景房，待计调确认差价"),
        BASELINE[4].model_copy(update={"label": "第3天 返程"}),
    ]
    sent = version(
        days,
        travel_dates="10月15日—10月17日",
        party="2大1小",
        reference_price=ReferencePrice(
            tong_ye_adult=4980, market_adult=5680.5, quote_source="同行价", party_total=13940
        ),
    )
    diff = diff_days(BASELINE, sent.days)
    text = handoff_text(plan, sent, diff)
    lines = text.splitlines()

    assert lines[:6] == [
        "方案 PL-Ab12Cd34 v2",
        "基线线路 RT-1021 伊犁北疆环线",
        "出发 10月15日—10月17日",
        "人数 2大1小",
        "参考价 同业价 成人 4980 / 市场价 成人 5680.5（同行价）",
        "定制差价待计调报价",
    ]
    # Nothing is marked up: it is pasted into the agency's own chat window.
    assert "#" not in text and "*" not in text and "|" not in text
    assert "【待计调确认】客人要换成湖景房，待计调确认差价" in lines
    assert "接机后入住四钻酒店" in lines
    assert f"相对上一版：{summarize(diff, parent_version=2)}" in lines
    # The requests again at the end, where the 计调 answers them.
    assert lines[-2:] == ["待计调确认事项：", "第2天 赛里木湖 客人要换成湖景房，待计调确认差价"]


def test_the_handoff_of_a_baseline_without_a_quote_says_so():
    plan = Plan(
        plan_id="PL-Ab12Cd34",
        session_id="session-of-the-plans-suite",
        user_id="advisor-of-the-plans-suite",
        route_id=1021,
        route_name="伊犁北疆环线",
    )
    first = version(BASELINE, parent=None)
    text = handoff_text(plan, first, diff_days(None, first.days))
    lines = text.splitlines()
    assert lines[0] == "方案 PL-Ab12Cd34 v1"
    assert "参考价待查" in lines
    # Neither line is written when the model sent neither fact.
    assert not [line for line in lines if line.startswith(("出发 ", "人数 "))]
    assert "相对上一版：基线原样" in lines
    assert "待计调确认事项：" not in text


def test_a_plan_id_reads_back_and_a_share_token_is_its_own():
    minted = {new_plan_id() for _ in range(50)}
    assert len(minted) == 50
    assert all(PLAN_ID.match(plan_id) for plan_id in minted)
    tokens = {new_share_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{10,}", token) for token in tokens)
