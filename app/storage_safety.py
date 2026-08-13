"""Filesystem and lifecycle safety helpers for managed storage cleanup."""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.model_library_conversion import is_reparse_point

_RETRY_DELAYS_SECONDS = (0.0, 0.15, 0.5, 1.0, 2.0)
_ACTIVE_STATUSES = frozenset({"queued", "loading", "queued_convert", "converting"})


class StorageConflict(RuntimeError):
    """A storage action cannot be completed safely in the current state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TreeMeasurement:
    present: bool
    size_bytes: int = 0
    unsafe: bool = False
    unreadable: bool = False


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _lexically_within(path: Path, root: Path) -> bool:
    path_abs = Path(os.path.abspath(path))
    root_abs = Path(os.path.abspath(root))
    return path_abs == root_abs or root_abs in path_abs.parents


def _measure_tree(path: Path, *, root: Path, allow_file: bool = False) -> TreeMeasurement:
    """Measure a managed path without following symbolic links or junctions."""

    if _path_exists(root) and is_reparse_point(root):
        return TreeMeasurement(present=_path_exists(path), unsafe=True)
    if not _lexically_within(path, root):
        return TreeMeasurement(present=_path_exists(path), unsafe=True)
    if not _path_exists(path):
        return TreeMeasurement(present=False)
    if is_reparse_point(path):
        return TreeMeasurement(present=True, unsafe=True)
    if path.is_file():
        if not allow_file:
            return TreeMeasurement(present=True, unsafe=True)
        try:
            return TreeMeasurement(present=True, size_bytes=path.stat().st_size)
        except OSError:
            return TreeMeasurement(present=True, unreadable=True)
    if not path.is_dir():
        return TreeMeasurement(present=True, unsafe=True)

    total = 0
    try:
        for current, directories, files in os.walk(path, topdown=True, followlinks=False):
            current_path = Path(current)
            if is_reparse_point(current_path):
                return TreeMeasurement(present=True, unsafe=True)
            for name in directories:
                if is_reparse_point(current_path / name):
                    return TreeMeasurement(present=True, unsafe=True)
            for name in files:
                candidate = current_path / name
                if is_reparse_point(candidate):
                    return TreeMeasurement(present=True, unsafe=True)
                total += candidate.stat().st_size
    except OSError:
        return TreeMeasurement(present=True, size_bytes=total, unreadable=True)
    return TreeMeasurement(present=True, size_bytes=total)


def _make_writable(path: str | os.PathLike[str]) -> None:
    try:
        mode = os.stat(path, follow_symlinks=False).st_mode
    except (OSError, NotImplementedError, TypeError):
        mode = stat.S_IWRITE
    writable = mode | stat.S_IWUSR | stat.S_IWRITE
    try:
        os.chmod(path, writable, follow_symlinks=False)
    except (OSError, NotImplementedError, TypeError):
        with contextlib.suppress(OSError):
            os.chmod(path, writable)


def _clear_readonly_and_retry(remover, path: str, _exc_info) -> None:
    _make_writable(path)
    remover(path)


def _remove_tree(
    path: Path,
    *,
    root: Path,
    description: str,
    allow_file: bool = False,
) -> int:
    """Remove one verified path with bounded Windows lock retries."""

    measurement = _measure_tree(path, root=root, allow_file=allow_file)
    if not measurement.present:
        return 0
    if measurement.unsafe:
        raise StorageConflict(
            "unsafe_path",
            f"Refusing to remove {description} through a symbolic link or Windows junction.",
        )
    if measurement.unreadable:
        raise StorageConflict(
            "storage_unreadable",
            (
                f"InferBridge could not inspect {description} safely. "
                "Close programs using it and retry."
            ),
        )

    last_error: OSError | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        if not _path_exists(path):
            return measurement.size_bytes
        try:
            if path.is_dir():
                shutil.rmtree(path, onerror=_clear_readonly_and_retry)
            else:
                _make_writable(path)
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            return measurement.size_bytes
        except OSError as exc:
            last_error = exc
            continue
        if not _path_exists(path):
            return measurement.size_bytes
        last_error = OSError("The managed storage path still exists after cleanup.")

    raise StorageConflict(
        "cleanup_failed",
        (
            f"InferBridge could not remove {description}. Close File Explorer windows, "
            "antivirus scans, or other programs using these files, then retry."
        ),
    ) from last_error


def _active_task(tasks: dict[str, Any], model_id: str) -> bool:
    task = tasks.get(model_id)
    return bool(task is not None and not task.done())


def _model_activity(manager: Any, model_id: str) -> dict[str, bool]:
    status = getattr(manager, "status_overrides", {}).get(model_id, {}).get("status")
    recovery_lock = getattr(manager, "_model_recovery_locks", {}).get(model_id)
    recovering = bool(recovery_lock is not None and recovery_lock.locked())
    preparing = (
        status in _ACTIVE_STATUSES
        or _active_task(getattr(manager, "load_tasks", {}), model_id)
        or _active_task(getattr(manager, "convert_tasks", {}), model_id)
        or recovering
    )
    return {
        "loaded": model_id in getattr(manager, "engines", {}),
        "preparing": preparing,
    }


def _all_lifecycle_idle(manager: Any) -> bool:
    if getattr(manager, "engines", {}):
        return False
    for tasks in (getattr(manager, "load_tasks", {}), getattr(manager, "convert_tasks", {})):
        if any(task is not None and not task.done() for task in tasks.values()):
            return False
    if any(
        lock is not None and lock.locked()
        for lock in getattr(manager, "_model_recovery_locks", {}).values()
    ):
        return False
    return not any(
        value.get("status") in _ACTIVE_STATUSES
        for value in getattr(manager, "status_overrides", {}).values()
        if isinstance(value, dict)
    )


def cleanup_capability(
    *,
    reclaimable_bytes: int,
    unsafe: bool = False,
    unreadable: bool = False,
    loaded: bool = False,
    preparing: bool = False,
    require_all_idle: bool = False,
    all_idle: bool = True,
    protected: bool = False,
) -> dict[str, Any]:
    available = reclaimable_bytes > 0
    reason = ""
    if protected:
        available = False
        reason = "Retained for automatic transaction recovery."
    elif unsafe:
        available = False
        reason = "A symbolic link or Windows junction was detected. Remove it manually."
    elif unreadable:
        available = False
        reason = "Some files could not be inspected safely."
    elif preparing:
        available = False
        reason = "Wait for model preparation to finish."
    elif loaded:
        available = False
        reason = "Unload the model before removing its managed files."
    elif require_all_idle and not all_idle:
        available = False
        reason = "Unload all models and wait for active operations to finish."
    elif reclaimable_bytes <= 0:
        reason = "No reclaimable files were found."
    return {
        "available": available,
        "reclaimable_bytes": reclaimable_bytes,
        "reason": reason,
    }


__all__ = [
    "StorageConflict",
    "TreeMeasurement",
    "_all_lifecycle_idle",
    "_measure_tree",
    "_model_activity",
    "_path_exists",
    "_remove_tree",
    "cleanup_capability",
]
