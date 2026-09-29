import runpy
import socket
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text

CAPACITY = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/capacity.py"))


def resources(admin):
    with admin.connect() as conn:
        return (
            set(conn.execute(text("SELECT datname FROM pg_database")).scalars()),
            set(conn.execute(text("SELECT rolname FROM pg_roles")).scalars()),
        )


def test_capacity_measures_every_operation_for_each_buyer(monkeypatch):
    calls = []

    def request(runtime, client, operation, key):
        if not key.startswith("warmup-"):
            calls.append((client, operation))

    monkeypatch.setitem(CAPACITY["measure"].__globals__, "request", request)
    result = CAPACITY["measure"](None, list(range(100)), CAPACITY["PROFILES"]["baseline"])
    assert len(calls) == 400
    assert set(calls) == {
        (buyer, operation) for buyer in range(100) for operation in CAPACITY["TARGETS"]
    }
    assert all(
        item["requests"] == 100 and not item["errors"] for item in result["operations"].values()
    )


def test_capacity_retains_failed_requests_without_exception_content(monkeypatch):
    def request(runtime, client, operation, key):
        if not key.startswith("warmup-") and operation == "managed_quote":
            raise ValueError("ACME private request content must not reach reports")

    monkeypatch.setitem(CAPACITY["measure"].__globals__, "request", request)
    result = CAPACITY["measure"](None, [None], CAPACITY["PROFILES"]["smoke"])
    assert result["passed"] is False
    assert result["operations"]["managed_quote"]["errors"] == {"ValueError": 10}
    assert "private request" not in str(result)


@pytest.mark.parametrize(
    "transport,server,client,processes",
    [
        ("service", "thread", "shared", 1),
        ("http", "thread", "shared", 1),
        ("http", "thread", "per-thread", 1),
        ("http", "process", "shared", 1),
        ("http", "process", "shared", 2),
        ("http", "process", "per-thread", 1),
        ("http", "process", "per-thread", 2),
    ],
)
def test_capacity_smoke_preserves_inventory_and_cleans_own_resources(
    database, transport, server, client, processes
):
    admin, _ = database
    before = resources(admin)
    report = CAPACITY["run"](
        admin.url.set(database="postgres").render_as_string(hide_password=False),
        "smoke",
        transport,
        http_server_mode=server,
        http_client_mode=client,
        http_server_processes=processes,
    )
    assert resources(admin) == before
    assert report["buyers"] == 4
    assert report["counts"] == {
        "supplier_product": 20,
        "departure": 100,
        "inventory_pool": 50,
        "contract_price": 50,
    }
    assert report["inventory_unchanged"] and report["disposable_resources_cleaned"]
    assert report["quote_snapshots_created"] == 11
    assert report["transport"] == transport
    assert report["http_server_stopped"] is (transport == "http")
    if transport == "http":
        assert report["environment"]["http_client_pool"] == client
        environment = report["environment"]
        assert environment["http_server_processes"] == processes
        for prefix in ("database", "auth"):
            assert environment[f"{prefix}_pool_limit_per_server"] * processes == 8
            assert environment[f"{prefix}_pool_limit"] == 8
        for result in report["operations"].values():
            assert sum(result["server_process_requests"].values()) == 10
            assert set(result["server_process_requests"]) <= {str(i) for i in range(processes)}
        assert ("separate processes" in report["environment"]["http_topology"]) is (
            server == "process"
        )
        assert all(
            row["server_timing_samples"] == 10 and row["server_p95_to_headers_ms"] >= 0
            for row in report["operations"].values()
        )
    assert all(not item["errors"] for item in report["operations"].values())
    # Timing is recorded, never asserted by CI on an arbitrarily busy machine.


def test_capacity_cleans_resources_after_setup_failure(database, monkeypatch):
    admin, _ = database
    before = resources(admin)

    def fail(*args):
        raise RuntimeError("ACME setup interrupted")

    monkeypatch.setitem(CAPACITY["run"].__globals__, "seed", fail)
    with pytest.raises(RuntimeError, match="ACME setup interrupted"):
        CAPACITY["run"](
            admin.url.set(database="postgres").render_as_string(hide_password=False), "smoke"
        )
    assert resources(admin) == before


@pytest.mark.parametrize("server,processes", [("thread", 1), ("process", 1), ("process", 2)])
def test_http_capacity_stops_listener_and_cleans_resources_after_measurement_failure(
    database, monkeypatch, server, processes
):
    admin, _ = database
    before = resources(admin)
    address = []
    original = CAPACITY["http_server"]

    @contextmanager
    def observed(*args):
        with original(*args) as http:
            address.append((http.base_url.host, http.base_url.port))
            yield http

    def fail(*args, **kwargs):
        raise RuntimeError("ACME measurement interrupted")

    monkeypatch.setitem(CAPACITY["run"].__globals__, "http_server", observed)
    monkeypatch.setitem(CAPACITY["run"].__globals__, "measure", fail)
    with pytest.raises(RuntimeError, match="ACME measurement interrupted"):
        CAPACITY["run"](
            admin.url.set(database="postgres").render_as_string(hide_password=False),
            "smoke",
            "http",
            http_server_mode=server,
            http_client_mode="per-thread",
            http_server_processes=processes,
        )
    assert resources(admin) == before and len(address) == 1
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(address[0]) != 0


def test_http_capacity_reports_status_without_private_response_content():
    def send(runtime, client, operation, key):
        if not key.startswith("warmup-"):
            raise CAPACITY["HttpFailure"](503)

    result = CAPACITY["measure"](None, [None], CAPACITY["PROFILES"]["smoke"], send=send)
    assert not result["passed"]
    assert all(row["errors"] == {"HTTP_503": 10} for row in result["operations"].values())


def test_http_child_rejects_application_and_remote_databases():
    helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/capacity_http.py"))
    base = "postgresql+psycopg://acme:fictional@127.0.0.1/warehouse_ci_acme_test"
    assert len(helper["fixture_urls"]({"runtime": base, "authentication": base})) == 2
    for invalid in (
        base.replace("warehouse_ci_acme_test", "warehouse"),
        base.replace("127.0.0.1", "acme.example"),
        base.replace("acme_test", "other_test"),
    ):
        with pytest.raises(ValueError):
            helper["fixture_urls"]({"runtime": base, "authentication": invalid})


@pytest.mark.parametrize("processes", [1, 2])
def test_http_child_crash_does_not_claim_success_or_leak_fixture_resources(
    database, monkeypatch, processes
):
    admin, _ = database
    before, children = resources(admin), []
    module = CAPACITY["run"].__globals__["subprocess"]
    original = module.Popen

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def crash(*args, **kwargs):
        assert len(children) == processes
        children[0].kill()
        children[0].wait(timeout=5)
        raise RuntimeError("ACME child crashed")

    monkeypatch.setattr(module, "Popen", spawn)
    monkeypatch.setitem(CAPACITY["run"].__globals__, "measure", crash)
    with pytest.raises(RuntimeError, match="server exited unsuccessfully"):
        CAPACITY["run"](
            admin.url.set(database="postgres").render_as_string(hide_password=False),
            "smoke",
            "http",
            http_server_mode="process",
            http_client_mode="per-thread",
            http_server_processes=processes,
        )
    assert all(child.poll() is not None for child in children) and resources(admin) == before


@pytest.mark.parametrize(
    "transport,mode,processes",
    [
        ("http", "process", 0),
        ("http", "process", 3),
        ("http", "process", True),
        ("http", "thread", 2),
        ("service", "process", 2),
    ],
)
def test_invalid_process_configuration_never_creates_resources(
    monkeypatch, transport, mode, processes
):
    def unexpected(*args):
        pytest.fail("Invalid configuration must fail before creating resources")

    monkeypatch.setitem(CAPACITY["run"].__globals__, "ISOLATED", unexpected)
    with pytest.raises(ValueError):
        CAPACITY["run"](
            "unused", "smoke", transport, http_server_mode=mode, http_server_processes=processes
        )


@pytest.mark.parametrize("failure", ["spawn", "missing_readiness"])
def test_second_child_start_failure_stops_first_and_closes_listener(database, monkeypatch, failure):
    admin, _ = database
    before, children, addresses = resources(admin), [], []
    module = CAPACITY["run"].__globals__["subprocess"]
    original = module.Popen

    def spawn(command, **kwargs):
        with socket.fromfd(int(command[2]), socket.AF_INET, socket.SOCK_STREAM) as listener:
            addresses.append(listener.getsockname())
        if children:
            if failure == "spawn":
                raise RuntimeError("ACME second child start failure")
            command = [command[0], "-c", "import sys; sys.stdin.read()"]
        child = original(command, **kwargs)
        children.append(child)
        return child

    def unexpected(*args, **kwargs):
        pytest.fail("Measurement must not start before every child acknowledges startup")

    monkeypatch.setattr(module, "Popen", spawn)
    monkeypatch.setitem(CAPACITY["run"].__globals__, "measure", unexpected)
    with pytest.raises(RuntimeError):
        CAPACITY["run"](
            admin.url.set(database="postgres").render_as_string(hide_password=False),
            "smoke",
            "http",
            http_server_mode="process",
            http_server_processes=2,
        )
    assert len(children) == (1 if failure == "spawn" else 2)
    assert all(child.poll() is not None for child in children)
    assert resources(admin) == before and len(set(addresses)) == 1
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(addresses[0]) != 0
