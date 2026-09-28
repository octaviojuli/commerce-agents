"""Disposable ACME API for advisor v3 real-model and browser acceptance.

Requires an explicit loopback WAREHOUSE_CI_ADMIN_URL and model configuration.
Every database/role belongs to this process and is removed on shutdown.
"""

import base64
import io
import json
import os
import secrets
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from ci_database import isolated_database
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.engine import make_url

from cloud_warehouse import auth, changes, documents, imports, pricing
from cloud_warehouse.admin import grant_auth, grant_runtime, migrate, onboard
from cloud_warehouse.api import create_app
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.catalog import list_departures, list_products
from cloud_warehouse.persistence import Principal, engine_for
from cloud_warehouse.route_doc import Day, Meal, Meals, Quality, RouteDoc, Source, Summary
from tour.api.warehouse_chat import factory

ROOT = Path(__file__).resolve().parents[2]


def commit(runtime, actor, proposal):
    cid = UUID(proposal["id"])
    changes.approve(runtime, actor, cid, proposal["payload_hash"])
    return changes.apply(runtime, actor, cid)


def serve(env, state):
    migrate(env["WAREHOUSE_TEST_ADMIN_URL"])
    admin, runtime, authentication = [
        engine_for(env[k])
        for k in (
            "WAREHOUSE_TEST_ADMIN_URL",
            "WAREHOUSE_TEST_DATABASE_URL",
            "WAREHOUSE_TEST_AUTH_URL",
        )
    ]
    grant_runtime(admin, make_url(env["WAREHOUSE_TEST_DATABASE_URL"]).username)
    grant_auth(admin, make_url(env["WAREHOUSE_TEST_AUTH_URL"]).username)
    ids = onboard(admin, "ACME 验收商户", "ACME 验收顾问", f"worker-{uuid4()}@acme.example")
    password = secrets.token_urlsafe(24)
    advisor_email, merchant_email = "advisor@acme.example", "merchant@acme.example"
    buyer_id, supplier_id = UUID(ids["buyer_id"]), UUID(ids["supplier_id"])
    advisor_id = auth.create_user(admin, advisor_email, password, {buyer_id: ["advisor"]})
    merchant_id = auth.create_user(
        admin, merchant_email, password, {supplier_id: ["supplier_admin"]}
    )
    buyer, merchant = Principal(advisor_id, buyer_id), Principal(merchant_id, supplier_id)
    worker = Principal(UUID(ids["worker_id"]), supplier_id)
    source = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO supplier_connection(id,supplier_org_id,name,connector_type,credential_ref,capabilities) VALUES(:id,:org,'ACME 统一价测试','excel','none','{\"inventory_owner\":\"warehouse\"}'::jsonb)"
            ),
            {"id": source, "org": supplier_id},
        )
        conn.execute(
            text(
                "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:s,:b,:c)"
            ),
            {"id": uuid4(), "s": supplier_id, "b": buyer_id, "c": source},
        )
    today = datetime.now(UTC).date()
    year = today.year if today.month < 12 else today.year + 1
    route_data = [
        ("X1", "ACME 德法意瑞慢游小镇", 12, "上海"),
        ("X2", "ACME 德法意瑞山湖漫游", 12, "上海"),
        ("X3", "ACME 德法意瑞城市巡游", 12, "重庆"),
        ("X4", "ACME 日本海边放松", 7, "上海"),
        ("X5", "ACME 澳大利亚自然风光", 10, "上海"),
    ]
    book = Workbook()
    sheet = book.active
    sheet.title = "团期导入"
    sheet.append(list(imports.HEADERS))
    for code, name, days, city in route_data:
        for index, day in enumerate((date(year, 12, 18), date(year, 12, 28), date(year + 1, 1, 2))):
            sheet.append(
                [
                    code,
                    code + "-" + name,
                    days,
                    city,
                    code + "D" + str(index),
                    str(day),
                    str(day + timedelta(days=days - 1)),
                    30,
                    0,
                    0,
                ]
            )
    out = io.BytesIO()
    book.save(out)
    batch = imports.upload(runtime, merchant, source, "ACME-advisor.xlsx", out.getvalue())
    if batch["status"] != "validated":
        raise ValueError(f"ACME fixture workbook rejected: {batch}")
    commit(runtime, merchant, imports.preview(runtime, merchant, batch["id"]))
    for dep in list_departures(runtime, buyer):
        offer = pricing.list_offers(runtime, buyer, dep["id"])[0]["id"]
        schedule = pricing.PriceSchedule(
            currency="CNY",
            market={"adult": "14800", "child": "12800", "senior": "14800", "single_room": "1800"},
            settlement={
                "adult": "12800",
                "child": "10800",
                "senior": "12800",
                "single_room": "1400",
            },
            fees_complete=True,
            child_occupies_seat=True,
            child_age_min=2,
            child_age_max=12,
            room_types=["双人标准间", "双床房"],
            room_supplements={
                "doubles": {"market": 0, "settlement": 0},
                "twins": {"market": 0, "settlement": 0},
                "singles": {"market": 1800, "settlement": 1400},
            },
            child_bed_prices={
                "occupied": {"market": 12800, "settlement": 10800},
                "unoccupied": {"market": 11800, "settlement": 9800},
            },
        )
        staged = pricing.propose(
            runtime,
            merchant,
            pricing.ContractProposal(
                offer_id=offer,
                buyer_org_id=buyer_id,
                expected_version=0,
                schedule=schedule,
                source_ref="ACME 虚构统一价",
                valid_from=datetime.now(UTC) - timedelta(minutes=1),
                valid_until=datetime.now(UTC) + timedelta(days=180),
            ),
        )
        commit(runtime, merchant, staged)
    store = LocalObjectStore(state / "objects")
    # Native source and reviewed content are deliberately identical fictional facts.
    for product in list_products(runtime, merchant):
        days = product["days"]
        texts = []
        for n in range(1, days + 1):
            fact = "ACME 小镇散步，含早餐；午餐和晚餐自理。每天预留自由活动时间。"
            if "城市巡游" in product["name"]:
                fact = "ACME 城市步行参观，城市之间乘车转移；含早餐，午餐和晚餐自理。"
            if n == 4:
                fact += " 儿童餐需要提前两天登记；安排以出发前商户确认结果为准。"
            texts.append(fact)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            )
            paragraphs = [
                line for n, line in enumerate(texts, 1) for line in (f"D{n} ACME 第{n}天", line)
            ]
            paragraphs.append("费用不含：个人消费。相邻房间不能保证，以酒店最终确认为准。")
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in paragraphs)
                + "</w:body></w:document>",
            )
        raw = data.getvalue()
        asset = documents.upload(runtime, merchant, store, product["id"], "ACME-source.docx", raw)

        def parse(work, payload, days=days, texts=texts):
            return RouteDoc(
                route_id=1,
                route_code=work["product_snapshot"]["code"],
                name=work["product_snapshot"]["name"],
                department="",
                summary=Summary(days=days),
                days=[
                    Day(
                        day=n,
                        title=f"ACME 第{n}天",
                        text=texts[n - 1],
                        meals=Meals(
                            breakfast=Meal(text="包含早餐", included=True),
                            lunch=Meal(text="自理", included=False),
                            dinner=Meal(text="自理", included=False),
                        ),
                        overnight="home",
                    )
                    for n in range(1, days + 1)
                ],
                inclusions=["ACME 行程早餐"],
                exclusions=["个人消费", "相邻房间不能保证，以酒店最终确认为准"],
                source=Source(
                    attachment_name=work["file_name"],
                    attachment_url="",
                    bytes=len(payload),
                    etag=work["file_hash"],
                    parsed_at=datetime.now(UTC),
                    parser="acme-fixture-v3",
                ),
                quality=Quality(completeness=1),
            )

        documents.run_once(runtime, worker, store, parse)
        detail = documents.get(runtime, merchant, UUID(asset["id"]))
        staged = documents.stage(
            runtime,
            merchant,
            documents.DocumentReview(
                target_id=product["id"],
                expected_version=detail["product_version"],
                parse_id=detail["parse"]["id"],
                content=RouteDoc.model_validate(detail["parse"]["body"]),
                note="ACME 虚构验收稿逐项核对",
            ),
        )
        commit(runtime, merchant, staged)
    record = {
        "advisor_email": advisor_email,
        "merchant_email": merchant_email,
        "password": password,
        "advisor_org": str(buyer_id),
        "merchant_org": str(supplier_id),
        "year": year,
        "api": "http://127.0.0.1:38126",
    }
    (state / "access.json").write_text(json.dumps(record))
    (state / "access.json").chmod(0o600)
    (state / "database.json").write_text(
        json.dumps(
            {
                k: env[k]
                for k in (
                    "WAREHOUSE_TEST_ADMIN_URL",
                    "WAREHOUSE_TEST_DATABASE_URL",
                    "WAREHOUSE_TEST_AUTH_URL",
                )
            }
        )
    )
    (state / "database.json").chmod(0o600)
    from cloud_warehouse.http_observation import configure_logging

    configure_logging()
    try:
        app = create_app(
            runtime, authentication, agent_factory=factory(runtime, {}), object_store=store
        )
        print("ACME disposable advisor fixture ready", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=38126, access_log=False, proxy_headers=False)
    finally:
        for engine in (runtime, authentication, admin):
            engine.dispose()


if __name__ == "__main__":
    state = ROOT / ".warehouse/advisor-v3"
    state.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    os.environ["WAREHOUSE_ADVISOR_MATERIAL_KEY"] = base64.b64encode(
        secrets.token_bytes(32)
    ).decode()
    with isolated_database(os.environ["WAREHOUSE_CI_ADMIN_URL"]) as environment:
        serve(environment, state)
