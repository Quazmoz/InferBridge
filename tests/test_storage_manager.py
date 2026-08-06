from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import storage_manager, storage_safety
from app.storage_manager import (
    StorageCleanupRequest,
    StorageConflict,
    StorageManagerService,
    register_storage_manager_routes,
)
from runtime.model_output_transaction import model_output_transaction_paths


class FakeConfig:
    def __init__(self, model_id: str, name: str, path: Path, source_model: str = "") -> None:
        self.id = model_id
        self.name = name
        self._path = path
        self.source_model = source_model

    def abs_path(self, _base: Path) -> Path:
        return self._path


class FakeTask:
    def __init__(self, done: bool = False) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


class FakeManager:
    def __init__(self, models_dir: Path, configs: list[FakeConfig]) -> None:
        self.catalog = {config.id: config for config in configs}
        self.engines: dict[str, object] = {}
        self.load_tasks: dict[str, FakeTask] = {}
        self.convert_tasks: dict[str, FakeTask] = {}
        self.status_overrides: dict[str, dict] = {}
        self._model_recovery_records: dict[str, dict] = {}
        self._models_dir = models_dir.resolve()
        self.request_calls: list[str] = []
        self.load_calls: list[str] = []
        self.convert_calls: list[str] = []

    def record_request(
        self,
        model_id: str,
        _prompt_tokens: int,
        _completion_tokens: int,
        _latency_s: float,
    ) -> None:
        self.request_calls.append(model_id)

    def schedule_load(self, model_id: str, *_args, **_kwargs):
        self.load_calls.append(model_id)
        return None

    def schedule_convert(self, model_id: str, *_args, **_kwargs):
        self.convert_calls.append(model_id)
        return None

    def _ensure_within_models_dir(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved != self._models_dir and self._models_dir not in resolved.parents:
            raise ValueError("outside models directory")

    def delete(self, model_id: str) -> dict:
        config = self.catalog[model_id]
        size = sum(
            path.stat().st_size
            for path in config.abs_path(Path()).rglob("*")
            if path.is_file()
        )
        shutil.rmtree(config.abs_path(Path()))
        return {"deleted": True, "freed_bytes": size}


def make_service(
    tmp_path: Path,
    configs: list[FakeConfig],
) -> tuple[StorageManagerService, FakeManager]:
    models_dir = tmp_path / "models"
    config_dir = tmp_path / "config"
    compiled_dir = tmp_path / "cache" / "openvino"
    models_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    compiled_dir.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(
        models_dir=models_dir,
        models_file=config_dir / "models.json",
        cache_dir=compiled_dir,
        api_key=None,
    )
    paths = SimpleNamespace(config_dir=config_dir)
    manager = FakeManager(models_dir, configs)
    return StorageManagerService(settings=settings, manager=manager, paths=paths), manager


def write_bytes(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * count)


def test_snapshot_counts_shared_cache_once_and_records_last_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_dir = tmp_path / "models"
    first = FakeConfig("first", "First", models_dir / "first", "org/shared")
    second = FakeConfig("second", "Second", models_dir / "second", "org/shared")
    write_bytes(first.abs_path(Path()) / "model.bin", 10)
    write_bytes(second.abs_path(Path()) / "model.bin", 20)

    hf_home = tmp_path / "cache" / "huggingface"
    write_bytes(hf_home / "hub" / "models--org--shared" / "blobs" / "blob", 7)
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.setattr(
        storage_manager,
        "conversion_health",
        lambda _cfg: {"status": "compatible", "label": "Healthy", "details": ""},
    )

    service, manager = make_service(tmp_path, [first, second])
    write_bytes(Path(service.settings.cache_dir) / "compiled.blob", 5)
    manager.record_request("first", 4, 2, 0.2)

    snapshot = asyncio.run(service.snapshot())

    assert snapshot["totals"]["converted_models_bytes"] == 30
    assert snapshot["totals"]["huggingface_cache_bytes"] == 7
    assert snapshot["totals"]["compiled_cache_bytes"] == 5
    assert len(snapshot["source_caches"]) == 1
    assert snapshot["source_caches"][0]["shared"] is True
    first_row = next(item for item in snapshot["models"] if item["model_id"] == "first")
    assert first_row["last_used"]["status"] == "recorded"
    usage = json.loads((Path(service.paths.config_dir) / "storage-usage.json").read_text())
    assert usage["models"]["first"] > 0


def test_source_cache_cleanup_rejects_other_active_model_with_same_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_dir = tmp_path / "models"
    first = FakeConfig("first", "First", models_dir / "first", "org/shared")
    second = FakeConfig("second", "Second", models_dir / "second", "org/shared")
    hf_home = tmp_path / "cache" / "huggingface"
    write_bytes(hf_home / "hub" / "models--org--shared" / "blobs" / "blob", 8)
    monkeypatch.setenv("HF_HOME", str(hf_home))

    service, manager = make_service(tmp_path, [first, second])
    manager.convert_tasks["second"] = FakeTask(done=False)

    with pytest.raises(StorageConflict, match="Wait for model loading or conversion"):
        asyncio.run(
            service.cleanup(
                StorageCleanupRequest(action="remove_huggingface_cache", model_id="first")
            )
        )
    assert (hf_home / "hub" / "models--org--shared").exists()


def test_compiled_cache_cleanup_requires_every_model_unloaded(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    config = FakeConfig("first", "First", models_dir / "first")
    service, manager = make_service(tmp_path, [config])
    write_bytes(Path(service.settings.cache_dir) / "compiled.blob", 11)
    manager.engines["first"] = object()

    with pytest.raises(StorageConflict, match="Unload all models"):
        asyncio.run(service.cleanup(StorageCleanupRequest(action="clear_compiled_cache")))
    assert (Path(service.settings.cache_dir) / "compiled.blob").is_file()


def test_incomplete_cleanup_preserves_transaction_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_dir = tmp_path / "models"
    config = FakeConfig("first", "First", models_dir / "first")
    write_bytes(config.abs_path(Path()) / "partial.bin", 13)
    staging, backup = model_output_transaction_paths(config.abs_path(Path()))
    write_bytes(staging / "partial.bin", 17)
    write_bytes(backup / "working.bin", 19)

    service, manager = make_service(tmp_path, [config])
    record = models_dir / ".inferbridge-recovery" / "first.json"
    write_bytes(record, 5)
    manager._model_recovery_records["first"] = {"model_id": "first"}
    monkeypatch.setattr(
        storage_manager,
        "conversion_health",
        lambda _cfg: {"status": "incomplete", "label": "Incomplete", "details": ""},
    )

    def remove_incomplete(_manager, cfg) -> bool:
        shutil.rmtree(cfg.abs_path(Path()), ignore_errors=False)
        shutil.rmtree(staging, ignore_errors=False)
        return True

    monkeypatch.setattr(
        storage_manager.model_recovery,
        "_remove_incomplete_output",
        remove_incomplete,
    )

    result = asyncio.run(
        service.cleanup(StorageCleanupRequest(action="remove_incomplete_data", model_id="first"))
    )

    assert result["freed_bytes"] == 35
    assert not config.abs_path(Path()).exists()
    assert not staging.exists()
    assert not record.exists()
    assert backup.is_dir()
    assert (backup / "working.bin").is_file()
    assert "first" not in manager._model_recovery_records


def test_measurement_rejects_reparse_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "cache"
    target.mkdir()
    monkeypatch.setattr(storage_safety, "is_reparse_point", lambda path: path == target)

    measured = storage_manager._measure_tree(target, root=target)

    assert measured.present is True
    assert measured.unsafe is True
    assert measured.size_bytes == 0


def test_cleanup_scope_blocks_new_lifecycle_work(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    config = FakeConfig("first", "First", models_dir / "first")
    service, manager = make_service(tmp_path, [config])

    with service._cleanup_scope(model_ids=("first",)):
        with pytest.raises(ValueError, match="Storage cleanup is active"):
            manager.schedule_load("first")
        with pytest.raises(ValueError, match="Storage cleanup is active"):
            manager.schedule_convert("first")
        with pytest.raises(ValueError, match="Storage cleanup is active"):
            manager.delete("first")

    manager.schedule_load("first")
    manager.schedule_convert("first")
    assert manager.load_calls == ["first"]
    assert manager.convert_calls == ["first"]


def test_storage_delete_uses_guarded_upstream_delete(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    config = FakeConfig("first", "First", models_dir / "first")
    write_bytes(config.abs_path(Path()) / "model.bin", 12)
    service, _manager = make_service(tmp_path, [config])

    result = asyncio.run(
        service.cleanup(
            StorageCleanupRequest(action="delete_converted_model", model_id="first")
        )
    )

    assert result["freed_bytes"] == 12
    assert not config.abs_path(Path()).exists()


def test_storage_routes_require_local_ui_for_cleanup(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    config = FakeConfig("first", "First", models_dir / "first")
    service, _manager = make_service(tmp_path, [config])
    app = FastAPI()
    app.state.settings = service.settings
    register_storage_manager_routes(app, service=service)
    client = TestClient(app)

    inventory = client.get("/v1/storage")
    assert inventory.status_code == 200
    assert inventory.headers["cache-control"] == "no-store"

    blocked = client.post(
        "/v1/storage/cleanup",
        json={"action": "clear_compiled_cache"},
    )
    assert blocked.status_code == 403

    allowed = client.post(
        "/v1/storage/cleanup",
        headers={"X-OV-LLM-UI": "1"},
        json={"action": "clear_compiled_cache"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["action"] == "clear_compiled_cache"


def test_unsafe_model_path_does_not_run_conversion_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_dir = tmp_path / "models"
    outside = tmp_path / "outside" / "first"
    write_bytes(outside / "model.bin", 4)
    config = FakeConfig("first", "First", outside)
    service, _manager = make_service(tmp_path, [config])

    def unexpected_health(_cfg):
        raise AssertionError("conversion health must not inspect an unsafe path")

    monkeypatch.setattr(storage_manager, "conversion_health", unexpected_health)
    snapshot = asyncio.run(service.snapshot())

    row = snapshot["models"][0]
    assert row["conversion_health"]["status"] == "unsafe_path"
    assert row["cleanup"]["available"] is False


def test_incomplete_cleanup_rejects_unsafe_model_before_health_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_dir = tmp_path / "models"
    outside = tmp_path / "outside" / "first"
    write_bytes(outside / "partial.bin", 4)
    config = FakeConfig("first", "First", outside)
    service, _manager = make_service(tmp_path, [config])

    def unexpected_health(_cfg):
        raise AssertionError("conversion health must not inspect an unsafe path")

    monkeypatch.setattr(storage_manager, "conversion_health", unexpected_health)
    with pytest.raises(StorageConflict, match="unsafe managed path"):
        asyncio.run(
            service.cleanup(
                StorageCleanupRequest(action="remove_incomplete_data", model_id="first")
            )
        )
    assert (outside / "partial.bin").is_file()
