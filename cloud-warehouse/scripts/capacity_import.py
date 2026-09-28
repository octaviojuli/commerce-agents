"""Measure a fictional XLSX import through real HTTP and restricted database roles.

Owns a disposable loopback database and HTTP process. Never loads application secrets.
One complete file is measured, not a latency percentile or a concurrent workload.
"""

import argparse
import json
import os
import platform
import runpy
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.engine import make_url

from cloud_warehouse import imports
from cloud_warehouse.admin import grant_auth, grant_runtime, migrate, onboard
from cloud_warehouse.persistence import Principal, engine_for, require_runtime_role

CAPACITY = runpy.run_path(str(Path(__file__).with_name("capacity.py")))


def workbook(rows):
    if not 1 <= rows <= imports.MAX_ROWS:
        raise ValueError("Import capacity rows must be within the supported file limit")
    book = Workbook()
    sheet = book.active
    sheet.title = "团期导入"
    sheet.append(list(imports.HEADERS))
    for number in range(rows):
        product = number // 10
        sheet.append(
            [
                f"ACME-R-{product}",
                f"ACME 环线 {product}",
                3,
                "ACME 城市",
                f"ACME-D-{number}",
                "2030-10-01",
                "2030-10-03",
                100,
                2,
                3,
            ]
        )
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def seed(admin):
    if not admin.url.database.startswith("warehouse_ci_") or not admin.url.database.endswith(
        "_test"
    ):
        raise ValueError("Import capacity requires an owned disposable database")
    ids = {
        key: UUID(value)
        for key, value in onboard(
            admin, "ACME Excel Supplier", "ACME Buyer", f"worker-{uuid4()}@acme.example"
        ).items()
    }
    clients = []
    with admin.begin() as conn:
        conn.execute(
            text("""UPDATE supplier_connection SET connector_type='excel',
              capabilities='{"inventory_owner":"warehouse"}'::jsonb WHERE id=:id"""),
            {"id": ids["connection_id"]},
        )
        for organization, role in (
            (ids["supplier_id"], "supplier_admin"),
            (ids["buyer_id"], "advisor"),
        ):
            user = uuid4()
            conn.execute(
                text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
                {"id": user, "email": f"{user}@acme.example"},
            )
            conn.execute(
                text(
                    "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,:roles)"
                ),
                {"id": user, "org": organization, "roles": [role]},
            )
            clients.append({"actor": Principal(user, organization)})
    CAPACITY["seed_sessions"](admin, clients)
    return ids["connection_id"], clients


def measure(admin, http, source, clients, body, rows):
    timings = {}

    def post(name, path, expected=200, **kwargs):
        started = time.perf_counter()
        response = http.post(path, headers=clients[0]["headers"], timeout=600, **kwargs)
        timings[name] = round(time.perf_counter() - started, 4)
        if response.status_code != expected:
            raise CAPACITY["HttpFailure"](response.status_code)
        assert response.headers["cache-control"] == "no-store"
        return response.json()

    params = {"connection_id": str(source)}
    # These requests exercise real authentication and business roles, before measurement.
    assert http.post("/v1/imports", params=params, content=body).status_code == 401
    assert (
        http.post(
            "/v1/imports", params=params, content=body, headers=clients[1]["headers"]
        ).status_code
        == 403
    )
    started = time.perf_counter()
    parsed, errors = imports.parse_xlsx(body)
    timings["validation"] = round(time.perf_counter() - started, 4)
    assert len(parsed) == rows and not errors
    batch = post("upload", "/v1/imports", expected=201, params=params, content=body)
    assert batch["status"] == "validated" and batch["row_count"] == rows
    proposal = post("preview", f"/v1/imports/{batch['id']}/preview")
    with admin.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM departure")) == 0
        assert conn.scalar(text("SELECT count(*) FROM inventory_pool")) == 0
    post(
        "approval",
        f"/v1/changes/{proposal['id']}/approve",
        json={"payload_hash": proposal["payload_hash"]},
    )
    result = post("publication", f"/v1/changes/{proposal['id']}/apply")
    assert result["departures"] == rows and result["inventory_movements"] == rows
    assert post("replay", f"/v1/changes/{proposal['id']}/apply") == result
    duplicate = post("duplicate_upload", "/v1/imports", expected=201, params=params, content=body)
    assert duplicate["id"] == batch["id"] and duplicate["duplicate"]
    assert duplicate["status"] == "published"
    with admin.connect() as conn:
        counts = {
            table: conn.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("supplier_product", "departure", "inventory_pool", "inventory_movement")
        }
        assert counts == {
            "supplier_product": (rows + 9) // 10,
            "departure": rows,
            "inventory_pool": rows,
            "inventory_movement": rows,
        }
        assert (
            conn.scalar(
                text("""SELECT count(*) FROM inventory_pool p
              LEFT JOIN (SELECT pool_id,sum(delta_total) total,sum(delta_sold) sold,
                sum(delta_blocked) blocked,count(*) movements FROM inventory_movement
                GROUP BY pool_id) m ON m.pool_id=p.id
              WHERE m.pool_id IS NULL OR m.movements<>1 OR p.total<>100 OR p.sold<>2
                OR p.blocked<>3 OR p.held<>0 OR p.total<>m.total OR p.sold<>m.sold
                OR p.blocked<>m.blocked""")
            )
            == 0
        )
        database_version = conn.scalar(text("SHOW server_version"))
    targets = {"upload": 2.0, "validation": 60.0}
    return {
        "seconds": timings,
        "targets_seconds": targets,
        "target_results": {name: timings[name] <= target for name, target in targets.items()},
        "passed": all(timings[name] <= target for name, target in targets.items()),
        "counts": counts,
        "draft_did_not_publish": True,
        "ledger_matches_every_pool": True,
        "replay_did_not_duplicate": True,
        "unauthenticated_and_buyer_upload_denied": True,
        "postgres": database_version,
    }


def run(value, rows=5000):
    body = workbook(rows)
    with CAPACITY["ISOLATED"](value) as environment:
        migrate(environment["WAREHOUSE_TEST_ADMIN_URL"])
        admin = engine_for(environment["WAREHOUSE_TEST_ADMIN_URL"])
        runtime = engine_for(
            make_url(environment["WAREHOUSE_TEST_DATABASE_URL"]).update_query_dict(
                {"options": "-c statement_timeout=30000 -c lock_timeout=5000"}
            )
        )
        authentication = engine_for(environment["WAREHOUSE_TEST_AUTH_URL"])
        try:
            grant_runtime(admin, runtime.url.username)
            require_runtime_role(runtime)
            grant_auth(admin, authentication.url.username)
            source, clients = seed(admin)
            with CAPACITY["http_server"](runtime, authentication, 1, "process") as http:
                result = measure(admin, http, source, clients, body, rows)
        finally:
            authentication.dispose()
            runtime.dispose()
            admin.dispose()
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "rows": rows,
        "file_bytes": len(body),
        "boundary": "one fictional XLSX through loopback HTTP, fixture sessions, real roles, RLS and explicit fixture approval; excludes login issuance, TLS/proxy, browser, upstream and concurrent traffic",
        "validation_boundary": "standalone parser in client process; HTTP upload separately includes server validation and persistence",
        "environment": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "http_server_stopped": True,
        "disposable_resources_cleaned": True,
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, "Report already exists; choose a new path\n")
    value = os.environ.get("WAREHOUSE_CAPACITY_ADMIN_URL")
    if not value:
        parser.exit(2, "Explicit isolated loopback maintenance URL required\n")
    try:
        report = run(value, args.rows)
    except Exception as error:
        parser.exit(1, f"Import capacity failed ({type(error).__name__}); no success report\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
