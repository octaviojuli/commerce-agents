"""Contracts the review found broken: a price, a sale, a need and a session each belong to one
deal, one version and one live login."""

import asyncio
import base64
import json
import os
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from tour.advisor import closing, db, selling, store
from tour.advisor.api import COOKIE, Settings, create_app
from tour.advisor.model import Understanding
from tour.advisor.need import Need
from tour.advisor.tests.test_story import CITY, DEP, SLOW, Scripted, warehouse
from tour.advisor.turns import Turns
from tour.advisor.warehouse import Warehouse


def database_url():
    url = os.environ.get("WAREHOUSE_TEST_ADMIN_URL")
    if not url:
        # The database CI job runs these with zero skips; plain unit runs have no database.
        pytest.skip("An isolated PostgreSQL test database is required")
    return url


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = database_url()
    assert make_url(url).database.endswith("_test")
    monkeypatch.setenv("ADVISOR_MATERIAL_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    app = create_app(
        Settings(database_url=url, warehouse_url="http://warehouse.test", material_dir=tmp_path),
        model=Scripted(),
        transport=httpx.MockTransport(warehouse),
    )
    engine = db.connect(url)
    with TestClient(app) as client:
        client.post(
            "/api/login",
            json={"email": f"review-{secrets.token_hex(6)}@acme.example", "password": "x"},
        ).raise_for_status()
        with engine.connect() as conn:
            row = (
                conn.execute(db.sessions.select().where(db.sessions.c.id == client.cookies[COOKIE]))
                .mappings()
                .one()
            )
        owner = store.Owner(row["org_id"], row["user_id"])
        yield client, engine, owner
    engine.dispose()


def ready(client, title="ACME 测试需求"):
    deal = client.post("/api/deals", json={"title": title}).json()["id"]
    for field, value in {
        "window": {"start": "2026-12-01", "end": "2026-12-31"},
        "destinations": {"must": ["德国", "法国", "意大利", "瑞士"]},
        "party": {"adults": 2, "children": [], "seniors": []},
        "rooms": {"twins": 1},
    }.items():
        client.put(f"/api/deals/{deal}/need/{field}", json={"value": value}).raise_for_status()
    client.post(
        f"/api/deals/{deal}/route", json={"product_id": SLOW, "title": "ACME 原线路"}
    ).raise_for_status()
    client.post(
        f"/api/deals/{deal}/departure",
        json={
            "departure_id": DEP,
            "offer_id": "o1",
            "date": "2026-12-28",
            "return_date": "2027-01-08",
        },
    ).raise_for_status()
    return deal


def quoted(client, deal, formal=True):
    if formal:
        sheet = client.post(f"/api/deals/{deal}/confirmation").json()
        client.post(
            f"/api/deals/{deal}/confirmation/{sheet['id']}", json={"evidence": "ACME 客人确认"}
        ).raise_for_status()
        response = client.post(f"/api/deals/{deal}/quotes/formal")
    else:
        response = client.post(f"/api/deals/{deal}/dates/{DEP}/price")
    response.raise_for_status()
    quote = response.json()
    assert quote["valid"], quote
    return quote


def test_changing_route_invalidates_old_quote_and_send(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.post(
        f"/api/deals/{deal}/route", json={"product_id": CITY, "title": "ACME 新线路"}
    ).raise_for_status()
    current = client.get(f"/api/deals/{deal}/quotes").json()["items"][0]
    sent = client.post(f"/api/deals/{deal}/quotes/{quote['id']}/send")
    assert not current["valid"] and sent.status_code == 409, (
        current,
        sent.status_code,
        sent.json(),
    )


def test_changing_window_invalidates_old_quote(env):
    client, _, _ = env
    deal = ready(client)
    quoted(client, deal)
    client.put(
        f"/api/deals/{deal}/need/window",
        json={"value": {"start": "2027-04-01", "end": "2027-04-30"}},
    ).raise_for_status()
    current = client.get(f"/api/deals/{deal}/quotes").json()["items"][0]
    assert not current["valid"], current


def test_repeated_sale_does_not_double_money(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    body = {"quote_id": quote["id"], "deposit": 10000}
    first = client.post(f"/api/deals/{deal}/sale", json=body)
    first.raise_for_status()
    second = client.post(f"/api/deals/{deal}/sale", json=body)
    after = client.get(f"/api/deals/{deal}/after").json()
    assert after["money"] == first.json()["money"], (
        second.status_code,
        first.json()["money"],
        after["money"],
    )


def test_sale_rejects_another_deals_quote(env):
    client, _, _ = env
    deal_a = ready(client, "ACME 客人甲")
    quote = quoted(client, deal_a)
    deal_b = ready(client, "ACME 客人乙")
    response = client.post(
        f"/api/deals/{deal_b}/sale", json={"quote_id": quote["id"], "deposit": 10000}
    )
    assert response.status_code in (404, 409), response.json()


def test_late_model_fill_preserves_manual_edit(env):
    client, engine, owner = env
    deal = UUID(ready(client))

    class InterleavedModel(Scripted):
        async def call(self, name, value):
            if name == "understand":
                # Equivalent to a manual save while the model awaits a response.
                closing.edit_need(engine, owner, deal, "depart_city", "杭州")
                return Understanding.model_validate(
                    {
                        "kinds": ["new_need"],
                        "changes": [
                            {"field": "depart_city", "value": "上海", "evidence": "从上海出发"},
                        ],
                    }
                )
            return await super().call(name, value)

    async def run():
        wh = Warehouse(
            "http://warehouse.test",
            "t",
            str(owner.org_id),
            transport=httpx.MockTransport(warehouse),
        )
        try:
            return await Turns(engine, InterleavedModel()).run(owner, wh, deal, "从上海出发")
        finally:
            await wh.aclose()

    asyncio.run(run())
    with engine.connect() as conn:
        saved = Need.model_validate(store.deal(conn, owner, deal)["need"])
    assert saved.get("depart_city") == "杭州", saved.depart_city


def test_plan_cannot_stamp_old_content_with_new_need_version(env):
    client, engine, owner = env
    deal = UUID(ready(client))
    changed = False

    class InterleavedWarehouse(Warehouse):
        async def document(self, *args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                closing.edit_need(
                    engine, owner, deal, "party", {"adults": 3, "children": [], "seniors": []}
                )
            return await super().document(*args, **kwargs)

    async def run():
        wh = InterleavedWarehouse(
            "http://warehouse.test",
            "t",
            str(owner.org_id),
            transport=httpx.MockTransport(warehouse),
        )
        try:
            return await selling.build_plan(engine, owner, wh, deal, [SLOW])
        finally:
            await wh.aclose()

    try:
        plan = asyncio.run(run())
    except store.Conflict:
        return
    with engine.connect() as conn:
        current = store.deal(conn, owner, deal)
    assert plan["need_version"] != current["need_version"] or "3" in plan["body"]["need"], (
        plan["need_version"],
        current["need_version"],
        plan["body"]["need"],
    )


def test_public_plan_stops_displaying_expired_prices(env, monkeypatch):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    plan = client.post(f"/api/deals/{deal}/plans", json={"product_ids": [SLOW]}).json()
    token = client.post(f"/api/deals/{deal}/plans/{plan['id']}/share").json()["token"]
    future = datetime.fromisoformat(quote["valid_until"]) + timedelta(days=1)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(selling, "datetime", Later)
    page = client.get(f"/api/public/plans/{token}").json()
    assert page["status"] != "sent" or all(
        r["total"] is None and r["per_person"] is None for r in page["routes"]
    ), page


def test_multiple_offers_require_explicit_choice(env):
    client, engine, owner = env
    deal = UUID(ready(client))
    calls = []

    def multiple(request):
        if request.url.path == "/v1/advisor/offers":
            return httpx.Response(
                200, json={"items": [{"id": "o1", "active": True}, {"id": "o2", "active": True}]}
            )
        if request.url.path == "/v1/advisor/quotes":
            calls.append(request.content.decode())
        return warehouse(request)

    async def run():
        wh = Warehouse(
            "http://warehouse.test", "t", str(owner.org_id), transport=httpx.MockTransport(multiple)
        )
        try:
            return await closing.price_departure(engine, owner, wh, deal, DEP)
        finally:
            await wh.aclose()

    try:
        result = asyncio.run(run())
    except (ValueError, store.Conflict):
        assert not calls
        return
    pytest.fail(
        f"No explicit offer choice, but a quote was returned: valid={result['valid']}; requests={calls}"
    )


def test_changed_sales_price_keeps_customer_breakdown_consistent(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.put(
        f"/api/deals/{deal}/quotes/{quote['id']}", json={"sales_total": 32000}
    ).raise_for_status()
    current = client.get(f"/api/deals/{deal}/quotes").json()["items"][0]
    sent = client.post(f"/api/deals/{deal}/quotes/{quote['id']}/send").json()
    lines_total = sum(
        (Decimal(line["total"]) for line in current["lines"] if line["total"]), Decimal(0)
    )
    assert lines_total == Decimal(current["sales_total"]), (
        lines_total,
        current["sales_total"],
        sent,
    )


def test_revoked_warehouse_session_cannot_keep_reading_private_deals(tmp_path, monkeypatch):
    url = database_url()
    assert make_url(url).database.endswith("_test")
    monkeypatch.setenv("ADVISOR_MATERIAL_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    revoked = False

    def controlled(request):
        if revoked:
            return httpx.Response(401, json={"message": "Session revoked"})
        return warehouse(request)

    app = create_app(
        Settings(database_url=url, warehouse_url="http://warehouse.test", material_dir=tmp_path),
        model=Scripted(),
        transport=httpx.MockTransport(controlled),
    )
    with TestClient(app) as client:
        client.post(
            "/api/login",
            json={"email": f"review-{secrets.token_hex(6)}@acme.example", "password": "x"},
        ).raise_for_status()
        deal = ready(client)
        revoked = True
        upstream = client.get("/api/routes")
        # The upstream denial is visible to this app, but its local session remains alive.
        assert upstream.status_code == 401, upstream.text
        private = client.get(f"/api/deals/{deal}")
        assert private.status_code in (401, 403), (private.status_code, private.json().get("title"))


def test_a_retried_receipt_is_recorded_once_and_a_second_sale_is_refused(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.post(f"/api/deals/{deal}/sale", json={"quote_id": quote["id"]}).raise_for_status()
    for _ in range(2):
        client.post(
            f"/api/deals/{deal}/receipts", json={"amount": 5000}, headers={"Idempotency-Key": "r-1"}
        ).raise_for_status()
    assert client.get(f"/api/deals/{deal}/after").json()["money"]["received"] == "5000.00"
    other = client.post(f"/api/deals/{deal}/quotes/formal").json()
    again = client.post(f"/api/deals/{deal}/sale", json={"quote_id": other["id"]})
    assert again.status_code == 409, again.json()


def test_conversation_route_change_uses_same_invalidation_as_button(env):
    client, engine, owner = env
    deal_id = ready(client)
    quoted(client, deal_id)
    deal = UUID(deal_id)
    with engine.begin() as conn:
        store.add(
            conn,
            owner,
            db.searches,
            deal_id=deal,
            need_version=4,
            body={
                "cards": [
                    {"product_id": CITY, "title": "ACME 城市巡游"},
                ]
            },
        )

    class SelectModel(Scripted):
        async def call(self, name, payload):
            if name == "understand":
                return Understanding.model_validate(
                    {"kinds": ["decide"], "selection": {"route": "城市巡游"}}
                )
            return await super().call(name, payload)

    async def run():
        wh = Warehouse(
            "http://warehouse.test",
            "t",
            str(owner.org_id),
            transport=httpx.MockTransport(warehouse),
        )
        try:
            return await Turns(engine, SelectModel()).run(owner, wh, deal, "就选城市巡游那条")
        finally:
            await wh.aclose()

    asyncio.run(run())
    with engine.connect() as conn:
        current = store.deal(conn, owner, deal)
        sheets = store.rows(conn, owner, db.confirmations, deal)
    assert current["route"]["product_id"] == CITY
    response = client.post(f"/api/deals/{deal}/quotes/formal")
    sent = None
    if response.status_code == 201:
        sent = client.post(f"/api/deals/{deal}/quotes/{response.json()['id']}/send").json()
    assert (
        current["departure"] is None
        and all(s["status"] == "void" for s in sheets)
        and response.status_code == 409
    ), (
        current["route"],
        current["departure"],
        [s["status"] for s in sheets],
        response.status_code,
        sent,
    )


def test_void_confirmation_cannot_be_revived_after_offer_change(env):
    client, engine, owner = env
    deal = ready(client)
    quoted(client, deal)
    old = client.get(f"/api/deals/{deal}/confirmation").json()["current"]
    client.post(
        f"/api/deals/{deal}/departure",
        json={
            "departure_id": DEP,
            "offer_id": "o2",
            "date": "2026-12-28",
            "return_date": "2027-01-08",
        },
    ).raise_for_status()
    with engine.connect() as conn:
        assert store.one(conn, owner, db.confirmations, UUID(old["id"]))["status"] == "void"
    late_reply = client.post(
        f"/api/deals/{deal}/confirmation/{old['id']}",
        json={"confirmed": True, "evidence": "对原套餐的迟到确认"},
    )
    formal = client.post(f"/api/deals/{deal}/quotes/formal")
    assert late_reply.status_code in (404, 409) and formal.status_code == 409, (
        late_reply.status_code,
        late_reply.json(),
        formal.status_code,
    )


def multi_offer(request, calls):
    calls.append((request.url.path, request.content.decode() if request.content else ""))
    if request.url.path == "/v1/advisor/offers":
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "o1", "code": "base", "name": "ACME 标准", "active": True},
                    {"id": "o2", "code": "upgrade", "name": "ACME 升级", "active": True},
                ]
            },
        )
    return warehouse(request)


def test_catalog_base_offer_does_not_silently_choose_for_advisor(env):
    client, engine, owner = env
    deal = UUID(ready(client))
    calls = []
    with engine.begin() as conn:
        store.update_deal(conn, owner, deal, departure=None)

    async def run():
        wh = Warehouse(
            "http://warehouse.test",
            "t",
            str(owner.org_id),
            transport=httpx.MockTransport(lambda r: multi_offer(r, calls)),
        )
        try:
            dates = await closing.dates(engine, owner, wh, deal)
            item = dates["items"][0]
            # This is offerOf(d) in DatesPage before any human selection: picked is empty.
            implicit_offer = {}.get(item["departure_id"], item["offer_id"])
            return await closing.price_departure(
                engine, owner, wh, deal, item["departure_id"], implicit_offer
            )
        finally:
            await wh.aclose()

    try:
        result = asyncio.run(run())
    except store.OfferChoice:
        return
    quoted_calls = [json.loads(body) for path, body in calls if path == "/v1/advisor/quotes"]
    assert not quoted_calls, (result["valid"], quoted_calls, [p for p, _ in calls])


def test_selected_nonbase_offer_survives_dates_reload(env):
    client, engine, owner = env
    deal = UUID(ready(client))
    calls = []
    with engine.begin() as conn:
        store.update_deal(conn, owner, deal, departure=None)

    async def run():
        wh = Warehouse(
            "http://warehouse.test",
            "t",
            str(owner.org_id),
            transport=httpx.MockTransport(lambda r: multi_offer(r, calls)),
        )
        try:
            check = await closing.price_departure(engine, owner, wh, deal, DEP, "o2")
            assert check["valid"], check
            return await closing.dates(engine, owner, wh, deal)
        finally:
            await wh.aclose()

    result = asyncio.run(run())
    item = result["items"][0]
    assert item["offer_id"] == "o2" and item["price"] is not None, item


def test_receipt_key_reuse_with_changed_amount_is_rejected(env):
    client, _, _ = env
    deal = ready(client)
    quote = quoted(client, deal)
    client.post(f"/api/deals/{deal}/sale", json={"quote_id": quote["id"]}).raise_for_status()
    headers = {"Idempotency-Key": "ACME-receipt-entry"}
    client.post(
        f"/api/deals/{deal}/receipts", json={"amount": 5000}, headers=headers
    ).raise_for_status()
    retry = client.post(f"/api/deals/{deal}/receipts", json={"amount": 8000}, headers=headers)
    assert retry.status_code == 409, (retry.status_code, retry.json()["money"])


def test_expired_price_is_also_removed_from_customer_reasons(env, monkeypatch):
    client, _, _ = env
    deal = ready(client)
    client.put(
        f"/api/deals/{deal}/need/budget", json={"value": {"per_person": 20000}}
    ).raise_for_status()
    quote = quoted(client, deal)
    plan_response = client.post(f"/api/deals/{deal}/plans", json={"product_ids": [SLOW, CITY]})
    plan_response.raise_for_status()
    plan = plan_response.json()
    token = client.post(f"/api/deals/{deal}/plans/{plan['id']}/share").json()["token"]
    before = client.get(f"/api/public/plans/{token}").json()
    assert "14,800" in before["routes"][0]["why"], before
    future = datetime.fromisoformat(quote["valid_until"]) + timedelta(days=1)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(selling, "datetime", Later)
    page = client.get(f"/api/public/plans/{token}").json()
    assert page["routes"][0]["total"] is None
    assert "14,800" not in page["routes"][0]["why"], page["routes"][0]


def test_a_departure_of_another_route_is_not_priced(env):
    client, _, _ = env
    deal = ready(client)
    response = client.post(f"/api/deals/{deal}/dates/WD-not-this-route/price")
    assert response.status_code == 409, response.json()


def test_an_upgrade_stops_on_duplicate_sales_and_keeps_them(env):
    _, engine, owner = env
    from sqlalchemy import text

    deal = UUID(int=7)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS advisor.ledger_one_sale"))
        for _ in range(2):
            store.add(
                conn,
                owner,
                db.ledger,
                deal_id=deal,
                kind="sale",
                amount=Decimal(1),
                occurred_on=datetime.now().date(),
            )
    try:
        with pytest.raises(RuntimeError, match="重复成交"):
            db.migrate(engine)
        with engine.connect() as conn:
            assert len(store.rows(conn, owner, db.ledger, deal)) == 2
    finally:
        with engine.begin() as conn:
            conn.execute(db.ledger.delete().where(db.ledger.c.deal_id == deal))
        db.migrate(engine)
