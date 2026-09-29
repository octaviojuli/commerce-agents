"""Real process death across catalog commit, using only disposable test databases."""

import asyncio
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from cloud_warehouse import jobs
from cloud_warehouse.catalog import list_departures, list_products
from cloud_warehouse.integrations import CatalogBatch
from cloud_warehouse.persistence import transaction

CHILD = r"""
import asyncio,json,sys
from contextlib import asynccontextmanager
from uuid import UUID
from sqlalchemy import text
from cloud_warehouse import jobs
from cloud_warehouse.integrations import CatalogBatch
from cloud_warehouse.persistence import engine_for,Principal
x=json.loads(sys.stdin.readline());engine=engine_for(x['url'])
assert engine.url.database.endswith('_test')
actor=Principal(UUID(x['user']),UUID(x['organization']))
source=UUID(x['source'])
def checkpoint(stage,**values):
 print(json.dumps({'stage':stage,**values}),flush=True)
 assert sys.stdin.readline()=='release\n'
if x['stage']=='before_commit':
 original=jobs.published
 def published(connection,claim,result):
  original(connection,claim,result)
  checkpoint('before_commit',run=result['sync_run_id'],backend=connection.scalar(text('SELECT pg_backend_pid()')))
 jobs.published=published
class Connector:
 async def read_catalog(self,start=None,end=None):
  return CatalogBatch(x['routes'],x['departures'])
@asynccontextmanager
async def factory(): yield Connector()
try:
 result=asyncio.run(jobs.work(engine,actor,{source:factory},once=True))
 if x['stage']=='after_commit': checkpoint('after_commit',result=result)
 else: print(json.dumps({'stage':'finished','result':result}),flush=True)
finally: engine.dispose()
"""


def _read(child, timeout=15):
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        assert selector.select(timeout), "Fixture worker did not reach its checkpoint"
        line = child.stdout.readline()
    assert line, "Fixture worker exited before its checkpoint"
    return json.loads(line)


@contextmanager
def _child(runtime, tenant, data, stage):
    assert runtime.url.database.endswith("_test")
    config = {
        "url": runtime.url.render_as_string(hide_password=False),
        "user": str(tenant.worker.user_id),
        "organization": str(tenant.worker.organization_id),
        "source": str(tenant.connection_id),
        "routes": data.routes,
        "departures": data.departures,
        "stage": stage,
    }
    # Pass only fixture configuration over stdin, never application secrets or env files.
    environment = {key: os.environ[key] for key in ("PATH", "PYTHONPATH") if key in os.environ}
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
    )
    try:
        child.stdin.write(json.dumps(config) + "\n")
        child.stdin.flush()
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()


def _job(runtime, tenant):
    with transaction(runtime, tenant.worker) as conn:
        return dict(
            conn.execute(
                text("SELECT * FROM sync_job WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
            .mappings()
            .one()
        )


def _facts(admin, tenant):
    with admin.connect() as conn:
        scope = {"source": tenant.connection_id, "org": tenant.supplier.organization_id}
        return {
            "products": list(
                conn.execute(
                    text(
                        "SELECT id,name,version FROM supplier_product WHERE connection_id=:source"
                    ),
                    scope,
                )
            ),
            "departures": list(
                conn.execute(
                    text(
                        "SELECT id,available_seats,version FROM departure WHERE connection_id=:source"
                    ),
                    scope,
                )
            ),
            "snapshots": conn.scalar(
                text("SELECT count(*) FROM source_snapshot WHERE connection_id=:source"), scope
            ),
            "audit": conn.scalar(
                text(
                    "SELECT count(*) FROM audit_event WHERE organization_id=:org AND action='catalog.sync'"
                ),
                scope,
            ),
            "outbox": conn.scalar(
                text(
                    "SELECT count(*) FROM outbox_event WHERE organization_id=:org AND kind='catalog.published'"
                ),
                scope,
            ),
        }


def probe(admin, runtime, tenant):
    """Both engines must point to the caller-owned isolated fixture database."""
    assert admin.url.database == runtime.url.database and admin.url.database.endswith("_test")
    assert jobs.LEASE_SECONDS == 60
    day = (datetime.now(UTC) + timedelta(days=30)).date()

    def batch(name, seats):
        return CatalogBatch(
            [{"routeId": 1, "routeCode": "ACME-R1", "routeName": name, "days": 3}],
            [
                {
                    "periodId": 2,
                    "routeId": 1,
                    "periodCode": "ACME-D2",
                    "departDate": str(day),
                    "returnDate": str(day + timedelta(days=2)),
                    "availableSeats": seats,
                }
            ],
        )

    data = batch("ACME before crash", 19)

    class Source:
        async def read_catalog(self, start=None, end=None):
            return data

    @asynccontextmanager
    async def factory():
        yield Source()

    baseline = asyncio.run(
        jobs.work(runtime, tenant.worker, {tenant.connection_id: factory}, once=True)
    )
    assert baseline["status"] == "published"
    original = _facts(admin, tenant)
    initial_job = _job(runtime, tenant)
    # Make the next fixture scan due, without altering leases or production defaults.
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE sync_job SET next_run_at=now() WHERE id=:id"),
            {"id": initial_job["id"]},
        )
    changed = batch("ACME recovered catalog", 7)
    with _child(runtime, tenant, changed, "before_commit") as child:
        checkpoint = _read(child)
        assert checkpoint["stage"] == "before_commit"
        interrupted = _job(runtime, tenant)
        assert interrupted["status"] == "running" and interrupted["attempts"] == 1
        assert interrupted["completed_count"] == 1
        assert _facts(admin, tenant) == original
        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
    stopped_at = time.monotonic()
    # Wait for the killed connection to disappear, rather than assuming rollback.
    deadline = time.monotonic() + 10
    while True:
        with admin.connect() as conn:
            active = conn.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=:pid)"),
                {"pid": checkpoint["backend"]},
            )
        if not active:
            break
        assert time.monotonic() < deadline, "Killed transaction connection survived"
        time.sleep(0.05)
    assert _facts(admin, tenant) == original
    # A fresh process must respect the still-valid dead process lease.
    with _child(runtime, tenant, changed, "once") as child:
        assert _read(child) == {"stage": "finished", "result": None}
        assert child.wait(timeout=10) == 0
    assert _job(runtime, tenant)["lease_id"] == interrupted["lease_id"]
    old = jobs.Claim(interrupted["id"], tenant.connection_id, interrupted["lease_id"], 1)
    deadline = time.monotonic() + jobs.LEASE_SECONDS + 10
    while True:
        with admin.connect() as conn:
            expired = conn.scalar(
                text("SELECT lease_until<=clock_timestamp() FROM sync_job WHERE id=:id"),
                {"id": old.id},
            )
        if expired:
            break
        assert time.monotonic() < deadline, "Dead worker lease did not expire"
        time.sleep(0.5)
    wait_seconds = time.monotonic() - stopped_at
    with _child(runtime, tenant, changed, "after_commit") as child:
        recovered = _read(child)
        assert recovered["stage"] == "after_commit"
        result = recovered["result"]
        assert result["status"] == "published"
        committed = _facts(admin, tenant)
        assert committed["products"] == [(original["products"][0][0], "ACME recovered catalog", 2)]
        assert committed["departures"] == [(original["departures"][0][0], 7, 2)]
        assert (committed["snapshots"], committed["audit"], committed["outbox"]) == (4, 2, 2)
        recovered_job = _job(runtime, tenant)
        assert recovered_job["completed_count"] == 2 and recovered_job["attempts"] == 0
        assert recovered_job["status"] == "waiting" and recovered_job["lease_id"] is None
        assert str(recovered_job["last_run_id"]) == result["sync_run_id"]
        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
    # A crash after COMMIT must not enqueue the successful scan again at startup.
    with _child(runtime, tenant, changed, "once") as child:
        assert _read(child) == {"stage": "finished", "result": None}
        assert child.wait(timeout=10) == 0
    assert _facts(admin, tenant) == committed
    assert _job(runtime, tenant)["completed_count"] == 2
    try:
        jobs.heartbeat(runtime, tenant.worker, old)
    except jobs.LeaseLost:
        pass
    else:
        raise AssertionError("Old worker retained ownership after recovery")
    with transaction(runtime, tenant.worker) as conn:
        runs = list(
            conn.execute(
                text("SELECT id,status,error_code FROM sync_run WHERE connection_id=:id"),
                {"id": tenant.connection_id},
            )
        )
    assert len(runs) == 3
    assert next(row for row in runs if str(row.id) == checkpoint["run"])[1:] == (
        "failed",
        "INTERRUPTED",
    )
    assert sum(row.status == "published" for row in runs) == 2
    assert list_products(runtime, tenant.buyer)[0]["name"] == "ACME recovered catalog"
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 7
    return {
        "lease_seconds": jobs.LEASE_SECONDS,
        "natural_expiry_wait_seconds": round(wait_seconds, 3),
        "sigkill_before_commit_rolled_back": True,
        "restart_before_expiry_did_not_claim": True,
        "restart_after_expiry_published": True,
        "sigkill_after_commit_did_not_republish": True,
        "stable_ids_single_version_increment": True,
        "interrupted_run_retained": True,
        "audit_and_outbox_atomic": True,
        "old_lease_refused": True,
        "buyer_reads_recovered_catalog": True,
        "child_processes_reaped": True,
    }


def test_worker_sigkill_before_and_after_commit_recovers_with_natural_lease(database, tenant):
    report = probe(*database, tenant)
    assert report["sigkill_before_commit_rolled_back"]
    print(json.dumps(report))
