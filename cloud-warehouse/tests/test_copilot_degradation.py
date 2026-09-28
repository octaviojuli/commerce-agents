"""F4: bounded model failures remain observable and never invent a change."""

import json

from cloud_warehouse.http_metrics import HttpMetrics
from commerce_common.testing import FakeCreateClient
from tour.api import copilot_model

from .test_copilot import new_deal, run_turn


async def test_timeout_logs_and_metrics_have_no_message_or_error_body(monkeypatch):
    class Messages:
        async def create(self, **kwargs):
            raise TimeoutError("ACME secret customer content must not be logged")

    class Client:
        messages = Messages()

    logged = []
    monkeypatch.setattr(copilot_model.LOGGER, "info", logged.append)
    model = copilot_model.TypedModel(Client())
    model.metrics = HttpMetrics("1" * 64)
    assert await model.call("extract", {"message": "ACME secret"}) is None
    assert len(logged) == 2
    assert json.loads(logged[-1])["degraded"] is True
    assert all(json.loads(row)["error_type"] == "timeout" for row in logged)
    output = model.metrics.render()[0].decode()
    assert 'warehouse_advisor_model_degraded_total{call="extract"} 1.0' in output
    assert "ACME" not in output + "".join(logged)


async def test_failed_model_turn_does_not_claim_requirement_change(database, tenant):
    runtime = database[1]
    identifier, _ = new_deal(runtime, tenant.buyer)
    events = await run_turn(
        runtime, tenant.buyer, identifier, FakeCreateClient([]), "ACME 继续看看"
    )
    reply = next(
        e.data["payload"]
        for e in events
        if e.type == "ui" and e.data["component"] == "copilot_reply"
    )
    assert reply["degraded"]
    assert "理解不完整" in reply["to_advisor"]
    assert "发现需求变化" not in str(reply)
    assert "收到您的调整" not in str(reply)
    assert not any(e.type == "ui" and e.data["component"] == "copilot_proposal" for e in events)
