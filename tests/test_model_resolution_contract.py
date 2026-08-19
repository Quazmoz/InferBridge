from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.model_manager import UnknownModel
from app.server import create_app
from runtime.openvino_engine import MockEngine

MODEL_ID = "tinyllama-1.1b-chat-fp16"
UNKNOWN_ID = "definitely-not-a-real-model"


@pytest.fixture()
def loaded_client():
    app = create_app(
        Settings(
            host="127.0.0.1",
            models_file=BASE_DIR / "models.json",
            models_dir=BASE_DIR / "models" / "openvino",
            force_mock=True,
            default_model=MODEL_ID,
        )
    )
    with TestClient(app) as client:
        manager = client.app.state.manager
        manager.engines[MODEL_ID] = MockEngine(MODEL_ID, device="CPU")
        manager.devices[MODEL_ID] = "CPU"
        yield client


def test_unknown_exact_model_never_falls_back_to_loaded_default(loaded_client):
    manager = loaded_client.app.state.manager

    with pytest.raises(UnknownModel, match=UNKNOWN_ID):
        manager.resolve_engine(UNKNOWN_ID)

    assert manager.resolve_engine(MODEL_ID).model_id == MODEL_ID


def test_documented_auto_selector_still_resolves_loaded_generation_model(loaded_client):
    manager = loaded_client.app.state.manager

    assert manager.resolve_engine("auto").model_id == MODEL_ID


def test_chat_completions_unknown_model_returns_404_even_with_default_loaded(loaded_client):
    response = loaded_client.post(
        "/v1/chat/completions",
        json={
            "model": UNKNOWN_ID,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == f"Unknown model '{UNKNOWN_ID}'"


def test_responses_unknown_model_returns_404_even_with_default_loaded(loaded_client):
    response = loaded_client.post(
        "/v1/responses",
        json={"model": UNKNOWN_ID, "input": "hello"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == f"Unknown model '{UNKNOWN_ID}'"


def test_embeddings_unknown_model_returns_404_instead_of_using_another_engine(loaded_client):
    response = loaded_client.post(
        "/v1/embeddings",
        json={"model": UNKNOWN_ID, "input": "hello"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == f"Unknown model '{UNKNOWN_ID}'"


def test_unknown_model_error_is_bounded_and_control_character_safe(loaded_client):
    manager = loaded_client.app.state.manager
    malicious = "unknown\r\nmodel-" + ("x" * 1000)

    with pytest.raises(UnknownModel) as captured:
        manager.resolve_engine(malicious)

    message = str(captured.value)
    assert "\r" not in message
    assert "\n" not in message
    assert len(message) < 190
    assert message.endswith("…'")


def test_invalid_auto_profile_error_is_bounded(loaded_client):
    manager = loaded_client.app.state.manager
    selector = "auto:" + ("not-a-profile" * 100)

    with pytest.raises(UnknownModel) as captured:
        manager.resolve_engine(selector)

    message = str(captured.value)
    assert len(message) < 280
    assert "Supported profiles:" in message
