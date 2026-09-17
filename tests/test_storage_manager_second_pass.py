from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import storage_safety
from app.storage_manager import StorageCleanupRequest, StorageManagerService
from app.storage_root_safety import install_storage_root_safety
from app.storage_safety import StorageConflict, _all_lifecycle_idle, _measure_tree


class FakeConfig:
    id = "first"
    name = "First"
    source_model = "org/shared"

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path

    def abs_path(self, _base: Path) -> Path:
        return self._model_path


class FakeTemporaryEngine:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self, config: FakeConfig, models_dir: Path) -> None:
        self.catalog = {config.id: config}
        self.engines: dict[str, object] = {}
        self.load_tasks: dict[str, object] = {}
        self.convert_tasks: dict[str, object] = {}
        self.status_overrides: dict[str, dict] = {}
        self._model_recovery_locks: dict[str, asyncio.Lock] = {}
        self._models_dir = models_dir.resolve()
        self.request_calls: list[str] = []
        self.temporary_started: asyncio.Event | None = None
        self.temporary_release: asyncio.Event | None = None

    def record_request(self, model_id: str, *_args) -> None:
        self.request_calls.append(model_id)

    def schedule_load(self, *_args, **_kwargs):
        return None

    def schedule_convert(self, *_args, **_kwargs):
        return None

    def delete(self, _model_id: str) -> dict:
        return {"deleted": False, "freed_bytes": 0}

    async def build_temporary_engine(self, _model_id: str, _device: str):
        assert self.temporary_started is not None
        assert self.temporary_release is not None
        self.temporary_started.set()
        await self.temporary_release.wait()
        return FakeTemporaryEngine(), 0.01

    def _ensure_within_models_dir(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved != self._models_dir and self._models_dir not in resolved.parents:
            raise ValueError("outside models directory")


def make_service(tmp_path: Path) -> tuple[StorageManagerService, FakeManager]:
    models_dir = tmp_path / "models"
    config_dir = tmp_path / "config"
    compiled_dir = tmp_path / "cache" / "openvino"
    hf_cache = tmp_path / "cache" / "huggingface"
    models_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    compiled_dir.mkdir(parents=True, exist_ok=True)
    config = FakeConfig(models_dir / "first")
    manager = FakeManager(config, models_dir)
    settings = SimpleNamespace(
        models_dir=models_dir,
        models_file=config_dir / "models.json",
        cache_dir=compiled_dir,
        api_key=None,
    )
    paths = SimpleNamespace(
        config_dir=config_dir,
        huggingface_cache_dir=hf_cache,
    )
    install_storage_root_safety()
    return StorageManagerService(settings=settings, manager=manager, paths=paths), manager


def test_measurement_rejects_reparse_point_root_before_child_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "blob").write_bytes(b"data")
    monkeypatch.setattr(storage_safety, "is_reparse_point", lambda path: path == root)

    measured = _measure_tree(child, root=root)

    assert measured.present is True
    assert measured.unsafe is True
    assert measured.size_bytes == 0


def test_huggingface_hub_environment_override_is_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_hub = tmp_path / "custom-hub"
    explicit_hub.mkdir(parents=True)
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(explicit_hub))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "ignored-home"))
    service, _manager = make_service(tmp_path)

    assert service._source_cache_path("org/shared") == explicit_hub / "models--org--shared"


def test_duplicate_service_reuses_one_runtime_state(tmp_path: Path) -> None:
    first, manager = make_service(tmp_path)
    second = StorageManagerService(
        settings=first.settings,
        manager=manager,
        paths=first.paths,
    )

    assert first._state is second._state
    manager.record_request("first", 1, 1, 0.1)
    assert manager.request_calls == ["first"]
    assert second._state.delete_model("first") == {"deleted": False, "freed_bytes": 0}


def test_temporary_engine_blocks_cleanup_until_caller_closes_it(tmp_path: Path) -> None:
    service, manager = make_service(tmp_path)

    async def scenario() -> None:
        manager.temporary_started = asyncio.Event()
        manager.temporary_release = asyncio.Event()
        task = asyncio.create_task(manager.build_temporary_engine("first", "CPU"))
        await manager.temporary_started.wait()

        with pytest.raises(StorageConflict, match="temporary model operation"):
            await service.cleanup(
                StorageCleanupRequest(
                    action="delete_converted_model",
                    model_id="first",
                )
            )
        with pytest.raises(StorageConflict, match="temporary model operation"):
            await service.cleanup(StorageCleanupRequest(action="clear_compiled_cache"))

        manager.temporary_release.set()
        engine, _load_time = await task
        assert engine.closed is False

        # Construction is complete, but the temporary native engine can still be
        # generating. The storage lease must remain active until caller-owned close().
        with pytest.raises(StorageConflict, match="temporary model operation"):
            await service.cleanup(
                StorageCleanupRequest(
                    action="delete_converted_model",
                    model_id="first",
                )
            )
        with pytest.raises(StorageConflict, match="temporary model operation"):
            await service.cleanup(StorageCleanupRequest(action="clear_compiled_cache"))

        engine.close()
        assert engine.closed is True
        assert service._state._temporary_models == {}

        # close() is idempotent with respect to the guard release.
        engine.close()
        assert service._state._temporary_models == {}
        with service._state.cleanup_scope(model_ids=("first",)):
            pass
        with service._state.cleanup_scope(global_cleanup=True):
            pass

    asyncio.run(scenario())


def test_recovery_lock_counts_as_active_lifecycle(tmp_path: Path) -> None:
    _service, manager = make_service(tmp_path)

    async def scenario() -> None:
        lock = asyncio.Lock()
        manager._model_recovery_locks["first"] = lock
        await lock.acquire()
        try:
            assert _all_lifecycle_idle(manager) is False
        finally:
            lock.release()

    asyncio.run(scenario())
