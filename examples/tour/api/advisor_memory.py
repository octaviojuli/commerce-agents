# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""What the workbench may remember about an advisor between conversations, and what it may not.

The core's extraction prompt is written for a store's one customer, whose live undertaking is
kept under ``current_project``. The workbench's user is an advisor, and their conversations are
their customers' trips, one after another: a trip kept as the advisor's own project is read into
the next customer's conversation, where it is wrong. So the same template is rendered for the
advisor — what qualifies is a standing habit of their own work — and ``advisor_write_filter``
refuses in code what the prompt asks to leave out, on every path a fact reaches the store:
the extraction pass, the model's own memory tool, and the workbench's memory editor.

A conversation that builds a 定制方案 is the same case and its card is the densest one: the
party, the 出行日期 and the plan's own id are that customer's trip written out, and each is
refused by a check of its own.
"""

from __future__ import annotations

import re
from dataclasses import replace

from commerce_common.memory import MEMORY_EXTRACTION_TEMPLATE, MemoryRuntime, MemoryWriteFilter

ADVISOR_MEMORY_EXTRACTION_PROMPT = MEMORY_EXTRACTION_TEMPLATE.format(
    keeper="a travel agency's advisor workbench",
    subject="one advisor, a salesperson of the agency",
    occasions="conversations",
    speaker="the advisor",
    qualifies=(
        "a standing habit or rule of the advisor's own work, stated by themselves in so many "
        "words: the city their customers usually depart from, a hotel standard or a 纯玩 rule "
        "they always ask for, how they want a quote laid out (per person or 合计, with or "
        "without 单房差). Write the value in Chinese, the advisor's own language."
    ),
    standalone_example=(
        '"成都出发" tells a future reader nothing, while "the advisor\'s customers usually '
        'depart from 成都" tells them everything'
    ),
    live_key_rule=(
        "Keep no current undertaking at all: a customer's trip — who is going, where, when, "
        "how many, at what budget — belongs to that one conversation and is not a fact about "
        "the advisor."
    ),
    excluded=(
        "any customer's trip or request (destination, dates, party, budget, the 线路 or 团期 "
        "discussed, the 定制方案 built for them); the advisor's own name, department and "
        "account, which their login already states; anything that came from listings, "
        "results, or the ERP's own records; the "
        "mechanics of this conversation (what was searched or held); anything inferred rather "
        "than said; and health, financial, or identity details of anyone."
    ),
)

# A key the core's prompt would give a customer's trip, or one that names a customer.
_TRIP_KEYS = re.compile(r"current_project|trip|customer|客人|客户", re.IGNORECASE)
# A party size: 两位, 4人, 2大1小, 三位客人.
_PARTY = re.compile(r"(?:\d+|[一二三四五六七八九十两])\s*(?:位|人|大|小)")
# A month or a holiday the trip is in, which says a trip only beside a journey.
_WHEN = re.compile(
    r"\d{1,2}\s*月|\d{4}-\d{2}|国庆|春节|暑假|寒假|中秋|五一|端午|清明|元旦|上旬|中旬|下旬|月初|月底"
)
# Going somewhere.
_GOING = re.compile(r"去|出发|出行|旅行|旅游|团期|线路|行程")

# One day of one month. A standing habit of the advisor's own work does not turn on a date,
# so a fact carrying one is a journey's: a search window, a 团期's departure day, the 出行日期
# on a 定制方案's card.
_DATE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}")
# The ids the records of one conversation carry: a 定制方案 (the shape ``api/plans.py`` mints)
# and the ERP's own 线路 and 团期 (``agent_config.product_id_patterns``). A fact naming one is
# that conversation's work, whatever it says about it.
_RECORD_ID = re.compile(r"PL-[A-Za-z0-9]{8}|RT-\d+|DP-\d+")


def is_customer_trip(key: str, value: str) -> bool:
    """Whether a candidate fact is one customer's trip rather than the advisor's own habit: a
    key the customer prompt would use, one of the ids the conversation's own records carry, a
    party size, a date, or a season on a journey. A habit has none of those — the advisor's
    customers usually depart from 成都 — and stays."""
    if _TRIP_KEYS.search(key) or _RECORD_ID.search(key) or _RECORD_ID.search(value):
        return True
    if _DATE.search(value):
        return True
    return bool(_PARTY.search(value)) or bool(_WHEN.search(value) and _GOING.search(value))


def advisor_write_filter() -> MemoryWriteFilter:
    """The core's blocked patterns, plus the check above."""
    return MemoryWriteFilter.build(checks=(is_customer_trip,))


def advisor_memory(runtime: MemoryRuntime) -> MemoryRuntime:
    """The agent's memory runtime extracting under the advisor's prompt instead of the
    customer's; everything else about it stays."""
    return replace(runtime, extraction_prompt=ADVISOR_MEMORY_EXTRACTION_PROMPT)
