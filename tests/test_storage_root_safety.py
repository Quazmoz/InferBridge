from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.storage_manager import StorageCleanupRequest, StorageConflict, StorageManagerService
from app.storage_root_safety import install_storage_root_safety


class FakeConfig:
    id = "first"
    name = "First"
    source_model = "org/shared"

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path

    def abs_path(self, _base: Path) -> Path:
        return self._model_path


class FakeManager:
    def __init__(self, config: FakeConfig, models_dir: Path) -> None:
        self.catalog = {config.id: config}
        self.engines: dict[str, object] = {}
        self.load_tasks: dict[str, object] = {}
        self.convert_tasks: dict[str, object] = {}
        self.status_overrides: dict[str, dict] = {}
        self._models_dir = models_dir.resolve()

    def record_request(self, *_args) -> None:
        return None

    def schedule_load(self, *_args, **_kwargs):
        return None

    def schedule_convert(self, *_args, **_kwargs):
        return None

    def delete(self, _model_id: str) -> dict:
        return {"deleted": False, "freed_bytes": 0}

    def _ensure_within_models_dir(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved != self._models_dir and self._models_dir not in resolved.parents:
            raise ValueError("outside models directory")


def make_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StorageManagerService:
    models_dir = tmp_path / "models"
    config_dir = tmp_path / "config"
    hf_cache = tmp_path / "cache" / "huggingface"
    models_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(hf_cache))
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(hf_cache / "hub"))
    config = FakeConfig(models_dir / "first")
    manager = FakeManager(config, models_dir)
    settings = SimpleNamespace(
        models_dir=models_dir,
        models_file=config_dir / "models.json",
        cache_dir=tmp_path / "cache" / "openvino",
        api_key=None,
    )
    paths = SimpleNamespace(
        config_dir=config_dir,
        huggingface_cache_dir=hf_cache,
    )
    install_storage_root_safety()
    return StorageManagerService(settings=settings, manager=manager, paths=paths)


def write_bytes(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * count)


def test_linked_source_cache_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path, monkeypatch)
    hub = Path(service.paths.huggingface_cache_dir) / "hub"
    target = hub / "actual-cache"
    write_bytes(target / "blob", 9)
    cache_link = hub / "models--org--shared"
    try:
        cache_link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable on this platform.")

    with pytest.raises(StorageConflict, match="symbolic link or Windows junction"):
        asyncio.run(
            service.cleanup(
                StorageCleanupRequest(action="remove_huggingface_cache", model_id="first")
            )
        )

    assert (target / "blob").is_file()


def test_linked_source_cache_root_is_reported_and_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path, monkeypatch)
    hf_cache = Path(service.paths.huggingface_cache_dir)
    actual_hub = tmp_path / "external-hub"
    write_bytes(actual_hub / "models--org--shared" / "blob", 9)
    hf_cache.mkdir(parents=True, exist_ok=True)
    hub_link = hf_cache / "hub"
    try:
        hub_link.symlink_to(actual_hub, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable on this platform.")

    rows, total, reclaimable = service._source_cache_rows(
        {
            "shared": {
                "source_model": "org/shared",
                "model_ids": ["first"],
                "model_names": ["First"],
                "path": hub_link / "models--org--shared",
                "preparing": False,
            }
        },
        hub_root=actual_hub,
    )

    assert total == 0
    assert reclaimable == 0
    assert rows[0]["state"] == "unsafe_path"
    assert rows[0]["cleanup"]["available"] is False
    with pytest.raises(StorageConflict, match="symbolic link or Windows junction"):
        asyncio.run(
            service.cleanup(
                StorageCleanupRequest(action="remove_huggingface_cache", model_id="first")
            )
        )
    assert (actual_hub / "models--org--shared" / "blob").is_file()
