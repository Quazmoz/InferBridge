import os
import stat
from pathlib import Path

import pytest

from app import model_recovery, model_recovery_cleanup
from app.model_recovery import RecoveryConflict


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


def test_cleanup_extension_replaces_raw_recovery_deletion_helpers() -> None:
    model_recovery_cleanup.install_model_recovery_cleanup()

    assert model_recovery._remove_incomplete_output is model_recovery_cleanup._remove_incomplete_output
    assert model_recovery._remove_download_cache is model_recovery_cleanup._remove_download_cache
