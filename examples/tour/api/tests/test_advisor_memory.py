# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The advisor is the subject of the workbench's memory, and a customer's trip is not a fact
about them: the prompt says so and the write filter enforces it on every path to the store."""

import pytest

from commerce_common.memory import MemoryWriteRejected
from tour.api.advisor_memory import (
    ADVISOR_MEMORY_EXTRACTION_PROMPT,
    advisor_write_filter,
    is_customer_trip,
)

TRIPS = [
    ("current_project", "国庆期间两位客人去斯里兰卡旅游,需要看国庆团期的线路,共2人"),
    ("travel_plan", "10月中旬去欧洲旅行,4位客人"),
    ("party", "2大1小，孩子5岁"),
    ("europe", "客人想10月去法意瑞，预算1.5万以内"),
    ("customer_hotel", "这位客人要五钻"),
]

HABITS = [
    ("departure_city", "顾问的客人通常从成都出发"),
    ("quote_style", "报价按每人报，单房差另列"),
    ("hotel_standard", "顾问一般只推纯玩五钻的线路"),
    ("department", "常在欧洲部下单"),
]


@pytest.mark.parametrize(("key", "value"), TRIPS)
def test_a_customers_trip_is_not_a_fact_about_the_advisor(key, value):
    assert is_customer_trip(key, value)
    assert advisor_write_filter().rejects(key, value)


@pytest.mark.parametrize(("key", "value"), HABITS)
def test_the_advisors_own_habit_stays(key, value):
    assert not is_customer_trip(key, value)
    assert not advisor_write_filter().rejects(key, value)


def test_the_cores_own_patterns_still_apply():
    assert advisor_write_filter().rejects("mobile", "13900000001")


def test_the_prompt_is_the_advisors():
    assert "the advisor" in ADVISOR_MEMORY_EXTRACTION_PROMPT
    assert "current_project" not in ADVISOR_MEMORY_EXTRACTION_PROMPT
    assert "any customer's trip" in ADVISOR_MEMORY_EXTRACTION_PROMPT
    # The login states who the advisor is; the memory is for what they said about their work.
    assert "which their login already states" in ADVISOR_MEMORY_EXTRACTION_PROMPT
    assert "Write the value in Chinese" in ADVISOR_MEMORY_EXTRACTION_PROMPT


def test_the_agents_memory_runs_under_the_advisors_prompt_and_filter(main):
    runtime = main.agent.memory
    assert runtime.extraction_prompt == ADVISOR_MEMORY_EXTRACTION_PROMPT
    with pytest.raises(MemoryWriteRejected):
        runtime.validate("current_project", TRIPS[0][1], "context", source_session_id="s1")
    fact = runtime.validate("departure_city", HABITS[0][1], "preference", source_session_id="s1")
    assert fact.value == HABITS[0][1]
