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
from pathlib import Path
from typing import Any, Callable

_INSTALL_FLAG = "_inferbridge_resilient_cleanup_installed"
_RETRY_DELAYS_SECONDS = (0.0, 0.1, 0.25, 0.5)


def _make_writable(path: str | os.PathLike[str]) -> None:
    """Best-effort removal of a Windows read-only attribute."""

    with contextlib.suppress(OSError, NotImplementedError):
        mode = os.stat(path, follow_symlinks=False).st_mode
        os.chmod(path, mode | stat.S_IWUSR, follow_symlinks=False)


def _clear_readonly_and_retry(
    remover: Callable[[str], Any],
    path: str,
    _exc_info: tuple[type[BaseException], BaseException, Any],
) -> None:
    """Retry one failed rmtree operation after making its target writable."""

    _make_writable(path)
    remover(path)


def _remove_tree(path: Path, *, description: str) -> bool:
    """Remove one verified tree with bounded retries for transient Windows locks."""

    if not path.exists():
        return False

    last_error: OSError | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, onerror=_clear_readonly_and_retry)
        except FileNotFoundError:
            return True
        except OSError as exc:
            last_error = exc
            continue
        if not path.exists():
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


def _remove_incomplete_output(manager: Any, cfg: Any) -> bool:
    from app import model_recovery

    model_dir = model_recovery._ensure_removable_model_path(manager, cfg)
    return _remove_tree(model_dir, description="the incomplete OpenVINO files")


def _remove_download_cache(cfg: Any) -> bool:
    from app import model_recovery

    cache_path = model_recovery._source_cache_path(cfg.source_model)
    if cache_path is None:
        raise model_recovery.RecoveryConflict(
            "cache_unavailable",
            "The source model cache cannot be safely identified for a fresh download.",
        )
    if cache_path.is_symlink():
        raise model_recovery.RecoveryConflict(
            "unsafe_cache_path",
            "Refusing to remove a Hugging Face cache through a symbolic link.",
        )
    return _remove_tree(cache_path, description="the cached Hugging Face source files")


def install_model_recovery_cleanup() -> None:
    """Replace recovery's raw tree deletion helpers with resilient equivalents."""

    from app import model_recovery

    if getattr(model_recovery, _INSTALL_FLAG, False):
        return
    model_recovery._remove_incomplete_output = _remove_incomplete_output
    model_recovery._remove_download_cache = _remove_download_cache
    setattr(model_recovery, _INSTALL_FLAG, True)


__all__ = ["install_model_recovery_cleanup"]
