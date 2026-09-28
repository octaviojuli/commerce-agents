"""The disposable database and a logged-in advisor the HTTP contract tests share."""

import base64
import os
import secrets

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from tour.advisor import db, store
from tour.advisor.api import COOKIE, Settings, create_app
from tour.advisor.tests.test_story import Scripted, warehouse


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
