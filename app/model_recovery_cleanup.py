"""Resilient filesystem cleanup for model preparation recovery.

Hugging Face caches and interrupted OpenVINO output can contain read-only files or be
briefly held by Windows Search, antivirus, or a recently exited converter process. The
recovery workflow should retry those transient failures and return a bounded actionable
conflict instead of leaking a raw local path or an unhandled filesystem exception.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_INSTALL_FLAG = "_inferbridge_resilient_cleanup_installed"
_RETRY_DELAYS_SECONDS = (0.0, 0.15, 0.5, 1.0, 2.0)


def _path_exists(path: Path) -> bool:
    """Return true for ordinary paths and dangling links without following them."""

    return os.path.lexists(path)


def _is_reparse_point(path: Path) -> bool:
    """Detect symbolic links and Windows junctions without following them."""

    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(reparse_flag and attributes & reparse_flag)


def _path_mode(path: str | os.PathLike[str]) -> int | None:
    """Read a path mode across supported Windows/Python combinations."""

    try:
        return os.stat(path, follow_symlinks=False).st_mode
    except (NotImplementedError, TypeError):
        try:
            return os.stat(path).st_mode
        except OSError:
            return None
    except OSError:
        return None


def _make_writable(path: str | os.PathLike[str]) -> None:
    """Best-effort removal of a Windows read-only attribute."""

    mode = _path_mode(path)
    if mode is None:
        return
    writable_mode = mode | stat.S_IWUSR | stat.S_IWRITE
    try:
        os.chmod(path, writable_mode, follow_symlinks=False)
        return
    except (NotImplementedError, TypeError):
        # Some supported Windows/Python combinations do not implement the
        # follow_symlinks keyword for chmod. The recovery paths are independently
        # checked for links before deletion, so the ordinary fallback remains safe.
        pass
    except OSError:
        # A second attempt without follow_symlinks handles Windows implementations
        # that reject the keyword with a platform-specific OSError.
        pass
    with contextlib.suppress(OSError):
        os.chmod(path, writable_mode)


def _clear_readonly_and_retry(
    remover: Callable[[str], Any],
    path: str,
    _exc_info: tuple[type[BaseException], BaseException, Any],
) -> None:
    """Retry one failed rmtree operation after making its target writable."""

    _make_writable(path)
    remover(path)


def _unsafe_path(message: str):
    from app.model_recovery import RecoveryConflict

    return RecoveryConflict("unsafe_output_path", message)


def _remove_tree(path: Path, *, description: str) -> bool:
    """Remove one verified tree with bounded retries for transient Windows locks."""

    if not _path_exists(path):
        return False
    if _is_reparse_point(path):
        raise _unsafe_path(
            f"Refusing to remove {description} through a symbolic link or junction."
        )
    if not path.is_dir():
        raise _unsafe_path(f"Refusing to remove an unexpected path for {description}.")

    last_error: OSError | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        if not _path_exists(path):
            return True
        try:
            shutil.rmtree(path, onerror=_clear_readonly_and_retry)
        except FileNotFoundError:
            return True
        except OSError as exc:
            last_error = exc
            continue
        if not _path_exists(path):
            return True
        last_error = OSError("The directory still exists after cleanup.")

    from app.model_recovery import RecoveryConflict

    raise RecoveryConflict(
        "cleanup_failed",
        (
            f"InferBridge could not remove {description}. Close File Explorer windows, "
            "antivirus scans, or other programs using the model files, then retry."
        ),
    ) from last_error


def _output_state_with_staging(cfg: Any) -> str:
    """Include transaction staging in recovery's incomplete-output classification."""

    from app import model_recovery, model_registry
    from runtime.model_output_transaction import model_output_transaction_paths

    model_dir = cfg.abs_path(model_recovery._base_dir())
    if model_registry.is_openvino_model_dir(model_dir):
        return "complete"
    staging_dir, _backup_dir = model_output_transaction_paths(model_dir)
    if model_dir.exists() or _path_exists(staging_dir):
        return "incomplete"
    return "missing"


def _remove_incomplete_output(manager: Any, cfg: Any) -> bool:
    from app import model_recovery
    from runtime.model_output_transaction import model_output_transaction_paths

    # Check reparse points before exists(). A dangling link reports exists() == False
    # and a Windows junction reports is_symlink() == False, but neither may be followed.
    model_dir = cfg.abs_path(model_recovery._base_dir())
    if _is_reparse_point(model_dir):
        raise model_recovery.RecoveryConflict(
            "unsafe_output_path",
            "Refusing to remove an incomplete model through a symbolic link or junction.",
        )
    model_dir = model_recovery._ensure_removable_model_path(manager, cfg)
    staging_dir, _backup_dir = model_output_transaction_paths(model_dir)

    ensure_within = getattr(manager, "_ensure_within_models_dir", None)
    if callable(ensure_within):
        try:
            ensure_within(model_dir)
            ensure_within(staging_dir)
        except ValueError as exc:
            raise model_recovery.RecoveryConflict(
                "unsafe_output_path",
                "The incomplete output is outside the configured model directory.",
            ) from exc

    if _is_reparse_point(staging_dir) or (
        _path_exists(staging_dir) and not staging_dir.is_dir()
    ):
        raise model_recovery.RecoveryConflict(
            "unsafe_output_path",
            "Refusing to remove an unexpected model staging link, junction, or file.",
        )

    removed_staging = _remove_tree(
        staging_dir,
        description="the incomplete staged OpenVINO files",
    )
    removed_output = _remove_tree(
        model_dir,
        description="the incomplete OpenVINO files",
    )
    return removed_staging or removed_output


def _remove_download_cache(cfg: Any) -> bool:
    from app import model_recovery

    cache_path = model_recovery._source_cache_path(cfg.source_model)
    if cache_path is None:
        raise model_recovery.RecoveryConflict(
            "cache_unavailable",
            "The source model cache cannot be safely identified for a fresh download.",
        )
    if _is_reparse_point(cache_path):
        raise model_recovery.RecoveryConflict(
            "unsafe_cache_path",
            "Refusing to remove a Hugging Face cache through a symbolic link or junction.",
        )
    return _remove_tree(cache_path, description="the cached Hugging Face source files")


def install_model_recovery_cleanup() -> None:
    """Replace recovery's raw deletion and output-state helpers with resilient versions."""

    from app import model_recovery

    if getattr(model_recovery, _INSTALL_FLAG, False):
        return
    model_recovery._output_state = _output_state_with_staging
    model_recovery._remove_incomplete_output = _remove_incomplete_output
    model_recovery._remove_download_cache = _remove_download_cache
    setattr(model_recovery, _INSTALL_FLAG, True)


__all__ = ["install_model_recovery_cleanup"]
