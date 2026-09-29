"""The storyline through the HTTP API, with a stub warehouse and a scripted model.

Needs an isolated PostgreSQL database (``WAREHOUSE_TEST_ADMIN_URL``); the advisor schema is
created in it. Nothing reaches a real warehouse or model.
"""

import base64
import json
import os
import secrets
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from tour.advisor.api import Settings, create_app
from tour.advisor.model import Answers, Draft, Understanding

SLOW, CITY = "WP-11111111-1111-1111-1111-111111111111", "WP-22222222-2222-2222-2222-222222222222"
DEP = "WD-33333333-3333-3333-3333-333333333333"
DAY = "ACME 小镇散步，含早餐；午餐和晚餐自理。每天预留自由活动时间。"


SUPPLIERS = {SLOW: ("S-A", "ACME 环线"), CITY: ("S-B", "ACME 城游")}


def product(pid, title, city):
    supplier_id, supplier_name = SUPPLIERS.get(pid, ("", ""))
    return {
        "product_id": pid,
        "title": title,
        "attributes": {
            "days": "12",
            "depart_city": city,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
        },
    }


def warehouse(request: httpx.Request):
    path, params = request.url.path, request.url.params
    if path == "/v1/auth/login":
        return httpx.Response(
            200,
            json={
                "access_token": "t",
                "expires_at": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
            },
        )
    if path == "/v1/me/organizations":
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "00000000-0000-0000-0000-00000000000a",
                        "name": "ACME 顾问",
                        "roles": ["advisor"],
                    }
                ]
            },
        )
    if path == "/v1/advisor/products":
        items = [
            product(SLOW, "X1-ACME 德法意瑞慢游小镇", "上海"),
            product(CITY, "X3-ACME 德法意瑞城市巡游", "重庆"),
        ]
        return httpx.Response(200, json={"items": items if params.get("query") else items})
    if path.endswith("/document"):
        body = {
            "days": [
                {
                    "day": n,
                    "title": f"ACME 第{n}天",
                    "text": DAY,
                    "meals": {"breakfast": {"included": True}},
                    "blocks": [],
                }
                for n in range(1, 13)
            ],
            "inclusions": ["ACME 行程早餐"],
            "exclusions": ["个人消费"],
        }
        return httpx.Response(200, json={"body": body})
    if path.startswith("/v1/advisor/products/"):
        pid = path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json=product(
                pid,
                "X1-ACME 德法意瑞慢游小镇" if pid == SLOW else "X3-ACME 德法意瑞城市巡游",
                "上海",
            ),
        )
    if path == "/v1/advisor/departures":
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "product_id": DEP,
                        "attributes": {
                            "depart_date": "2026-12-28",
                            "return_date": "2027-01-08",
                            "availability": "available",
                            "offer_id": "o1",
                        },
                        "option_values": {"团期": "X1D1"},
                    }
                ]
            },
        )
    if path == "/v1/advisor/offers":
        return httpx.Response(200, json={"items": [{"id": "o1", "active": True}]})
    if path == "/v1/advisor/quotes":
        party = json.loads(request.content)["party"]
        child = "12800.00" if party["rooms"]["child_bed"] else "11800.00"
        settle = "10800.00" if party["rooms"]["child_bed"] else "9800.00"
        market = [
            {
                "code": "adult",
                "quantity": party["adults"],
                "unit_amount": "14800.00",
                "total": str(14800 * party["adults"]) + ".00",
            },
            {"code": "child", "quantity": party["children"], "unit_amount": child, "total": None},
            {
                "code": "senior",
                "quantity": party["seniors"],
                "unit_amount": "14800.00",
                "total": str(14800 * party["seniors"]) + ".00",
            },
        ]
        settlement = [
            dict(m, unit_amount=u)
            for m, u in zip(market, ["12800.00", settle, "12800.00"], strict=True)
        ]
        for lines in (market, settlement):
            for line in lines:
                line["total"] = f"{float(line['unit_amount']) * line['quantity']:.2f}"
        return httpx.Response(
            201,
            json={
                "quote_id": secrets.token_hex(4),
                "complete": True,
                "currency": "CNY",
                "departure_date": "2026-12-28",
                "market_lines": market,
                "settlement_lines": settlement,
                "market_total": f"{sum(float(x['total']) for x in market):.2f}",
                "settlement_total": f"{sum(float(x['total']) for x in settlement):.2f}",
                "quote_valid_until": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                "fresh_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
        )
    return httpx.Response(404, json={"message": "not found"})


class Scripted:
    """Understands the four storyline messages the way the real model does."""

    def __init__(self):
        self.metrics = None

    async def call(self, name, data):
        if name == "answer":
            facts = data["facts"]
            meal = next(f for f in facts if "午餐和晚餐自理" in f["text"])
            return Answers.model_validate(
                {
                    "answers": [
                        {
                            "index": q["index"],
                            "answer": "含早餐；午餐和晚餐自理",
                            "fact_ids": [meal["fact_id"]],
                            "kind": "fact",
                        }
                        for q in data["questions"]
                    ]
                }
            )
        if name == "draft":
            ask = data["ask_next"]
            answer = data["answers"][0]["a"] if data["answers"] else ""
            return Draft(
                to_advisor="已整理",
                to_customer=f"王女士您好。{answer}。{ask}".replace("。。", "。"),
                may_ask=["要带护照吗？"],
            )
        message = data["message"]
        if "一家四口" in message:
            return Understanding.model_validate(
                {
                    "kinds": ["new_need"],
                    "changes": [
                        {
                            "field": "party",
                            "value": {"adults": 2, "children": [{"age": 8}, {"age": 5}]},
                            "evidence": "2个大人2个小孩，孩子8岁和5岁",
                        },
                        {"field": "days", "value": {"min": 11, "max": 13}, "evidence": "12天左右"},
                        {"field": "depart_city", "value": "上海", "evidence": "上海走"},
                        {
                            "field": "window",
                            "value": {
                                "start": "2026-12-11",
                                "end": "2026-12-31",
                                "label": "12月中下旬",
                            },
                            "source": "inferred",
                            "evidence": "12月中下旬",
                            "hint": "没说年份",
                        },
                        {
                            "field": "budget",
                            "value": {"per_person": 20000},
                            "evidence": "每人2万以内",
                        },
                    ],
                    "concerns": [{"text": "最怕行程太赶", "topic": "行程节奏"}],
                    "salutation": "王女士",
                }
            )
        if "吃饭" in message:
            return Understanding.model_validate(
                {
                    "kinds": ["ask"],
                    "questions": [{"text": "吃饭怎么安排？", "topic": "餐食", "route": "慢游小镇"}],
                }
            )
        if "70" in message:
            return Understanding.model_validate(
                {
                    "kinds": ["change"],
                    "changes": [
                        {
                            "field": "party",
                            "value": {"seniors": [{"age": 70}]},
                            "evidence": "我妈也想去，她70了",
                        }
                    ],
                }
            )
        if "占床" in message:
            return Understanding.model_validate(
                {
                    "kinds": ["new_need"],
                    "changes": [
                        {
                            "field": "party",
                            "value": {
                                "children": [{"age": 8, "bed": True}, {"age": 5, "bed": False}]
                            },
                            "evidence": "5岁不占床，8岁占床",
                        },
                        {
                            "field": "rooms",
                            "value": {"doubles": 1, "twins": 1, "singles": 1},
                            "evidence": "阿姨单住一间",
                        },
                    ],
                }
            )
        return Understanding.model_validate({"kinds": ["chitchat"]})


@pytest.fixture
def client(tmp_path):
    url = os.environ.get("WAREHOUSE_TEST_ADMIN_URL")
    if not url:
        pytest.skip("An isolated PostgreSQL test database is required")
    assert make_url(url).database.endswith("_test")
    os.environ["ADVISOR_MATERIAL_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
    settings = Settings(
        database_url=url, warehouse_url="http://warehouse.test", material_dir=tmp_path
    )
    app = create_app(settings, model=Scripted(), transport=httpx.MockTransport(warehouse))
    with TestClient(app) as c:
        c.post(
            "/api/login",
            json={"email": f"advisor-{secrets.token_hex(3)}@acme.example", "password": "x"},
        ).raise_for_status()
        yield c


def test_the_storyline_from_first_words_to_the_sale(client):
    deal = client.post("/api/deals", json={"title": "王女士一家"}).json()["id"]
    turn = client.post(
        f"/api/deals/{deal}/turns",
        json={
            "text": "我们一家四口，2个大人2个小孩，孩子8岁和5岁，想12月中下旬去德法意瑞，上海走，12天左右，每人2万以内。最怕行程太赶"
        },
    ).json()
    read = next(c for c in turn["cards"] if c["type"] == "read")
    assert {i["field"] for i in read["items"]} >= {"destinations", "party", "window", "budget"}
    assert turn["asked"] == "" and "占床" not in turn["draft"]["text"]

    turn = client.post(f"/api/deals/{deal}/turns", json={"text": "先帮她看看有哪些线路"}).json()
    routes = next(c for c in turn["cards"] if c["type"] == "routes")
    assert [c["title"] for c in routes["cards"]] == ["ACME 德法意瑞慢游小镇"]
    assert routes["relax"] and "不限出发地" in routes["relax"][0]["text"]

    turn = client.post(
        f"/api/deals/{deal}/turns", json={"text": "慢游小镇那条吃饭怎么安排？"}
    ).json()
    answers = next(c for c in turn["cards"] if c["type"] == "answers")
    assert answers["items"][0]["kind"] == "fact" and answers["items"][0]["facts"][0]["section"] in (
        "行程",
        "餐食",
    )

    turn = client.post(f"/api/deals/{deal}/turns", json={"text": "我妈也想去，她70了"}).json()
    change = next(c for c in turn["cards"] if c["type"] == "change")
    assert change["items"][0]["field"] == "party"
    assert any(i["title"] == "房间要重新定" for i in change["impact"])
    adopted = client.post(f"/api/deals/{deal}/proposals/{change['proposal_id']}", json={}).json()
    assert adopted["adopted"] == ["party"]

    client.post(
        f"/api/deals/{deal}/turns", json={"text": "阿姨单住一间，5岁不占床，8岁占床"}
    ).raise_for_status()
    detail = client.get(f"/api/deals/{deal}").json()
    assert detail["gates"]["quote"]["ready"] and detail["need"]["beds"] == "8 岁占床、5 岁不占床"

    client.post(
        f"/api/deals/{deal}/route", json={"product_id": SLOW, "title": "X1-ACME 德法意瑞慢游小镇"}
    ).raise_for_status()
    price = client.post(f"/api/deals/{deal}/dates/{DEP}/price").json()
    assert price["market_total"] == "69000.00" and price["valid"]
    client.post(
        f"/api/deals/{deal}/departure",
        json={
            "departure_id": DEP,
            "offer_id": "o1",
            "date": "2026-12-28",
            "return_date": "2027-01-08",
        },
    ).raise_for_status()

    assert (
        client.post(f"/api/deals/{deal}/quotes/formal").status_code == 409
    )  # only from a confirmed sheet
    sheet = client.post(f"/api/deals/{deal}/confirmation").json()
    client.post(
        f"/api/deals/{deal}/confirmation/{sheet['id']}", json={"evidence": "王女士：确认"}
    ).raise_for_status()
    formal = client.post(f"/api/deals/{deal}/quotes/formal").json()
    assert (formal["market_total"], formal["settlement_total"], formal["profit"]) == (
        "69000.00",
        "59000.00",
        "10000.00",
    )

    after = client.post(
        f"/api/deals/{deal}/sale", json={"quote_id": formal["id"], "deposit": 20000}
    ).json()
    assert after["money"]["outstanding"] == "49000.00" and len(after["tasks"]) >= 5
    assert client.get(f"/api/deals/{deal}").json()["stage"] == 4


def test_a_customer_page_never_shows_trade_prices(client):
    deal = client.post("/api/deals", json={"title": "测试"}).json()["id"]
    client.post(
        f"/api/deals/{deal}/turns",
        json={
            "text": "我们一家四口，2个大人2个小孩，孩子8岁和5岁，想12月中下旬去德法意瑞，上海走，12天左右，每人2万以内"
        },
    )
    plan = client.post(f"/api/deals/{deal}/plans", json={"product_ids": [SLOW, CITY]}).json()
    token = client.post(f"/api/deals/{deal}/plans/{plan['id']}/share").json()["token"]
    page = client.get(f"/api/public/plans/{token}").text
    assert "同业" not in page and "settlement" not in page and "毛利" not in page
    client.get(f"/api/public/plans/{token}?signal=select&route=0")
    assert client.get(f"/api/deals/{deal}").json()["plans"][0]["signals"][0]["signal"] == "select"


def test_another_advisor_cannot_open_the_deal(client):
    deal = client.post("/api/deals", json={"title": "私有"}).json()["id"]
    client.cookies.clear()
    client.post(
        "/api/login", json={"email": "someone-else@acme.example", "password": "x"}
    ).raise_for_status()
    assert client.get(f"/api/deals/{deal}").status_code == 404
