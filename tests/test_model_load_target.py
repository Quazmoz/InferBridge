"""Regression tests for explicit device selection during model loading."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.server import create_app
from runtime.openvino_engine import MockEngine

MODEL_ID = "tinyllama-1.1b-chat-fp16"


def _client() -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        device="GPU",
        models_file=BASE_DIR / "models.json",
        models_dir=BASE_DIR / "models" / "openvino",
        default_model=None,
        api_key=None,
        force_mock=True,
    )
    return TestClient(create_app(settings))


def _model_entry(client: TestClient) -> dict:
    status = client.get("/v1/system/status").json()
    return next(model for model in status["models"]["available"] if model["id"] == MODEL_ID)


def _wait_for_device(client: TestClient, expected_device: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last_entry: dict = {}
    while time.time() < deadline:
        last_entry = _model_entry(client)
        if last_entry["is_loaded"] and last_entry["device"] == expected_device:
            return last_entry
        time.sleep(0.02)
    raise AssertionError(
        f"{MODEL_ID} did not load on {expected_device}; last status was {last_entry}"
    )


def _wait_for_load_task(client: TestClient, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    manager = client.app.state.manager
    last_entry: dict = {}
    while time.time() < deadline:
        last_entry = _model_entry(client)
        task = manager.load_tasks.get(MODEL_ID)
        if task is None or task.done():
            return last_entry
        time.sleep(0.02)
    raise AssertionError(f"{MODEL_ID} load did not settle; last status was {last_entry}")


def test_explicit_npu_load_switches_model_already_loaded_on_gpu() -> None:
    with _client() as client:
        first = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "GPU"},
        )
        assert first.status_code == 200, first.text
        _wait_for_device(client, "GPU")

        switch = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "NPU"},
        )
        assert switch.status_code == 200, switch.text

        entry = _wait_for_device(client, "NPU")
        assert entry["progress"]["phase"] == "ready"
        assert client.app.state.manager.devices[MODEL_ID] == "NPU"


def test_failed_device_switch_keeps_the_working_engine_available() -> None:
    with _client() as client:
        first = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "GPU"},
        )
        assert first.status_code == 200, first.text
        _wait_for_device(client, "GPU")

        manager = client.app.state.manager
        working_engine = manager.engines[MODEL_ID]
        original_build = manager._build_engine

        def fail_npu_build(
            model_id: str,
            device: str,
            draft_model_path: str | None = None,
        ) -> MockEngine:
            if device == "NPU":
                raise RuntimeError("simulated NPU compiler failure")
            return original_build(model_id, device, draft_model_path)

        manager._build_engine = fail_npu_build
        switch = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "NPU"},
        )
        assert switch.status_code == 200, switch.text

        entry = _wait_for_load_task(client)
        assert entry["is_loaded"] is True
        assert entry["device"] == "GPU"
        assert entry["status"] == "loaded"
        assert "Continuing on GPU" in entry["progress"]["message"]
        assert manager.engines[MODEL_ID] is working_engine


def test_loaded_model_remains_usable_while_replacement_compiles() -> None:
    replacement_started = threading.Event()
    release_replacement = threading.Event()

    with _client() as client:
        first = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "GPU"},
        )
        assert first.status_code == 200, first.text
        _wait_for_device(client, "GPU")

        manager = client.app.state.manager
        working_engine = manager.engines[MODEL_ID]
        working_lock = manager.get_lock(MODEL_ID)

        def blocking_npu_build(
            model_id: str,
            device: str,
            draft_model_path: str | None = None,
        ) -> MockEngine:
            assert device == "NPU"
            replacement_started.set()
            if not release_replacement.wait(timeout=5):
                raise TimeoutError("Test did not release the replacement engine build")
            return MockEngine(model_id, str(Path("models") / model_id), device)

        manager._build_engine = blocking_npu_build
        switch = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "NPU"},
        )
        assert switch.status_code == 200, switch.text
        assert replacement_started.wait(timeout=2), "NPU replacement build did not start"

        try:
            assert manager.engines[MODEL_ID] is working_engine
            assert manager.get_lock(MODEL_ID) is working_lock
            assert working_lock.locked() is False

            chat = client.post(
                "/v1/chat/completions",
                json={
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": "still available"}],
                    "max_tokens": 16,
                    "stream": False,
                },
            )
            assert chat.status_code == 200, chat.text
            assert chat.json()["choices"][0]["message"]["content"]
        finally:
            release_replacement.set()

        entry = _wait_for_device(client, "NPU")
        assert entry["device"] == "NPU"


def test_newest_device_request_retargets_in_flight_first_load() -> None:
    first_build_started = threading.Event()
    release_first_build = threading.Event()
    build_devices: list[str] = []

    with _client() as client:
        manager = client.app.state.manager

        def blocking_build(
            model_id: str,
            device: str,
            draft_model_path: str | None = None,
        ) -> MockEngine:
            build_devices.append(device)
            if len(build_devices) == 1:
                first_build_started.set()
                if not release_first_build.wait(timeout=5):
                    raise TimeoutError("Test did not release the first mock engine build")
            return MockEngine(
                model_id,
                str(Path("models") / model_id),
                device,
            )

        manager._build_engine = blocking_build

        first = client.post(
            "/v1/models/load",
            json={"model": MODEL_ID, "device": "GPU"},
        )
        assert first.status_code == 200, first.text
        assert first_build_started.wait(timeout=2), "GPU build did not start"

        try:
            retarget = client.post(
                "/v1/models/load",
                json={"model": MODEL_ID, "device": "NPU"},
            )
            assert retarget.status_code == 200, retarget.text
        finally:
            release_first_build.set()

        entry = _wait_for_device(client, "NPU")
        assert entry["device"] == "NPU"
        assert build_devices == ["GPU", "NPU"]
