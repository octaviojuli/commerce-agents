"""The judge decides what rules could only guess; the program's rules and fallbacks still hold."""

import asyncio
import json
from uuid import UUID

import httpx

from tour.advisor import grounding
from tour.advisor.judge import Judge
from tour.advisor.model import Draft, Understanding
from tour.advisor.tests.test_review import ready, run_turn
from tour.advisor.tests.test_story import Scripted


def fake(decide):
    """A judge endpoint that answers every question through ``decide(task_kind, state, key)``."""

    def handle(request):
        body = json.loads(request.content)
        answers = {}
        for key, question in body["questions"].items():
            choice, confidence = decide(body["state"], key, question)
            answers[key] = {"type": "choice", "choice": choice, "confidence": confidence}
        return httpx.Response(200, json={"answers": answers})

    return httpx.MockTransport(handle)


def test_the_judge_is_off_without_a_key_and_its_failures_are_not_decisions():
    assert asyncio.run(Judge(key="").ask("t", {}, {"q": {}})) is None
    down = Judge(key="k", mode="on", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert asyncio.run(down.check_draft(["ACME 全程 4 星。"], [], [], {})) is None


def test_the_judge_decides_support_but_not_numbers_or_hard_rules():
    facts = [{"fact_id": "f", "text": "全程 4 星酒店"}]
    text = (
        "全程 4 星酒店。今年天气很好。价格一定最低。全程 5 星酒店，很舒服。含早餐的安排我去确认。"
    )
    rules = grounding.check(text, facts)
    verdicts = [("safe", 0.9), ("unsafe", 0.3), ("safe", 0.99), ("safe", 0.9), ("safe", 0.2)]
    out = grounding.judged(rules, verdicts, facts)
    by = {d["text"]: d["reason"] for d in out["decisions"]}
    assert by["全程 4 星酒店。"] == ""
    assert by["今年天气很好。"] == "judged_unsafe"
    assert by["价格一定最低。"] == "promise"  # a hard rule the judge cannot lift
    assert by["全程 5 星酒店，很舒服。"] == "unproven"  # 5 is in no fact
    assert by["含早餐的安排我去确认。"] == rules["decisions"][4]["reason"]  # unsure: rules stand


def test_a_turn_uses_the_judge_to_cut_and_to_withhold_consent(env):
    client, engine, owner = env
    deal = ready(client)

    class Says(Scripted):
        async def call(self, name, payload):
            if name == "understand":
                return Understanding.model_validate({"kinds": ["chitchat"]})
            if name == "draft":
                return Draft(to_customer="您好，收到啦。这边一月份天气很舒服。")
            return await super().call(name, payload)

    def decide(state, key, question):
        if "sentences" in state:
            sentence = state["sentences"][key]
            return ("unsafe", 0.9) if "天气" in sentence else ("safe", 0.95)
        return ("wait", 0.9)

    judge = Judge(key="k", mode="on", transport=fake(decide))
    result = run_turn(engine, owner, UUID(deal), Says(), "好的", judge=judge)
    assert "天气" not in result["draft"]["text"] and "收到" in result["draft"]["text"]


def test_consent_needs_both_readers(env):
    client, engine, owner = env
    deal = ready(client)
    sheet = client.post(f"/api/deals/{deal}/confirmation").json()

    class Confirms(Scripted):
        async def call(self, name, payload):
            if name == "understand":
                return Understanding.model_validate({"kinds": ["confirm"], "confirms": True})
            return await super().call(name, payload)

    def decide(state, key, question):
        if "sentences" in state:
            return ("safe", 0.9)
        return ("dispute", 0.8) if key == "action" else ("none", 0.9)

    judge = Judge(key="k", mode="on", transport=fake(decide))
    result = run_turn(
        engine, owner, UUID(deal), Confirms(), "行吧，就这样，不过酒店再看看", judge=judge
    )
    assert any("确认单先不勾" in n for n in result["notes"])
    current = client.get(f"/api/deals/{deal}/confirmation").json()["current"]
    assert current["id"] == sheet["id"] and current["status"] == "open"


def test_an_unsure_unsafe_leaves_the_rules_and_kept_personal_numbers_are_never_said():
    facts = [{"fact_id": "r", "text": "ACME 城游 13 天，上海出发", "section": "目录"}]
    rules = grounding.check("1. ACME 城游（13 天，上海出发）。手机号和护照号也帮您留好了。", facts)
    out = grounding.judged(rules, [("unsafe", 0.04), ("safe", 0.99)], facts)
    by = {d["text"]: d["reason"] for d in out["decisions"]}
    assert by["1. ACME 城游（13 天，上海出发）。"] == ""
    assert by["手机号和护照号也帮您留好了。"] == "private"
