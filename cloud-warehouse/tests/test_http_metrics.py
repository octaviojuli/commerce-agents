import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from cloud_warehouse import auth
from cloud_warehouse.api import create_app
from cloud_warehouse.http_metrics import HttpMetrics
from cloud_warehouse.http_observation import HttpObservation

TOKEN = "c71f29b0e8a6492d5f1a4c57e0d613bfdd3e5aa734bc43829437e01d02e0ae1c"


def samples(metrics):
    payload, _ = metrics.render()
    return [
        sample
        for family in text_string_to_metric_families(payload.decode())
        for sample in family.samples
    ]


def value(metrics, name, labels=None):
    return sum(
        sample.value
        for sample in samples(metrics)
        if sample.name == name and (labels is None or labels.items() <= sample.labels.items())
    )


@pytest.mark.parametrize("token", ["", "short", "REPLACE_WITH_RANDOM_64_HEX", "x" * 64, "A" * 64])
def test_invalid_metrics_configuration_fails_before_serving(token):
    with pytest.raises(ValueError, match="WAREHOUSE_METRICS_TOKEN"):
        create_app(None, None, metrics_token=token)


@pytest.mark.parametrize(
    "name,value",
    [
        ("WEB_CONCURRENCY", "2"),
        ("PROMETHEUS_MULTIPROC_DIR", "/tmp/acme"),
        ("prometheus_multiproc_dir", "/tmp/acme"),
    ],
)
def test_unsupported_multiworker_configuration_is_not_silently_misreported(
    monkeypatch, name, value
):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="one API worker"):
        HttpMetrics(TOKEN)


def test_disabled_endpoint_and_separate_operations_authentication():
    disabled = TestClient(create_app(None, None))
    assert disabled.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}).status_code == 404
    client = TestClient(create_app(None, None, metrics_token=TOKEN))
    for authorization in [
        "",
        "Bearer ACME-platform-session",
        "Bearer " + "0" * 64,
        "Basic " + TOKEN,
    ]:
        response = client.get("/metrics", headers={"Authorization": authorization})
        assert response.status_code == 401
        assert "warehouse_http_in_progress" not in response.text
        assert TOKEN not in response.text
    response = client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert "warehouse_http_in_progress 0.0" in response.text
    assert 'route="/metrics"' not in response.text
    assert "process_" not in response.text and "python_" not in response.text
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


def test_endpoint_counts_responses_and_bounds_labels_without_body_or_identity():
    client = TestClient(create_app(None, None, metrics_token=TOKEN))
    for index in range(30):
        client.get(f"/ACME-private-{index}?password=ACME-private-content")
    response = client.post("/v1/advisor/quotes", content="ACME-private-content")
    assert response.status_code == 401
    output = client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}).text
    rows = [
        sample for family in text_string_to_metric_families(output) for sample in family.samples
    ]
    unmatched = [
        s
        for s in rows
        if s.name == "warehouse_http_requests_total"
        and s.labels["route"] == "<unmatched>"
        and s.labels["method"] == "GET"
    ]
    assert len(unmatched) == 1 and unmatched[0].value == 30
    assert "ACME-private" not in output and TOKEN not in output
    assert any(
        s.name == "warehouse_http_requests_total"
        and s.labels
        == {
            "method": "POST",
            "route": "/v1/advisor/quotes",
            "status": "401",
            "outcome": "complete",
        }
        and s.value == 1
        for s in rows
    )
    assert all(set(s.labels) <= {"method", "route", "status", "outcome", "le"} for s in rows)
    # Separate app registries are independent, even within the same process.
    other = TestClient(create_app(None, None, metrics_token=TOKEN))
    output = other.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}).text
    assert 'route="<unmatched>"' not in output


def event(**overrides):
    return {
        "route": "/items/{id}",
        "method": "GET",
        "status": 200,
        "outcome": "complete",
        "duration_ms": 300,
        "headers_ms": 20,
        "response_bytes": 4,
        "organization_id": str(uuid4()),
        "request_id": str(uuid4()),
        **overrides,
    }


def test_histograms_keep_thresholds_and_untrusted_labels_do_not_expand_series():
    metrics = HttpMetrics(TOKEN)
    metrics.routes = frozenset({"/items/{id}"})
    for _ in range(10):
        metrics.start()
        metrics.finish(event())
    assert value(metrics, "warehouse_http_in_progress") == 0
    assert value(metrics, "warehouse_http_duration_seconds_count") == 10
    assert value(metrics, "warehouse_http_duration_seconds_bucket", {"le": "0.3"}) == 10
    assert value(metrics, "warehouse_http_duration_seconds_bucket", {"le": "0.2"}) == 0
    assert value(metrics, "warehouse_http_response_bytes_total") == 40
    for index in range(20):
        metrics.start()
        metrics.finish(
            event(
                route=f"/ACME-private-{index}", method=str(index), status=9000, outcome=str(index)
            )
        )
    assert (
        value(
            metrics,
            "warehouse_http_requests_total",
            {
                "route": "<unmatched>",
                "method": "OTHER",
                "status": "none",
                "outcome": "incomplete",
            },
        )
        == 20
    )
    assert "ACME-private" not in metrics.render()[0].decode()


async def test_stream_active_gauge_and_interruption_are_recorded():
    metrics = HttpMetrics(TOKEN)
    first, finish = asyncio.Event(), asyncio.Event()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ACME-private", "more_body": True})
        first.set()
        await finish.wait()

    async def send(message):
        pass

    scope = {"type": "http", "method": "GET", "path": "/stream", "headers": []}
    task = asyncio.create_task(HttpObservation(app, metrics)(scope, None, send))
    await asyncio.wait_for(first.wait(), 2)
    assert value(metrics, "warehouse_http_in_progress") == 1
    assert value(metrics, "warehouse_http_requests_total") == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert value(metrics, "warehouse_http_in_progress") == 0
    assert (
        value(metrics, "warehouse_http_requests_total", {"status": "200", "outcome": "cancelled"})
        == 1
    )
    assert value(metrics, "warehouse_http_response_bytes_total") == len(b"ACME-private")


async def test_parallel_completions_and_scrapes_keep_exact_counts():
    metrics = HttpMetrics(TOKEN)

    def complete():
        metrics.start()
        metrics.render()
        metrics.finish(event())

    await asyncio.gather(*(asyncio.to_thread(complete) for _ in range(200)))
    assert value(metrics, "warehouse_http_requests_total") == 200
    assert value(metrics, "warehouse_http_duration_seconds_count") == 200
    assert value(metrics, "warehouse_http_in_progress") == 0


def test_platform_login_cannot_scrape_operations_metrics(database, authentication, tenant):
    admin, runtime = database
    email, password = f"{uuid4()}@acme.example", "ACME-test-password-only"
    auth.create_user(admin, email, password, {tenant.supplier.organization_id: ["supplier_admin"]})
    with TestClient(create_app(runtime, authentication, metrics_token=TOKEN)) as client:
        login = client.post("/v1/auth/login", json={"email": email, "password": password}).json()
        response = client.get(
            "/metrics",
            headers={
                "Authorization": "Bearer " + login["access_token"],
                "X-Organization-ID": str(tenant.supplier.organization_id),
            },
        )
        assert response.status_code == 401
        output = client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}).text
    assert str(tenant.supplier.organization_id) not in output and email not in output
    assert login["access_token"] not in output
