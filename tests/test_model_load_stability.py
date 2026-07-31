"""Regression coverage for stable long-running model loads."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path

import pytest

from app import model_load_target
from app.config import BASE_DIR, Settings
from app.model_manager import ModelManager
from runtime import device_check
from runtime.openvino_engine import MockEngine

MODEL_ID = "tinyllama-1.1b-chat-fp16"
SECOND_MODEL_ID = "tinyllama-1.1b-chat-int4"


def _manager() -> ModelManager:
    return ModelManager(
        Settings(
            host="127.0.0.1",
            port=8000,
            device="CPU",
            models_file=BASE_DIR / "models.json",
            models_dir=BASE_DIR / "models" / "openvino",
            default_model=None,
            api_key=None,
            force_mock=True,
        )
    )


def test_queued_load_reports_position_and_elapsed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(model_load_target, "_LOAD_WAIT_UPDATE_SECONDS", 0.02)
        manager = _manager()
        await manager._load_lock.acquire()

        task = manager.schedule_load(MODEL_ID, "CPU")
        assert task is not None
        await asyncio.sleep(0.07)

        progress = manager.progress[MODEL_ID]
        assert progress["phase"] == "queued"
        assert "Waiting for another model preparation" in progress["message"]
        assert "queue 1 of 1" in progress["message"]
        assert "elapsed" in progress["message"]

        manager._load_lock.release()
        await asyncio.wait_for(task, timeout=2)
        assert manager.devices[MODEL_ID] == "CPU"
        await manager.shutdown()

    asyncio.run(scenario())


def test_queued_loads_keep_fifo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(model_load_target, "_LOAD_WAIT_UPDATE_SECONDS", 0.02)
        manager = _manager()
        build_order: list[str] = []

        def record_build(
            model_id: str,
            device: str,
            draft_model_path: str | None = None,
        ) -> MockEngine:
            build_order.append(model_id)
            return MockEngine(model_id, str(Path("models") / model_id), device)

        manager._build_engine = record_build
        await manager._load_lock.acquire()
        first = manager.schedule_load(MODEL_ID, "CPU")
        second = manager.schedule_load(SECOND_MODEL_ID, "CPU")
        assert first is not None
        assert second is not None
        await asyncio.sleep(0.07)

        assert "queue 1 of 2" in manager.progress[MODEL_ID]["message"]
        assert "queue 2 of 2" in manager.progress[SECOND_MODEL_ID]["message"]

        manager._load_lock.release()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        assert build_order == [MODEL_ID, SECOND_MODEL_ID]
        await manager.shutdown()

    asyncio.run(scenario())


def test_cancelled_queued_load_does_not_leave_an_orphaned_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(model_load_target, "_LOAD_WAIT_UPDATE_SECONDS", 0.02)
        manager = _manager()
        await manager._load_lock.acquire()

        task = manager.schedule_load(MODEL_ID, "CPU")
        assert task is not None
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert manager._load_lock.locked()
        manager._load_lock.release()
        await asyncio.sleep(0.05)
        assert not manager._load_lock.locked()
        await manager.shutdown()

    asyncio.run(scenario())


def test_native_compile_emits_heartbeat_and_long_load_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(model_load_target, "_NATIVE_LOAD_HEARTBEAT_SECONDS", 0.02)
        monkeypatch.setattr(model_load_target, "_NATIVE_LOAD_LONG_WARNING_SECONDS", 0.04)
        manager = _manager()
        started = threading.Event()
        release = threading.Event()

        def blocking_build(
            model_id: str,
            device: str,
            draft_model_path: str | None = None,
        ) -> MockEngine:
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("Test did not release the model build")
            return MockEngine(
                model_id,
                str(Path("models") / model_id),
                device,
            )

        manager._build_engine = blocking_build
        task = manager.schedule_load(MODEL_ID, "NPU")
        assert task is not None

        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert started.is_set()

        await asyncio.sleep(0.08)
        message = manager.progress[MODEL_ID]["message"]
        assert "Still compiling" in message
        assert "First load can take several minutes" in message
        assert "OpenVINO cache" in message

        release.set()
        await asyncio.wait_for(task, timeout=2)
        assert manager.devices[MODEL_ID] == "NPU"
        await manager.shutdown()

    asyncio.run(scenario())


def test_invalid_device_fails_before_waiting_for_native_load_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        manager = _manager()
        manager.force_mock = False
        monkeypatch.setattr(device_check, "available_devices", lambda: ["CPU"])
        await manager._load_lock.acquire()

        task = manager.schedule_load(MODEL_ID, "NPU")
        assert task is not None
        await asyncio.wait_for(task, timeout=1)

        assert manager.status_overrides[MODEL_ID]["status"] == "error"
        assert manager._load_lock.locked()
        manager._load_lock.release()
        await manager.shutdown()

    asyncio.run(scenario())
