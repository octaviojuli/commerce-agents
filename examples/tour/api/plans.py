# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""定制方案: what a custom plan is made of and the reading of one version against the one
before it. A plan takes one published 线路 as its baseline; every day-by-day card the model
sends is one immutable version of it, so a version is never edited and the advisor always
has the version they sent the customer. This module holds the records — ``Plan``, its
``PlanVersion``, the ``PlanDay`` lines and the ``ReferencePrice`` beside them — and the pure
functions over them: ``mark_requests`` for what the customer asked for that the 线路 does
not carry, ``diff_days`` for what one version changed, ``summarize`` for that diff in one
Chinese line, and ``handoff_text`` for the plain text the advisor copies to the agency's
计调. ``api/store.py`` is where a version is kept; nothing here reads or writes it.

A day is compared by what it says and not by where it sits: the label is normalised with
its 第N天 marker removed, so inserting a day in the middle changes one day and renumbers the
rest rather than changing all of them.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

# The phrase the model writes into a day's note for anything the customer wants that the
# baseline 线路 does not carry. It is the agency's own wording, and it is what makes a day a
# request rather than a rewrite of the itinerary.
REQUEST_NOTE = "待计调确认"

# The 第N天 marker a day label opens with, in the four shapes the model writes it: 第5天,
# 第 5 天, 第五天 (up to 第二十天), D5 and Day 5. Numbering is position, not content, so it is
# stripped before two days are compared and read back when a day is named in a summary.
DAY_MARKER = re.compile(
    r"^\s*(?:第\s*(?P<zh>[0-9〇零一二三四五六七八九十百]+)\s*天"
    r"|(?:day|d)\s*(?P<num>[0-9]+))\s*[：:、，,.。·\-—–]*\s*",
    re.IGNORECASE,
)

# The shape of a plan id, which ``new_plan_id`` mints against.
PLAN_ID = re.compile(r"^PL-[A-Za-z0-9]{8}$")

_ZH_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

# What separates a day's two compared fields in its key: a character no label or note holds.
_KEY_SEPARATOR = "\x00"


class ReferencePrice(BaseModel):
    """What the baseline 团期 was quoted at, carried on the version so the advisor reads the
    same figures back later. The 定制 difference is not here: only the agency's 计调 prices a
    custom plan."""

    tong_ye_adult: float | None = None
    market_adult: float | None = None
    quote_source: str
    party_total: float | None = None


class PlanDay(BaseModel):
    """One day of a version. ``request`` is set by :func:`mark_requests` and not by the
    model: a day is a request when its note says the 计调 has to confirm it."""

    label: str = Field(max_length=80)
    note: str = Field(max_length=300)
    request: bool = False


class PlanVersion(BaseModel):
    """One card the model sent, kept as it was sent. ``parent_version`` is the version it
    was written against, and ``None`` on the first, which is the baseline 线路 as published.
    ``share_token`` is this version's own link, so a customer who was sent v2 keeps reading
    v2 after the advisor sends v3."""

    plan_id: str
    version: int = Field(ge=1)
    parent_version: int | None = None
    title: str = Field(max_length=80)
    travel_dates: str | None = Field(default=None, max_length=60)
    party: str | None = Field(default=None, max_length=20)
    days: list[PlanDay] = Field(min_length=1, max_length=20)
    reference_price: ReferencePrice | None = None
    share_token: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DayDiff(BaseModel):
    """One day of a version read against the version before it. ``index`` is the day's place
    in the new version; a removed day has none, so its index is the place in the new version
    it would sit before, which is where the card draws it struck through. ``fields`` names
    what differs on a changed day and is empty on every other kind."""

    index: int
    kind: Literal["same", "changed", "added", "removed"]
    fields: list[Literal["label", "note"]] = Field(default_factory=list)
    label: str
    note: str


class Plan(BaseModel):
    """The plan itself: which conversation it was built in, which advisor's it is, and which
    published 线路 and 团期 it took as its baseline. ``erp_route_id`` is the 线路 the agency
    created in the ERP for this plan once the 计调 has priced it, so it is set later."""

    plan_id: str
    session_id: str
    user_id: str
    route_id: int
    route_name: str
    line_type: str | None = None
    departure_id: int | None = None
    erp_route_id: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def new_plan_id() -> str:
    """A plan id: ``PL-`` and eight characters that read back over the phone and paste into
    the ERP, which is why the hyphens and underscores of a URL-safe token are drawn again."""
    while True:
        candidate = "PL-" + secrets.token_urlsafe(8)[:8]
        if PLAN_ID.match(candidate):
            return candidate


def new_share_token() -> str:
    """One version's link. A token stands for the version and never for the plan, so an
    older link keeps showing what the customer was sent."""
    return secrets.token_urlsafe(12)


def mark_requests(days: list[PlanDay]) -> list[PlanDay]:
    """The days with ``request`` set from what each note says. The flag is derived here and
    on every version, so a day the model marked and did not write the phrase into is not a
    request, and a day it wrote the phrase into is one whether it marked it or not."""
    return [day.model_copy(update={"request": REQUEST_NOTE in day.note}) for day in days]


def diff_days(parent: list[PlanDay] | None, days: list[PlanDay]) -> list[DayDiff]:
    """``days`` read against ``parent``. The first version has no parent and every day of it
    is ``same``: there is nothing yet to have changed from.

    The two lists are aligned on what each day says rather than on where it sits, so a day
    inserted in the middle is one ``added`` and the days after it stay ``same`` however they
    are renumbered. Days the alignment replaces are paired off in order, each pair naming
    the fields that differ, and whatever is left over of the pairing is ``added`` or
    ``removed``."""
    if parent is None:
        return [_diff(index, "same", day) for index, day in enumerate(days)]
    matcher = SequenceMatcher(None, [_key(day) for day in parent], [_key(day) for day in days])
    read: list[DayDiff] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            read += [_diff(index, "same", days[index]) for index in range(new_start, new_end)]
        elif tag == "insert":
            read += [_diff(index, "added", days[index]) for index in range(new_start, new_end)]
        elif tag == "delete":
            read += [
                _diff(new_start, "removed", parent[index]) for index in range(old_start, old_end)
            ]
        else:
            paired = min(old_end - old_start, new_end - new_start)
            for offset in range(paired):
                new = days[new_start + offset]
                read.append(
                    _diff(
                        new_start + offset,
                        "changed",
                        new,
                        fields=_changed_fields(parent[old_start + offset], new),
                    )
                )
            read += [
                _diff(index, "added", days[index]) for index in range(new_start + paired, new_end)
            ]
            read += [
                _diff(new_end, "removed", parent[index])
                for index in range(old_start + paired, old_end)
            ]
    return read


def summarize(diff: list[DayDiff], *, parent_version: int | None) -> str:
    """The one Chinese line the advisor reads above a version: what it added, changed and
    removed, naming each day by its own 第N天 marker where it has one and by its place where
    it does not. ``parent_version`` is the version's own, so the baseline says it is the
    baseline instead of claiming it changed nothing."""
    if parent_version is None:
        return "基线原样"
    added = [day for day in diff if day.kind == "added"]
    changed = [day for day in diff if day.kind == "changed"]
    removed = [day for day in diff if day.kind == "removed"]
    parts = []
    if added:
        parts.append(f"+{len(added)} 天（{_days_named(added)}）")
    if changed:
        parts.append(f"改 {len(changed)} 天（{_days_named(changed)}）")
    if removed:
        parts.append(f"删 {len(removed)} 天")
    return "；".join(parts) if parts else "与上一版相同"


def handoff_text(plan: Plan, version: PlanVersion, diff: list[DayDiff]) -> str:
    """The plain text the advisor copies into the agency's own channel for the 计调: the
    plan and the 线路 it was built from, the party and what the baseline was quoted at, the
    itinerary day by day, what this version changed, and the requests the 计调 has to answer
    gathered at the end. Plain lines, because it is pasted into a chat window."""
    lines = [
        f"方案 {plan.plan_id} v{version.version}",
        f"基线线路 RT-{plan.route_id} {plan.route_name}",
    ]
    if version.travel_dates:
        lines.append(f"出发 {version.travel_dates}")
    if version.party:
        lines.append(f"人数 {version.party}")
    lines += [_price_line(version.reference_price), "定制差价待计调报价"]
    for day in version.days:
        lines += ["", day.label]
        if day.note:
            lines.append(f"【{REQUEST_NOTE}】{day.note}" if day.request else day.note)
    lines += ["", f"相对上一版：{summarize(diff, parent_version=version.parent_version)}"]
    if requests := [day for day in version.days if day.request]:
        lines += ["", f"{REQUEST_NOTE}事项："]
        lines += [f"{day.label} {day.note}".strip() for day in requests]
    return "\n".join(lines)


def _diff(
    index: int,
    kind: Literal["same", "changed", "added", "removed"],
    day: PlanDay,
    fields: list[Literal["label", "note"]] | None = None,
) -> DayDiff:
    return DayDiff(index=index, kind=kind, fields=fields or [], label=day.label, note=day.note)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _label_text(label: str) -> str:
    """A label as it is compared: its 第N天 marker gone and its whitespace collapsed."""
    return _collapse(DAY_MARKER.sub("", label))


def _key(day: PlanDay) -> str:
    return f"{_label_text(day.label)}{_KEY_SEPARATOR}{_collapse(day.note)}"


def _changed_fields(old: PlanDay, new: PlanDay) -> list[Literal["label", "note"]]:
    fields: list[Literal["label", "note"]] = []
    if _label_text(old.label) != _label_text(new.label):
        fields.append("label")
    if _collapse(old.note) != _collapse(new.note):
        fields.append("note")
    return fields


def _zh_number(text: str) -> int | None:
    """一, 十二, 二十 as the number they are; anything else as nothing."""
    total = current = 0
    for char in text:
        if char == "十":
            total += current * 10 if current else 10
            current = 0
        elif char in _ZH_DIGITS:
            current = _ZH_DIGITS[char]
        else:
            return None
    return (total + current) or None


def _day_number(day: DayDiff) -> int:
    """The number a day is named by: the one its own label carries, or its place."""
    match = DAY_MARKER.match(day.label)
    if match is None:
        return day.index + 1
    if written := match.group("num"):
        return int(written)
    zh = match.group("zh")
    return int(zh) if zh.isdigit() else (_zh_number(zh) or day.index + 1)


def _days_named(days: list[DayDiff]) -> str:
    return "第 " + "、".join(str(_day_number(day)) for day in days) + " 天"


def _money(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _price_line(price: ReferencePrice | None) -> str:
    if price is None or (price.tong_ye_adult is None and price.market_adult is None):
        return "参考价待查"
    tong_ye = _money(price.tong_ye_adult) if price.tong_ye_adult is not None else "待查"
    market = _money(price.market_adult) if price.market_adult is not None else "待查"
    return f"参考价 同业价 成人 {tong_ye} / 市场价 成人 {market}（{price.quote_source}）"
