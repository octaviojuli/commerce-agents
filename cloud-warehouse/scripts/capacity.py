"""Reproducible ACME capacity probe in a disposable local PostgreSQL database.

Measures services or a real loopback HTTP server with RLS and restricted database pools.
It excludes browser, model generation and upstream APIs; it is not a production SLA proof.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import runpy
import secrets
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import uvicorn
from sqlalchemy import text
from sqlalchemy.engine import make_url

from cloud_warehouse import quotes, search
from cloud_warehouse.admin import grant_auth, grant_runtime, migrate, onboard
from cloud_warehouse.advisor import WarehouseAdvisorBackend, product_id
from cloud_warehouse.api import create_app
from cloud_warehouse.catalog import list_departures
from cloud_warehouse.persistence import Principal, engine_for, require_runtime_role, transaction
from cloud_warehouse.pricing import PriceSchedule
from shopping_agent import ShoppingSessionContext

ISOLATED = runpy.run_path(str(Path(__file__).with_name("ci_database.py")))["isolated_database"]
HTTP_HELPER = runpy.run_path(str(Path(__file__).with_name("capacity_http.py")))
TIMED_APP = HTTP_HELPER["timed_application"]
PROCESS_POOL_SIZE = HTTP_HELPER["process_pool_size"]
PROFILES = {
    "smoke": {
        "suppliers": 2,
        "products_per_supplier": 10,
        "departures_per_product": 5,
        "concurrency": 4,
        "requests": 40,
    },
    "baseline": {
        "suppliers": 50,
        "products_per_supplier": 200,
        "departures_per_product": 10,
        "concurrency": 100,
        "requests": 400,
    },
    "extended": {
        "suppliers": 50,
        "products_per_supplier": 200,
        "departures_per_product": 10,
        "concurrency": 100,
        "requests": 20000,
    },
}
TARGETS = {"catalog": 800, "departures": 800, "managed_stock": 300, "managed_quote": 500}


def seed(admin, runtime, profile):
    """Fictional bulk setup only; never run on an application database."""
    if not admin.url.database.startswith("warehouse_ci_") or not admin.url.database.endswith(
        "_test"
    ):
        raise ValueError("Capacity seed requires a database created by isolated_database")
    sources, buyers = [], []
    schedule = PriceSchedule(
        currency="CNY",
        market={"adult": "120.00"},
        settlement={"adult": "100.00"},
        fees_complete=True,
        child_occupies_seat=True,
    )
    for number in range(profile["suppliers"]):
        ids = {
            key: UUID(value)
            for key, value in onboard(
                admin,
                f"ACME supplier {number}",
                f"ACME buyer {number}-0",
                f"worker-{uuid4()}@acme.example",
            ).items()
        }
        owner, source, worker = ids["supplier_id"], ids["connection_id"], ids["worker_id"]
        managed = number % 2 == 0
        with admin.begin() as conn:
            conn.execute(
                text(
                    "UPDATE supplier_connection SET connector_type=:kind,capabilities=CAST(:caps AS jsonb) WHERE id=:id"
                ),
                {
                    "id": source,
                    "kind": "excel" if managed else "tour_b2b",
                    "caps": json.dumps(
                        {
                            "catalog_read": not managed,
                            "inventory_owner": "warehouse" if managed else "supplier",
                        }
                    ),
                },
            )
            for ordinal, buyer in enumerate((ids["buyer_id"], uuid4())):
                if ordinal:
                    conn.execute(
                        text(
                            "INSERT INTO organization(id,name,kinds) VALUES(:id,:name,ARRAY['buyer'])"
                        ),
                        {"id": buyer, "name": f"ACME buyer {number}-1"},
                    )
                    conn.execute(
                        text(
                            "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:owner,:buyer,:source)"
                        ),
                        {"id": uuid4(), "owner": owner, "buyer": buyer, "source": source},
                    )
                user = uuid4()
                conn.execute(
                    text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
                    {"id": user, "email": f"{user}@acme.example"},
                )
                conn.execute(
                    text(
                        "INSERT INTO membership(user_id,organization_id,roles) VALUES(:user,:org,ARRAY['advisor'])"
                    ),
                    {"user": user, "org": buyer},
                )
                buyers.append({"actor": Principal(user, buyer), "supplier": number})
            batch = uuid4()
            conn.execute(
                text(
                    "INSERT INTO sync_run(id,supplier_org_id,connection_id,status,actor_id,completed_at) VALUES(:id,:owner,:source,'published',:worker,now())"
                ),
                {"id": batch, "owner": owner, "source": source, "worker": worker},
            )
            conn.execute(
                text("""INSERT INTO supplier_product(id,supplier_org_id,connection_id,external_id,code,name,days,gateway,source,source_hash,status,observed_at,sync_run_id)
              SELECT gen_random_uuid(),:owner,:source,n::text,'ACME-R-'||n,'ACME 环线 '||n,3,'ACME 城市','{}','ACME','published',now(),:batch
              FROM generate_series(1,:products) n"""),
                {
                    "owner": owner,
                    "source": source,
                    "batch": batch,
                    "products": profile["products_per_supplier"],
                },
            )
            conn.execute(
                text("""INSERT INTO departure(id,product_id,supplier_org_id,connection_id,external_id,code,depart_date,return_date,available_seats,source,source_hash,observed_at,availability_expires_at,sync_run_id)
              SELECT gen_random_uuid(),p.id,:owner,:source,p.external_id||'-'||n,'ACME-D-'||p.external_id||'-'||n,
                current_date+60+n,current_date+62+n,100,'{}','ACME',now(),now()+interval '1 day',:batch
              FROM supplier_product p CROSS JOIN generate_series(1,:departures) n WHERE p.connection_id=:source"""),
                {
                    "owner": owner,
                    "source": source,
                    "batch": batch,
                    "departures": profile["departures_per_product"],
                },
            )
            if managed:
                # Retain genuine pool/ledger/version invariants despite bulk fixture setup.
                conn.execute(
                    text("""INSERT INTO inventory_pool(id,supplier_org_id,departure_id,total)
                  SELECT gen_random_uuid(),:owner,id,100 FROM departure WHERE connection_id=:source"""),
                    {"owner": owner, "source": source},
                )
                conn.execute(
                    text("""INSERT INTO change_request(id,organization_id,kind,payload,payload_hash,target_id,expected_version,created_by,status,applied_by,applied_at,result)
                  SELECT i.id,:owner,'inventory','{}','ACME',i.id,0,:worker,'applied',:worker,now(),'{}'
                  FROM inventory_pool i WHERE i.supplier_org_id=:owner"""),
                    {"owner": owner, "worker": worker},
                )
                conn.execute(
                    text("""INSERT INTO inventory_movement(id,supplier_org_id,pool_id,change_id,kind,business_key,delta_total,reason,actor_id)
                  SELECT gen_random_uuid(),:owner,i.id,i.id,'open','ACME-opening',100,'ACME capacity fixture',:worker
                  FROM inventory_pool i WHERE i.supplier_org_id=:owner"""),
                    {"owner": owner, "worker": worker},
                )
                conn.execute(
                    text("""INSERT INTO contract_price(id,supplier_org_id,connection_id,offer_id,layer,schedule,source_ref,valid_from,valid_until)
                  SELECT gen_random_uuid(),:owner,:source,id,'standard',CAST(:schedule AS jsonb),'ACME capacity fixture',now()-interval '1 day',now()+interval '1 year'
                  FROM offer WHERE connection_id=:source"""),
                    {"owner": owner, "source": source, "schedule": schedule.model_dump_json()},
                )
            product = conn.scalar(
                text("SELECT id FROM supplier_product WHERE connection_id=:id ORDER BY id LIMIT 1"),
                {"id": source},
            )
            departures = list(
                conn.execute(
                    text("SELECT id FROM departure WHERE product_id=:id ORDER BY id"),
                    {"id": product},
                ).scalars()
            )
            offer = (
                conn.execute(
                    text(
                        "SELECT o.id,d.depart_date FROM offer o JOIN departure d ON d.id=o.departure_id WHERE d.product_id=:id ORDER BY o.id LIMIT 1"
                    ),
                    {"id": product},
                )
                .mappings()
                .one()
            )
        with transaction(runtime, Principal(worker, owner)) as conn:
            search.rebuild(conn)
        sources.append(
            {
                "owner": owner,
                "connection": source,
                "product": product,
                "departures": departures,
                "offer": dict(offer),
            }
        )
    # Every buyer has two suppliers with overlapping external IDs; returned IDs must
    # stay inside that live grant set, not merely be present somewhere in the fixture.
    with admin.begin() as conn:
        for buyer in buyers:
            own = buyer["supplier"]
            other = (own + 1) % len(sources)
            extra = sources[other]
            conn.execute(
                text(
                    "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:owner,:buyer,:source)"
                ),
                {
                    "id": uuid4(),
                    "owner": extra["owner"],
                    "buyer": buyer["actor"].organization_id,
                    "source": extra["connection"],
                },
            )
            buyer["owners"] = {str(sources[own]["owner"]), str(extra["owner"])}
            buyer["sample"] = sources[own if own % 2 == 0 else other]
        counts = {
            name: conn.scalar(text(f"SELECT count(*) FROM {name}"))
            for name in ("supplier_product", "departure", "inventory_pool", "contract_price")
        }
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("ANALYZE"))
    return buyers, counts


def request(runtime, client, operation, key):
    actor, sample = client["actor"], client["sample"]
    backend = WarehouseAdvisorBackend(runtime, actor)
    session = ShoppingSessionContext(session_id=key, user_id=str(actor.user_id))
    if operation == "catalog":
        page = backend.catalog_page(session, query="ACME 环线", limit=25)
        assert page["items"] and all(
            p.attributes["supplier_id"] in client["owners"] for p in page["items"]
        )
    elif operation == "departures":
        page = backend.departures_page(session, product_id(sample["product"]), limit=25)
        assert len(page["items"]) == len(sample["departures"])
        assert all(p.attributes["supplier_id"] in client["owners"] for p in page["items"])
    elif operation == "managed_stock":
        rows = list_departures(runtime, actor, product_id=sample["product"])
        assert {r["id"] for r in rows} == set(sample["departures"])
        assert all(
            r["available_seats"] == 100 and r["availability_status"] == "managed" for r in rows
        )
    else:
        body = asyncio.run(
            quotes.create(
                runtime,
                actor,
                quotes.QuoteRequest(
                    offer_id=sample["offer"]["id"],
                    departure_date=sample["offer"]["depart_date"],
                    party=quotes.Party(adults=2),
                ),
                key,
            )
        )
        assert (
            body["settlement_total"] == "200.00"
            and body["availability"] == "available"
            and "available_seats" not in body
            and body["reservation_created"] is False
        )


class HttpFailure(Exception):
    def __init__(self, status):
        self.code = f"HTTP_{int(status)}"
        super().__init__(self.code)


def seed_sessions(admin, clients):
    """Issue fixture sessions; measured HTTP requests still run real authentication."""
    if not admin.url.database.startswith("warehouse_ci_") or not admin.url.database.endswith(
        "_test"
    ):
        raise ValueError("Fixture sessions require a disposable capacity database")
    with admin.begin() as conn:
        for client in clients:
            token = secrets.token_urlsafe(32)
            conn.execute(
                text(
                    "INSERT INTO auth_session(token_hash,user_id,expires_at) VALUES(:hash,:user,now()+interval '1 hour')"
                ),
                {
                    "hash": hashlib.sha256(token.encode()).hexdigest(),
                    "user": client["actor"].user_id,
                },
            )
            client["headers"] = {
                "Authorization": "Bearer " + token,
                "X-Organization-Id": str(client["actor"].organization_id),
            }


def http_request(http, client, operation, key):
    sample, headers = client["sample"], client["headers"]
    if operation == "catalog":
        response = http.get(
            "/v1/advisor/products", headers=headers, params={"query": "ACME 环线", "limit": 25}
        )
    elif operation == "departures":
        response = http.get(
            "/v1/advisor/departures",
            headers=headers,
            params={"product_id": product_id(sample["product"]), "limit": 25},
        )
    elif operation == "managed_stock":
        response = http.get(
            "/v1/departures", headers=headers, params={"product_id": str(sample["product"])}
        )
    else:
        response = http.post(
            "/v1/quotes",
            headers={**headers, "Idempotency-Key": key},
            json={
                "offer_id": str(sample["offer"]["id"]),
                "departure_date": sample["offer"]["depart_date"].isoformat(),
                "party": {"adults": 2},
            },
        )
    if response.status_code != (201 if operation == "managed_quote" else 200):
        raise HttpFailure(response.status_code)
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    if operation in ("catalog", "departures"):
        assert body["items"] and all(
            row["attributes"]["supplier_id"] in client["owners"] for row in body["items"]
        )
        if operation == "departures":
            assert {row["product_id"] for row in body["items"]} == {
                "WD-" + str(value) for value in sample["departures"]
            }
    elif operation == "managed_stock":
        assert {row["id"] for row in body["items"]} == {
            str(value) for value in sample["departures"]
        }
        assert all(
            row["availability"] == "available"
            and "available_seats" not in row
            and row["availability_status"] == "managed"
            for row in body["items"]
        )
    else:
        assert (
            body["settlement_total"] == "200.00"
            and body["availability"] == "available"
            and "available_seats" not in body
            and body["reservation_created"] is False
        )
    return {
        "server_ms": float(response.headers["x-capacity-server-ms"]),
        "server_index": int(response.headers["x-capacity-server-index"]),
    }


@contextmanager
def http_clients(base_url, concurrency, mode):
    with ExitStack() as stack:
        count, maximum = (1, concurrency) if mode == "shared" else (concurrency + 1, 1)
        clients = [
            stack.enter_context(
                httpx.Client(
                    base_url=base_url,
                    trust_env=False,
                    timeout=45,
                    limits=httpx.Limits(max_connections=maximum, max_keepalive_connections=maximum),
                )
            )
            for _ in range(count)
        ]
        if mode == "shared":
            yield clients[0]
            return
        available, local, lock = list(clients), threading.local(), threading.Lock()

        class PerThread:
            base_url = clients[0].base_url

            def client(self):
                if not hasattr(local, "http"):
                    with lock:
                        local.http = available.pop()
                return local.http

            def get(self, *args, **kwargs):
                return self.client().get(*args, **kwargs)

            def post(self, *args, **kwargs):
                return self.client().post(*args, **kwargs)

        yield PerThread()


def wait_processes(processes):
    """Require every child to finish startup, not just one shared-socket health response."""
    with selectors.DefaultSelector() as ready:
        for process in processes:
            ready.register(process.stdout, selectors.EVENT_READ)
        buffers = {process.stdout: b"" for process in processes}
        deadline = time.monotonic() + 15
        while ready.get_map():
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("Capacity HTTP server did not start")
            if time.monotonic() >= deadline:
                raise RuntimeError("Capacity HTTP server did not start")
            for key, _ in ready.select(timeout=0.05):
                chunk = os.read(key.fd, 64)
                buffers[key.fileobj] += chunk
                value = buffers[key.fileobj]
                if value == b"CAPACITY_READY\n":
                    ready.unregister(key.fileobj)
                elif not chunk or len(value) > 64 or b"\n" in value:
                    raise RuntimeError("Invalid capacity HTTP startup acknowledgement")


def stop_processes(processes):
    # Signal all children first. Even an earlier crash/timeout must not leave siblings alive.
    requested = []
    for process in processes:
        if process.stdin and not process.stdin.closed:
            with suppress(OSError):
                process.stdin.close()
        running = process.poll() is None
        requested.append(running)
        if running:
            process.terminate()
    failed = None
    for process, requested_stop in zip(processes, requested, strict=True):
        try:
            result = process.wait(timeout=10)
            # Uvicorn re-raises the signal after a graceful shutdown.
            if not requested_stop or result not in (0, -signal.SIGTERM):
                failed = failed or "Capacity HTTP server exited unsuccessfully"
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            failed = "Capacity HTTP server required forced termination"
        finally:
            process.stdout.close()
    if failed:
        raise RuntimeError(failed)


@contextmanager
def http_server(
    runtime, authentication, concurrency, server_mode="thread", client_mode="shared", processes=1
):
    """Own one loopback listener; split the same request-pool budget across API processes."""
    PROCESS_POOL_SIZE(processes)
    if server_mode not in {"thread", "process"} or (server_mode == "thread" and processes != 1):
        raise ValueError("Multiple HTTP servers require process mode")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        children, thread = [], None
        try:
            if server_mode == "thread":
                server = uvicorn.Server(
                    uvicorn.Config(
                        TIMED_APP(create_app(runtime, authentication)),
                        access_log=False,
                        log_level="critical",
                        timeout_graceful_shutdown=5,
                    )
                )
                thread = threading.Thread(
                    target=server.run, kwargs={"sockets": [listener]}, name="acme-capacity-api"
                )
                thread.start()
            else:
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith(("WAREHOUSE_", "TOUR_ERP_", "ANTHROPIC_", "SUPPLIER_"))
                }
                configuration = json.dumps(
                    {
                        "runtime": runtime.url.render_as_string(hide_password=False),
                        "authentication": authentication.url.render_as_string(hide_password=False),
                    }
                ).encode()
                listener.listen(2048)
                for index in range(processes):
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__).with_name("capacity_http.py")),
                            str(listener.fileno()),
                            str(processes),
                            str(index),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        pass_fds=(listener.fileno(),),
                        env=environment,
                    )
                    children.append(child)
                    child.stdin.write(configuration)
                    child.stdin.close()
                wait_processes(children)

            def alive():
                return thread.is_alive() if thread else all(p.poll() is None for p in children)

            with http_clients(f"http://127.0.0.1:{port}", concurrency, client_mode) as http:
                deadline = time.monotonic() + 15
                while True:
                    if not alive() or time.monotonic() >= deadline:
                        raise RuntimeError("Capacity HTTP server did not start")
                    try:
                        if http.get("/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                assert http.get("/v1/advisor/products").status_code == 401

                class Guarded:
                    base_url = http.base_url

                    def check(self):
                        if not alive():
                            raise RuntimeError("Capacity HTTP server exited during measurement")

                    def get(self, *args, **kwargs):
                        self.check()
                        return http.get(*args, **kwargs)

                    def post(self, *args, **kwargs):
                        self.check()
                        return http.post(*args, **kwargs)

                yield Guarded()
        finally:
            if thread is not None:
                server.should_exit = True
                thread.join(10)
                if thread.is_alive():
                    server.force_exit = True
                    thread.join(10)
                if thread.is_alive():
                    raise RuntimeError("Capacity HTTP server did not stop")
            else:
                stop_processes(children)


def measure(runtime, clients, profile, *, send=None):
    send = send or request
    operations = list(TARGETS)
    run_id = uuid4().hex
    for operation in operations:
        send(runtime, clients[0], operation, f"warmup-{operation}-{run_id}")
    barrier = threading.Barrier(profile["concurrency"])
    start = time.perf_counter()

    def call(index):
        operation = operations[index % len(operations)]
        if index < profile["concurrency"]:
            barrier.wait(timeout=15)
        begin = time.perf_counter()
        error = None
        diagnostics = None
        try:
            diagnostics = send(
                runtime,
                clients[(index // len(operations)) % len(clients)],
                operation,
                f"capacity-{run_id}-{index}",
            )
        except Exception as failure:
            # No SQL, URLs, credentials or response content in reports.
            error = failure.code if isinstance(failure, HttpFailure) else type(failure).__name__
        return {
            "operation": operation,
            "milliseconds": (time.perf_counter() - begin) * 1000,
            "error": error,
            "server_ms": diagnostics.get("server_ms") if diagnostics else None,
            "server_index": diagnostics.get("server_index") if diagnostics else None,
        }

    with ThreadPoolExecutor(max_workers=profile["concurrency"]) as executor:
        results = [
            future.result()
            for future in as_completed(
                [executor.submit(call, n) for n in range(profile["requests"])]
            )
        ]
    summaries = {}
    for operation, target in TARGETS.items():
        rows = [r for r in results if r["operation"] == operation]
        times = sorted(r["milliseconds"] for r in rows)
        percentiles = {
            f"p{p}_ms": round(times[max(0, math.ceil(len(times) * p / 100) - 1)], 2)
            for p in (50, 95, 99)
        }
        errors = {
            kind: sum(r["error"] == kind for r in rows)
            for kind in sorted({r["error"] for r in rows if r["error"]})
        }
        summaries[operation] = {
            "requests": len(rows),
            "errors": errors,
            **percentiles,
            "max_ms": round(max(times), 2),
            "target_p95_ms": target,
            "passed": not errors and percentiles["p95_ms"] <= target,
        }
        server_times = sorted(row["server_ms"] for row in rows if row["server_ms"] is not None)
        if server_times:
            summaries[operation]["server_timing_samples"] = len(server_times)
            summaries[operation]["server_process_requests"] = {
                str(index): sum(row["server_index"] == index for row in rows)
                for index in sorted(
                    {row["server_index"] for row in rows if row["server_index"] is not None}
                )
            }
            summaries[operation]["server_p95_to_headers_ms"] = round(
                server_times[math.ceil(len(server_times) * 0.95) - 1], 2
            )
    return {
        "operations": summaries,
        "elapsed_seconds": round(time.perf_counter() - start, 2),
        "passed": all(r["passed"] for r in summaries.values()),
    }


def run(
    value,
    name,
    transport="service",
    *,
    http_server_mode="thread",
    http_client_mode="shared",
    http_server_processes=1,
):
    if transport not in {"service", "http"}:
        raise ValueError("Unknown capacity transport")
    if http_server_mode not in {"thread", "process"} or http_client_mode not in {
        "shared",
        "per-thread",
    }:
        raise ValueError("Unknown HTTP capacity topology")
    pool_size = PROCESS_POOL_SIZE(http_server_processes)
    if http_server_processes != 1 and (transport != "http" or http_server_mode != "process"):
        raise ValueError("Multiple HTTP servers require HTTP process mode")
    profile = PROFILES[name]
    started = time.perf_counter()
    with ISOLATED(value) as environment:
        migrate(environment["WAREHOUSE_TEST_ADMIN_URL"])
        admin = engine_for(environment["WAREHOUSE_TEST_ADMIN_URL"])
        runtime_url = make_url(environment["WAREHOUSE_TEST_DATABASE_URL"]).update_query_dict(
            {"options": "-c statement_timeout=10000 -c lock_timeout=5000"}
        )
        runtime = engine_for(runtime_url)
        authentication = engine_for(environment["WAREHOUSE_TEST_AUTH_URL"])
        try:
            grant_runtime(admin, runtime_url.username)
            require_runtime_role(runtime)
            clients, counts = seed(admin, runtime, profile)
            if transport == "http":
                grant_auth(admin, authentication.url.username)
                seed_sessions(admin, clients)
            seeded = time.perf_counter()
            if transport == "http":
                with http_server(
                    runtime,
                    authentication,
                    profile["concurrency"],
                    http_server_mode,
                    http_client_mode,
                    http_server_processes,
                ) as http:
                    # Forged organizations are rejected before the timed workload.
                    bad = {**clients[0]["headers"], "X-Organization-Id": str(uuid4())}
                    assert http.get("/v1/advisor/products", headers=bad).status_code == 403
                    result = measure(
                        runtime,
                        clients,
                        profile,
                        send=lambda _, client, operation, key: http_request(
                            http, client, operation, key
                        ),
                    )
            else:
                result = measure(runtime, clients, profile)
            with admin.connect() as conn:
                assert (
                    conn.scalar(
                        text(
                            "SELECT count(*) FROM inventory_pool WHERE sold<>0 OR held<>0 OR blocked<>0 OR total<>100 OR version<>1"
                        )
                    )
                    == 0
                )
                quote_count = conn.scalar(text("SELECT count(*) FROM quote_snapshot"))
                expected_quotes = 1 + profile["requests"] // len(TARGETS)
                distinct_quotes = quote_count == expected_quotes
                database_version = conn.scalar(text("SHOW server_version"))
                database_max_connections = int(conn.scalar(text("SHOW max_connections")))
        finally:
            authentication.dispose()
            runtime.dispose()
            admin.dispose()
    result["passed"] = result["passed"] and distinct_quotes
    return {
        "profile": name,
        "transport": transport,
        "observed_at": datetime.now(UTC).isoformat(),
        "scale": profile,
        "buyers": len(clients),
        "counts": counts,
        "environment": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
            "postgres": database_version,
            "database_max_connections": database_max_connections,
            "database_pool_limit": 8,
            "database_pool": "psycopg_pool",
            "auth_pool_limit": 8 if transport == "http" else 0,
            "http_server_processes": http_server_processes if transport == "http" else 0,
            "database_pool_limit_per_server": pool_size if transport == "http" else 0,
            "auth_pool_limit_per_server": pool_size if transport == "http" else 0,
            "http_client_connection_limit": (
                profile["concurrency"] + (http_client_mode == "per-thread")
            )
            if transport == "http"
            else 0,
            "http_client_pool": http_client_mode if transport == "http" else None,
            "http_topology": (
                "client threads and server in separate processes"
                if http_server_mode == "process"
                else "client threads and one server event loop in the same process"
            )
            if transport == "http"
            else None,
        },
        "setup_seconds": round(seeded - started, 2),
        "boundary": (
            "real loopback TCP/HTTP including fixture-session authentication, request queueing, serialization, RLS, pool wait and quote writes; excludes login issuance, TLS/proxy, browser, LLM and upstream APIs"
            if transport == "http"
            else "application services including RLS, pool wait and quote snapshot writes; excludes HTTP, LLM and upstream APIs"
        ),
        "http_server_stopped": transport == "http",
        "inventory_unchanged": True,
        "quote_snapshots_created": quote_count,
        "distinct_quote_count_matches": distinct_quotes,
        "disposable_resources_cleaned": True,
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="smoke")
    parser.add_argument("--transport", choices=("service", "http"), default="service")
    parser.add_argument("--http-server-mode", choices=("thread", "process"), default="thread")
    parser.add_argument("--http-client-mode", choices=("shared", "per-thread"), default="shared")
    parser.add_argument("--http-server-processes", type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, "Report already exists; choose a new output path\n")
    value = os.environ.get("WAREHOUSE_CAPACITY_ADMIN_URL")
    if not value:
        parser.exit(
            2,
            "WAREHOUSE_CAPACITY_ADMIN_URL must explicitly name a dedicated loopback maintenance database\n",
        )
    try:
        report = run(
            value,
            args.profile,
            args.transport,
            http_server_mode=args.http_server_mode,
            http_client_mode=args.http_client_mode,
            http_server_processes=args.http_server_processes,
        )
    except Exception as error:
        parser.exit(
            1, f"Capacity probe failed ({type(error).__name__}); no report claims success\n"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
