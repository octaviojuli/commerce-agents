# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``api/review.py`` over two conversations written the way the host writes them: the advisor's
text, an assistant message with its ``tool_use`` blocks, the user message of ``tool_result``
blocks after it, and the shapes a report has to survive — a thinking block, a block type it does
not know, a message that is neither side of the conversation. The result texts are the real ones:
``search_result_text`` builds the zero-result and the relaxed search, and each refusal is
formatted from the template its own module defines."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from commerce_common.memory import JsonFileMemoryStore
from commerce_common.types import MemoryCategory, MemoryFact
from demo_common.host import append_user_turn
from shopping_agent import Product
from shopping_agent.executor import ShoppingToolExecutor
from shopping_agent.serialization import search_result_text
from tour.api.plans import (
    REQUEST_NOTE,
    Plan,
    PlanDay,
    PlanVersion,
    diff_days,
    mark_requests,
    new_share_token,
)
from tour.api.review import (
    HOLD_ROUTE_FIRST,
    MEMORY_FILE,
    REFUSE_ERP,
    REFUSE_NO_CHANGE,
    REFUSE_NOT_CONFIGURED,
    REFUSE_SHORTLIST,
    SAID_APP,
    SAID_DAYS,
    SAID_HOLD,
    SESSIONS_FILE,
    candidates,
    read_sessions,
    render,
    review_sessions,
)
from tour.api.shortlist import NOT_PRESENTED
from tour.api.store import SqliteSessionStore
from tour.api.tour_backend import TourToolExecutor

ADVISOR = "advisor-of-the-review-suite"
OTHER = "advisor-of-the-review-suite-2"
# A destination no rule in tag-rules.json names, so it is both a zero-result search and a
# candidate word; 伊犁 has a rule and is neither.
UNKNOWN_PLACE = "塞舌尔"
ROUTE = "RT-1021"
ROUTE_NAME = "伊犁北疆环线"
# The 定制方案 the first conversation builds on that 线路, in the two versions the two
# ``present_itinerary`` calls sent. The 待计调确认 day stands in both, which is one candidate
# for the product staff and not two.
PLAN = "PL-abcd1234"
PLAN_TITLE = f"{ROUTE_NAME} 定制方案"
REQUEST_DAY = PlanDay(
    label="第 2 天 · 乌鲁木齐—赛里木湖", note=f"环湖看落日。客人要湖景房，{REQUEST_NOTE}。"
)
FIRST_DAYS = [PlanDay(label="第 1 天 · 乌鲁木齐集合", note="接机入住，晚上发行程册。"), REQUEST_DAY]
SECOND_DAYS = [
    PlanDay(label="第 1 天 · 乌鲁木齐集合", note="接机入住，客人航班晚点，接机改到夜里。"),
    REQUEST_DAY,
]


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant(text: str, *calls: tuple[str, str, dict]) -> dict:
    blocks: list[dict] = [{"type": "text", "text": text}]
    blocks += [
        {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
        for call_id, name, tool_input in calls
    ]
    return {"role": "assistant", "content": blocks}


def results(*rows: tuple[str, str, bool]) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": is_error}
            for call_id, text, is_error in rows
        ],
    }


def search(destination: str, **attributes: str) -> dict:
    return {
        "query": destination,
        "filters": {"attributes": {"destination": destination, **attributes}},
    }


def route(match: str, days: str) -> Product:
    return Product(
        product_id=ROUTE,
        title="伊犁北疆环线",
        price=4980,
        attributes={"match": match, "days": days, "destination": "伊犁"},
    )


def not_offered(detail: str) -> str:
    return ShoppingToolExecutor.not_offered_text.format(detail=detail)


def itinerary(days: list[PlanDay], plan_id: str | None = None) -> dict:
    """One ``present_itinerary`` call as the model sends it: the whole plan every time, and
    the plan's own id only on a call that revises one."""
    call = {
        "title": PLAN_TITLE,
        "route_id": ROUTE,
        "days": [{"label": day.label, "note": day.note} for day in days],
    }
    return call if plan_id is None else {**call, "plan_id": plan_id}


def store_plan(store: SqliteSessionStore, session_id: str) -> None:
    """The plan those two calls left in the plan tables, stored the way the backend stores
    one: the days marked from what their notes say, each version read against the one before
    it."""
    store.create_plan(
        Plan(
            plan_id=PLAN,
            session_id=session_id,
            user_id=ADVISOR,
            route_id=1021,
            route_name=ROUTE_NAME,
        )
    )
    parent: list[PlanDay] | None = None
    for number, written in ((1, FIRST_DAYS), (2, SECOND_DAYS)):
        days = mark_requests(written)
        store.add_version(
            PlanVersion(
                plan_id=PLAN,
                version=number,
                parent_version=None if number == 1 else number - 1,
                title=PLAN_TITLE,
                days=days,
                share_token=new_share_token(),
            ),
            diff_days(parent, days),
        )
        parent = days


@pytest.fixture
def store(tmp_path) -> SqliteSessionStore:
    """Two conversations in a fresh state directory: one advisor searching, one whose calls the
    ERP and the gates refused."""
    store = SqliteSessionStore(tmp_path / SESSIONS_FILE)
    first = store.start(ADVISOR)
    first.messages += [
        user(f"客人问{UNKNOWN_PLACE}有没有团"),
        assistant("我查一下。", ("t1", "search_products", search(UNKNOWN_PLACE, adults="2"))),
        results(("t1", search_result_text(UNKNOWN_PLACE, []), False)),
        assistant(f"{UNKNOWN_PLACE}目前没有在售线路。"),
        user("那看伊犁，10月中旬两位，8天左右"),
        assistant(
            "好的。",
            ("t2", "search_products", search("伊犁", days_min="7", days_max="9", adults="2")),
        ),
        results(("t2", search_result_text("伊犁", [route("adjacent_date", "10")]), False)),
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "先看这条线的团期"},
                {"type": "tool_use", "id": "t3", "name": "get_product_details", "input": {}},
            ],
        },
        results(("t3", TourToolExecutor.route_first_text.format(product_id=ROUTE), False)),
        assistant("已为客人锁位，10月17日出发，共 8 天。"),
        user("就这条线给客人做个定制方案"),
        assistant("好的。", ("t9", "present_itinerary", itinerary(FIRST_DAYS))),
        results(("t9", ShoppingToolExecutor.displayed_text, False)),
        assistant("已出 v1，逐日行程按行程附件摆好了。"),
        user("第一天接机改到夜里，其余不动"),
        assistant("好的。", ("t10", "present_itinerary", itinerary(SECOND_DAYS, PLAN))),
        results(("t10", ShoppingToolExecutor.displayed_text, False)),
        assistant("已出 v2，改了第一天。"),
    ]
    store.save(first)
    store_plan(store, first.session_id)
    second = store.start(OTHER)
    second.pending_app_events.append("客人已在分享页选定团期 DP-3008")
    append_user_turn(second, "帮客人占两个位", "客人的动作")
    second.messages += [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "客人在 App 里也能看到这个团期。"},
                {"type": "image", "source": {"type": "base64", "data": "x"}},
                {"type": "tool_use", "id": "t4", "name": "add_to_cart", "input": {}},
            ],
        },
        results(
            ("t4", not_offered("未配置下单客户（TOUR_ERP_CUSTOMER_CODE）"), True),
            ("t5", "结果对不上任何调用", False),
        ),
        assistant(
            "那我先发个清单。", ("t6", "present_shortlist", {}), ("t7", "remove_from_cart", {})
        ),
        results(
            ("t6", NOT_PRESENTED, True),
            ("t7", not_offered("在对话里取消占位（ERP 没有取消接口）"), True),
        ),
        assistant("再试一次。", ("t8", "add_to_cart", {})),
        results(("t8", TourToolExecutor.erp_rule_text.format(detail="团期已停售"), True)),
        {"role": "assistant", "content": None},
        {"role": "system", "content": "既不是顾问也不是助手的消息"},
    ]
    store.save(second)
    return store


@pytest.fixture
def memory_path(tmp_path):
    """The memory file beside the sessions: two facts for one advisor, a purge for the other."""
    store = JsonFileMemoryStore(tmp_path / MEMORY_FILE)
    facts = [
        MemoryFact(key="常带团目的地", value="伊犁、南疆", category=MemoryCategory.CONTEXT),
        MemoryFact(
            key="报价习惯", value="先报同业价再报市场价", category=MemoryCategory.PREFERENCE
        ),
    ]
    asyncio.run(store.upsert_facts(ADVISOR, facts))
    asyncio.run(store.upsert_facts(OTHER, [MemoryFact(key="口头习惯", value="叫客人老板")]))
    asyncio.run(store.clear(OTHER))
    return tmp_path / MEMORY_FILE


@pytest.fixture
def review(store, memory_path):
    return review_sessions(read_sessions(store), memory_path=memory_path, plan_store=store)


def test_the_overview_counts_sessions_turns_and_advisors(review):
    assert len(review.sessions) == 2
    # Five advisor messages: four in the first conversation, two of them the plan's, and the
    # one the app-event note rides in front of in the second.
    assert review.turns == 5
    assert review.users == [ADVISOR, OTHER]
    assert review.period == (datetime.now(UTC).date(), datetime.now(UTC).date())


def test_every_search_carries_its_attributes_and_its_result_count(review):
    empty, relaxed = review.searches
    assert empty.destination == UNKNOWN_PLACE
    assert empty.attributes == {"destination": UNKNOWN_PLACE, "adults": "2"}
    assert empty.count == 0 and not empty.relaxed_only
    assert relaxed.destination == "伊犁"
    assert relaxed.conditions == "days_min=7、days_max=9、adults=2"
    assert relaxed.count == 1
    assert relaxed.matches == ("adjacent_date",) and relaxed.relaxed_only


def test_the_plans_built_in_the_conversations(review):
    first = next(session for session in review.sessions if session.user_id == ADVISOR)
    assert [
        (plan.session_id, plan.plan_id, plan.route, plan.versions) for plan in review.plans
    ] == [(first.session_id, PLAN, f"{ROUTE} {ROUTE_NAME}", 2)]


def test_a_day_that_waits_on_the_ji_diao_is_one_candidate_however_often_it_is_sent(review):
    # The day stands unchanged in both versions, so the product staff read it once.
    assert [(day.plan_id, day.route, day.label, day.note) for day in review.requests] == [
        (PLAN, f"{ROUTE} {ROUTE_NAME}", REQUEST_DAY.label, REQUEST_DAY.note)
    ]
    report = render(review)
    assert PLAN in report and REQUEST_DAY.note in report


def test_without_the_plan_tables_the_section_is_empty_rather_than_absent(store, memory_path):
    review = review_sessions(read_sessions(store), memory_path=memory_path)
    assert review.plans == [] and review.requests == []
    assert "## 定制方案" in render(review)


def test_the_gates_and_the_refusals_are_counted_with_an_example(review):
    assert {label: len(examples) for label, examples in review.refusals.items()} == {
        HOLD_ROUTE_FIRST: 1,
        REFUSE_NOT_CONFIGURED: 1,
        REFUSE_SHORTLIST: 1,
        REFUSE_NO_CHANGE: 1,
        REFUSE_ERP: 1,
    }
    assert ROUTE in review.refusals[HOLD_ROUTE_FIRST][0]
    assert "团期已停售" in review.refusals[REFUSE_ERP][0]


def test_the_shapes_a_transcript_may_hold_that_the_report_cannot_read(review):
    # The image block, the result no call in the transcript owns, the message with no content,
    # and the message that is neither side of the conversation.
    assert review.unparsed == 4


def test_the_facts_each_advisor_has_and_the_purge_the_file_records(review):
    remembered = {entry.user_id: entry for entry in review.remembered}
    assert len(remembered[ADVISOR].facts) == 2
    assert remembered[ADVISOR].purges == 0
    assert remembered[OTHER].facts == () and remembered[OTHER].purges == 1
    assert review.memory_note is None
    assert "无法判断" in render(review)


def test_a_missing_memory_file_is_said_rather_than_guessed(store, tmp_path):
    review = review_sessions(read_sessions(store), memory_path=tmp_path / "nowhere.json")
    assert review.remembered == [] and "nowhere.json" in (review.memory_note or "")


def test_the_words_for_the_vocabulary_to_review(review):
    unknown, empty = candidates(review.searches)
    assert unknown == {UNKNOWN_PLACE: 1}
    assert empty == {UNKNOWN_PLACE: 1}
    report = render(review)
    assert UNKNOWN_PLACE in report and "尚未写入" in report


def test_what_the_assistant_wrote_that_is_worth_a_second_look(review):
    assert {label: len(examples) for label, examples in review.behaviour.items()} == {
        SAID_APP: 1,
        SAID_HOLD: 1,
        SAID_DAYS: 1,
    }
    assert "锁位" in review.behaviour[SAID_HOLD][0]
    assert "8 天" in review.behaviour[SAID_DAYS][0]


def test_since_keeps_the_sessions_updated_on_or_after_that_day(store):
    today = datetime.now(UTC).date()
    assert len(read_sessions(store, since=today)) == 2
    assert read_sessions(store, since=today + timedelta(days=1)) == []
    assert len(read_sessions(store, since=today - timedelta(days=1))) == 2


def test_the_report_is_the_seven_sections_in_order(review):
    report = render(review)
    headings = [line for line in report.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 概览",
        "## 搜索",
        "## 定制方案",
        "## 门禁与拒绝",
        "## 记忆",
        "## 待审词表",
        "## 模型行为",
    ]
    assert "本报告只统计，不改动任何数据" in report


def test_the_memory_file_is_read_as_json_file_memory_store_wrote_it(memory_path):
    # The report reads the file's own layout; this is the shape it relies on.
    raw = json.loads(memory_path.read_text(encoding="utf-8"))
    assert raw["version"] == 2 and set(raw) == {"version", "facts", "purges"}
