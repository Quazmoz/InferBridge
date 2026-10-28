"""POST /v1/models/unload: the API behind Eject in the native Chat and the web chat bar."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.server import create_app

MODEL_ID = "tinyllama-1.1b-chat-fp16"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        "app.huggingface_access.HuggingFaceAccessService.preflight",
        AsyncMock(return_value={"code": "hf_access_granted"}),
    )
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _load(client: TestClient) -> None:
    assert client.post("/v1/models/load", json={"model": MODEL_ID}).status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline:
        if MODEL_ID in client.get("/v1/system/status").json()["models"]["loaded"]:
            return
        time.sleep(0.05)
    raise AssertionError("model did not load in time")


def _entry(client: TestClient) -> dict:
    models = client.get("/v1/system/status").json()["models"]["available"]
    return next(model for model in models if model["id"] == MODEL_ID)


def test_unload_frees_the_model_and_chat_then_refuses_it(client):
    _load(client)
    assert _entry(client)["can_unload"] is True

    response = client.post("/v1/models/unload", json={"model": MODEL_ID})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "unloaded"
    assert body["model"]["id"] == MODEL_ID

    status = client.get("/v1/system/status").json()
    assert MODEL_ID not in status["models"]["loaded"]
    assert _entry(client)["is_loaded"] is False
    assert any("Unloaded" in event["message"] for event in status["events"])
    # The activity feed the native Engine page reads reports the unload too.
    assert any("Unloaded" in event["message"] for event in client.get("/v1/events").json()["data"])
    chat = client.post(
        "/v1/chat/completions",
        json={"model": MODEL_ID, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code in (409, 503)


def test_unload_twice_and_unknown_models_are_refused_with_reasons(client):
    _load(client)
    assert client.post("/v1/models/unload", json={"model": MODEL_ID}).status_code == 200
    again = client.post("/v1/models/unload", json={"model": MODEL_ID})
    assert again.status_code == 409
    assert "not loaded" in again.json()["detail"]
    unknown = client.post("/v1/models/unload", json={"model": "does-not-exist"})
    assert unknown.status_code == 404


def test_unload_is_refused_while_a_generation_holds_the_model(client):
    """Native Eject stops the stream first and retries only this 409 'is busy' refusal."""

    _load(client)
    manager = client.app.state.manager

    class HeldLock:
        def locked(self) -> bool:
            return True

    original = manager.locks.get(MODEL_ID)
    manager.locks[MODEL_ID] = HeldLock()
    try:
        busy = client.post("/v1/models/unload", json={"model": MODEL_ID})
        assert busy.status_code == 409
        assert "is busy" in busy.json()["detail"]
        assert MODEL_ID in client.get("/v1/system/status").json()["models"]["loaded"]
    finally:
        if original is None:
            manager.locks.pop(MODEL_ID, None)
        else:
            manager.locks[MODEL_ID] = original
    assert client.post("/v1/models/unload", json={"model": MODEL_ID}).status_code == 200


def test_reload_after_unload_is_an_explicit_request(client):
    """Unloading never schedules a reload; only a new load request brings it back."""

    _load(client)
    assert client.post("/v1/models/unload", json={"model": MODEL_ID}).status_code == 200
    time.sleep(0.3)
    assert MODEL_ID not in client.get("/v1/system/status").json()["models"]["loaded"]
    _load(client)
    assert MODEL_ID in client.get("/v1/system/status").json()["models"]["loaded"]
