"""Operation-scoped cancellation for model preparation.

Cancellation is intentionally conservative. Conversion subprocesses and preparation
that has not entered native OpenVINO compilation can be cancelled. Once native model
compilation begins, the request is rejected because cancelling the asyncio task cannot
stop the worker thread immediately and a successful-looking response would be false.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.local_request_security import require_safe_browser_origin

logger = logging.getLogger("ov-llm.cancellation")

_MANAGER_INSTALL_FLAG = "_MODEL_CANCELLATION_INSTALLED"
_ROUTE_INSTALL_FLAG = "_ovllm_model_cancellation_routes_installed"
_PRECOMPILE_PHASES = frozenset({"queued", "resolving", "downloading", "converting", "finalizing"})
_TERMINAL_PHASES = frozenset({"ready", "cancelled", "error"})
_OPERATION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class ModelCancelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128, pattern=_OPERATION_ID_PATTERN)


class CancellationConflict(RuntimeError):
    """A cancellation request no longer matches a cancellable operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        current_operation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.current_operation_id = current_operation_id


def _active(task: asyncio.Task[Any] | None) -> bool:
    return bool(task is not None and not task.done())


def _progress_identity(manager: Any, model_id: str) -> tuple[str | None, str, str | None]:
    progress = getattr(manager, "progress", {}).get(model_id, {})
    operation_id = progress.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        operation_id = None
    phase = str(progress.get("phase") or "idle").lower()
    operation_type = progress.get("operation_type")
    if operation_type not in {"load", "convert"}:
        operation_type = None
    return operation_id, phase, operation_type


def _capability(manager: Any, model_id: str) -> dict[str, Any]:
    operation_id, phase, operation_type = _progress_identity(manager, model_id)
    load_task = getattr(manager, "load_tasks", {}).get(model_id)
    convert_task = getattr(manager, "convert_tasks", {}).get(model_id)

    if operation_id is None:
        return {
            "can_cancel": False,
            "cancel_mode": None,
            "cancel_reason": "No model preparation operation is active.",
        }
    if phase in _TERMINAL_PHASES:
        return {
            "can_cancel": False,
            "cancel_mode": None,
            "cancel_reason": "The model preparation operation has already finished.",
        }
    if _active(convert_task):
        return {
            "can_cancel": True,
            "cancel_mode": "conversion",
            "cancel_reason": None,
        }
    if _active(load_task):
        if phase in _PRECOMPILE_PHASES:
            mode = "conversion" if phase in {"downloading", "converting", "finalizing"} else "preparation"
            return {
                "can_cancel": True,
                "cancel_mode": mode,
                "cancel_reason": None,
            }
        if phase == "loading":
            return {
                "can_cancel": False,
                "cancel_mode": None,
                "cancel_reason": (
                    "Native OpenVINO compilation has started. Let loading finish, then unload "
                    "the model if it is no longer needed."
                ),
            }
    label = "conversion" if operation_type == "convert" else "preparation"
    return {
        "can_cancel": False,
        "cancel_mode": None,
        "cancel_reason": f"The {label} task is no longer active.",
    }


def install_model_cancellation_manager_extension() -> None:
    """Add cancellation and capability reporting to the final composed manager."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _MANAGER_INSTALL_FLAG, False):
        return

    original_catalog_entry = manager_class.catalog_entry

    def cancellation_capability(self, model_id: str) -> dict[str, Any]:
        return _capability(self, model_id)

    def catalog_entry_with_cancellation(self, model_id: str) -> dict[str, Any]:
        entry = original_catalog_entry(self, model_id)
        entry.update(_capability(self, model_id))
        return entry

    async def cancel_operation(self, model_id: str, operation_id: str) -> dict[str, Any]:
        if model_id not in self.catalog:
            raise KeyError(model_id)

        locks = getattr(self, "_model_cancellation_locks", None)
        if locks is None:
            locks = {}
            self._model_cancellation_locks = locks
        lock = locks.setdefault(model_id, asyncio.Lock())

        async with lock:
            current_operation_id, phase, operation_type = _progress_identity(self, model_id)
            if current_operation_id is None:
                raise CancellationConflict(
                    "no_active_operation",
                    f"Model '{model_id}' has no active preparation operation.",
                )
            if current_operation_id != operation_id:
                raise CancellationConflict(
                    "stale_operation",
                    "The requested operation is no longer current. Refresh model status before retrying.",
                    current_operation_id=current_operation_id,
                )
            if phase == "cancelled":
                return {
                    "status": "cancelled",
                    "operation_id": operation_id,
                    "cancel_mode": None,
                    "already_cancelled": True,
                }

            capability = _capability(self, model_id)
            if not capability["can_cancel"]:
                code = "native_load_in_progress" if phase == "loading" else "not_cancellable"
                raise CancellationConflict(
                    code,
                    str(capability["cancel_reason"]),
                    current_operation_id=current_operation_id,
                )

            convert_task = self.convert_tasks.get(model_id)
            load_task = self.load_tasks.get(model_id)
            task = convert_task if _active(convert_task) else load_task if _active(load_task) else None
            if task is None:
                raise CancellationConflict(
                    "task_finished",
                    "The model preparation task finished before it could be cancelled.",
                    current_operation_id=current_operation_id,
                )

            cancel_mode = str(capability["cancel_mode"])
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - task state below is authoritative and sanitized
                logger.exception("Model preparation raised while cancelling '%s'", model_id)

            # Deferred post-conversion loads must never survive an explicit cancellation.
            for attr in ("_post_conversion_loads", "_post_conversion_callbacks"):
                mapping = getattr(self, attr, None)
                if isinstance(mapping, dict):
                    mapping.pop(model_id, None)

            latest_operation_id, latest_phase, _ = _progress_identity(self, model_id)
            if latest_operation_id == operation_id and latest_phase != "cancelled":
                cfg = self.catalog[model_id]
                self._set_status(model_id, "cancelled")
                self._set_progress(
                    model_id,
                    "cancelled",
                    f"Model preparation cancelled for {cfg.name}.",
                )

            final_operation_id, final_phase, _ = _progress_identity(self, model_id)
            if final_operation_id != operation_id:
                raise CancellationConflict(
                    "operation_replaced",
                    "A newer model preparation operation started while cancellation was completing.",
                    current_operation_id=final_operation_id,
                )
            if final_phase != "cancelled":
                raise CancellationConflict(
                    "cancellation_incomplete",
                    "The operation could not be confirmed as cancelled.",
                    current_operation_id=final_operation_id,
                )

            cfg = self.catalog[model_id]
            label = "conversion" if cancel_mode == "conversion" else "preparation"
            self.emit_event("info", f"Cancelled {label} for {cfg.name}")
            return {
                "status": "cancelled",
                "operation_id": operation_id,
                "operation_type": operation_type,
                "cancel_mode": cancel_mode,
                "already_cancelled": False,
            }

    manager_class.cancellation_capability = cancellation_capability
    manager_class.catalog_entry = catalog_entry_with_cancellation
    manager_class.cancel_operation = cancel_operation
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
    if not any(secrets.compare_digest(supplied, key) for key in configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def register_model_cancellation_routes(app: FastAPI) -> None:
    if getattr(app.state, "model_cancellation_routes_registered", False):
        return

    router = APIRouter(
        prefix="/v1/models",
        tags=["models"],
        dependencies=[Depends(_require_access)],
    )

    @router.post("/cancel")
    async def cancel_model_operation(request: Request, body: ModelCancelRequest):
        manager = getattr(request.app.state, "manager", None)
        if manager is None or not hasattr(manager, "cancel_operation"):
            raise HTTPException(status_code=503, detail="Model cancellation is unavailable.")
        if body.model not in manager.catalog:
            raise HTTPException(status_code=404, detail=f"Unknown model '{body.model}'")

        try:
            result = await manager.cancel_operation(body.model, body.operation_id)
        except CancellationConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "message": str(exc)[:300],
                    "current_operation_id": exc.current_operation_id,
                },
            ) from exc

        entry = manager.catalog_entry(body.model)
        already = bool(result.get("already_cancelled"))
        message = (
            f"{entry['name']} was already cancelled."
            if already
            else f"Cancelled model preparation for {entry['name']}."
        )
        return {
            **result,
            "message": message,
            "model": entry,
        }

    app.include_router(router)
    app.state.model_cancellation_routes_registered = True


def install_model_cancellation_routes_extension() -> None:
    """Register cancellation routes on InferBridge FastAPI applications."""

    if getattr(FastAPI, _ROUTE_INSTALL_FLAG, False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_cancellation(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_model_cancellation_routes(self)

    FastAPI.__init__ = init_with_cancellation  # type: ignore[method-assign]
    setattr(FastAPI, _ROUTE_INSTALL_FLAG, True)


__all__ = [
    "CancellationConflict",
    "ModelCancelRequest",
    "install_model_cancellation_manager_extension",
    "install_model_cancellation_routes_extension",
]
