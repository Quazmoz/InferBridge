"""Recovery contracts and actions for interrupted model preparation.

Recovery is intentionally layered on top of the existing lifecycle scheduler. It never
runs a second converter implementation. Instead it records sanitized terminal state,
classifies reusable Hugging Face cache data and incomplete OpenVINO output, and maps
explicit user actions back to ``schedule_convert`` or ``schedule_load``.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import model_registry as registry
from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.local_request_security import (
    matches_any_secret,
    require_safe_browser_origin,
)

_MANAGER_INSTALL_FLAG = "_MODEL_RECOVERY_INSTALLED"
_ROUTE_INSTALL_FLAG = "_ovllm_model_recovery_routes_installed"
_RECORD_SCHEMA_VERSION = 1
_MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_SOURCE_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_TERMINAL_PHASES = frozenset({"cancelled", "error"})
_ACTIVE_STATUSES = frozenset({"queued", "loading", "queued_convert", "converting"})
RecoveryAction = Literal[
    "resume",
    "retry_failed_stage",
    "restart_download",
    "remove_incomplete_files",
]


class ModelRecoveryActionRequest(BaseModel):
    model: str = Field(min_length=1, max_length=128, pattern=_MODEL_ID_PATTERN)
    recovery_id: str = Field(min_length=1, max_length=128)
    action: RecoveryAction
    device: str | None = Field(default=None, min_length=1, max_length=160)


class RecoveryConflict(RuntimeError):
    """A recovery action cannot be applied to the current model state."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_recovery_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.current_recovery_id = current_recovery_id


def _active(task: asyncio.Task[Any] | None) -> bool:
    return bool(task is not None and not task.done())


def _record_directory(manager: Any) -> Path:
    return Path(manager.settings.models_dir) / ".inferbridge-recovery"


def _record_path(manager: Any, model_id: str) -> Path:
    return _record_directory(manager) / f"{model_id}.json"


def _load_records(manager: Any) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    directory = _record_directory(manager)
    if not directory.is_dir():
        return records
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        model_id = payload.get("model_id")
        if (
            isinstance(model_id, str)
            and re.fullmatch(_MODEL_ID_PATTERN, model_id)
            and model_id in manager.catalog
        ):
            records[model_id] = payload
    return records


def _write_record(manager: Any, model_id: str, record: dict[str, Any]) -> None:
    directory = _record_directory(manager)
    directory.mkdir(parents=True, exist_ok=True)
    path = _record_path(manager, model_id)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    manager._model_recovery_records[model_id] = record


def _clear_record(manager: Any, model_id: str) -> None:
    records = getattr(manager, "_model_recovery_records", None)
    if isinstance(records, dict):
        records.pop(model_id, None)
    settings = getattr(manager, "settings", None)
    if settings is None or not getattr(settings, "models_dir", None):
        return
    with contextlib.suppress(OSError):
        _record_path(manager, model_id).unlink()


def _hub_cache_root() -> Path:
    configured = (
        os.environ.get("HUGGINGFACE_HUB_CACHE")
        or (str(Path(os.environ["HF_HOME"]) / "hub") if os.environ.get("HF_HOME") else "")
        or str(Path.home() / ".cache" / "huggingface" / "hub")
    )
    return Path(configured).expanduser().resolve()


def _source_cache_path(source_model: str) -> Path | None:
    source = str(source_model or "").strip()
    if not _SOURCE_MODEL_PATTERN.fullmatch(source):
        return None
    root = _hub_cache_root()
    candidate = (root / f"models--{source.replace('/', '--')}").resolve()
    if root not in candidate.parents:
        return None
    return candidate


def _directory_has_entries(path: Path) -> bool:
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False


def _download_state(cfg: registry.ModelConfig) -> str:
    cache_path = _source_cache_path(cfg.source_model)
    if cache_path is None:
        return "unknown"
    if _directory_has_entries(cache_path / "snapshots") or _directory_has_entries(
        cache_path / "blobs"
    ):
        return "reusable"
    return "not_found"


def _base_dir() -> Path:
    from app.config import BASE_DIR

    return BASE_DIR


def _output_state(cfg: registry.ModelConfig) -> str:
    model_dir = cfg.abs_path(_base_dir())
    if registry.is_openvino_model_dir(model_dir):
        return "complete"
    return "incomplete" if model_dir.exists() else "missing"


def _failed_stage(previous_phase: str, operation_type: str | None) -> str:
    phase = previous_phase.lower()
    if phase == "loading":
        return "load"
    if phase in {"converting", "finalizing"}:
        return "conversion"
    if phase in {"resolving", "downloading", "queued"}:
        return "download"
    return "conversion" if operation_type == "convert" else "load"


def _last_completed_stage(
    previous_phase: str,
    *,
    download_state: str,
    output_state: str,
) -> str:
    if output_state == "complete":
        return "conversion"
    if previous_phase.lower() == "finalizing":
        return "conversion"
    if previous_phase.lower() in {"converting", "loading"} or download_state == "reusable":
        return "download"
    return "none"


def _recommended_action(
    *,
    failed_stage: str,
    download_state: str,
    output_state: str,
) -> RecoveryAction:
    if failed_stage == "load" and output_state == "complete":
        return "retry_failed_stage"
    if download_state == "reusable":
        return "resume"
    return "restart_download"


def _action_capabilities(
    cfg: registry.ModelConfig,
    *,
    failed_stage: str,
    output_state: str,
) -> dict[str, bool]:
    has_source = bool(cfg.source_model)
    cache_path = _source_cache_path(cfg.source_model)
    can_prepare = has_source and output_state != "complete"
    return {
        "resume": can_prepare or (failed_stage == "load" and output_state == "complete"),
        "retry_failed_stage": can_prepare
        or (failed_stage == "load" and output_state == "complete"),
        "restart_download": can_prepare and cache_path is not None,
        "remove_incomplete_files": output_state == "incomplete",
        "view_failure_details": True,
    }


def _summary_from_record(
    manager: Any,
    model_id: str,
    record: dict[str, Any],
    *,
    include_details: bool,
) -> dict[str, Any]:
    cfg = manager.catalog[model_id]
    download_state = _download_state(cfg)
    output_state = _output_state(cfg)
    failed_stage = str(record.get("failed_stage") or "conversion")
    last_completed = str(record.get("last_completed_stage") or "none")
    recommended = _recommended_action(
        failed_stage=failed_stage,
        download_state=download_state,
        output_state=output_state,
    )
    payload: dict[str, Any] = {
        "available": True,
        "schema_version": _RECORD_SCHEMA_VERSION,
        "recovery_id": str(record.get("recovery_id") or ""),
        "model_id": model_id,
        "model_name": cfg.name,
        "interrupted_at": int(record.get("interrupted_at") or 0),
        "terminal_state": str(record.get("terminal_state") or "error"),
        "downloaded_files": download_state,
        "conversion_output": output_state,
        "failed_stage": failed_stage,
        "last_completed_stage": last_completed,
        "recommended_action": recommended,
        "actions": _action_capabilities(
            cfg,
            failed_stage=failed_stage,
            output_state=output_state,
        ),
    }
    if include_details:
        payload["failure_details"] = {
            "message": str(record.get("message") or "Model preparation was interrupted."),
            "log_tail": [
                str(line) for line in record.get("log_tail", []) if isinstance(line, str) and line
            ][-10:],
        }
    return payload


def _inferred_record(manager: Any, model_id: str) -> dict[str, Any] | None:
    cfg = manager.catalog[model_id]
    if _output_state(cfg) != "incomplete":
        return None
    download_state = _download_state(cfg)
    return {
        "schema_version": _RECORD_SCHEMA_VERSION,
        "recovery_id": f"inferred-{uuid.uuid4().hex}",
        "model_id": model_id,
        "operation_id": None,
        "operation_type": "convert",
        "terminal_state": "error",
        "interrupted_at": int(time.time()),
        "failed_stage": "conversion",
        "last_completed_stage": "download" if download_state == "reusable" else "none",
        "message": "An incomplete OpenVINO conversion directory was found.",
        "log_tail": [],
    }


def _model_is_active(manager: Any, model_id: str) -> bool:
    status = getattr(manager, "status_overrides", {}).get(model_id, {}).get("status")
    return (
        status in _ACTIVE_STATUSES
        or _active(getattr(manager, "load_tasks", {}).get(model_id))
        or _active(getattr(manager, "convert_tasks", {}).get(model_id))
    )


def _ensure_removable_model_path(manager: Any, cfg: registry.ModelConfig) -> Path:
    model_dir = cfg.abs_path(_base_dir())
    if registry.is_openvino_model_dir(model_dir):
        raise RecoveryConflict(
            "complete_output",
            "The model directory contains a complete OpenVINO model and was not removed.",
        )
    if not model_dir.exists():
        return model_dir
    if model_dir.is_symlink():
        raise RecoveryConflict(
            "unsafe_output_path",
            "Refusing to remove an incomplete model through a symbolic link.",
        )
    ensure_within = getattr(manager, "_ensure_within_models_dir", None)
    if callable(ensure_within):
        try:
            ensure_within(model_dir)
        except ValueError as exc:
            raise RecoveryConflict(
                "unsafe_output_path",
                "The incomplete output is outside the configured model directory.",
            ) from exc
    return model_dir


def _remove_incomplete_output(manager: Any, cfg: registry.ModelConfig) -> bool:
    model_dir = _ensure_removable_model_path(manager, cfg)
    if not model_dir.exists():
        return False
    shutil.rmtree(model_dir)
    return True


def _remove_download_cache(cfg: registry.ModelConfig) -> bool:
    cache_path = _source_cache_path(cfg.source_model)
    if cache_path is None:
        raise RecoveryConflict(
            "cache_unavailable",
            "The source model cache cannot be safely identified for a fresh download.",
        )
    if not cache_path.exists():
        return False
    if cache_path.is_symlink():
        raise RecoveryConflict(
            "unsafe_cache_path",
            "Refusing to remove a Hugging Face cache through a symbolic link.",
        )
    shutil.rmtree(cache_path)
    return True


def install_model_recovery_manager_extension() -> None:
    """Add persisted recovery state and actions to the composed model manager."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _MANAGER_INSTALL_FLAG, False):
        return

    original_init = manager_class.__init__
    original_set_progress = manager_class._set_progress
    original_catalog_entry = manager_class.catalog_entry
    original_delete = manager_class.delete

    def init_with_model_recovery(self, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._model_recovery_records = _load_records(self)
        self._model_recovery_locks: dict[str, asyncio.Lock] = {}

    def set_progress_with_recovery(
        self,
        model_id: str,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        append_log: str | None = None,
    ) -> None:
        previous = dict(getattr(self, "progress", {}).get(model_id, {}))
        original_set_progress(
            self,
            model_id,
            phase,
            message,
            percent=percent,
            append_log=append_log,
        )

        if phase == "ready":
            _clear_record(self, model_id)
            return
        if phase not in _TERMINAL_PHASES or model_id not in self.catalog:
            return

        current = dict(self.progress.get(model_id, {}))
        operation_id = current.get("operation_id")
        operation_type = current.get("operation_type")
        existing = self._model_recovery_records.get(model_id, {})
        recovery_id = (
            existing.get("recovery_id")
            if operation_id and existing.get("operation_id") == operation_id
            else f"recovery-{uuid.uuid4().hex}"
        )
        cfg = self.catalog[model_id]
        download_state = _download_state(cfg)
        output_state = _output_state(cfg)
        previous_phase = str(previous.get("phase") or operation_type or "conversion")
        safe_message = self._sanitize_progress_line(current.get("message") or message)
        safe_log_tail = [
            self._sanitize_progress_line(line)
            for line in current.get("log_tail", [])
            if isinstance(line, str)
        ]
        record = {
            "schema_version": _RECORD_SCHEMA_VERSION,
            "recovery_id": recovery_id,
            "model_id": model_id,
            "operation_id": operation_id if isinstance(operation_id, str) else None,
            "operation_type": operation_type if operation_type in {"load", "convert"} else None,
            "terminal_state": phase,
            "interrupted_at": int(time.time()),
            "failed_stage": _failed_stage(previous_phase, operation_type),
            "last_completed_stage": _last_completed_stage(
                previous_phase,
                download_state=download_state,
                output_state=output_state,
            ),
            "message": safe_message or "Model preparation was interrupted.",
            "log_tail": [line for line in safe_log_tail if line][-10:],
        }
        try:
            _write_record(self, model_id, record)
        except OSError:
            manager_module.logger.exception("Could not persist recovery state for '%s'", model_id)
            self._model_recovery_records[model_id] = record

    def model_recovery(
        self,
        model_id: str,
        *,
        include_details: bool = False,
    ) -> dict[str, Any] | None:
        if model_id not in self.catalog or _model_is_active(self, model_id):
            return None
        record = self._model_recovery_records.get(model_id)
        if record is None:
            record = _inferred_record(self, model_id)
            if record is None:
                return None
            try:
                _write_record(self, model_id, record)
            except OSError:
                self._model_recovery_records[model_id] = record
        return _summary_from_record(
            self,
            model_id,
            record,
            include_details=include_details,
        )

    def catalog_entry_with_recovery(self, model_id: str) -> dict[str, Any]:
        entry = original_catalog_entry(self, model_id)
        recovery = model_recovery(self, model_id, include_details=False)
        if recovery is not None:
            entry["recovery"] = recovery
        return entry

    async def recover_model(
        self,
        model_id: str,
        recovery_id: str,
        action: RecoveryAction,
        *,
        device: str | None = None,
    ) -> dict[str, Any]:
        if model_id not in self.catalog:
            raise KeyError(model_id)
        lock = self._model_recovery_locks.setdefault(model_id, asyncio.Lock())
        async with lock:
            recovery = model_recovery(self, model_id, include_details=True)
            if recovery is None:
                raise RecoveryConflict(
                    "no_recovery",
                    f"Model '{model_id}' has no interrupted preparation to recover.",
                )
            current_recovery_id = str(recovery.get("recovery_id") or "")
            if current_recovery_id != recovery_id:
                raise RecoveryConflict(
                    "stale_recovery",
                    "The recovery state changed. Refresh model status before retrying.",
                    current_recovery_id=current_recovery_id,
                )
            if _model_is_active(self, model_id):
                raise RecoveryConflict(
                    "operation_active",
                    "Another model preparation operation is already active.",
                    current_recovery_id=current_recovery_id,
                )

            cfg = self.catalog[model_id]
            output_state = _output_state(cfg)
            failed_stage = str(recovery.get("failed_stage") or "conversion")
            action_allowed = recovery.get("actions", {}).get(action) is True
            if not action_allowed:
                raise RecoveryConflict(
                    "action_unavailable",
                    f"The '{action}' action is not valid for the current recovery state.",
                    current_recovery_id=current_recovery_id,
                )
            if model_id in self.engines and action in {
                "restart_download",
                "remove_incomplete_files",
            }:
                raise RecoveryConflict(
                    "model_loaded",
                    "Unload the model before removing or replacing model files.",
                    current_recovery_id=current_recovery_id,
                )

            if action == "remove_incomplete_files":
                removed = await asyncio.to_thread(_remove_incomplete_output, self, cfg)
                record = self._model_recovery_records.get(model_id)
                if record is not None:
                    record = {**record, "interrupted_at": int(time.time())}
                    with contextlib.suppress(OSError):
                        _write_record(self, model_id, record)
                return {
                    "status": "cleaned",
                    "action": action,
                    "removed_incomplete_output": removed,
                    "message": (
                        f"Removed incomplete conversion files for {cfg.name}."
                        if removed
                        else f"No incomplete conversion files remained for {cfg.name}."
                    ),
                    "recovery": model_recovery(self, model_id, include_details=False),
                }

            if action == "restart_download":
                await asyncio.to_thread(_remove_incomplete_output, self, cfg)
                await asyncio.to_thread(_remove_download_cache, cfg)
                task = self.schedule_convert(
                    model_id,
                    device,
                    load_after=True,
                )
                started_action = "restart_download"
            elif failed_stage == "load" and output_state == "complete":
                task = self.schedule_load(model_id, device)
                started_action = "retry_load"
            else:
                await asyncio.to_thread(_remove_incomplete_output, self, cfg)
                task = self.schedule_convert(
                    model_id,
                    device,
                    load_after=True,
                )
                started_action = (
                    "retry_conversion" if action == "retry_failed_stage" else "resume_conversion"
                )

            if task is None and model_id not in self.engines:
                raise RecoveryConflict(
                    "recovery_not_started",
                    "The requested recovery operation could not be started.",
                    current_recovery_id=current_recovery_id,
                )
            _clear_record(self, model_id)
            self.emit_event("info", f"Started recovery for {cfg.name}")
            return {
                "status": "started",
                "action": action,
                "started_action": started_action,
                "message": f"Recovery started for {cfg.name}.",
                "model": self.catalog_entry(model_id),
            }

    def delete_with_recovery_cleanup(self, model_id: str) -> dict:
        result = original_delete(self, model_id)
        _clear_record(self, model_id)
        return result

    manager_class.__init__ = init_with_model_recovery
    manager_class._set_progress = set_progress_with_recovery
    manager_class.model_recovery = model_recovery
    manager_class.catalog_entry = catalog_entry_with_recovery
    manager_class.recover_model = recover_model
    manager_class.delete = delete_with_recovery_cleanup
    setattr(manager_class, _MANAGER_INSTALL_FLAG, True)


async def _require_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    require_safe_browser_origin(request)
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Server settings are unavailable.")
    configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
    if not configured:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    supplied = authorization.removeprefix("Bearer ")
    if not matches_any_secret(supplied, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def register_model_recovery_routes(app: FastAPI) -> None:
    if getattr(app.state, "model_recovery_routes_registered", False):
        return

    router = APIRouter(
        prefix="/v1/models/recovery",
        tags=["models"],
        dependencies=[Depends(_require_access)],
    )

    @router.get("/{model_id}")
    async def get_model_recovery(request: Request, model_id: str):
        manager = getattr(request.app.state, "manager", None)
        if manager is None or not hasattr(manager, "model_recovery"):
            raise HTTPException(status_code=503, detail="Model recovery is unavailable.")
        if model_id not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{model_id}'")
        recovery = manager.model_recovery(model_id, include_details=True)
        if recovery is None:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_id}' has no interrupted preparation to recover.",
            )
        return recovery

    @router.post("/action")
    async def apply_model_recovery(
        request: Request,
        body: ModelRecoveryActionRequest,
    ):
        manager = getattr(request.app.state, "manager", None)
        if manager is None or not hasattr(manager, "recover_model"):
            raise HTTPException(status_code=503, detail="Model recovery is unavailable.")
        if body.model not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{body.model}'")
        try:
            return await manager.recover_model(
                body.model,
                body.recovery_id,
                body.action,
                device=body.device,
            )
        except RecoveryConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "message": str(exc)[:300],
                    "current_recovery_id": exc.current_recovery_id,
                },
            ) from exc

    app.include_router(router)
    app.state.model_recovery_routes_registered = True


def install_model_recovery_routes_extension() -> None:
    """Register recovery routes on InferBridge FastAPI applications."""

    if getattr(FastAPI, _ROUTE_INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_model_recovery(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_model_recovery_routes(self)

    FastAPI.__init__ = init_with_model_recovery  # type: ignore[method-assign]
    setattr(FastAPI, _ROUTE_INSTALL_FLAG, True)


__all__ = [
    "ModelRecoveryActionRequest",
    "RecoveryConflict",
    "install_model_recovery_manager_extension",
    "install_model_recovery_routes_extension",
]
