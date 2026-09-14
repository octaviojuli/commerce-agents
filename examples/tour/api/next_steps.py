# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What the conversation may do next, read off what it has already done.

Every turn ends with chips (``present_suggestions``), and a chip the state does not allow is
worse than no chip: the advisor taps 把这两个团期发给客人 before a 团期 has been opened, and
the tool that would send it refuses. So the steps are the backend's, the way the 聚焦卡's
chips are the catalog's — the model writes the words and picks among them, and invents no
step of its own.

The stage is read from what the session holds: the 预留 it wrote, the 团期 and 线路 whose
cards it showed, and whether a search is standing over more lines than the advisor can read.
``block`` is what rides on a tool result, in the shape the 目录概览 block already uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What the four kinds of opening ask for, which is also what a thin request is missing: a
# destination, when they travel, how many travel, and how long. A search runs without them —
# a 线路 answers 北欧线路有哪些 — and the reply asks for what would narrow it next.
WANTED = ("目的地", "出行时间", "人数", "天数")

# The two id shapes a chip may name, which are the ones the conversation's own cards carry.
_ID = re.compile(r"\b(?:RT|DP)-\d+\b")

# A step whose tool refuses unless the conversation has reached the stage that makes it
# legal. The word is what a chip carrying that step reads like; the stage names the state.
_GATED = {
    "清单": "departure",
    "发给客人": "departure",
    "占位": "departure",
    "锁位": "departure",
    "定制方案": "route",
    "逐日行程": "route",
    "行程单": "route",
}


@dataclass(frozen=True)
class Stage:
    """One reading of a conversation: what it has reached, and the steps that reading allows.
    ``reached`` carries every stage at or below the one it is at, so a gate asks it rather
    than comparing names."""

    name: str
    reached: frozenset[str]
    steps: tuple[str, ...]

    def allows(self, chip: str) -> bool:
        """Whether a chip's step is one this conversation has reached. A chip naming no gated
        step is allowed: the model may always offer to search again or to ask the customer
        something."""
        needed = {stage for word, stage in _GATED.items() if word in chip}
        return needed <= self.reached

    def block(self) -> str:
        """The steps as the model reads them, under a heading of their own."""
        lines = ["下一步（聚合卡的 chips 只能从这里挑，措辞可改，不要发明这里没有的一步）："]
        lines.extend(f"- {step}" for step in self.steps)
        lines.append(
            "Every chip names a step above and an id this turn actually showed. A step the "
            "state does not allow is refused by the tool behind it, so offering it wastes the "
            "advisor's tap."
        )
        return "\n".join(lines)


_OPEN = Stage(
    "open",
    frozenset({"open"}),
    (
        "换一个方向再找（目录里有的线路系）",
        "把出行时间放宽一点（换一个月份）",
        "按客人条件找（预算、酒店标准、纯玩）",
        "问客人还没说的：出行时间、人数、天数",
    ),
)
_NARROW = Stage(
    "narrow",
    frozenset({"open", "narrow"}),
    (
        "按某个方向收窄（线路系）",
        "按天数收窄",
        "按酒店标准或纯玩收窄",
        "按预算收窄",
    ),
)
_ROUTE = Stage(
    "route",
    frozenset({"open", "narrow", "route"}),
    (
        "看某条线的逐日行程",
        "查某条线的团期",
        "把某条线的行程单发过来",
        "再收窄一层，或换一条同方向的",
    ),
)
_DEPARTURE = Stage(
    "departure",
    frozenset({"open", "narrow", "route", "departure"}),
    (
        "按人数结构报价",
        "占位这个团期（30 分钟自动释放）",
        "把这两个团期做成清单发给客人",
        "看另一个团期，或换一条线",
    ),
)
_HELD = Stage(
    "held",
    frozenset({"open", "narrow", "route", "departure", "held"}),
    (
        "查这张报名单",
        "再占一个团期",
        "把占住的团期发清单给客人",
        "按这条线做定制方案",
    ),
)


def stage_of(
    *,
    holds: int,
    departures_seen: bool,
    routes_presented: bool,
    overview: bool,
) -> Stage:
    """The conversation as one of the five readings, the furthest it has reached. A standing
    overview means the last search was wider than a shortlist, so the next step is narrowing
    it and not opening anything."""
    if holds:
        return _HELD
    if departures_seen:
        return _DEPARTURE
    if routes_presented:
        return _ROUTE
    if overview:
        return _NARROW
    return _OPEN


def keep(chips: list[str], stage: Stage, shown: set[str]) -> tuple[list[str], list[str]]:
    """The chips this state allows and the ones it does not, in the order they were written.
    A chip naming a 线路 or a 团期 id the turn did not show is dropped with them: the advisor
    would tap it and the tool behind it would say it has never seen that id."""
    kept: list[str] = []
    dropped: list[str] = []
    for chip in chips:
        ids = _ids(chip)
        (kept if stage.allows(chip) and ids <= shown else dropped).append(chip)
    return kept, dropped


def _ids(chip: str) -> set[str]:
    return set(_ID.findall(chip))
