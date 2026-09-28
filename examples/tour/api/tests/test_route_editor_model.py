from types import SimpleNamespace

import pytest

from cloud_warehouse.route_editing import FactReview
from tour.api.route_editor_model import ModelEditor


def test_editor_uses_only_structured_call_and_rejects_partial_output(monkeypatch):
    captured = []
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="submit_result",
                input={
                    "unsupported_claims": [],
                    "omitted_facts_or_conditions": [],
                    "changed_meanings": [],
                },
            )
        ],
        stop_reason="tool_use",
    )

    def create(**kwargs):
        captured.append(kwargs)
        return response

    client = SimpleNamespace(messages=SimpleNamespace(create=create), close=lambda: None)
    monkeypatch.setattr("tour.api.route_editor_model.anthropic.Anthropic", lambda **_: client)
    editor = ModelEditor(
        {"ANTHROPIC_API_KEY": "ACME-fictional", "TOUR_CONTENT_MODEL": "ACME-model"}
    )
    assert not editor("ACME static rule", {"text": "ACME data"}, FactReview).changed_meanings
    assert captured[0]["tool_choice"] == {"type": "tool", "name": "submit_result"}
    assert captured[0]["thinking"] == {"type": "disabled"}
    assert captured[0]["messages"][0]["content"].startswith("<supplier_data>")
    response.stop_reason = "max_tokens"
    with pytest.raises(ValueError, match="RESPONSE_SHAPE"):
        editor("ACME", {}, FactReview)
    response.stop_reason = "end_turn"
    response.content = []
    with pytest.raises(ValueError, match="RESPONSE_SHAPE"):
        editor("ACME", {}, FactReview)
    with pytest.raises(ValueError, match="NOT_CONFIGURED"):
        ModelEditor({})
