import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from app import model_recovery, model_recovery_cleanup
from app.config import Settings
from app.model_manager import ModelManager
from app.model_recovery import RecoveryConflict

MODEL_ID = "cleanup-model"


def _manager(tmp_path: Path) -> ModelManager:
    models_dir = tmp_path / "models" / "openvino"
    catalog_file = tmp_path / "models.json"
    catalog_file.write_text(
        json.dumps(
            {
                MODEL_ID: {
                    "name": "Cleanup Model",
                    "model_path": str(models_dir / MODEL_ID),
                    "source_model": "org/cleanup-model",
                    "weight_format": "fp16",
                    "recommended_device": "CPU",
                }
            }
        ),
        encoding="utf-8",
    )
    return ModelManager(
        Settings(
            models_file=catalog_file,
            models_dir=models_dir,
            cache_dir=tmp_path / "compiled-cache",
            benchmark_results_file=tmp_path / "benchmarks.json",
            force_mock=True,
            device="CPU",
        )
    )


def _interrupted_recovery(manager: ModelManager) -> dict:
    model_dir = Path(manager.catalog[MODEL_ID].model_path)
    model_dir.mkdir(parents=True)
    (model_dir / "partial.bin").write_bytes(b"partial")
    manager._set_status(MODEL_ID, "converting")
    manager._set_progress(MODEL_ID, "converting", "Converting model", percent=25)
    manager._set_status(MODEL_ID, "error", error="conversion failed")
    manager._set_progress(MODEL_ID, "error", "Conversion failed")
    recovery = manager.model_recovery(MODEL_ID)
    assert recovery is not None
    return recovery


def test_remove_tree_retries_a_transient_windows_lock(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "models--org--model"
    target.mkdir()
    (target / "weights.bin").write_bytes(b"weights")
    real_rmtree = model_recovery_cleanup.shutil.rmtree
    calls = 0

    def flaky_rmtree(path, *, onerror):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("file is temporarily busy")
        return real_rmtree(path, onerror=onerror)

    monkeypatch.setattr(model_recovery_cleanup.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(model_recovery_cleanup.time, "sleep", lambda _delay: None)

    assert model_recovery_cleanup._remove_tree(
        target,
        description="the cached Hugging Face source files",
    ) is True
    assert calls == 2
    assert not target.exists()


def test_readonly_callback_makes_a_file_writable_before_retry(tmp_path: Path) -> None:
    target = tmp_path / "readonly.bin"
    target.write_bytes(b"data")
    target.chmod(stat.S_IREAD)

    def remove(path: str) -> None:
        assert os.stat(path).st_mode & stat.S_IWUSR
        Path(path).unlink()

    error = PermissionError("read only")
    model_recovery_cleanup._clear_readonly_and_retry(
        remove,
        str(target),
        (PermissionError, error, None),
    )

    assert not target.exists()


def test_cleanup_failure_is_actionable_and_does_not_expose_the_local_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "private" / "models--org--model"
    target.mkdir(parents=True)

    def locked_rmtree(_path, *, onerror):
        del onerror
        raise PermissionError("still locked")

    monkeypatch.setattr(model_recovery_cleanup.shutil, "rmtree", locked_rmtree)
    monkeypatch.setattr(model_recovery_cleanup.time, "sleep", lambda _delay: None)

    with pytest.raises(RecoveryConflict) as captured:
        model_recovery_cleanup._remove_tree(
            target,
            description="the cached Hugging Face source files",
        )

    assert captured.value.code == "cleanup_failed"
    assert str(target) not in str(captured.value)
    assert "antivirus" in str(captured.value).lower()
    assert target.exists()


def test_restart_download_keeps_recovery_when_cleanup_remains_locked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    recovery = _interrupted_recovery(manager)
    scheduled = False

    def locked_rmtree(_path, *, onerror):
        del onerror
        raise PermissionError("still locked")

    def schedule_convert(*_args, **_kwargs):
        nonlocal scheduled
        scheduled = True
        return object()

    monkeypatch.setattr(model_recovery_cleanup.shutil, "rmtree", locked_rmtree)
    monkeypatch.setattr(model_recovery_cleanup.time, "sleep", lambda _delay: None)
    manager.schedule_convert = schedule_convert

    with pytest.raises(RecoveryConflict) as captured:
        asyncio.run(
            manager.recover_model(
                MODEL_ID,
                recovery["recovery_id"],
                "restart_download",
                device="CPU",
            )
        )

    assert captured.value.code == "cleanup_failed"
    assert scheduled is False
    current = manager.model_recovery(MODEL_ID)
    assert current is not None
    assert current["recovery_id"] == recovery["recovery_id"]


def test_cleanup_extension_replaces_raw_recovery_deletion_helpers() -> None:
    model_recovery_cleanup.install_model_recovery_cleanup()

    assert model_recovery._remove_incomplete_output is model_recovery_cleanup._remove_incomplete_output
    assert model_recovery._remove_download_cache is model_recovery_cleanup._remove_download_cache
