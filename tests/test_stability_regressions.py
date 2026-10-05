"""Regression tests for streaming cancellation, auth throttling, and conversion conflicts."""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.server import create_app
from runtime.openvino_engine import MockEngine, StreamHandle

MODEL_ID = "tinyllama-1.1b-chat-fp16"


def make_settings(*, api_key: str | None = None) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=api_key,
        force_mock=True,
    )


def test_stream_handle_cancellation_releases_full_queue() -> None:
    handle = StreamHandle()
    for index in range(handle._q.maxsize):
        handle._q.put_nowait(str(index))

    started = threading.Event()
    push_results: list[bool] = []

    def producer() -> None:
        started.set()
        push_results.append(handle.push("blocked"))
        handle.finish()

    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    assert started.wait(0.5)
    time.sleep(0.05)

    handle.request_stop()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert push_results == [False]
    assert handle.wait_closed(timeout=0.1)


def test_convert_loaded_model_returns_conflict(monkeypatch) -> None:
    async def unexpected_preflight(*_args, **_kwargs):
        pytest.fail("loaded model conflict must be reported before network access")

    monkeypatch.setattr(
        "app.huggingface_access.HuggingFaceAccessService.preflight", unexpected_preflight
    )
    app = create_app(make_settings())
    with TestClient(app) as client:
        manager = client.app.state.manager
        manager.engines[MODEL_ID] = MockEngine(MODEL_ID)
        manager.locks[MODEL_ID] = asyncio.Lock()

        response = client.post(
            "/v1/models/convert",
            json={"model": MODEL_ID, "weight_format": "int4"},
        )

        assert response.status_code == 409
        assert "Unload it before converting" in response.json()["detail"]
        assert MODEL_ID not in manager.convert_tasks


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"model": MODEL_ID, "device": "MULTI:NPU,BOGUS"}, 400),
        ({"model": MODEL_ID, "ratio": 2}, 422),
        ({"model": "missing", "source_model": "owner/model"}, 404),
    ],
)
def test_conversion_validation_does_not_need_network(monkeypatch, payload, status):
    async def unexpected_preflight(*_args, **_kwargs):
        pytest.fail("invalid conversion must be reported before network access")

    monkeypatch.setattr(
        "app.huggingface_access.HuggingFaceAccessService.preflight", unexpected_preflight
    )
    with TestClient(create_app(make_settings())) as client:
        assert client.post("/v1/models/convert", json=payload).status_code == status


def test_invalid_api_keys_are_throttled_and_success_resets_client() -> None:
    app = create_app(make_settings(api_key="correct-key"))
    wrong_headers = {"Authorization": "Bearer wrong-key"}
    good_headers = {"Authorization": "Bearer correct-key"}

    with TestClient(app) as client:
        for _ in range(10):
            response = client.get("/v1/models", headers=wrong_headers)
            assert response.status_code == 401

        blocked = client.get("/v1/models", headers=wrong_headers)
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1

        accepted = client.get("/v1/models", headers=good_headers)
        assert accepted.status_code == 200

        after_reset = client.get("/v1/models", headers=wrong_headers)
        assert after_reset.status_code == 401
