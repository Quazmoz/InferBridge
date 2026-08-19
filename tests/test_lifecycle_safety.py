"""Regression coverage for cross-operation model lifecycle safety."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.config import BASE_DIR, Settings
from app.model_manager import ModelManager

MODEL_ID = "tinyllama-1.1b-chat-fp16"


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


def test_delete_rejects_an_active_conversion_task() -> None:
    async def scenario() -> None:
        manager = _manager()
        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        manager.convert_tasks[MODEL_ID] = task
        manager._set_status(MODEL_ID, "converting")

        with pytest.raises(ValueError, match="still being prepared"):
            manager.delete(MODEL_ID)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        manager.convert_tasks.clear()
        await manager.shutdown()

    asyncio.run(scenario())


def test_delete_rejects_tracked_preparation_without_active_task() -> None:
    async def scenario() -> None:
        manager = _manager()
        # A tracked preparation status must reject the delete even when no live
        # task object is present (e.g. queued work waiting on the load lock).
        manager._set_status(MODEL_ID, "loading")

        with pytest.raises(ValueError, match="still being prepared"):
            manager.delete(MODEL_ID)

        manager._clear_status(MODEL_ID)
        await manager.shutdown()

    asyncio.run(scenario())


def test_latest_load_request_resumes_after_conversion() -> None:
    async def scenario() -> None:
        manager = _manager()
        release = asyncio.Event()

        async def fake_conversion() -> None:
            try:
                await release.wait()
                manager._clear_status(MODEL_ID)
                manager._set_progress(
                    MODEL_ID,
                    "ready",
                    "Conversion complete.",
                    percent=100,
                )
            finally:
                manager.convert_tasks.pop(MODEL_ID, None)

        conversion = asyncio.create_task(fake_conversion())
        manager.convert_tasks[MODEL_ID] = conversion
        manager._set_status(MODEL_ID, "converting")
        manager._set_progress(MODEL_ID, "converting", "Converting…", percent=40)

        first = manager.schedule_load(MODEL_ID, "GPU")
        second = manager.schedule_load(MODEL_ID, "NPU")
        assert first is conversion
        assert second is conversion
        assert "load on NPU" in manager.progress[MODEL_ID]["message"]

        release.set()
        await conversion
        await asyncio.sleep(0)

        load_task = manager.load_tasks.get(MODEL_ID)
        assert load_task is not None
        await load_task
        assert manager.devices[MODEL_ID] == "NPU"
        await manager.shutdown()

    asyncio.run(scenario())


def test_deferred_load_failure_redacts_secret_and_machine_path() -> None:
    async def scenario() -> None:
        manager = _manager()
        release = asyncio.Event()

        async def fake_conversion() -> None:
            try:
                await release.wait()
                manager._clear_status(MODEL_ID)
            finally:
                manager.convert_tasks.pop(MODEL_ID, None)

        conversion = asyncio.create_task(fake_conversion())
        manager.convert_tasks[MODEL_ID] = conversion
        manager._set_status(MODEL_ID, "converting")
        manager.schedule_load(MODEL_ID, "NPU")

        original_schedule_load = manager.schedule_load
        secret = "hf_" + "s" * 32

        def fail_load(*_args, **_kwargs):
            raise RuntimeError(r"C:\Users\Quinn\private\model " + secret)

        manager.schedule_load = fail_load
        release.set()
        await conversion
        await asyncio.sleep(0)

        error = manager.status_overrides[MODEL_ID]["error"]
        assert secret not in error
        assert r"C:\Users\Quinn" not in error
        assert "[redacted]" in error

        manager.schedule_load = original_schedule_load
        manager._clear_status(MODEL_ID)
        await manager.shutdown()

    asyncio.run(scenario())


def test_shutdown_does_not_start_a_deferred_load() -> None:
    async def scenario() -> None:
        manager = _manager()
        release = asyncio.Event()

        async def fake_conversion() -> None:
            try:
                await release.wait()
            finally:
                manager.convert_tasks.pop(MODEL_ID, None)

        conversion = asyncio.create_task(fake_conversion())
        manager.convert_tasks[MODEL_ID] = conversion
        manager._set_status(MODEL_ID, "converting")
        manager.schedule_load(MODEL_ID, "NPU")

        shutdown = asyncio.create_task(manager.shutdown())
        await asyncio.sleep(0)
        release.set()
        await shutdown
        await asyncio.sleep(0)

        assert MODEL_ID not in manager.load_tasks
        assert MODEL_ID not in manager.engines

    asyncio.run(scenario())
