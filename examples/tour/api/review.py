# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What a period of the workbench's conversations came to, as numbers a person reads: the
functions behind ``scripts/review_sessions.py``, which writes nothing. The report says what the
advisors searched for, where the system fell short — a search with no results, a call a gate or
the ERP refused, an answer that reads wrong — what the memory file holds, and which destination
words ``data/tag-rules.json`` has no rule for. That last list is one to review; a change to the
vocabulary is made by hand afterwards.

A transcript is read the way the model saw it: the advisor's turn is a user message with text,
an assistant message carries its ``tool_use`` blocks, and the user message after it carries one
``tool_result`` per call, whose text is the header and fenced payload
``shopping_agent.serialization`` built — which is where a search's result count and each result's
``match`` are read from. A refusal is recognised by the literal part of the template its own
module defines, so no wording is repeated here. A shape none of that describes is skipped and
counted, so a transcript another version of the host wrote reports as partly unread, not as a
failure.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from commerce_common.types import MemoryFact
from shopping_agent.executor import ShoppingToolExecutor
from shopping_agent.fencing import STOREFRONT_FENCE
from shopping_agent.serialization import SEARCH_EMPTY_HEADER

from .shortlist import NOT_PRESENTED, NOTHING_LEFT
from .store import SqliteSessionStore, display_text
from .tags import DATA_DIR, load_rules
from .tour_backend import TourToolExecutor

# The two files ``api/main.py`` builds its stores on, inside TOUR_STATE_DIR.
SESSIONS_FILE = "sessions.sqlite"
MEMORY_FILE = "memory-store.json"

SEARCH_TOOL = "search_products"
ADD_TOOL = "add_to_cart"
# The attributes ``TourBackend.search_products`` reads a search off: the destination, then the
# window, the day count, the party and the preferences, which is the order they are printed in.
SEARCH_ATTRIBUTES = ("destination", "depart_from", "depart_to", "days_min", "days_max", "adults",
                     "children", "child_ages", "no_shopping", "hotel_level", "departure_city",
                     "family")  # fmt: skip
NO_DESTINATION = "（未写目的地）"
EXCERPT_CHARS = 70

HOLD_ROUTE_FIRST = "route_first 门禁"
REFUSE_SHORTLIST = "分享清单拒绝"
REFUSE_NOT_CONFIGURED = "未配置客户/门店"
REFUSE_NO_CHANGE = "取消改单"
REFUSE_NOT_OFFERED = "其他不提供的请求"
REFUSE_UNAVAILABLE = "团期不可下单（Unavailable）"
REFUSE_ERP = "ERP 拒绝（Nothing changed）"
TOOL_ERROR = "工具错误"
SAID_APP = "提到 App"
SAID_HOLD = "说锁位但本轮没有 add_to_cart"
SAID_DAYS = "疑似自己算天数"


def _literal(template: str) -> str:
    """The longest literal run of a ``str.format`` template: what a stored result text is
    matched on, so each wording stays defined only where it is written."""
    return max(re.split(r"\{[^{}]*\}", template), key=len).strip()


# As much of the 详情 ``TourBackend`` raises ``NotOffered`` with as separates its two cases.
_NOT_CONFIGURED = "未配置"
_NO_CHANGE = "在对话里"
_NOT_OFFERED = _literal(ShoppingToolExecutor.not_offered_text)
_MARKERS = (
    (_literal(TourToolExecutor.route_first_text), HOLD_ROUTE_FIRST),
    (NOT_PRESENTED, REFUSE_SHORTLIST),
    (NOTHING_LEFT, REFUSE_SHORTLIST),
    (_literal(ShoppingToolExecutor.sold_out_text), REFUSE_UNAVAILABLE),
    (_literal(TourToolExecutor.erp_rule_text), REFUSE_ERP),
)

_PRODUCT_ID = re.compile(r'"product_id":\s*"([^"]+)"')
_MATCH = re.compile(r'"match":\s*"(\w+)"')
_DAYS = re.compile(r'"days":\s*"(\d+)"')
_DATE_WORD = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{2}-\d{2}")
_DAY_COUNT = re.compile(r"(\d{1,2})\s*天")
_SENTENCE = re.compile(r"[^。！？\n]+")


def classify(text: str, is_error: bool) -> str | None:
    """Which refusal one tool result is, or None when the call went through."""
    if _NOT_OFFERED in text:
        if _NOT_CONFIGURED in text:
            return REFUSE_NOT_CONFIGURED
        return REFUSE_NO_CHANGE if _NO_CHANGE in text else REFUSE_NOT_OFFERED
    for marker, label in _MARKERS:
        if marker in text:
            return label
    return TOOL_ERROR if is_error else None


def fenced_payload(text: str) -> Any:
    """The payload inside a result's fence, or None when it is absent or was truncated."""
    start, end = text.find(STOREFRONT_FENCE.open), text.rfind(STOREFRONT_FENCE.close)
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start + len(STOREFRONT_FENCE.open) : end].strip())
    except ValueError:
        return None


def result_count(text: str) -> int | None:
    """How many results a ``search_products`` result carries: the payload's own count, else the
    product ids in it, else zero for the empty-result header. None reads as unparsed."""
    payload = fenced_payload(text)
    if isinstance(payload, dict) and isinstance(payload.get("result_count"), int):
        return int(payload["result_count"])
    if ids := set(_PRODUCT_ID.findall(text)):
        return len(ids)
    return 0 if SEARCH_EMPTY_HEADER in text else None


@dataclass(frozen=True)
class Session:
    """One stored conversation as the report reads it."""

    session_id: str
    user_id: str
    updated_at: datetime
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class Search:
    """One ``search_products`` call and what came back."""

    session_id: str
    query: str
    attributes: dict[str, str]
    count: int | None
    matches: tuple[str, ...]

    @property
    def destination(self) -> str:
        return (self.attributes.get("destination") or self.query or "").strip() or NO_DESTINATION

    @property
    def conditions(self) -> str:
        """What the advisor stated besides the destination, as the model wrote it."""
        stated = [f"{k}={self.attributes[k]}" for k in SEARCH_ATTRIBUTES[1:] if self.attributes.get(k)]  # fmt: skip
        return "、".join(stated) or "—"

    @property
    def relaxed_only(self) -> bool:
        """Results, but not one the backend called an exact match."""
        return bool(self.count) and bool(self.matches) and "exact" not in self.matches


@dataclass(frozen=True)
class Remembered:
    """One advisor's facts in the memory file, and how many times they were purged."""

    user_id: str
    facts: tuple[MemoryFact, ...]
    purges: int


@dataclass
class Review:
    """The whole report as data, so the tests read numbers rather than Markdown. ``refusals``
    and ``behaviour`` are one label to its examples, newest last."""

    since: date | None = None
    sessions: list[Session] = field(default_factory=list)
    turns: int = 0
    searches: list[Search] = field(default_factory=list)
    refusals: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    behaviour: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    remembered: list[Remembered] = field(default_factory=list)
    memory_note: str | None = None
    unparsed: int = 0

    @property
    def users(self) -> list[str]:
        return sorted({session.user_id for session in self.sessions})

    @property
    def period(self) -> tuple[date, date] | None:
        days = sorted(session.updated_at.date() for session in self.sessions)
        return (days[0], days[-1]) if days else None


def read_sessions(store: SqliteSessionStore, since: date | None = None) -> list[Session]:
    """Every advisor's conversations in the store, newest first; those last updated on or after
    ``since`` when it is given, on the UTC dates the store stamps its rows with."""
    sessions = []
    for user_id in store.user_ids():
        for row in store.summaries(user_id):
            if since is None or row.updated_at.date() >= since:
                messages = store.transcript(row.session_id)
                sessions.append(Session(row.session_id, user_id, row.updated_at, messages))
    return sessions


@dataclass
class _Turn:
    """One exchange: what the assistant said in it and which tools it called."""

    texts: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


def _note(notes: dict[str, list[str]], label: str, session_id: str, text: str) -> None:
    notes[label].append(f"{' '.join(text.split())[:EXCERPT_CHARS]}（{session_id[:8]}）")


def _result_text(block: dict[str, Any]) -> str | None:
    """A result's text, whether it was stored as a string or as content blocks."""
    content = block.get("content")
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict)).strip()
    return content if isinstance(content, str) else None


def _scan(session: Session, review: Review) -> None:
    """One session's messages: the advisor's turns, every call and its result, and the assistant
    text of each turn, which is read once the whole session's records are known."""
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    turns = [_Turn()]
    days_seen: set[int] = set()
    for message in session.messages:
        role, content = message.get("role"), message.get("content")
        if role not in ("user", "assistant"):
            review.unparsed += 1
            continue
        if role == "user" and display_text(message):
            review.turns += 1
            turns.append(_Turn())
        if not isinstance(content, str | list):
            review.unparsed += 1
            continue
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for block in blocks:
            kind = block.get("type") if isinstance(block, dict) else None
            if kind == "text" and role == "assistant" and isinstance(block.get("text"), str):
                turns[-1].texts.append(block["text"])
            elif kind == "tool_use":
                pending[str(block.get("id"))] = (str(block.get("name")), block.get("input") or {})
                turns[-1].tools.append(str(block.get("name")))
            elif kind == "tool_result":
                text = _result_text(block)
                call = pending.pop(str(block.get("tool_use_id")), None)
                if text is None or call is None:
                    review.unparsed += 1
                    continue
                days_seen.update(int(days) for days in _DAYS.findall(text))
                _read_result(session, call, bool(block.get("is_error")), text, review)
            elif kind not in ("text", "thinking", "redacted_thinking"):
                review.unparsed += 1
    _read_behaviour(session, turns, days_seen, review)


def _read_result(
    session: Session, call: tuple[str, dict[str, Any]], is_error: bool, text: str, review: Review
) -> None:
    """One finished call: the refusal it was, and, for a search, what the advisor asked and how
    many 线路 came back."""
    name, tool_input = call
    if label := classify(text, is_error):
        _note(review.refusals, label, session.session_id, text)
    if name != SEARCH_TOOL:
        return
    filters = tool_input.get("filters")
    raw = (filters.get("attributes") if isinstance(filters, dict) else None) or {}
    payload = fenced_payload(text)
    results = payload.get("results") if isinstance(payload, dict) else None
    matches = (
        [str((row.get("attributes") or {}).get("match", "")) for row in results]
        if isinstance(results, list)
        else _MATCH.findall(text)
    )
    review.searches.append(
        Search(
            session_id=session.session_id,
            query=str(tool_input.get("query") or ""),
            attributes={str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {},
            count=result_count(text),
            matches=tuple(match for match in matches if match),
        )
    )


def _read_behaviour(session: Session, turns: list[_Turn], days: set[int], review: Review) -> None:
    """The three things in the assistant's own words worth a second look. The day count is a
    guess and is marked as one: a sentence carrying dates and a 天 the records do not."""
    for turn in turns:
        for text in turn.texts:
            if "App" in text:
                _note(review.behaviour, SAID_APP, session.session_id, text)
            if "锁位" in text and ADD_TOOL not in turn.tools:
                _note(review.behaviour, SAID_HOLD, session.session_id, text)
            for sentence in _SENTENCE.findall(text):
                said = {int(count) for count in _DAY_COUNT.findall(sentence)}
                if days and said - days and _DATE_WORD.search(sentence):
                    _note(review.behaviour, SAID_DAYS, session.session_id, sentence)


def read_memory(path: Path) -> tuple[list[Remembered], str | None]:
    """The memory file as ``JsonFileMemoryStore`` wrote it: ``facts`` keyed by subject and
    ``purges`` counted per subject, a subject that was purged clean included. A fact holds only
    its current value, so a rewrite or one deletion leaves no trace there; a purge leaves its
    counter."""
    if not path.exists():
        return [], f"{path.name} 不存在（这段时间没有写入过记忆）。"
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        return [], f"{path.name} 读不出 JSON，无法统计。"
    facts, purges = raw.get("facts") or {}, raw.get("purges") or {}
    remembered = [
        Remembered(
            user_id=str(subject),
            facts=tuple(
                MemoryFact.model_validate(row) for row in (facts.get(subject) or {}).values()
            ),
            purges=int(purges.get(subject) or 0),
        )
        for subject in sorted(set(facts) | set(purges))
    ]
    return remembered, None


def candidates(searches: list[Search], data_dir: Path = DATA_DIR) -> tuple[Counter, Counter]:
    """The destination words to put in front of the product staff: those no ``destination`` rule
    of ``tag-rules.json`` matches, and those a search returned nothing for."""
    rules = load_rules(data_dir).get("destination")
    unknown: Counter = Counter()
    empty: Counter = Counter()
    for search in searches:
        word = (search.attributes.get("destination") or "").strip()
        if not word:
            continue
        if rules is None or rules.rank(word) is None:
            unknown[word] += 1
        if search.count == 0:
            empty[word] += 1
    return unknown, empty


def review_sessions(
    sessions: list[Session], memory_path: Path | None = None, since: date | None = None
) -> Review:
    """Every number the report prints, read off the transcripts and the memory file."""
    review = Review(since=since, sessions=list(sessions))
    for session in sessions:
        _scan(session, review)
    if memory_path is not None:
        review.remembered, review.memory_note = read_memory(memory_path)
    return review


SEARCH_HEADERS = ("目的地", "其他条件", "结果数", "匹配", "会话")
REFUSAL_HEADERS = ("类别", "次数", "例子")
BEHAVIOUR_HEADERS = ("现象", "次数", "例子")


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """A Markdown table, or 无。 for an empty one. A cell is model or ERP text, so the pipe it
    may carry is escaped rather than ending the column."""
    if not rows:
        return ["无。", ""]
    head = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    body = ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return head + body + [""]


def _search_rows(searches: list[Search]) -> list[tuple[str, ...]]:
    """One row per call, grouped by destination."""
    return [
        (s.destination, s.conditions, str(s.count), "、".join(s.matches) or "—", s.session_id[:8])
        for s in sorted(searches, key=lambda s: (s.destination, s.session_id))
    ]


def _example_rows(notes: dict[str, list[str]]) -> list[tuple[str, ...]]:
    ordered = sorted(notes.items(), key=lambda item: (-len(item[1]), item[0]))
    return [(label, str(len(examples)), examples[0]) for label, examples in ordered]


def _memory_lines(review: Review) -> list[str]:
    lines = [review.memory_note, ""] if review.memory_note else []
    for entry in review.remembered:
        facts = [f"- {fact.key}：{fact.value}（{fact.category.value}）" for fact in entry.facts]
        lines += [
            f"### {entry.user_id}（{len(entry.facts)} 条，整体清除 {entry.purges} 次）",
            "",
            *(facts or ["无。"]),
            "",
        ]
    return lines + ["是否有事实被改写或单条删除：无法判断——记忆文件只存每条事实的当前值。", ""]


def render(review: Review) -> str:
    """The report as Markdown, in the six sections the agency's staff read."""
    span = f"{review.period[0]}—{review.period[1]}" if review.period else "无会话"
    unknown, empty = candidates(review.searches)
    lines = [
        f"# 会话复盘（{span}）",
        "",
        "本报告只统计，不改动任何数据。词表与提示词的调整由人工审阅后另行提交。",
        "",
        "## 概览",
        "",
        f"- 会话：{len(review.sessions)}",
        f"- 顾问提问轮次：{review.turns}",
        f"- 顾问：{len(review.users)}（{'、'.join(review.users) or '—'}）",
        f"- 覆盖日期（UTC，会话最后更新的那天）：{span}"
        + (f"，--since {review.since}" if review.since else ""),
        f"- 未解析的消息片段：{review.unparsed}",
        "",
        "## 搜索",
        "",
        f"共 {len(review.searches)} 次 {SEARCH_TOOL} 调用。",
        "",
    ]
    for title, picked in (
        ("全部搜索", review.searches),
        ("零结果搜索", [s for s in review.searches if s.count == 0]),
        ("只靠放宽才有结果的搜索", [s for s in review.searches if s.relaxed_only]),
    ):
        lines += [f"### {title}", "", *_table(SEARCH_HEADERS, _search_rows(picked))]
    lines += ["## 门禁与拒绝", "", *_table(REFUSAL_HEADERS, _example_rows(review.refusals))]
    lines += ["## 记忆", "", *_memory_lines(review)]
    lines += ["## 待审词表", "", "以下是候选词，尚未写入 `data/tag-rules.json`。", ""]
    for title, counted in (
        ("tag-rules.json 没有规则的目的地词", unknown),
        ("零结果的目的地词", empty),
    ):
        rows = [(word, str(times)) for word, times in counted.most_common()]
        lines += [f"### {title}", "", *_table(("词", "次数"), rows)]
    return "\n".join(
        lines + ["## 模型行为", "", *_table(BEHAVIOUR_HEADERS, _example_rows(review.behaviour))]
    )
