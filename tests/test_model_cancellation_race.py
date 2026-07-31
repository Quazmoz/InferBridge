import asyncio
import json

import pytest

from app.config import Settings
from app.model_cancellation import CancellationConflict
from app.model_manager import ModelManager

MODEL_ID = "model-1"


def _manager(tmp_path) -> ModelManager:
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
    return ModelManager(
        Settings(
            models_file=catalog_file,
            models_dir=tmp_path / "models",
            cache_dir=tmp_path / "cache",
            benchmark_results_file=tmp_path / "benchmarks.json",
            force_mock=True,
            device="CPU",
        )
    )


def test_successful_completion_is_not_overwritten_by_cancellation(tmp_path) -> None:
    async def scenario() -> None:
        manager = _manager(tmp_path)
        manager._set_status(MODEL_ID, "converting")
        manager._set_progress(MODEL_ID, "converting", "Converting model")
        operation_id = manager.progress[MODEL_ID]["operation_id"]

        async def completes_when_cancelled() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                manager._set_progress(MODEL_ID, "ready", "Model is ready", percent=100)
                manager._clear_status(MODEL_ID)

        task = asyncio.create_task(completes_when_cancelled())
        manager.convert_tasks[MODEL_ID] = task

        with pytest.raises(CancellationConflict) as raised:
            await manager.cancel_operation(MODEL_ID, operation_id)

        assert raised.value.code == "task_finished"
        assert manager.progress[MODEL_ID]["phase"] == "ready"
        assert manager.progress[MODEL_ID]["operation_id"] == operation_id
        assert manager.status_overrides.get(MODEL_ID) is None

    asyncio.run(scenario())
