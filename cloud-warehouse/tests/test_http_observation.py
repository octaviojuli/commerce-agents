import asyncio
import json
import logging
import traceback
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from cloud_warehouse import auth, http_observation
from cloud_warehouse.api import create_app
from cloud_warehouse.http_observation import HttpObservation


@pytest.fixture
def observations(monkeypatch):
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            assert record.exc_info is None
            records.append(json.loads(record.getMessage()))

    monkeypatch.setattr(http_observation.LOGGER, "handlers", [Capture()])
    monkeypatch.setattr(http_observation.LOGGER, "propagate", False)
    previous_level = http_observation.LOGGER.level
    http_observation.LOGGER.setLevel(logging.INFO)
    yield records
    http_observation.LOGGER.setLevel(previous_level)


def app():
    application = FastAPI()
    application.add_middleware(HttpObservation)

    @application.get("/items/{item_id}")
    async def item(item_id: str, request: Request):
        return {"request_id": request.state.request_id}

    @application.get("/failure")
    def failure():
        raise ValueError("ACME-private-password-and-chat")

    return application


def test_records_only_templates_and_generated_ids(observations):
    with TestClient(app()) as client:
        supplied = str(uuid4())
        response = client.get(
            "/items/ACME-private-customer?token=ACME-private-token",
            headers={
                "X-Request-ID": supplied,
                "Authorization": "Bearer ACME-private-token",
                "X-Organization-ID": str(uuid4()),
                "User-Agent": "ACME-private-browser",
            },
        )
        identifier = response.headers["x-request-id"]
        assert UUID(identifier).version == 4 and identifier != supplied
        assert response.json()["request_id"] == identifier
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        missing = client.get("/ACME-private-missing?password=ACME-private-value")
        assert missing.status_code == 404
    assert len(observations) == 2
    assert observations[0]["route"] == "/items/{item_id}"
    assert observations[1]["route"] == "<unmatched>"
    assert all(r["organization_id"] is None for r in observations)
    assert all(r["outcome"] == "complete" and r["response_complete"] for r in observations)
    assert observations[0]["response_bytes"] == len(response.content)
    assert observations[0]["duration_ms"] >= observations[0]["headers_ms"] >= 0
    assert "ACME-private" not in json.dumps(observations)


def test_unhandled_failure_is_sanitized_and_correlated(observations):
    with TestClient(app()) as client:
        response = client.get("/failure")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_OPERATION_UNCONFIRMED"
    assert "ACME-private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert observations[0]["request_id"] == response.headers["x-request-id"]
    assert observations[0]["status"] == 500 and observations[0]["outcome"] == "error"
    assert "ACME-private" not in json.dumps(observations)


async def test_concurrent_requests_keep_independent_ids(observations):
    application = app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://acme.example"
    ) as client:
        replies = await asyncio.gather(*(client.get(f"/items/{n}") for n in range(30)))
    ids = {reply.headers["x-request-id"] for reply in replies}
    assert len(ids) == len(observations) == 30
    assert ids == {record["request_id"] for record in observations}
    assert all(reply.json()["request_id"] == reply.headers["x-request-id"] for reply in replies)


def scope():
    return {"type": "http", "method": "GET", "path": "/ACME-private", "headers": []}


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_stream_is_forwarded_without_buffering_and_logged_after_last_body(observations):
    first_sent, finish = asyncio.Event(), asyncio.Event()
    sent = []

    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ACME-private", "more_body": True})
        first_sent.set()
        await finish.wait()
        await send({"type": "http.response.body", "body": b"end", "more_body": False})

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(HttpObservation(stream)(scope(), receive, send))
    try:
        await asyncio.wait_for(first_sent.wait(), 2)
        assert len(sent) == 2 and sent[1]["body"] == b"ACME-private"
        assert not observations
        finish.set()
        await task
    finally:
        task.cancel()
    assert len(observations) == 1
    assert observations[0]["outcome"] == "complete"
    assert observations[0]["response_bytes"] == len(b"ACME-privateend")
    assert "ACME-private" not in json.dumps(observations)


async def test_cancelled_stream_keeps_200_status_but_is_not_a_completion(observations):
    started = asyncio.Event()

    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        started.set()
        await asyncio.Event().wait()

    async def send(message):
        pass

    task = asyncio.create_task(HttpObservation(stream)(scope(), receive, send))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert observations[0]["status"] == 200
    assert observations[0]["outcome"] == "cancelled"
    assert not observations[0]["response_complete"]


@pytest.mark.parametrize("disconnect", [False, True])
async def test_started_stream_error_is_not_replaced_or_logged_with_secret(observations, disconnect):
    sent = []

    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        if disconnect:
            await receive()
        raise ValueError("ACME-private-token")

    async def disconnected_receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError, match="WAREHOUSE_HTTP_STREAM_INTERRUPTED") as result:
        await HttpObservation(stream)(scope(), disconnected_receive, send)
    assert result.value.__suppress_context__
    assert len(sent) == 1
    assert observations[0]["outcome"] == ("disconnected" if disconnect else "error")
    assert observations[0]["status"] == 200 and not observations[0]["response_complete"]
    assert "ACME-private" not in json.dumps(observations)


async def test_disconnect_while_sending_error_does_not_expose_original_exception(observations):
    async def downstream(scope, receive, send):
        raise ValueError("ACME-private-database-parameters")

    async def send(message):
        raise OSError("ACME-private-transport")

    with pytest.raises(RuntimeError, match="WAREHOUSE_HTTP_RESPONSE_INTERRUPTED") as result:
        await HttpObservation(downstream)(scope(), receive, send)
    assert "ACME-private" not in "".join(traceback.format_exception(result.value))
    assert observations[0]["status"] is None
    assert observations[0]["outcome"] == "disconnected"
    assert not observations[0]["response_complete"]


def test_real_streaming_response_reports_interruption(observations):
    application = app()

    @application.get("/stream")
    async def stream():
        async def body():
            yield "data: ACME-private-chat\n\n"
            raise ValueError("ACME-private-model-key")

        return StreamingResponse(body(), media_type="text/event-stream")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/stream")
    assert response.status_code == 200
    assert observations[0]["outcome"] == "error"
    assert not observations[0]["response_complete"]
    assert "ACME-private" not in json.dumps(observations)


async def test_non_http_scope_is_untouched(observations):
    sentinel = {"type": "lifespan"}

    async def downstream(scope, receive, send):
        assert scope is sentinel

    await HttpObservation(downstream)(sentinel, None, None)
    assert not observations and sentinel == {"type": "lifespan"}


def test_logging_sink_failure_does_not_fail_business_response(monkeypatch):
    def fail(*args):
        raise OSError("ACME-private-sink")

    monkeypatch.setattr(http_observation.LOGGER, "info", fail)
    with TestClient(app()) as client:
        assert client.get("/items/1").status_code == 200


def test_authenticated_organization_only(database, authentication, tenant, observations):
    admin, runtime = database
    email = f"{uuid4()}@acme.example"
    password = "ACME-private-password"
    auth.create_user(admin, email, password, {tenant.buyer.organization_id: ["advisor"]})
    with TestClient(create_app(runtime, authentication)) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": password}).json()[
            "access_token"
        ]
        assert observations[-1]["organization_id"] is None
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": str(tenant.buyer.organization_id),
        }
        assert client.get("/v1/advisor/products", headers=headers).status_code == 200
        assert observations[-1]["organization_id"] == str(tenant.buyer.organization_id)
        headers["X-Organization-Id"] = str(tenant.supplier.organization_id)
        assert client.get("/v1/advisor/products", headers=headers).status_code == 403
        assert observations[-1]["organization_id"] is None
        assert observations[-1]["status"] == 403
        headers.pop("Authorization")
        assert client.get("/v1/advisor/products", headers=headers).status_code == 401
        assert observations[-1]["organization_id"] is None
    output = json.dumps(observations)
    assert password not in output and token not in output and email not in output
