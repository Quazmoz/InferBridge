"""Backend responses the native Chat parity features parse strictly.

``ContextBudget.Parse``, ``HardwareAdvisor.Parse`` and ``EngineEvent.ParsePage`` in
the WinUI app refuse a response that lacks a field, so these keys are a contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.server import create_app
from tests.test_context_budget import MODEL_ID, _install_engine, _settings

NATIVE_BUDGET_FIELDS = {
    "model",
    "prompt_tokens",
    "max_prompt_tokens",
    "prompt_budget_percent",
    "max_context_tokens",
    "requested_output_tokens",
    "available_output_tokens",
    "effective_output_tokens",
    "output_limited",
    "context_usage_tokens",
    "message_count",
    "retained_message_count",
    "dropped_turn_count",
    "will_truncate",
    "prompt_over_budget",
    "blocked",
    "dropped_messages",
    "attachment_count",
}


def test_context_budget_reports_every_field_the_native_chip_reads(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _install_engine(client)
        # The native check sends what Send would send, plus image_count, and may end
        # on an assistant turn when nothing is typed yet.
        response = client.post(
            "/v1/chat/context-budget",
            json={
                "model": MODEL_ID,
                "messages": [
                    {"role": "system", "content": "Be brief."},
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ],
                "max_tokens": 32,
                "temperature": 0.7,
                "top_p": 1.0,
                "image_count": 0,
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert NATIVE_BUDGET_FIELDS <= payload.keys()
    assert payload["model"] == MODEL_ID
    for name in ("output_limited", "will_truncate", "prompt_over_budget", "blocked"):
        assert isinstance(payload[name], bool), name
    assert payload["max_prompt_tokens"] >= 1 and payload["max_context_tokens"] >= 1


def test_telemetry_carries_the_advisor_shape_the_native_card_reads(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/v1/system/telemetry")
    assert response.status_code == 200
    advisor = response.json()["metrics"]["advisor"]
    assert isinstance(advisor["profile_order"], list) and advisor["profile_order"]
    assert isinstance(advisor["profiles"], dict)
    assert advisor["default_profile"] in advisor["profile_order"]
    assert set(advisor["profile_order"]) <= set(advisor["profile_labels"])
    assert isinstance(advisor["models"], list)
    for model in advisor["models"]:
        assert {"id", "name", "backend", "compatibility", "fit_score"} <= model.keys()
    for recommendation in advisor["profiles"].values():
        if recommendation is None:
            continue
        assert {
            "model_id",
            "model_name",
            "device",
            "precision",
            "context_length",
            "output_tokens",
            "compatibility",
            "fit_score",
            "reason",
            "warnings",
        } <= recommendation.keys()


def test_event_log_page_has_the_fields_the_native_activity_card_reads(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        client.app.state.manager.emit_event("warning", "Native parity probe")
        payload = client.get("/v1/events", params={"limit": 100}).json()
    assert isinstance(payload["data"], list)
    event = next(item for item in payload["data"] if item["message"] == "Native parity probe")
    assert event["level"] == "warning"
    assert isinstance(event["timestamp"], int) and isinstance(event["id"], int)
