import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from cloud_warehouse.admin import grant_auth, grant_runtime, migrate, onboard
from cloud_warehouse.persistence import Principal, engine_for


@pytest.fixture(scope="session")
def database():
    admin_url = os.environ.get("WAREHOUSE_TEST_ADMIN_URL")
    runtime_url = os.environ.get("WAREHOUSE_TEST_DATABASE_URL")
    if not admin_url or not runtime_url:
        pytest.skip("Explicit isolated PostgreSQL test URLs are required")
    assert make_url(admin_url).database.endswith("_test"), "Never run against application data"
    assert make_url(admin_url).database == make_url(runtime_url).database
    migrate(admin_url)
    admin = engine_for(admin_url)
    runtime = engine_for(runtime_url)
    grant_runtime(admin, make_url(runtime_url).username)
    yield admin, runtime
    runtime.dispose()
    admin.dispose()


@dataclass
class Tenant:
    supplier: Principal
    buyer: Principal
    worker: Principal
    connection_id: UUID
    grant_id: UUID


@pytest.fixture
def tenant(database):
    admin, _ = database
    ids = onboard(admin, "ACME Supplier", "ACME Buyer", f"sync-{uuid4()}@acme.example")
    supplier_id, buyer_id = UUID(ids["supplier_id"]), UUID(ids["buyer_id"])
    supplier, buyer = uuid4(), uuid4()
    with admin.begin() as conn:
        for user, org, roles in (
            (supplier, supplier_id, ["supplier_admin"]),
            (buyer, buyer_id, ["advisor"]),
        ):
            conn.execute(
                text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
                {"id": user, "email": f"{user}@acme.example"},
            )
            conn.execute(
                text(
                    "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,:roles)"
                ),
                {"id": user, "org": org, "roles": roles},
            )
    return Tenant(
        Principal(supplier, supplier_id),
        Principal(buyer, buyer_id),
        Principal(UUID(ids["worker_id"]), supplier_id),
        UUID(ids["connection_id"]),
        UUID(ids["grant_id"]),
    )


@pytest.fixture(scope="session")
def authentication(database):
    url = os.environ.get("WAREHOUSE_TEST_AUTH_URL")
    if not url:
        pytest.skip("Separate test authentication role required")
    assert make_url(url).database.endswith("_test")
    admin, _ = database
    grant_auth(admin, make_url(url).username)
    engine = engine_for(url)
    yield engine
    engine.dispose()
