"""Run the v3 repetition criteria against the disposable real-model HTTP fixture."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from uuid import uuid4

import httpx

STATE = Path(".warehouse/advisor-v3")
FAMILY = "我们一家四口，2个大人2个小孩，孩子8岁和5岁，想12月中下旬去德法意瑞，上海走，12天左右，别太累，每人2万以内"
VAGUE = "想带家里人出去玩玩，有啥推荐吗"


class Journey:
    def __init__(self, label):
        cfg = json.loads((STATE / "access.json").read_text())
        self.client = httpx.Client(base_url=cfg["api"], timeout=120)
        login = self.client.post(
            "/v1/auth/login", json={"email": cfg["advisor_email"], "password": cfg["password"]}
        )
        login.raise_for_status()
        self.headers = {
            "Authorization": "Bearer " + login.json()["access_token"],
            "X-Organization-Id": cfg["advisor_org"],
        }
        self.id = self.post(
            "/v1/copilot/deals", {"request_id": str(uuid4()), "title": "ACME " + label}
        )["id"]
        self.base = "/v1/conversations/" + self.id
        self.private = "/v1/copilot/deals/" + self.id
        self.turns = []

    def post(self, path, body):
        response = self.client.post(path, headers=self.headers, json=body)
        response.raise_for_status()
        return response.json()

    def brief(self):
        result = self.client.get(self.base + "/brief", headers=self.headers)
        result.raise_for_status()
        return result.json()

    def command(self, path, body):
        return self.post(
            path, {"request_id": str(uuid4()), "expected_version": self.brief()["version"], **body}
        )

    def turn(self, message):
        started = time.monotonic()
        response = self.client.post(
            self.base + "/chat",
            headers={**self.headers, "Idempotency-Key": str(uuid4())},
            json={"message": message},
        )
        response.raise_for_status()
        events = []
        for frame in response.text.split("\n\n"):
            lines = frame.splitlines()
            kind = next((line[7:] for line in lines if line.startswith("event: ")), None)
            data = next((line[6:] for line in lines if line.startswith("data: ")), None)
            if kind and data:
                events.append({"type": kind, "data": json.loads(data)})
        assert any(e["type"] == "turn_complete" for e in events), events
        reply = self.card(events, "copilot_reply")
        assert reply and reply["to_customer"].strip(), events
        item = {
            "message": message,
            "reply": reply,
            "events": events,
            "seconds": round(time.monotonic() - started, 2),
            "brief": self.brief(),
        }
        self.turns.append(item)
        assert not reply["degraded"], reply["degraded"]
        assert not re.search(
            r"W[PDO]-|X\d+-|同业价|结算价|利润|ACME 统一价", reply["to_customer"]
        ), reply
        return events, reply

    @staticmethod
    def card(events, name):
        return next(
            (
                e["data"]["payload"]
                for e in events
                if e["type"] == "ui" and e["data"].get("component") == name
            ),
            None,
        )

    def adopt(self, events):
        proposal = self.card(events, "copilot_proposal")
        if proposal:
            self.command(self.private + "/proposals/" + proposal["proposal_id"], {"accept": True})

    def close(self):
        self.client.close()


def scenario(kind, repetition):
    journey = Journey(f"{kind}-{repetition}")
    try:
        if kind == "vague":
            _, reply = journey.turn(VAGUE)
            assert 1 <= reply["to_customer"].count("？") <= 2, reply["to_customer"]
            assert "核实" not in reply["to_customer"], reply["to_customer"]
        elif kind == "family":
            _, reply = journey.turn(FAMILY)
            assert any(w in reply["to_customer"] for w in ("慢", "累", "孩子", "儿童", "小朋友")), (
                reply
            )
            assert journey.brief()["readiness"]["search"]["ready"], journey.brief()
        else:
            events, _ = journey.turn(FAMILY.replace("上海", "重庆"))
            journey.adopt(events)
            events, _ = journey.turn("改为上海出发，其他条件不变，请展示变化。")
            assert journey.card(events, "copilot_proposal"), events
            journey.adopt(events)
            _, reply = journey.turn("请按新需求重新找线，帮我回复客人。")
            for sentence in re.split(r"[。！？\n]", reply["to_customer"]):
                if "重庆" in sentence:
                    assert "备选" in sentence and "需要确认" in sentence, sentence
        result = {"case": kind, "repetition": repetition, "passed": True}
    except Exception as error:
        result = {
            "case": kind,
            "repetition": repetition,
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error)[:1800],
        }
    finally:
        result.update(deal=journey.id, turns=journey.turns)
        journey.close()
    return result


def main():
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(scenario, kind, n)
            for kind in ("vague", "family", "shanghai")
            for n in range(1, 6)
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            (STATE / "f1-repetitions.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
            print(
                json.dumps({k: v for k, v in result.items() if k != "turns"}, ensure_ascii=False),
                flush=True,
            )
    assert all(r["passed"] for r in results), "Some F1 cases need repair; see private evidence file"


if __name__ == "__main__":
    main()
