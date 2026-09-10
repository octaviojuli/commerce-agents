# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""``SqliteSessionStore`` and the two history routes over it: what a fresh store finds in a
file another one wrote (which is the restart the advisor sees), the compare-and-set the base
class runs on ``write_state``, the append and the rewrite of a transcript, one advisor's
sessions apart from another's, and the display view the routes answer with. The memory file
is here too, because it lives in the same state directory."""

import json

import pytest

from commerce_common.memory import JsonFileMemoryStore
from commerce_common.types import MemoryCategory, MemoryFact
from demo_common import SESSION_HEADER, SessionConflictError
from demo_common.host import append_user_turn
from shopping_agent import Product, ShoppingSessionState
from tour.api.store import UNTITLED, SessionSummary, SqliteSessionStore, display_messages

ADVISOR = "advisor-of-the-store-suite"
OTHER = "advisor-of-the-store-suite-2"
# The advisor's first line of a conversation, longer than a title.
OPENING = "10月中旬两位客人去伊犁，8天左右，想住四钻，不要购物店，从上海出发，预算一人五千以内"


def departure(product_id: str) -> Product:
    return Product(product_id=product_id, title=f"伊犁北疆环线 {product_id}", price=4980)


def contents(messages: list[dict]) -> list:
    return [message["content"] for message in messages]


@pytest.fixture
def store(tmp_path) -> SqliteSessionStore:
    return SqliteSessionStore(tmp_path / "sessions.sqlite")


@pytest.fixture
def reopen(tmp_path):
    """Returns a builder for a second store over the same file: what the next process sees."""
    return lambda: SqliteSessionStore(tmp_path / "sessions.sqlite")


def test_the_file_and_its_parent_are_created_at_construction(tmp_path):
    store = SqliteSessionStore(tmp_path / "nested" / "sessions.sqlite")
    assert (tmp_path / "nested" / "sessions.sqlite").exists()
    session_id = store.start(ADVISOR).session_id
    assert SqliteSessionStore(tmp_path / "nested" / "sessions.sqlite").require(session_id)


def test_a_started_session_round_trips_through_a_fresh_store(store, reopen):
    record = store.start(ADVISOR)
    record.state.remember_products([departure("DP-3008")])
    record.pending_app_events.append("客人已在分享页选定团期 DP-3008")
    record.messages.append({"role": "user", "content": OPENING})
    record.messages.append({"role": "assistant", "content": [{"type": "text", "text": "好的"}]})
    store.save(record)

    loaded = reopen().require(record.session_id)
    assert (loaded.session_id, loaded.user_id) == (record.session_id, ADVISOR)
    assert loaded.state == record.state
    assert isinstance(loaded.state, ShoppingSessionState)
    assert loaded.messages == record.messages
    assert loaded.pending_app_events == ["客人已在分享页选定团期 DP-3008"]
    # The loaded record knows what the store holds, so its next save writes the difference.
    assert (loaded.version, loaded.stored_messages) == (record.version, 2)


def test_an_unknown_session_has_no_state_and_no_messages(store):
    assert store.read_state("not-a-session") is None
    assert store.transcript("not-a-session") == []


def test_a_write_behind_the_stored_version_is_a_conflict(store, reopen):
    record = store.start(ADVISOR)
    stale = reopen().require(record.session_id)

    record.state.remember_products([departure("DP-3008")])
    store.save(record)  # the stored version is now ahead of what stale loaded

    stale.state.remember_products([departure("DP-4001")])
    with pytest.raises(SessionConflictError):
        reopen().save(stale)
    # Nothing of the losing write landed: the state document is still the winner's.
    assert reopen().require(record.session_id).state == record.state


def test_messages_append_and_a_compaction_rewrites_the_transcript(store, reopen):
    session_id = store.start(ADVISOR).session_id
    store.write_messages(session_id, [{"role": "user", "content": "一"}], 0)
    store.write_messages(session_id, [{"role": "assistant", "content": "二"}], 1)
    assert contents(reopen().read_messages(session_id)) == ["一", "二"]

    # A start inside the transcript replaces the tail; 0 replaces the whole transcript.
    store.write_messages(session_id, [{"role": "assistant", "content": "三"}], 1)
    assert contents(store.read_messages(session_id)) == ["一", "三"]
    store.write_messages(session_id, [{"role": "user", "content": "四"}], 0)
    assert contents(reopen().read_messages(session_id)) == ["四"]


def test_a_reset_session_is_gone_from_the_file(store, reopen):
    record = store.start(ADVISOR)
    record.messages.append({"role": "user", "content": "一"})
    store.save(record)
    store.reset(record)
    assert reopen().read_state(record.session_id) is None
    assert reopen().transcript(record.session_id) == []


def test_a_users_sessions_are_newest_first_and_only_theirs(store, reopen):
    first = store.start(ADVISOR)
    second = store.start(ADVISOR)
    theirs = store.start(OTHER)

    assert reopen().session_ids_for_user(ADVISOR) == [second.session_id, first.session_id]
    first.messages.append({"role": "user", "content": "一"})
    store.save(first)  # a write moves it to the front
    assert reopen().session_ids_for_user(ADVISOR) == [first.session_id, second.session_id]
    assert reopen().session_ids_for_user(OTHER) == [theirs.session_id]
    assert reopen().session_ids_for_user("nobody") == []
    assert [record.user_id for record in reopen().sessions_for_user(OTHER)] == [OTHER]


def test_a_summary_is_titled_by_the_first_advisor_message_and_counts_the_view(store, reopen):
    empty = store.start(ADVISOR)
    said = store.start(ADVISOR)
    # A turn as the host writes one: the app-event note rides in front of the advisor's
    # message, and the model's tool call and its result are two more messages.
    said.pending_app_events.append("客人已在分享页选定团期 DP-3008。")
    append_user_turn(said, OPENING, "App events")
    said.messages += [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "search"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "找到三条线路"}]},
    ]
    store.save(said)

    summaries = reopen().summaries(ADVISOR)
    assert [summary.session_id for summary in summaries] == [said.session_id, empty.session_id]
    assert isinstance(summaries[0], SessionSummary)
    # Forty characters of the advisor's own words: the note and the tool exchange are not it.
    assert summaries[0].title == OPENING[:40]
    assert len(summaries[0].title) == 40
    assert summaries[0].message_count == 2
    assert summaries[0].updated_at.tzinfo is not None
    assert (summaries[1].title, summaries[1].message_count) == (UNTITLED, 0)
    assert reopen().summaries(OTHER) == []


def test_a_transcript_comes_back_as_stored_and_the_view_drops_the_tool_exchange(store):
    stored = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[App events since your last reply: 客人选定 DP-3008]"},
                {"type": "text", "text": "帮我出个报价"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "同业价 4980"},
                {"type": "tool_use", "id": "t1", "name": "present_products"},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        {"role": "assistant", "content": "市场价 5680"},
    ]
    session_id = store.start(ADVISOR).session_id
    store.write_messages(session_id, stored, 0)
    assert store.transcript(session_id) == stored
    assert display_messages(store.transcript(session_id)) == [
        {"role": "user", "text": "帮我出个报价"},
        {"role": "assistant", "text": "同业价 4980"},
        {"role": "assistant", "text": "市场价 5680"},
    ]


def test_what_the_file_holds_is_readable_json(store, tmp_path):
    """An advisor's own words are in this file, so it is JSON and Chinese as written: a
    deployment reading it with anything but this class gets the same records."""
    record = store.start(ADVISOR)
    record.messages.append({"role": "user", "content": "去伊犁"})
    store.save(record)
    _, document = store.read_state(record.session_id)
    assert set(document) == {"user_id", "state", "pending_app_events"}
    assert "去伊犁" in (tmp_path / "sessions.sqlite").read_bytes().decode("utf-8", "replace")


# -- the deployment's own stores and its two routes ---------------------------------------


def test_the_deployment_keeps_its_sessions_and_its_memory_in_the_state_directory(main):
    assert main.host.sessions is main.sessions
    assert isinstance(main.sessions, SqliteSessionStore)
    assert (main.STATE_DIR / "sessions.sqlite").exists()
    store = main.agent.memory.store
    assert isinstance(getattr(store, "inner", store), JsonFileMemoryStore)


async def test_a_facts_write_lands_in_the_state_directorys_memory_file(main):
    fact = MemoryFact(key="报价习惯", value="常给客人报四钻", category=MemoryCategory.PREFERENCE)
    await main.agent.memory.store.upsert_facts(ADVISOR, [fact])
    path = main.STATE_DIR / "memory-store.json"
    saved = json.loads(path.read_text(encoding="utf-8"))["facts"][ADVISOR]
    assert saved["报价习惯"]["value"] == "常给客人报四钻"
    # A fresh store over the same file is what the next process reads it with.
    assert [held.key for held in await JsonFileMemoryStore(path).get_facts(ADVISOR)] == ["报价习惯"]


def start(client, user_id: str) -> dict[str, str]:
    started = client.post("/api/session", json={"user_id": user_id}).json()
    return {SESSION_HEADER: started["session_id"]}


def test_a_turns_messages_land_in_the_file_and_the_next_process_continues_them(main, client):
    """What the client does across a restart: the turn's messages are saved, and a store
    built over the same file resumes the conversation the header names. The turn is driven
    through the store rather than the model, which the suite never calls."""
    session_id = start(client, ADVISOR)[SESSION_HEADER]
    record = main.host.sessions.require(session_id)
    append_user_turn(record, OPENING, "App events")
    record.messages.append({"role": "assistant", "content": [{"type": "text", "text": "好的"}]})
    main.host.sessions.save(record)

    restarted = SqliteSessionStore(main.STATE_DIR / "sessions.sqlite")
    resumed = restarted.require(session_id)
    assert contents(resumed.messages) == [OPENING, [{"type": "text", "text": "好的"}]]

    # The next turn appends to the transcript it loaded rather than starting one.
    append_user_turn(resumed, "换成11月", "App events")
    restarted.save(resumed)
    assert contents(main.host.sessions.require(session_id).messages)[-1] == "换成11月"
    assert len(main.host.sessions.require(session_id).messages) == 3


def test_the_history_list_is_the_callers_own_newest_first(main, client):
    older = start(client, ADVISOR)[SESSION_HEADER]
    record = main.host.sessions.require(older)
    append_user_turn(record, "帮客人看看9月的南疆", "App events")
    record.messages.append({"role": "assistant", "content": [{"type": "text", "text": "好的"}]})
    main.host.sessions.save(record)
    current = start(client, ADVISOR)[SESSION_HEADER]
    theirs = start(client, OTHER)

    listed = client.get("/api/sessions", headers={SESSION_HEADER: current}).json()["sessions"]
    assert [row["session_id"] for row in listed][:2] == [current, older]
    assert [row["current"] for row in listed][:2] == [True, False]
    assert theirs[SESSION_HEADER] not in {row["session_id"] for row in listed}
    assert len(listed) <= main.MAX_HISTORY
    assert listed[1] == {
        "session_id": older,
        "title": "帮客人看看9月的南疆",
        "updated_at": listed[1]["updated_at"],
        "message_count": 2,
        "current": False,
    }
    # The list is the same records the store summarises, dates included.
    assert listed[1]["updated_at"] == main.sessions.summaries(ADVISOR)[1].updated_at.isoformat()

    # Their own list holds theirs and not the caller's.
    mine = {row["session_id"] for row in listed}
    assert not mine & {
        row["session_id"] for row in client.get("/api/sessions", headers=theirs).json()["sessions"]
    }


def test_the_messages_route_answers_the_display_view_and_hides_another_advisors(main, client):
    mine = start(client, ADVISOR)
    record = main.host.sessions.require(mine[SESSION_HEADER])
    record.pending_app_events.append("客人已在分享页选定团期 DP-3008。")
    append_user_turn(record, "把这个团期发给客人", "App events")
    record.messages += [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "search"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "已经发过去了"}]},
    ]
    main.host.sessions.save(record)
    path = f"/api/sessions/{mine[SESSION_HEADER]}/messages"

    assert client.get(path, headers=mine).json() == {
        "session_id": mine[SESSION_HEADER],
        "messages": [
            {"role": "user", "text": "把这个团期发给客人"},
            {"role": "assistant", "text": "已经发过去了"},
        ],
    }
    assert client.get(path, headers=start(client, OTHER)).status_code == 404
    assert client.get("/api/sessions/nope/messages", headers=mine).status_code == 404
