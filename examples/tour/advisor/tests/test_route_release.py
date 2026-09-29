"""A chosen route can be taken back, and the whole route reads as the customer will read it."""

import base64
import secrets

import httpx
from fastapi.testclient import TestClient

from tour.advisor import db
from tour.advisor.api import Settings, create_app
from tour.advisor.tests.conftest import database_url
from tour.advisor.tests.test_review import quoted, ready
from tour.advisor.tests.test_story import SLOW, Scripted, warehouse


def test_taking_a_route_back_clears_what_was_made_for_it(env):
    client, engine, owner = env
    deal = ready(client)
    quoted(client, deal)
    released = client.delete(f"/api/deals/{deal}/route").json()
    assert released["route"] is None and released["previous"]["product_id"] == SLOW
    detail = client.get(f"/api/deals/{deal}").json()
    assert detail["route"] is None and detail["departure"] is None
    assert "看团期" not in [c["label"] for c in detail["chips"]]
    with engine.connect() as conn:
        live = conn.execute(
            db.quotes.select().where(db.quotes.c.status == "active", db.quotes.c.deal_id == deal)
        ).all()
        sheets = conn.execute(
            db.confirmations.select().where(
                db.confirmations.c.status != "void", db.confirmations.c.deal_id == deal
            )
        ).all()
    assert not live and not sheets


def test_the_whole_route_is_the_route_kit_page_in_its_customer_view(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_MATERIAL_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    body = {
        "schema": "route-kit/1",
        "title": "ACME 小镇慢游 12 天",
        "cover_asset_id": "c1",
        "days": [],
        "highlights": [],
        "inclusions": [],
        "exclusions": [],
        "notices": [],
        "policies": {},
        "shopping": [],
        "optional_items": [],
        "prices": [],
        "cover_facts": [],
    }

    def routed(request):
        if request.url.path.endswith("/document"):
            return httpx.Response(200, json={"body": body})
        if request.url.path == "/v1/route-media/c1":
            return httpx.Response(
                200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}
            )
        return warehouse(request)

    app = create_app(
        Settings(
            database_url=database_url(),
            warehouse_url="http://warehouse.test",
            material_dir=tmp_path,
        ),
        model=Scripted(),
        transport=httpx.MockTransport(routed),
    )
    with TestClient(app) as client:
        client.post("/api/login", json={"email": "page@acme.example", "password": "x"})
        page = client.get(f"/api/routes/{SLOW}/page")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "ACME 小镇慢游 12 天" in page.text and '"audience": "customer"' in page.text
    assert "data:image/jpeg;base64," in page.text
