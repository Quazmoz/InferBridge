"""Cross-operation model lifecycle guards.

Model preparation is asynchronous, so load, convert, delete, and shutdown requests can
otherwise race one another. This extension serializes the user intent without changing
the public API: loads requested during conversion are resumed afterward, destructive
deletes are rejected while work is active, and shutdown never starts a deferred load.
"""

from __future__ import annotations

import asyncio
from typing import Any

_INSTALL_FLAG = "_MODEL_LIFECYCLE_SAFETY_INSTALLED"
_FOLLOWUPS_ATTR = "_post_conversion_loads"
_CALLBACKS_ATTR = "_post_conversion_callbacks"
_SHUTTING_DOWN_ATTR = "_model_manager_shutting_down"


def _mapping(manager: Any, name: str) -> dict:
    value = getattr(manager, name, None)
    if value is None:
        value = {}
        setattr(manager, name, value)
    return value


def _reject_reparse_point_delete(manager: Any, model_id: str) -> None:
    """Keep Delete from traversing a symbolic link or Windows junction."""

    from app.config import BASE_DIR
    from app.model_library_conversion import is_reparse_point

    cfg = manager.catalog.get(model_id)
    if cfg is None:
        return
    model_dir = cfg.abs_path(BASE_DIR)
    if is_reparse_point(model_dir):
        raise ValueError(
            f"Refusing to delete model '{model_id}' through a symbolic link or Windows junction. "
            "Remove the link manually after confirming its target."
        )


def install_model_lifecycle_safety() -> None:
    """Install conversion/load/delete coordination on ``ModelManager``."""

    from app import errors, model_manager as manager_module
    from runtime import device_check

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return

    original_schedule_load = manager_class.schedule_load
    original_schedule_convert = manager_class.schedule_convert
    original_delete = manager_class.delete
    original_shutdown = manager_class.shutdown

    def _active(task: asyncio.Task | None) -> bool:
        return bool(task and not task.done())

    def _remember_post_conversion_load(
        self,
        model_id: str,
        task: asyncio.Task,
        device: str,
        draft_model: str | None,
    ) -> None:
        followups = _mapping(self, _FOLLOWUPS_ATTR)
        callbacks = _mapping(self, _CALLBACKS_ATTR)
        followups[model_id] = {
            "device": device,
            "draft_model": draft_model,
        }

        if callbacks.get(model_id) is task:
            return
        callbacks[model_id] = task

        def resume_after_conversion(done: asyncio.Task) -> None:
            if callbacks.get(model_id) is not done:
                return
            callbacks.pop(model_id, None)
            request = followups.pop(model_id, None)
            if request is None or getattr(self, _SHUTTING_DOWN_ATTR, False):
                return
            if done.cancelled():
                return

            try:
                done.result()
            except Exception:  # noqa: BLE001 - conversion status already contains the safe error
                return

            status = self.status_overrides.get(model_id, {}).get("status")
            if status == "error":
                return

            try:
                self.schedule_load(
                    model_id,
                    request["device"],
                    draft_model=request["draft_model"],
                )
            except Exception as exc:  # noqa: BLE001 - callback cannot return an HTTP error
                cfg = self.catalog.get(model_id)
                name = cfg.name if cfg else model_id
                detail = self._sanitize_progress_line(
                    errors.format_model_load_error(exc),
                    limit=180,
                )
                message = (
                    f"Conversion finished, but loading could not start: {detail}"
                    if detail
                    else "Conversion finished, but loading could not start."
                )
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", message)
                manager_module.logger.exception(
                    "Failed to resume load for '%s' after conversion",
                    model_id,
                )
                self.emit_event("error", f"Could not load {name} after conversion")

        task.add_done_callback(resume_after_conversion)

    def coordinated_schedule_load(
        self,
        model_id: str,
        device: str | None = None,
        *,
        draft_model: str | None = None,
    ) -> asyncio.Task | None:
        conversion = self.convert_tasks.get(model_id)
        if _active(conversion):
            target = device_check.normalize_device(device or self.settings.device)
            _remember_post_conversion_load(
                self,
                model_id,
                conversion,
                target,
                draft_model,
            )
            cfg = self.catalog.get(model_id)
            name = cfg.name if cfg else model_id
            current = self.progress.get(model_id, {})
            phase = str(current.get("phase") or "converting")
            if phase not in {"queued", "downloading", "converting", "finalizing"}:
                phase = "converting"
            self._set_progress(
                model_id,
                phase,
                f"Preparing {name}. It will load on {target} when conversion finishes…",
                percent=current.get("percent"),
            )
            return conversion

        return original_schedule_load(
            self,
            model_id,
            device,
            draft_model=draft_model,
        )

    def coordinated_schedule_convert(
        self,
        model_id: str,
        device: str | None = None,
        *,
        load_after: bool = True,
        weight_format: str | None = None,
        group_size: int | None = None,
        ratio: float | None = None,
        sym: bool | None = None,
        trust_remote_code: bool | None = None,
    ) -> asyncio.Task | None:
        existing = self.convert_tasks.get(model_id)
        target = device_check.normalize_device(device or self.settings.device)
        if _active(existing):
            if load_after:
                _remember_post_conversion_load(
                    self,
                    model_id,
                    existing,
                    target,
                    None,
                )
            return existing

        # Reset stale elapsed time/logs from a prior attempt. The converter will
        # populate a fresh bounded log immediately after it is queued.
        self._clear_progress(model_id)
        task = original_schedule_convert(
            self,
            model_id,
            target,
            load_after=False,
            weight_format=weight_format,
            group_size=group_size,
            ratio=ratio,
            sym=sym,
            trust_remote_code=trust_remote_code,
        )

        if load_after:
            if _active(task):
                _remember_post_conversion_load(
                    self,
                    model_id,
                    task,
                    target,
                    None,
                )
            elif model_id in self.catalog:
                # Already-converted models return no conversion task.
                return self.schedule_load(model_id, target)
        return task

    def guarded_delete(self, model_id: str) -> dict:
        if model_id in self.engines:
            raise ValueError(f"Model '{model_id}' is loaded. Unload it before deleting.")

        load_task = self.load_tasks.get(model_id)
        convert_task = self.convert_tasks.get(model_id)
        # ``status_overrides`` is populated by the core manager's ``__init__``; guard the
        # access so lightweight callers that only track tasks are still protected.
        status = getattr(self, "status_overrides", {}).get(model_id, {}).get("status")
        if status in {"queued", "loading", "queued_convert", "converting"}:
            raise ValueError(
                f"Model '{model_id}' is still being prepared. "
                "Wait for loading or conversion to finish before deleting its files."
            )
        if _active(load_task):
            raise ValueError(f"Model '{model_id}' is still loading and cannot be deleted.")
        if _active(convert_task):
            raise ValueError(f"Model '{model_id}' is still converting and cannot be deleted.")

        _reject_reparse_point_delete(self, model_id)
        _mapping(self, _FOLLOWUPS_ATTR).pop(model_id, None)
        _mapping(self, _CALLBACKS_ATTR).pop(model_id, None)
        return original_delete(self, model_id)

    async def shutdown_without_deferred_loads(self) -> None:
        setattr(self, _SHUTTING_DOWN_ATTR, True)
        _mapping(self, _FOLLOWUPS_ATTR).clear()
        try:
            await original_shutdown(self)
        finally:
            _mapping(self, _CALLBACKS_ATTR).clear()

    manager_class.schedule_load = coordinated_schedule_load
    manager_class.schedule_convert = coordinated_schedule_convert
    manager_class.delete = guarded_delete
    manager_class.shutdown = shutdown_without_deferred_loads
    setattr(manager_class, _INSTALL_FLAG, True)
