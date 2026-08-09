"""Hard-state safeguards for runtime maintenance."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import runtime_health
from app.runtime_health import RuntimeHealthService, maintenance_recommendation
from app.storage_safety import StorageConflict


class HardeningConfig:
    def __init__(self, model_id: str, root: Path) -> None:
        self.id = model_id
        self.name = model_id
        self.source_model = f"org/{model_id}"
        self.backend = "openvino-genai"
        self.weight_format = "int4"
        self.recommended_device = "CPU"
        self._root = root

    def abs_path(self, _base_dir: Path) -> Path:
        return self._root / self.id


class HardeningManager:
    def __init__(self, catalog) -> None:
        self.catalog = catalog
        self.force_mock = True
        self.engines = {}
        self.load_tasks = {}
        self.convert_tasks = {}
        self.status_overrides = {}
        self._model_recovery_locks = {}
        self.events = []

    async def build_temporary_engine(self, _model_id: str, _device: str):
        engine = SimpleNamespace(device="CPU", close=lambda: None)
        return engine, 0.01

    def emit_event(self, level: str, message: str) -> None:
        self.events.append((level, message))


class HardeningStorage:
    def __init__(self) -> None:
        self.calls = 0

    async def cleanup(self, _request):
        self.calls += 1
        return {"status": "completed", "freed_bytes": 0}


def test_hard_artifact_failure_overrides_old_successful_validation():
    recommendation = maintenance_recommendation(
        "incomplete",
        validation_current=True,
        acknowledged_current=True,
        source_cache_reusable=True,
    )
    assert recommendation["action"] == "reconvert"
    assert recommendation["label"] == "Reconvert from existing HF cache"


def test_cache_rebuild_preflights_all_targets_before_clearing_shared_cache(tmp_path, monkeypatch):
    catalog = {
        "healthy": HardeningConfig("healthy", tmp_path),
        "damaged": HardeningConfig("damaged", tmp_path),
    }
    manager = HardeningManager(catalog)
    storage = HardeningStorage()
    service = RuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    statuses = {"healthy": "stale_runtime", "damaged": "invalid_metadata"}
    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda cfg: {"status": statuses[cfg.id], "label": statuses[cfg.id], "details": ""},
    )

    with pytest.raises(StorageConflict) as exc_info:
        asyncio.run(service._rebuild_compiled_cache(["healthy", "damaged"], None))

    assert exc_info.value.code == "reconversion_required"
    assert storage.calls == 0


def test_validation_records_actual_device_after_manager_safety_routing(tmp_path, monkeypatch):
    cfg = HardeningConfig("model-a", tmp_path)
    manager = HardeningManager({"model-a": cfg})
    storage = HardeningStorage()
    service = RuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    service._conversion_fingerprint = lambda _cfg: "fingerprint"
    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda _cfg: {"status": "legacy_untracked", "label": "Legacy", "details": ""},
    )
    monkeypatch.setattr(
        runtime_health,
        "current_runtime_versions",
        lambda: {
            "application": "1.0.0",
            "openvino": "2026.2.0",
            "openvino_genai": "2026.2.0",
        },
    )

    result = asyncio.run(service._validate_one("model-a", "AUTO:NPU,GPU,CPU"))

    assert result["requested_device"] == "AUTO:NPU,GPU,CPU"
    assert result["device"] == "CPU"
    state = service._read_state()["models"]["model-a"]["validation"]
    assert state["requested_device"] == "AUTO:NPU,GPU,CPU"
    assert state["device"] == "CPU"
