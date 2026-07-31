import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.model_cancellation import CancellationConflict
from app.model_manager import ModelManager
from app.server import create_app

MODEL_ID = "model-1"


def _settings(tmp_path, *, api_key: str | None = None) -> Settings:
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                MODEL_ID: {
                    "name": "Model One",
                    "model_path": "models/openvino/model-1",
                    "source_model": "org/model-1",
                    "weight_format": "int4",
                    "recommended_device": "CPU",
                }
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        models_file=catalog_file,
        models_dir=tmp_path / "models" / "openvino",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
        device="CPU",
        api_key=api_key,
    )


def _manager(tmp_path) -> ModelManager:
    return ModelManager(_settings(tmp_path))


async def _waiting_task() -> None:
    await asyncio.Event().wait()


def test_conversion_cancellation_is_operation_scoped_and_terminal(tmp_path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager._set_status(MODEL_ID, "converting")
        manager._set_progress(MODEL_ID, "converting", "Converting model", percent=42)
        operation_id = manager.progress[MODEL_ID]["operation_id"]
        task = asyncio.create_task(_waiting_task())
        manager.convert_tasks[MODEL_ID] = task

        capability = manager.cancellation_capability(MODEL_ID)
        assert capability == {
            "can_cancel": True,
            "cancel_mode": "conversion",
            "cancel_reason": None,
        }

        result = await manager.cancel_operation(MODEL_ID, operation_id)

        assert task.cancelled()
        assert result["status"] == "cancelled"
        assert result["operation_id"] == operation_id
        assert result["cancel_mode"] == "conversion"
        assert manager.status_overrides[MODEL_ID]["status"] == "cancelled"
        assert manager.progress[MODEL_ID]["phase"] == "cancelled"
        assert manager.progress[MODEL_ID]["operation_id"] == operation_id
        assert manager.progress[MODEL_ID]["revision"] > 1
        assert manager.catalog_entry(MODEL_ID)["can_cancel"] is False

    asyncio.run(scenario())


def test_duplicate_cancel_is_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager._set_status(MODEL_ID, "cancelled")
        manager._set_progress(MODEL_ID, "cancelled", "Already cancelled")
        operation_id = manager.progress[MODEL_ID]["operation_id"]

        result = await manager.cancel_operation(MODEL_ID, operation_id)

        assert result["status"] == "cancelled"
        assert result["already_cancelled"] is True
        assert result["operation_id"] == operation_id

    asyncio.run(scenario())


def test_stale_operation_id_cannot_cancel_newer_attempt(tmp_path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager._set_status(MODEL_ID, "converting")
        manager._set_progress(MODEL_ID, "converting", "Current conversion")
        current_operation_id = manager.progress[MODEL_ID]["operation_id"]
        task = asyncio.create_task(_waiting_task())
        manager.convert_tasks[MODEL_ID] = task

        with pytest.raises(CancellationConflict) as raised:
            await manager.cancel_operation(MODEL_ID, "convert-stale-operation")

        assert raised.value.code == "stale_operation"
        assert raised.value.current_operation_id == current_operation_id
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_native_load_compilation_is_not_reported_as_cancellable(tmp_path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager._set_status(MODEL_ID, "loading")
        manager._set_progress(MODEL_ID, "loading", "Compiling model for CPU")
        operation_id = manager.progress[MODEL_ID]["operation_id"]
        task = asyncio.create_task(_waiting_task())
        manager.load_tasks[MODEL_ID] = task

        capability = manager.cancellation_capability(MODEL_ID)
        assert capability["can_cancel"] is False
        assert "Native OpenVINO compilation" in capability["cancel_reason"]

        with pytest.raises(CancellationConflict) as raised:
            await manager.cancel_operation(MODEL_ID, operation_id)
        assert raised.value.code == "native_load_in_progress"
        assert not task.done()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_queued_load_can_be_cancelled_before_native_compile(tmp_path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager._set_status(MODEL_ID, "queued")
        manager._set_progress(MODEL_ID, "queued", "Queued model load")
        operation_id = manager.progress[MODEL_ID]["operation_id"]
        task = asyncio.create_task(_waiting_task())
        manager.load_tasks[MODEL_ID] = task

        entry = manager.catalog_entry(MODEL_ID)
        assert entry["can_cancel"] is True
        assert entry["cancel_mode"] == "preparation"

        result = await manager.cancel_operation(MODEL_ID, operation_id)
        assert task.cancelled()
        assert result["cancel_mode"] == "preparation"
        assert manager.progress[MODEL_ID]["phase"] == "cancelled"

    asyncio.run(scenario())


def test_cancel_route_returns_structured_conflict(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        manager = client.app.state.manager

        async def stale_cancel(_model: str, _operation_id: str):
            raise CancellationConflict(
                "stale_operation",
                "The operation changed.",
                current_operation_id="convert-current",
            )

        manager.cancel_operation = stale_cancel
        response = client.post(
            "/v1/models/cancel",
            json={"model": MODEL_ID, "operation_id": "convert-old"},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == {
        "code": "stale_operation",
        "message": "The operation changed.",
        "current_operation_id": "convert-current",
    }


def test_cancel_route_requires_exact_model_and_safe_browser_origin(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        missing = client.post(
            "/v1/models/cancel",
            json={"model": "missing", "operation_id": "convert-operation"},
        )
        cross_site = client.post(
            "/v1/models/cancel",
            json={"model": MODEL_ID, "operation_id": "convert-operation"},
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

    assert missing.status_code == 404
    assert cross_site.status_code == 403


def test_cancel_route_uses_existing_api_key_policy(tmp_path) -> None:
    app = create_app(_settings(tmp_path, api_key="secret-key"))
    with TestClient(app) as client:
        missing = client.post(
            "/v1/models/cancel",
            json={"model": MODEL_ID, "operation_id": "convert-operation"},
        )
        wrong = client.post(
            "/v1/models/cancel",
            json={"model": MODEL_ID, "operation_id": "convert-operation"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
