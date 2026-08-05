"""Transactional publication for converted OpenVINO model directories.

Optimum writes many files over a long-running export. Writing those files directly into
the live model directory lets cancellation, disk exhaustion, or a failed requantization
mix partial output with a previously runnable model. This module keeps conversion output
in a sibling staging directory, validates it, and only then publishes it with a bounded
backup/rollback window.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from runtime.model_artifacts import validate_openvino_model_dir

logger = logging.getLogger("ov-llm.convert.transaction")

_STAGING_SUFFIX = ".inferbridge-staging"
_BACKUP_SUFFIX = ".inferbridge-backup"
_LOCK_SUFFIX = ".inferbridge.lock"
_LOCK_CONFLICT_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}


def _transaction_path(final_dir: Path, suffix: str) -> Path:
    return final_dir.parent / f".{final_dir.name}{suffix}"


def model_output_transaction_paths(final_dir: str | Path) -> tuple[Path, Path]:
    """Return the staging and backup paths owned by one model output transaction."""

    final = Path(final_dir)
    return (
        _transaction_path(final, _STAGING_SUFFIX),
        _transaction_path(final, _BACKUP_SUFFIX),
    )


def _lock_conflict() -> RuntimeError:
    return RuntimeError(
        "Another InferBridge process is already preparing this model. Wait for it to "
        "finish, or close the other InferBridge instance before retrying."
    )


def _acquire_file_lock(lock_file: BinaryIO) -> str:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)

    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in _LOCK_CONFLICT_ERRNOS:
                raise _lock_conflict() from exc
            raise
        return "windows"

    import fcntl

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _LOCK_CONFLICT_ERRNOS:
            raise _lock_conflict() from exc
        raise
    return "posix"


def _release_file_lock(lock_file: BinaryIO, lock_kind: str) -> None:
    lock_file.seek(0)
    if lock_kind == "windows":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _model_output_lock(final_dir: Path) -> Iterator[None]:
    lock_path = _transaction_path(final_dir, _LOCK_SUFFIX)
    try:
        lock_file = lock_path.open("a+b")
    except OSError as exc:
        raise RuntimeError(
            "InferBridge could not create the model preparation lock. Check write "
            "permissions for the configured model directory, then retry."
        ) from exc

    lock_kind: str | None = None
    try:
        lock_kind = _acquire_file_lock(lock_file)
        yield
    finally:
        if lock_kind is not None:
            with contextlib.suppress(OSError):
                _release_file_lock(lock_file, lock_kind)
        lock_file.close()


def _clear_readonly_and_retry(function, path: str, _exc_info) -> None:
    try:
        mode = os.stat(path, follow_symlinks=False).st_mode
    except (OSError, NotImplementedError, TypeError):
        mode = stat.S_IWRITE
    try:
        os.chmod(path, mode | stat.S_IWUSR | stat.S_IWRITE, follow_symlinks=False)
    except (OSError, NotImplementedError, TypeError):
        os.chmod(path, mode | stat.S_IWUSR | stat.S_IWRITE)
    function(path)


def _remove_path(path: Path) -> None:
    """Remove one transaction-owned path without following links."""

    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)
    else:
        path.unlink(missing_ok=True)


def _recover_stale_backup(final_dir: Path, backup_dir: Path) -> None:
    """Resolve a prior process interruption during the short publication window."""

    if not backup_dir.exists() and not backup_dir.is_symlink():
        return
    if backup_dir.is_symlink():
        backup_dir.unlink(missing_ok=True)
        return

    final_validation = validate_openvino_model_dir(final_dir)
    if final_validation.ready:
        _remove_path(backup_dir)
        return

    backup_validation = validate_openvino_model_dir(backup_dir)
    if backup_validation.ready:
        _remove_path(final_dir)
        os.replace(backup_dir, final_dir)
        logger.warning("Recovered the previous model artifact after an interrupted publication.")
        return

    _remove_path(backup_dir)


def _publish_staged_output(staging_dir: Path, final_dir: Path, backup_dir: Path) -> None:
    validation = validate_openvino_model_dir(staging_dir)
    if not validation.ready:
        raise RuntimeError(
            "Optimum exited successfully, but the staged OpenVINO model is incomplete: "
            f"{validation.reason}. The previous model files were preserved."
        )

    previous_moved = False
    if final_dir.exists() or final_dir.is_symlink():
        if final_dir.is_symlink():
            raise RuntimeError("Refusing to replace a model directory through a symbolic link.")
        os.replace(final_dir, backup_dir)
        previous_moved = True

    try:
        os.replace(staging_dir, final_dir)
    except BaseException:
        with contextlib.suppress(Exception):
            _remove_path(final_dir)
        if previous_moved and backup_dir.exists():
            with contextlib.suppress(Exception):
                os.replace(backup_dir, final_dir)
        raise

    if previous_moved:
        try:
            _remove_path(backup_dir)
        except OSError as exc:
            # The new model is already durable and valid. A transient Windows lock on
            # the backup should not turn a successful conversion into a false failure;
            # the next transaction will remove or recover this backup deterministically.
            logger.warning("Could not remove the previous model backup yet: %s", exc)


@contextmanager
def staged_model_output(final_dir: str | Path) -> Iterator[Path]:
    """Yield a clean staging path and publish it only after validation.

    The live model remains untouched while Optimum downloads and exports. Any exception
    removes only the staging path. A successful context validates the staged IR, moves
    the old model aside, publishes the new one, and then removes the backup. The staging
    directory is deliberately absent at entry so Optimum can create it using its normal
    first-conversion path.
    """

    final = Path(final_dir)
    if final.is_symlink():
        raise RuntimeError("Refusing to convert into a model directory symbolic link.")

    final.parent.mkdir(parents=True, exist_ok=True)
    staging, backup = model_output_transaction_paths(final)

    with _model_output_lock(final):
        _recover_stale_backup(final, backup)
        _remove_path(staging)

        try:
            yield staging
            _publish_staged_output(staging, final, backup)
        except BaseException:
            try:
                _remove_path(staging)
            except OSError as cleanup_error:
                logger.warning("Could not remove incomplete staged model output: %s", cleanup_error)
            raise


__all__ = ["model_output_transaction_paths", "staged_model_output"]
