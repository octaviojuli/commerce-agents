"""Explicit opt-in real-model acceptance; fictional ACME data in the isolated test database."""

import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from cloud_warehouse import auth
from cloud_warehouse.api import create_app
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.copilot_engine import CONFIRM_ITEMS
from tour.api.warehouse_chat import factory

from .test_imports import excel_source as excel_source
from .test_quotes import FUTURE, commit, contract
from .test_quotes import managed_offer as managed_offer


def test_real_model_advisor_merchant_multiturn(
    database, authentication, tenant, managed_offer, tmp_path
):
    assert os.environ.get("COPILOT_LIVE_ACCEPTANCE") == "1"
    admin, runtime = database
    commit(runtime, tenant.supplier, contract(runtime, tenant, managed_offer))
    password = secrets.token_urlsafe(24)
    advisor_email = f"copilot-advisor-{uuid4()}@acme.example"
    supplier_email = f"copilot-merchant-{uuid4()}@acme.example"
    auth.create_user(admin, advisor_email, password, {tenant.buyer.organization_id: ["advisor"]})
    auth.create_user(
        admin, supplier_email, password, {tenant.supplier.organization_id: ["supplier_admin"]}
    )
    store = LocalObjectStore(tmp_path / "objects")
    reports = []
    with TestClient(
        create_app(runtime, authentication, agent_factory=factory(runtime, {}), object_store=store),
        client=("127.1." + str(uuid4().bytes[0]) + "." + str(uuid4().bytes[0]), 50000),
    ) as client:

        def login(email, organization):
            token = client.post(
                "/v1/auth/login", json={"email": email, "password": password}
            ).json()["access_token"]
            return {"Authorization": "Bearer " + token, "X-Organization-Id": str(organization)}

        advisor = login(advisor_email, tenant.buyer.organization_id)
        merchant = login(supplier_email, tenant.supplier.organization_id)
        customer = client.post(
            "/v1/copilot/customers",
            headers=advisor,
            json={"request_id": str(uuid4()), "customer": {"name": "ACME 家庭"}},
        )
        assert customer.status_code == 201, customer.text
        deal = client.post(
            "/v1/copilot/deals",
            headers=advisor,
            json={
                "request_id": str(uuid4()),
                "customer_id": customer.json()["id"],
                "title": "ACME 多轮闭环验收",
            },
        )
        assert deal.status_code == 201, deal.text
        identifier = deal.json()["id"]
        base = f"/v1/conversations/{identifier}"
        private = f"/v1/copilot/deals/{identifier}"

        def brief():
            response = client.get(base + "/brief", headers=advisor)
            assert response.status_code == 200, response.text
            return response.json()

        def command(path, payload, headers=None):
            response = client.post(
                path,
                headers=headers or advisor,
                json={
                    "request_id": str(uuid4()),
                    "expected_version": brief()["version"],
                    **payload,
                },
            )
            assert response.status_code in {200, 201}, response.text
            return response.json()

        def turn(message, case):
            started = time.monotonic()
            response = client.post(
                base + "/chat",
                headers={**advisor, "Idempotency-Key": str(uuid4())},
                json={"message": message},
            )
            assert response.status_code == 200, response.text
            events = []
            for frame in response.text.split("\n\n"):
                lines = frame.splitlines()
                kind = next((x[7:] for x in lines if x.startswith("event: ")), None)
                data = next((x[6:] for x in lines if x.startswith("data: ")), None)
                if kind and data:
                    events.append({"type": kind, "data": json.loads(data)})
            assert any(e["type"] == "turn_complete" for e in events), response.text
            assert any(e["type"] == "text_delta" and e["data"].get("text") for e in events)
            reply = next(
                e["data"]["payload"]
                for e in events
                if e["type"] == "ui" and e["data"].get("component") == "copilot_reply"
            )
            reports.append(
                {
                    "case": case,
                    "seconds": round(time.monotonic() - started, 2),
                    "reply": reply,
                    "events": [e["data"].get("component") for e in events if e["type"] == "ui"],
                }
            )
            Path(".warehouse/advisor-copilot/live-progress.json").write_text(
                json.dumps(
                    {"reports": reports, "last_events": events, "brief": brief()},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            assert not reply.get("degraded"), reports[-1]
            return events

        first = turn(
            f"客人想去 ACME，{FUTURE} 出发，3天，2个成人、没有儿童，一间双人房。", "完整需求选线"
        )
        routes = next(
            e["data"]["payload"]["items"]
            for e in first
            if e["type"] == "ui" and e["data"].get("component") == "warehouse_routes"
        )
        assert routes and not any(
            e["data"].get("component") == "warehouse_departures" for e in first
        )
        route = routes[0]["product_id"]
        command(base + "/actions", {"action": "departures", "product_id": route})
        saved = client.get(base, headers=advisor).json()
        deps = [
            e["data"]["payload"]["items"]
            for t in saved["turns"]
            for e in t["events"]
            if e["type"] == "ui" and e["data"].get("component") == "warehouse_departures"
        ][-1]
        departure = deps[0]["product_id"]
        command(base + "/actions", {"action": "offers", "product_id": departure})
        command(
            base + "/actions",
            {"action": "quote", "product_id": departure, "offer_id": str(managed_offer)},
        )
        selected = brief()["body"]["route_id"]
        turn(
            "选的就是这条。客人追问：可以安排儿童餐吗？先核实一下，不要换线路。",
            "报价后追问不退回选线",
        )
        assert brief()["body"]["route_id"] == selected
        inquiry = command(
            private + "/inquiries", {"product_id": route, "question": "ACME 本团儿童餐如何安排？"}
        )
        inbox = client.get("/v1/copilot/inquiries", headers=merchant).json()["items"]
        assert inquiry["id"] in {x["id"] for x in inbox}
        reply = client.post(
            f"/v1/copilot/inquiries/{inquiry['id']}/replies",
            headers=merchant,
            json={
                "request_id": str(uuid4()),
                "answer": "本次 ACME 团可提供清淡儿童餐，须在出发前两天登记；不额外收费。",
            },
        )
        assert reply.status_code == 200, reply.text
        command(private + f"/replies/{reply.json()['id']}/adopt", {})
        turn(
            "商户关于儿童餐的回复我已经核对采纳。请帮我回复客人：儿童餐怎么办？",
            "商户回复进入下一轮答疑",
        )
        assert "儿童餐" in reports[-1]["reply"]["to_customer"] and reports[-1]["reply"]["claims"]
        inbox = client.get("/v1/copilot/inquiries", headers=merchant).json()["items"]
        assert next(x for x in inbox if x["id"] == inquiry["id"])["status"] == "adopted"
        turn("如果临时不去了，你能保证全部退费吗？", "未知退款条件不承诺")
        assert not any("保证全部退" in x["text"] for x in reports[-1]["reply"]["claims"])
        command(
            private + "/confirm",
            {
                "confirmed_items": list(CONFIRM_ITEMS),
                "evidence": "ACME 客人已核对线路日期、人数房型、费用和退改材料要求",
            },
        )
        retail = command(private + "/retail-quotes", {"sales_total": "260.00"})
        assert retail["body"]["profit"] == "60.00"
        link = command(private + f"/retail-quotes/{retail['id']}/share", {})
        sale = command(
            private + "/sales",
            {"retail_quote_id": retail["id"], "supplier_order_number": "ACME-MANUAL-001"},
        )
        command(
            private + "/receipts",
            {
                "sale_id": sale["id"],
                "amount": "60",
                "category": "deposit",
                "received_on": str(FUTURE),
                "reference": "ACME 线下收款凭据",
            },
        )
        changed = turn(
            "客户现在改成3个成人，没有儿童，两间双人房。先别报价，更新需求给我确认。",
            "人数房型变化等待采纳",
        )
        assert brief()["body"]["adults"]["value"] == 2
        proposal = next(
            e["data"]["payload"]
            for e in changed
            if e["type"] == "ui" and e["data"].get("component") == "copilot_proposal"
        )
        command(private + f"/proposals/{proposal['proposal_id']}", {"accept": True})
        assert brief()["body"]["adults"]["value"] == 3
        public = client.post("/v1/public/quote", json={"token": link["token"]})
        assert public.status_code in {404, 403}, public.text
        final = client.get(private, headers=advisor).json()
        assert any(r["kind"] == "sale" for r in final["records"])
        primary_identifier = identifier

        def new_scenario(title):
            nonlocal identifier, base, private
            item = client.post(
                "/v1/copilot/deals",
                headers=advisor,
                json={"request_id": str(uuid4()), "title": title},
            )
            assert item.status_code == 201, item.text
            identifier = item.json()["id"]
            base = f"/v1/conversations/{identifier}"
            private = f"/v1/copilot/deals/{identifier}"

        def accept_pending(events, accept=True):
            candidates = [
                e["data"]["payload"]
                for e in events
                if e["type"] == "ui" and e["data"].get("component") == "copilot_proposal"
            ]
            if candidates:
                command(private + f"/proposals/{candidates[0]['proposal_id']}", {"accept": accept})
            return candidates

        new_scenario("ACME 模糊需求与亲子房型")
        turn("客人只说想出去放松几天，目的地、日期和人数都还没定。", "模糊需求不编造人数日期")
        assert brief()["body"]["adults"]["value"] is None
        assert brief()["body"]["window"]["value"] is None
        family = turn(
            f"目的地 ACME，{FUTURE} 出发，3天，2个成人、1个儿童，年龄还没问。",
            "亲子人数明确但年龄缺失",
        )
        accept_pending(family)
        assert brief()["body"]["children"]["value"] == 1
        assert not brief()["readiness"]["quote"]["ready"]
        room = turn("孩子7岁，需要1间双床房，孩子不占床。", "补充儿童年龄与不占床")
        accept_pending(room)
        assert brief()["body"]["child_ages"]["value"] == [7]
        assert brief()["body"]["rooms"]["value"]["child_bed"] is False
        changed_room = turn(
            "刚确认孩子其实8岁，需要占床。请给我核对变化，先不报价。", "再次变更年龄占床先确认"
        )
        assert brief()["body"]["child_ages"]["value"] == [7]
        assert accept_pending(changed_room)
        assert brief()["body"]["child_ages"]["value"] == [8]
        assert brief()["body"]["rooms"]["value"]["child_bed"] is True

        new_scenario("ACME 多国需求与撤销变更")
        countries = turn(
            f"德法意瑞四国都必须去，{FUTURE} 出发，12天，2个成人，没有儿童。", "四国必须全部保留"
        )
        accept_pending(countries)
        assert set(brief()["body"]["destinations"]["value"]) == {"德国", "法国", "意大利", "瑞士"}
        change_countries = turn(
            "改成只去德国和法国，意大利和瑞士不去了。先展示变化。", "减少国家须采纳"
        )
        assert set(brief()["body"]["destinations"]["value"]) == {"德国", "法国", "意大利", "瑞士"}
        assert accept_pending(change_countries, accept=False)
        turn("上一条调整先不采用，仍然要原来的四国。", "拒绝变更后保持原方案")
        assert set(brief()["body"]["destinations"]["value"]) == {"德国", "法国", "意大利", "瑞士"}
        identifier = primary_identifier
        # Credentials remain private for the following local browser acceptance.
        output = Path(".warehouse/advisor-copilot")
        output.mkdir(exist_ok=True, parents=True)
        fixture = {
            "advisor_email": advisor_email,
            "merchant_email": supplier_email,
            "password": password,
            "advisor_org": str(tenant.buyer.organization_id),
            "merchant_org": str(tenant.supplier.organization_id),
            "deal_id": identifier,
        }
        (output / "browser-fixture.json").write_text(json.dumps(fixture))
        (output / "browser-fixture.json").chmod(0o600)
        (output / "live-scenarios.json").write_text(
            json.dumps(reports, ensure_ascii=False, indent=2)
        )
        assert len(reports) >= 5
        print(
            json.dumps(
                {
                    "real_model_turns": len(reports),
                    "merchant_roundtrip": True,
                    "max_turn_seconds": max(r["seconds"] for r in reports),
                },
                ensure_ascii=False,
            )
        )
