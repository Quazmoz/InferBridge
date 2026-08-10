"""Hard-state safeguards for runtime maintenance."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import runtime_health
from app.runtime_health import (
    RuntimeHealthActionRequest,
    RuntimeHealthService,
    maintenance_recommendation,
)
from app.runtime_health_safety import HardenedRuntimeHealthService
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


class TrackedEngine:
    def __init__(self) -> None:
        self.device = "CPU"
        self.closed = False

    def close(self) -> None:
        self.closed = True


class GuardManager(HardeningManager):
    def __init__(self, catalog) -> None:
        super().__init__(catalog)
        self.load_calls = 0
        self.convert_calls = 0
        self.delete_calls = 0
        self.register_calls = 0
        self.reload_calls = 0
        self.validation_started: asyncio.Event | None = None
        self.validation_release: asyncio.Event | None = None
        self.last_engine: TrackedEngine | None = None

    def schedule_load(self, _model_id: str, *args, **kwargs):
        self.load_calls += 1
        return None

    def schedule_convert(self, _model_id: str, *args, **kwargs):
        self.convert_calls += 1
        return None

    def delete(self, _model_id: str, *args, **kwargs):
        self.delete_calls += 1
        return {}

    def register_model(self, *args, **kwargs):
        self.register_calls += 1
        return None

    def reload_catalog(self, *args, **kwargs):
        self.reload_calls += 1
        return None

    async def build_temporary_engine(self, _model_id: str, _device: str):
        if self.validation_started is not None:
            self.validation_started.set()
        if self.validation_release is not None:
            await self.validation_release.wait()
        engine = TrackedEngine()
        self.last_engine = engine
        return engine, 0.01


class GuardStorage:
    def __init__(self) -> None:
        self._cleanup_lock = asyncio.Lock()
        self.clear_calls = 0
        self.cache_cleared: asyncio.Event | None = None

    async def _clear_compiled_cache(self):
        self.clear_calls += 1
        if self.cache_cleared is not None:
            self.cache_cleared.set()
        return {"freed_bytes": 64}


def _patch_validation_runtime(monkeypatch, status: str) -> None:
    monkeypatch.setattr(
        runtime_health,
        "conversion_health",
        lambda _cfg: {"status": status, "label": status, "details": ""},
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


def _runtime_record(status: str, *, acknowledgment: bool = False) -> dict:
    record = {
        "status": status,
        "runtime": {
            "application": "1.0.0",
            "openvino": "2026.2.0",
            "openvino_genai": "2026.2.0",
        },
        "conversion_fingerprint": "fingerprint",
    }
    if acknowledgment:
        record["acknowledged_at"] = 1
    else:
        record["validated_at"] = 1
    return record


def test_hard_artifact_failure_overrides_old_successful_validation():
    recommendation = maintenance_recommendation(
        "incomplete",
        validation_current=True,
        acknowledged_current=True,
        source_cache_reusable=True,
    )
    assert recommendation["action"] == "reconvert"
    assert recommendation["label"] == "Reconvert from existing HF cache"


def test_cache_rebuild_preflights_all_targets_before_clearing_shared_cache(
    tmp_path, monkeypatch
):
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


def test_hardened_revalidation_blocks_new_lifecycle_work(tmp_path, monkeypatch):
    async def exercise() -> None:
        cfg = HardeningConfig("model-a", tmp_path)
        manager = GuardManager({"model-a": cfg})
        storage = GuardStorage()
        manager.validation_started = asyncio.Event()
        manager.validation_release = asyncio.Event()
        service = HardenedRuntimeHealthService(
            settings=SimpleNamespace(device="CPU"),
            manager=manager,
            paths=SimpleNamespace(config_dir=tmp_path / "config"),
            storage=storage,
        )
        service._conversion_fingerprint = lambda _cfg: "fingerprint"
        _patch_validation_runtime(monkeypatch, "legacy_untracked")

        task = asyncio.create_task(
            service.perform(RuntimeHealthActionRequest(action="revalidate", model_id="model-a"))
        )
        await asyncio.wait_for(manager.validation_started.wait(), timeout=1)

        with pytest.raises(ValueError, match="Runtime model maintenance is active"):
            manager.schedule_load("model-a", "CPU")
        with pytest.raises(ValueError, match="Runtime model maintenance is active"):
            manager.schedule_convert("model-a", "CPU")
        with pytest.raises(ValueError, match="Runtime model maintenance is active"):
            manager.delete("model-a")

        manager.validation_release.set()
        result = await asyncio.wait_for(task, timeout=1)
        assert result["status"] == "validated"
        assert manager.load_calls == 0
        assert manager.convert_calls == 0
        assert manager.delete_calls == 0

    asyncio.run(exercise())


def test_cancelled_validation_keeps_gate_until_native_engine_is_closed(tmp_path, monkeypatch):
    async def exercise() -> None:
        cfg = HardeningConfig("model-a", tmp_path)
        manager = GuardManager({"model-a": cfg})
        storage = GuardStorage()
        manager.validation_started = asyncio.Event()
        manager.validation_release = asyncio.Event()
        service = HardenedRuntimeHealthService(
            settings=SimpleNamespace(device="CPU"),
            manager=manager,
            paths=SimpleNamespace(config_dir=tmp_path / "config"),
            storage=storage,
        )
        service._conversion_fingerprint = lambda _cfg: "fingerprint"
        _patch_validation_runtime(monkeypatch, "legacy_untracked")

        task = asyncio.create_task(
            service.perform(RuntimeHealthActionRequest(action="revalidate", model_id="model-a"))
        )
        await asyncio.wait_for(manager.validation_started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)

        assert task.done() is False
        assert service._maintenance_active is True
        with pytest.raises(ValueError, match="Runtime model maintenance is active"):
            manager.schedule_load("model-a", "CPU")

        manager.validation_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert manager.last_engine is not None
        assert manager.last_engine.closed is True
        assert service._maintenance_active is False
        manager.schedule_load("model-a", "CPU")
        assert manager.load_calls == 1

    asyncio.run(exercise())


def test_cache_rebuild_keeps_gate_after_shared_cache_is_cleared(tmp_path, monkeypatch):
    async def exercise() -> None:
        cfg = HardeningConfig("model-a", tmp_path)
        manager = GuardManager({"model-a": cfg})
        storage = GuardStorage()
        manager.validation_started = asyncio.Event()
        manager.validation_release = asyncio.Event()
        storage.cache_cleared = asyncio.Event()
        service = HardenedRuntimeHealthService(
            settings=SimpleNamespace(device="CPU"),
            manager=manager,
            paths=SimpleNamespace(config_dir=tmp_path / "config"),
            storage=storage,
        )
        service._conversion_fingerprint = lambda _cfg: "fingerprint"
        _patch_validation_runtime(monkeypatch, "stale_runtime")

        task = asyncio.create_task(
            service.perform(
                RuntimeHealthActionRequest(
                    action="rebuild_compiled_cache",
                    model_id="model-a",
                )
            )
        )
        await asyncio.wait_for(storage.cache_cleared.wait(), timeout=1)
        await asyncio.wait_for(manager.validation_started.wait(), timeout=1)

        with pytest.raises(ValueError, match="Runtime model maintenance is active"):
            manager.schedule_load("model-a", "CPU")

        manager.validation_release.set()
        result = await asyncio.wait_for(task, timeout=1)
        assert result["status"] == "completed"
        assert storage.clear_calls == 1
        assert manager.load_calls == 0

    asyncio.run(exercise())


def test_hardened_service_returns_controlled_invalid_device_error(tmp_path):
    cfg = HardeningConfig("model-a", tmp_path)
    manager = GuardManager({"model-a": cfg})
    storage = GuardStorage()
    service = HardenedRuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )

    with pytest.raises(StorageConflict) as exc_info:
        service._select_device(cfg, "definitely-not-a-device")

    assert exc_info.value.code == "invalid_device"


def test_hardened_service_ignores_oversized_state_file(tmp_path):
    cfg = HardeningConfig("model-a", tmp_path)
    manager = GuardManager({"model-a": cfg})
    storage = GuardStorage()
    service = HardenedRuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    service.state_file.parent.mkdir(parents=True, exist_ok=True)
    service.state_file.write_bytes(b"x" * 1_000_001)

    assert service._read_state() == {"schema_version": 1, "models": {}}


def test_hardened_fingerprint_tracks_support_file_changes(tmp_path):
    cfg = HardeningConfig("model-a", tmp_path)
    model_dir = cfg.abs_path(tmp_path)
    model_dir.mkdir(parents=True)
    support_file = model_dir / "tokenizer_config.json"
    support_file.write_text('{"version":1}', encoding="utf-8")
    manager = GuardManager({"model-a": cfg})
    storage = GuardStorage()
    service = HardenedRuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )

    before = service._conversion_fingerprint(cfg)
    support_file.write_text('{"version":200}', encoding="utf-8")
    after = service._conversion_fingerprint(cfg)

    assert before != after


def test_failed_validation_cannot_be_hidden_by_acknowledgment(tmp_path, monkeypatch):
    cfg = HardeningConfig("model-a", tmp_path)
    manager = GuardManager({"model-a": cfg})
    storage = GuardStorage()
    service = HardenedRuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    service._conversion_fingerprint = lambda _cfg: "fingerprint"
    service._source_cache_state = lambda _cfg: "reusable"
    _patch_validation_runtime(monkeypatch, "stale_runtime")
    service._write_state(
        {
            "schema_version": 1,
            "models": {
                "model-a": {
                    "validation": _runtime_record("failed"),
                    "acknowledgment": _runtime_record("acknowledged", acknowledgment=True),
                }
            },
        }
    )

    snapshot = service._snapshot_sync()
    row = snapshot["models"][0]

    assert row["recommendation"]["action"] == "reconvert"
    assert row["recommendation"]["label"] == "Reconvert from existing HF cache"
    assert row["can_leave_unchanged"] is False
    assert row["acknowledged_current_runtime"] is False
    with pytest.raises(StorageConflict) as exc_info:
        service._acknowledge("model-a")
    assert exc_info.value.code == "acknowledgment_unavailable"


def test_failed_validation_overrides_compatible_metadata(tmp_path, monkeypatch):
    cfg = HardeningConfig("model-a", tmp_path)
    manager = GuardManager({"model-a": cfg})
    storage = GuardStorage()
    service = HardenedRuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    service._conversion_fingerprint = lambda _cfg: "fingerprint"
    service._source_cache_state = lambda _cfg: "not_found"
    _patch_validation_runtime(monkeypatch, "compatible")
    service._write_state(
        {
            "schema_version": 1,
            "models": {"model-a": {"validation": _runtime_record("failed")}},
        }
    )

    snapshot = service._snapshot_sync()
    row = snapshot["models"][0]

    assert row["recommendation"]["action"] == "reconvert"
    assert snapshot["summary"]["reconvert"] == 1
    assert snapshot["summary"]["needs_attention"] == 1


def test_reconvert_refuses_to_race_catalog_update(tmp_path):
    cfg = HardeningConfig("model-a", tmp_path)
    manager = GuardManager({"model-a": cfg})
    manager._catalog_lock = threading.Lock()
    storage = GuardStorage()
    service = HardenedRuntimeHealthService(
        settings=SimpleNamespace(device="CPU"),
        manager=manager,
        paths=SimpleNamespace(config_dir=tmp_path / "config"),
        storage=storage,
    )
    manager._catalog_lock.acquire()
    try:
        with pytest.raises(StorageConflict) as exc_info:
            asyncio.run(
                service.perform(
                    RuntimeHealthActionRequest(action="reconvert", model_id="model-a")
                )
            )
    finally:
        manager._catalog_lock.release()

    assert exc_info.value.code == "maintenance_conflict"
    assert manager.convert_calls == 0
