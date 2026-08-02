import json
from pathlib import Path

from app import status_split
from app.config import Settings
from app.model_manager import ModelManager

MODEL_ID = "model-1"


def _manager(tmp_path: Path) -> ModelManager:
    models_dir = tmp_path / "models" / "openvino"
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                MODEL_ID: {
                    "name": "Model One",
                    "model_path": str(models_dir / MODEL_ID),
                    "source_model": "org/model-1",
                    "weight_format": "int4",
                    "recommended_device": "CPU",
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        models_file=catalog_file,
        models_dir=models_dir,
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        force_mock=True,
        device="CPU",
    )
    return ModelManager(settings)


def test_split_status_includes_compact_recovery_summary(tmp_path) -> None:
    manager = _manager(tmp_path)
    model_dir = Path(manager.catalog[MODEL_ID].model_path)
    model_dir.mkdir(parents=True)
    (model_dir / "partial.bin").write_bytes(b"partial")
    manager._set_status(MODEL_ID, "converting")
    manager._set_progress(MODEL_ID, "converting", "Converting model")
    manager._set_status(MODEL_ID, "error", error="conversion failed")
    manager._set_progress(MODEL_ID, "error", "Conversion failed")

    entry = status_split._lifecycle_catalog_entry(manager, MODEL_ID)

    assert entry["recovery"]["available"] is True
    assert entry["recovery"]["model_id"] == MODEL_ID
    assert entry["recovery"]["conversion_output"] == "incomplete"
    assert "failure_details" not in entry["recovery"]
