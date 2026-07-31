"""Keep explicit model load device requests authoritative and failure-safe.

The newest explicit device request wins, including when a startup load or a previous
OpenVINO compilation is already in flight. A working engine is retained until its
replacement has compiled successfully so a bad target or driver failure does not
silently take the model offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any

_INSTALL_FLAG = "_DEVICE_AUTHORITATIVE_LOAD_INSTALLED"
_CACHE_INSTALL_FLAG = "_INFERBRIDGE_FAST_NPU_CACHE_INSTALLED"
_TARGETS_ATTR = "_requested_load_devices"
_LOAD_WAIT_UPDATE_SECONDS = 5.0
_NATIVE_LOAD_HEARTBEAT_SECONDS = 15.0
_NATIVE_LOAD_LONG_WARNING_SECONDS = 90.0


def _load_targets(manager: Any) -> dict[str, str]:
    targets = getattr(manager, _TARGETS_ATTR, None)
    if targets is None:
        targets = {}
        setattr(manager, _TARGETS_ATTR, targets)
    return targets


def _duration_label(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, remaining = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {remaining:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _queue_position(manager: Any, model_id: str) -> tuple[int, int]:
    active = [
        candidate
        for candidate, task in getattr(manager, "load_tasks", {}).items()
        if task is not None and not task.done()
    ]
    if model_id not in active:
        return 1, max(1, len(active))
    return active.index(model_id) + 1, len(active)


async def _acquire_with_progress(
    manager: Any,
    lock: asyncio.Lock,
    model_id: str,
    message: Callable[[float, int, int], str],
) -> None:
    """Acquire a lifecycle lock while keeping queued work visibly alive."""

    started = time.monotonic()
    interval = max(0.01, float(_LOAD_WAIT_UPDATE_SECONDS))
    while True:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=interval)
            return
        except TimeoutError:
            position, total = _queue_position(manager, model_id)
            manager._set_status(model_id, "queued")
            manager._set_progress(
                model_id,
                "queued",
                message(time.monotonic() - started, position, total),
            )


def _install_fast_npu_cache() -> None:
    """Prefer the faster reusable NPU cache format for packaged model loads."""

    from runtime import device_check, openvino_engine

    if getattr(openvino_engine, _CACHE_INSTALL_FLAG, False):
        return

    original_build_plugin_config = openvino_engine.build_plugin_config

    def build_plugin_config_for_fast_loads(
        device: str,
        max_prompt_len: int | None,
        cache_dir=None,
    ) -> dict:
        config = original_build_plugin_config(device, max_prompt_len, cache_dir)
        if device_check.normalize_device(device) == "NPU" and config.get("CACHE_DIR"):
            config.setdefault("CACHE_MODE", "OPTIMIZE_SPEED")
            config.setdefault("GENERATE_HINT", "FAST_COMPILE")
        return config

    openvino_engine.build_plugin_config = build_plugin_config_for_fast_loads
    setattr(openvino_engine, _CACHE_INSTALL_FLAG, True)


def install_model_load_target_routing() -> None:
    """Patch ``ModelManager`` so the newest explicit load target always wins."""

    from app import errors, model_manager as manager_module, model_registry as registry
    from app.config import BASE_DIR
    from runtime import device_check

    _install_fast_npu_cache()

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return

    original_shutdown = manager_class.shutdown

    def _inspect_target(
        self,
        model_id: str,
        current_device: str,
    ) -> bool:
        """Validate the target and return whether converted artifacts are local."""

        cfg = self.catalog[model_id]
        if self.force_mock:
            return True

        available = device_check.available_devices()
        if not device_check.is_device_available(current_device, available):
            raise RuntimeError(errors.format_device_error(current_device, available))
        return registry.is_downloaded(cfg, BASE_DIR)

    def _require_local_model(
        self,
        model_id: str,
        current_device: str,
    ) -> None:
        cfg = self.catalog[model_id]
        if _inspect_target(self, model_id, current_device):
            return
        raise RuntimeError(
            errors.format_model_not_converted(
                cfg.name,
                str(cfg.abs_path(BASE_DIR)),
                cfg.source_model,
                weight_format=cfg.weight_format,
            )
        )

    async def _build_engine_cancellation_safe(
        self,
        model_id: str,
        current_device: str,
        draft_model_path: str | None,
    ):
        """Compile with heartbeats and never orphan an uncancellable native worker."""

        cfg = self.catalog[model_id]
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            None,
            self._build_engine,
            model_id,
            current_device,
            draft_model_path,
        )
        started = time.monotonic()
        heartbeat = max(0.01, float(_NATIVE_LOAD_HEARTBEAT_SECONDS))

        try:
            while True:
                done, _ = await asyncio.wait({future}, timeout=heartbeat)
                if future in done:
                    return future.result()

                elapsed = time.monotonic() - started
                if elapsed >= _NATIVE_LOAD_LONG_WARNING_SECONDS:
                    message = (
                        f"Still compiling {cfg.name} for {current_device} "
                        f"({_duration_label(elapsed)}). First load can take several minutes; "
                        "later loads should reuse the OpenVINO cache."
                    )
                else:
                    message = (
                        f"Compiling {cfg.name} for {current_device} "
                        f"({_duration_label(elapsed)} elapsed)…"
                    )
                self._set_status(model_id, "loading")
                self._set_progress(model_id, "loading", message)
        except asyncio.CancelledError:
            self._set_progress(
                model_id,
                "loading",
                (
                    f"Shutdown requested while compiling {cfg.name}. "
                    "Waiting for native OpenVINO work to finish safely…"
                ),
            )
            with contextlib.suppress(Exception):
                engine = await asyncio.shield(future)
                engine.close()
            raise

    async def device_authoritative_load_task(
        self,
        model_id: str,
        device: str,
        draft_model_path: str | None = None,
    ) -> None:
        cfg = self.catalog[model_id]
        targets = _load_targets(self)
        current_device = device_check.normalize_device(device)

        try:
            while True:
                current_device = targets.get(model_id, current_device)
                loaded_engine = self.engines.get(model_id)
                if loaded_engine is not None:
                    loaded_device = device_check.normalize_device(loaded_engine.device)
                    if loaded_device == current_device:
                        self._set_progress(
                            model_id,
                            "ready",
                            f"{cfg.name} is already loaded on {loaded_device}.",
                            percent=100,
                        )
                        self._clear_status(model_id)
                        return

                # Fail invalid devices and missing local artifacts before waiting behind
                # a long native compile. Automatic conversion remains serialized with
                # loading to avoid overlapping two memory-intensive model operations.
                self._set_status(model_id, "loading")
                self._set_progress(
                    model_id,
                    "loading",
                    f"Checking local model files and {current_device} availability for {cfg.name}…",
                )
                downloaded = _inspect_target(self, model_id, current_device)
                if not downloaded and not self.settings.auto_convert:
                    _require_local_model(self, model_id, current_device)

                latest_target = targets.get(model_id, current_device)
                if latest_target != current_device:
                    manager_module.logger.info(
                        "Retargeting queued load for '%s' from %s to %s before compile",
                        model_id,
                        current_device,
                        latest_target,
                    )
                    current_device = latest_target
                    continue

                loaded_engine = self.engines.get(model_id)
                model_lock = self.locks.get(model_id) if loaded_engine is not None else None
                if model_lock is not None:
                    loaded_device = device_check.normalize_device(loaded_engine.device)
                    await _acquire_with_progress(
                        self,
                        model_lock,
                        model_id,
                        lambda elapsed, _position, _total: (
                            f"Waiting for the active request before switching {cfg.name} "
                            f"from {loaded_device} to {current_device} "
                            f"({_duration_label(elapsed)} elapsed)…"
                        ),
                    )

                replacement = None
                load_lock_acquired = False
                try:
                    await _acquire_with_progress(
                        self,
                        self._load_lock,
                        model_id,
                        lambda elapsed, position, total: (
                            f"Waiting for another model preparation to finish before loading "
                            f"{cfg.name} on {current_device} "
                            f"(queue {position} of {total}, {_duration_label(elapsed)} elapsed)…"
                        ),
                    )
                    load_lock_acquired = True

                    latest_target = targets.get(model_id, current_device)
                    if latest_target != current_device:
                        current_device = latest_target
                        continue

                    loaded_engine = self.engines.get(model_id)
                    if loaded_engine is not None:
                        loaded_device = device_check.normalize_device(loaded_engine.device)
                        if loaded_device == current_device:
                            self._set_progress(
                                model_id,
                                "ready",
                                f"{cfg.name} is already loaded on {loaded_device}.",
                                percent=100,
                            )
                            self._clear_status(model_id)
                            return

                    downloaded = _inspect_target(self, model_id, current_device)
                    if not downloaded:
                        manager_module.logger.info(
                            "Model '%s' is not available locally; starting automatic conversion",
                            model_id,
                        )
                        self._set_status(model_id, "converting")
                        self._set_progress(
                            model_id,
                            "downloading",
                            f"{cfg.name} is not converted yet. Downloading and converting first…",
                        )
                        await self._convert_task(model_id, current_device, load_after=False)
                        self._set_status(model_id, "loading")
                        _require_local_model(self, model_id, current_device)

                    latest_target = targets.get(model_id, current_device)
                    if latest_target != current_device:
                        current_device = latest_target
                        continue

                    self._set_status(model_id, "loading")
                    self._set_progress(
                        model_id,
                        "loading",
                        f"Compiling {cfg.name} for {current_device}…",
                    )
                    replacement = await _build_engine_cancellation_safe(
                        self,
                        model_id,
                        current_device,
                        draft_model_path,
                    )

                    latest_target = targets.get(model_id, current_device)
                    if latest_target != current_device:
                        manager_module.logger.info(
                            "Discarding '%s' engine built on %s; newest target is %s",
                            model_id,
                            current_device,
                            latest_target,
                        )
                        with contextlib.suppress(Exception):
                            replacement.close()
                        replacement = None
                        self._set_progress(
                            model_id,
                            "loading",
                            f"Retargeting {cfg.name} to {latest_target}…",
                        )
                        current_device = latest_target
                        continue

                    previous_engine = self.engines.get(model_id)
                    previous_device = (
                        device_check.normalize_device(previous_engine.device)
                        if previous_engine is not None
                        else None
                    )

                    self.engines[model_id] = replacement
                    self.locks[model_id] = asyncio.Lock()
                    self.devices[model_id] = replacement.device
                    replacement = None

                    if previous_engine is not None:
                        with contextlib.suppress(Exception):
                            previous_engine.close()
                        manager_module.logger.info(
                            "Switched '%s' from %s to %s",
                            model_id,
                            previous_device,
                            self.devices[model_id],
                        )
                        self.emit_event(
                            "info",
                            f"Switched {cfg.name} from {previous_device} "
                            f"to {self.devices[model_id]}",
                        )
                    else:
                        manager_module.logger.info(
                            "Loaded '%s' on %s",
                            model_id,
                            self.devices[model_id],
                        )
                        self.emit_event(
                            "info",
                            f"Loaded {cfg.name} on {self.devices[model_id]}",
                        )

                    self._set_progress(
                        model_id,
                        "ready",
                        f"{cfg.name} is ready on {self.devices[model_id]}.",
                        percent=100,
                    )
                    self._clear_status(model_id)
                    return
                finally:
                    if replacement is not None:
                        with contextlib.suppress(Exception):
                            replacement.close()
                    if load_lock_acquired and self._load_lock.locked():
                        self._load_lock.release()
                    if model_lock is not None and model_lock.locked():
                        model_lock.release()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced through model status
            message = errors.format_model_load_error(exc)
            existing = self.engines.get(model_id)
            if existing is not None:
                existing_device = device_check.normalize_device(existing.device)
                self._clear_status(model_id)
                self._set_progress(
                    model_id,
                    "ready",
                    f"Could not switch {cfg.name} to {current_device}. "
                    f"Continuing on {existing_device}. {message}",
                    percent=100,
                )
                manager_module.logger.warning(
                    "Failed to switch '%s' to %s; retained %s: %s",
                    model_id,
                    current_device,
                    existing_device,
                    message,
                )
                self.emit_event(
                    "warning",
                    f"Could not switch {cfg.name} to {current_device}; "
                    f"continuing on {existing_device}",
                )
            else:
                self.engines.pop(model_id, None)
                self.locks.pop(model_id, None)
                self.devices.pop(model_id, None)
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", f"Load failed: {message}")
                manager_module.logger.exception("Failed to load '%s': %s", model_id, message)
                self.emit_event("error", f"Failed to load {cfg.name}: {message}")
        finally:
            self.load_tasks.pop(model_id, None)
            targets.pop(model_id, None)

    def device_authoritative_schedule_load(
        self,
        model_id: str,
        device: str | None = None,
        *,
        draft_model: str | None = None,
    ) -> asyncio.Task | None:
        if model_id not in self.catalog:
            manager_module.logger.warning("Refusing to load unknown model '%s'", model_id)
            return None

        target_device = device_check.normalize_device(device or self.settings.device)
        cfg = self.catalog[model_id]
        targets = _load_targets(self)
        loaded_engine = self.engines.get(model_id)

        if loaded_engine is not None:
            loaded_device = device_check.normalize_device(loaded_engine.device)
            if loaded_device == target_device:
                self._set_progress(
                    model_id,
                    "ready",
                    f"{cfg.name} is already loaded on {loaded_device}.",
                    percent=100,
                )
                self._clear_status(model_id)
                return None

        existing = self.load_tasks.get(model_id)
        if existing and not existing.done():
            previous_target = targets.get(model_id)
            targets[model_id] = target_device
            if previous_target != target_device:
                self._set_progress(
                    model_id,
                    "loading",
                    f"Retargeting {cfg.name} to {target_device}…",
                )
                manager_module.logger.info(
                    "Updated in-flight load target for '%s' from %s to %s",
                    model_id,
                    previous_target or "unknown",
                    target_device,
                )
            return existing

        draft_model_path = self._resolve_draft_model_path(model_id, draft_model)
        targets[model_id] = target_device

        # A new attempt must not inherit elapsed time or a nearly-complete progress
        # bar from a previous failed/finished attempt.
        self._clear_progress(model_id)
        self._set_status(model_id, "queued")
        if loaded_engine is not None:
            loaded_device = device_check.normalize_device(loaded_engine.device)
            message = (
                f"Queued {cfg.name} to switch from {loaded_device} to {target_device}. "
                f"The current model remains available until the replacement is ready."
            )
        else:
            message = f"Queued {cfg.name} to load on {target_device}…"
        self._set_progress(model_id, "queued", message)

        task = asyncio.create_task(
            self._load_task(
                model_id,
                target_device,
                draft_model_path=draft_model_path,
            )
        )
        self.load_tasks[model_id] = task
        return task

    async def shutdown_with_target_cleanup(self) -> None:
        try:
            await original_shutdown(self)
        finally:
            _load_targets(self).clear()

    manager_class._load_task = device_authoritative_load_task
    manager_class.schedule_load = device_authoritative_schedule_load
    manager_class.shutdown = shutdown_with_target_cleanup
    setattr(manager_class, _INSTALL_FLAG, True)
