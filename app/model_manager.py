"""Hardware-advised extension of the core model lifecycle manager.

The original lifecycle implementation is retained verbatim in
:mod:`app.model_manager_core`. This thin subclass adds conservative hardware
preflight metadata, profile-based ``model=auto`` routing, and a short benchmark
after successful real-hardware loads.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import threading
import time
from dataclasses import replace
from functools import partial
from typing import Any

from app import (
    context_configuration,
    model_load_safety,
    model_manager_core as _core,
    model_registry as registry,
)
from app.config import BASE_DIR, Settings
from app.hardware_advisor import HardwareAdvisor, parse_auto_model
from app.model_manager_core import *  # noqa: F401,F403 - preserve the public module contract
from app.model_manager_core import (
    ModelManager as _CoreModelManager,
    NoModelsLoaded,
    UnknownModel,
)
from runtime import device_check
from runtime.openvino_engine import BaseEngine, GenParams, StreamHandle, create_engine


class ContextBusyError(RuntimeError):
    """A predictable lifecycle conflict with a safe public message."""


class ModelManager(_CoreModelManager):
    """Core lifecycle manager with hardware-aware recommendation services."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._catalog_lock = threading.RLock()
        self._context_changes: set[str] = set()
        self.advisor = HardwareAdvisor(settings, self.catalog, force_mock=self.force_mock)
        self._install_advisor_load_hook()

    def context_details(self, model_id: str) -> dict:
        cfg = self.catalog[model_id]
        engine = self.engines.get(model_id)
        details = context_configuration.describe_context(
            cfg, BASE_DIR, device="MOCK" if self.force_mock else getattr(engine, "device", None)
        )
        details["loaded"] = engine is not None
        details["active_device"] = getattr(engine, "device", None)
        details["changing"] = model_id in self._context_changes
        estimate = self.advisor.estimate_model(cfg, device=details["active_device"])
        details["converted_weight_gib_estimate"] = estimate["converted_size_gb"]
        details["converted_weight_source"] = estimate["converted_size_source"]
        snapshot = self.advisor.hardware_snapshot()
        memory = snapshot.get("memory") or {}
        available = memory.get("available_gb")
        # Keep device-allocated memory unknown. These are intentionally rough
        # host-RAM planning figures, not NPU telemetry or an OOM prediction.
        try:
            available_gib = max(0.0, float(available)) if available is not None else None
        except (TypeError, ValueError):
            available_gib = None
        details["available_system_ram_gib"] = available_gib
        reserve = max(2.0, available_gib * 0.15) if available_gib is not None else None
        details["reserved_system_ram_gib_estimate"] = reserve
        other = sum(
            self.advisor.estimate_model(other_cfg, device=self.devices.get(other_id))[
                "runtime_memory_gb"
            ]
            for other_id, other_cfg in self.catalog.items()
            if other_id != model_id and other_id in self.engines
        )
        details["other_loaded_models_ram_gib_lower_bound"] = round(other, 2)
        for option in details["options"]:
            kv = option["fp16_kv_cache_gib_estimate"]
            if kv is None:
                option["estimated_host_ram_gib"] = None
                option["fit_guidance"] = "unverified-memory"
                continue
            needed = estimate["converted_size_gb"] * 1.22 + kv + 0.35 + other
            option["estimated_host_ram_gib"] = round(needed, 2)
            option["fit_guidance"] = (
                "unverified-memory"
                if available_gib is None
                else "memory-constrained"
                if needed + reserve > available_gib
                else "estimated-headroom"
            )
        return details

    async def configure_context(
        self, model_id: str, tokens: int, *, expected_context_tokens: int
    ) -> dict:
        """Stage, compile, persist, then atomically hand off a model's context.

        No catalog or engine mutation occurs before a successful replacement build.
        The existing engine lease handles requests queued on the previous model lock.
        """
        if model_id not in self.catalog:
            raise KeyError(model_id)
        if model_id in self._context_changes:
            raise ContextBusyError("A context change is already in progress for this model.")
        suite_lock = getattr(self, "_benchmark_suite_lock", None)
        if suite_lock is not None and suite_lock.locked():
            raise ContextBusyError(
                "Wait for the active benchmark to finish before changing context."
            )
        cfg = self.catalog[model_id]
        if cfg.max_context_len != expected_context_tokens:
            raise ContextBusyError("Context changed in another client. Refresh before applying.")
        metadata = context_configuration.model_metadata(cfg, BASE_DIR)
        context_configuration.validate_context(cfg, tokens, metadata)
        if tokens == cfg.max_context_len:
            return self.context_details(model_id)
        if any(
            task is not None and not task.done()
            for task in (self.load_tasks.get(model_id), self.convert_tasks.get(model_id))
        ):
            raise ContextBusyError("Wait for model loading or conversion to finish.")

        self._context_changes.add(model_id)
        replacement = None
        try:
            async with self._load_lock:
                suite_lock = getattr(self, "_benchmark_suite_lock", None)
                if suite_lock is not None and suite_lock.locked():
                    raise ContextBusyError(
                        "Wait for the active benchmark to finish before changing context."
                    )
                if self.catalog.get(model_id) != cfg:
                    raise ContextBusyError("Model metadata changed. Refresh before applying.")
                if any(
                    task is not None and not task.done()
                    for task in (self.load_tasks.get(model_id), self.convert_tasks.get(model_id))
                ):
                    raise ContextBusyError("Model loading or conversion started. Try again later.")
                updated = replace(
                    cfg,
                    max_context_len=tokens,
                    recommended_context_len=cfg.recommended_context_len or cfg.max_context_len,
                )
                old_engine = self.engines.get(model_id)
                if old_engine is not None:
                    device = device_check.normalize_device(old_engine.device)
                    if not self.force_mock:
                        safe_device = model_load_safety.safe_load_device(
                            updated,
                            BASE_DIR,
                            device,
                            available=device_check.available_devices(),
                        )
                        if safe_device != device:
                            raise ContextBusyError(
                                "Current device is not eligible for this model profile."
                            )
                    draft = getattr(old_engine, "_ovllm_draft_model_path", None)
                    build = partial(
                        create_engine,
                        model_id=model_id,
                        model_path=str(updated.abs_path(BASE_DIR)),
                        device=device,
                        max_prompt_len=updated.max_prompt_len,
                        force_mock=self.force_mock,
                        cache_dir=self.settings.cache_dir,
                        backend=updated.backend,
                        draft_model_path=draft,
                        max_response_len=updated.max_output_tokens,
                    )
                    worker = asyncio.get_running_loop().run_in_executor(None, build)
                    replacement, cancelled = await self._await_resilient_future(worker)
                    if cancelled is not None:
                        raise cancelled
                    replacement._ovllm_draft_model_path = draft

                # A short per-model handoff lock allows active generations to finish
                # against their original engine. Other loaded models stay operational.
                lock = self.get_lock(model_id)
                async with lock:
                    if (
                        self.engines.get(model_id) is not old_engine
                        or self.catalog.get(model_id) != cfg
                    ):
                        raise ContextBusyError("Model lifecycle changed. Refresh and retry.")
                    with self._catalog_lock:
                        staged = dict(self.catalog)
                        staged[model_id] = updated
                        registry.save_catalog(self.settings.models_file, staged)
                        self.catalog = staged
                        self.advisor.catalog = staged
                    if replacement is not None:
                        self.engines[model_id] = replacement
                        self.devices[model_id] = replacement.device
                        # Queued engine leases recheck this identity after acquiring.
                        self.locks[model_id] = asyncio.Lock()
                        replacement = None
                        with contextlib.suppress(Exception):
                            old_engine.close()
            self.emit_event("info", f"Updated context for {cfg.name} to {tokens} tokens")
            self._context_changes.discard(model_id)
            return self.context_details(model_id)
        finally:
            self._context_changes.discard(model_id)
            if replacement is not None:
                with contextlib.suppress(Exception):
                    replacement.close()

    def _build_engine(
        self, model_id: str, device: str, draft_model_path: str | None = None
    ) -> BaseEngine:
        """Preflight crash-prone model/device combinations before native compilation."""

        if self.force_mock:
            engine = super()._build_engine(model_id, device, draft_model_path)
            engine._ovllm_draft_model_path = draft_model_path  # type: ignore[attr-defined]
            return engine

        cfg = self.catalog[model_id]
        requested = device_check.normalize_device(device)
        safe_device = model_load_safety.safe_load_device(
            cfg,
            BASE_DIR,
            requested,
            available=device_check.available_devices(),
        )
        if safe_device != requested:
            message = (
                f"Excluded NPU while loading {cfg.name} because its local INT4 artifact "
                f"does not have a verified NPU conversion profile; using {safe_device}."
            )
            _core.logger.warning(message)
            self.emit_event("warning", message)
        engine = super()._build_engine(model_id, safe_device, draft_model_path)
        engine._ovllm_draft_model_path = draft_model_path  # type: ignore[attr-defined]
        return engine

    def _install_advisor_load_hook(self) -> None:
        """Observe the final composed load scheduler without replacing lifecycle guards.

        ``Settings`` installs device-authoritative and cross-operation lifecycle wrappers
        before manager instances are created. Observing the bound scheduler here keeps
        advisor measurements compatible with those newer wrappers and with future
        scheduler composition, instead of competing for the class-level ``_load_task``.
        """
        upstream_schedule_load = self.schedule_load
        observed_tasks: set[asyncio.Task[Any]] = set()

        def schedule_load_with_advisor(
            model_id: str,
            device: str | None = None,
            *,
            draft_model: str | None = None,
        ) -> asyncio.Task[Any] | None:
            cfg = self.catalog.get(model_id)
            previous_engine = self.engines.get(model_id)
            was_downloaded = bool(
                cfg is not None and (self.force_mock or registry.is_downloaded(cfg, BASE_DIR))
            )
            queued_behind_another_load = self._load_lock.locked()
            started = time.perf_counter()

            task = upstream_schedule_load(model_id, device, draft_model=draft_model)
            if task is None or cfg is None or task in observed_tasks:
                return task

            observed_tasks.add(task)

            def after_load(done: asyncio.Task[Any]) -> None:
                observed_tasks.discard(done)
                if done.cancelled():
                    return
                try:
                    done.result()
                except Exception:
                    return

                current_engine = self.engines.get(model_id)
                # Conversion tasks can be returned while a deferred load is queued, and
                # failed device switches intentionally retain the previous engine. Only
                # a newly installed engine represents a successful load worth measuring.
                if current_engine is None or current_engine is previous_engine:
                    return

                elapsed_ms = (time.perf_counter() - started) * 1000.0
                measured_load_ms = (
                    elapsed_ms
                    if was_downloaded and not queued_behind_another_load and not self.force_mock
                    else None
                )
                finalize_task = asyncio.create_task(
                    self._finalize_advisor_load(
                        model_id,
                        cfg,
                        load_time_ms=measured_load_ms,
                    ),
                    name=f"advisor-load-finalize-{model_id}",
                )
                self.advisor._tasks.add(finalize_task)
                finalize_task.add_done_callback(self.advisor._tasks.discard)

            task.add_done_callback(after_load)
            return task

        self.schedule_load = schedule_load_with_advisor  # type: ignore[method-assign]

    async def _finalize_advisor_load(
        self,
        model_id: str,
        cfg: registry.ModelConfig,
        *,
        load_time_ms: float | None,
    ) -> None:
        """Record safe local evidence without allowing advisor work to fail a load."""
        try:
            await asyncio.to_thread(self.advisor.measure_converted_size, cfg)
        except Exception:  # noqa: BLE001 - advisory evidence must not break model loading
            _core.logger.exception("Could not measure converted size for '%s'", model_id)

        try:
            self.advisor.schedule_auto_benchmark(
                self,
                model_id,
                load_time_ms=load_time_ms,
            )
        except Exception:  # noqa: BLE001 - advisory evidence must not break model loading
            _core.logger.exception("Could not schedule advisor benchmark for '%s'", model_id)

    def register_model(self, req: Any) -> registry.ModelConfig:
        with self._catalog_lock:
            original_catalog = dict(self.catalog)
            try:
                return super().register_model(req)
            except Exception:
                self.catalog = original_catalog
                advisor = getattr(self, "advisor", None)
                if advisor is not None:
                    advisor.catalog = self.catalog
                try:
                    registry.save_catalog(self.settings.models_file, original_catalog)
                except Exception:  # noqa: BLE001 - preserve the original registration error
                    pass
                raise

    def resolve_engine(self, model_id: str):
        text = str(model_id or "").strip()
        if text.lower().startswith("auto"):
            try:
                profile = parse_auto_model(text)
            except ValueError as exc:
                raise UnknownModel(str(exc)) from exc
            if profile is None:
                return super().resolve_engine(model_id)
            selected = self.advisor.select_loaded_model(profile, self.engines, self.devices)
            if selected is None:
                raise NoModelsLoaded(
                    "No loaded text-generation model is available for advisor profile "
                    f"'{profile}'. Load at least one compatible generation model first."
                )
            return self.engines[selected]
        return super().resolve_engine(model_id)

    def catalog_entry(self, model_id: str) -> dict[str, Any]:
        entry = super().catalog_entry(model_id)
        cfg = self.catalog[model_id]
        entry["advisor"] = self.advisor.evaluate_model(
            cfg,
            downloaded=bool(entry.get("is_downloaded")),
            loaded=bool(entry.get("is_loaded")),
            loaded_device=entry.get("device"),
        )
        return entry

    def metrics_summary(self) -> dict[str, Any]:
        summary = super().metrics_summary()
        summary["advisor"] = self.advisor.summary(self.engines, self.devices)
        return summary

    async def _convert_task(
        self,
        model_id: str,
        device: str,
        load_after: bool,
        weight_format: str | None = None,
        group_size: int | None = None,
        ratio: float | None = None,
        sym: bool | None = None,
        trust_remote_code: bool | None = None,
    ) -> None:
        initial_cfg = self.catalog[model_id]
        was_downloaded = registry.is_downloaded(initial_cfg, BASE_DIR)
        effective_format, group_size, ratio, sym = model_load_safety.resolve_conversion_profile(
            initial_cfg,
            weight_format=weight_format,
            group_size=group_size,
            ratio=ratio,
            sym=sym,
        )
        if not was_downloaded or weight_format is not None:
            try:
                model_load_safety.invalidate_load_profile(initial_cfg, BASE_DIR)
            except RuntimeError as exc:
                message = str(exc)
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", message)
                self.emit_event("error", f"Could not prepare {initial_cfg.name} for conversion")
                _core.logger.exception("Could not invalidate load profile for '%s'", model_id)
                return
        # The core schedules load immediately after conversion, before this subclass
        # can record the NPU-safe profile. Defer loading until the marker is durable.
        await super()._convert_task(
            model_id,
            device,
            False,
            weight_format=weight_format,
            group_size=group_size,
            ratio=ratio,
            sym=sym,
            trust_remote_code=trust_remote_code,
        )
        # The core lifecycle converts failures into a persisted error state instead
        # of re-raising. An older IR directory can still exist after a failed
        # requantization, so never relabel or re-certify it as the requested format.
        if self.status_overrides.get(model_id, {}).get("status") == "error":
            return
        # A direct call can discover an already-converted model after scheduling. Do
        # not retroactively stamp portable defaults onto an artifact we did not create.
        if was_downloaded and weight_format is None:
            if load_after:
                self.schedule_load(model_id, device)
            return
        cfg = self.catalog.get(model_id)
        if cfg is None:
            return
        if not registry.is_downloaded(cfg, BASE_DIR):
            message = (
                "Conversion finished without the required OpenVINO IR files. "
                "Review the conversion log and retry."
            )
            self._set_status(model_id, "error", error=message)
            self._set_progress(model_id, "error", message)
            self.emit_event("error", f"Conversion output was incomplete for {cfg.name}")
            _core.logger.error("Conversion produced no usable OpenVINO IR for '%s'", model_id)
            return

        if weight_format and cfg.weight_format != weight_format:
            try:
                cfg = await asyncio.to_thread(
                    self._persist_converted_weight_format, model_id, weight_format
                )
            except Exception as exc:  # noqa: BLE001 - surface persistence failure safely
                message = (
                    "Converted model files were created, but the model catalog could not be "
                    "updated. Restart after repairing the writable data directory."
                )
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", message)
                self.emit_event("error", f"Catalog update failed for {cfg.name}")
                _core.logger.exception(
                    "Could not persist converted precision for '%s': %s", model_id, exc
                )
                return

        try:
            from app.model_library import record_conversion_metadata

            await asyncio.to_thread(record_conversion_metadata, cfg, self.settings)
            await asyncio.to_thread(
                model_load_safety.record_load_profile,
                cfg,
                BASE_DIR,
                weight_format=effective_format,
                group_size=group_size,
                ratio=ratio,
                sym=sym,
            )
        except Exception:  # noqa: BLE001 - metadata must not fail a successful conversion
            _core.logger.exception(
                "Could not record conversion compatibility metadata for '%s'", model_id
            )

        if load_after:
            self.schedule_load(model_id, device)

        try:
            await asyncio.to_thread(self.advisor.measure_converted_size, cfg)
        except Exception:  # noqa: BLE001 - advisory evidence must not fail conversion
            _core.logger.exception("Could not measure converted size for '%s'", model_id)

    def _persist_converted_weight_format(
        self, model_id: str, weight_format: str
    ) -> registry.ModelConfig:
        with self._catalog_lock:
            current = self.catalog[model_id]
            updated = replace(current, weight_format=weight_format)
            staged_catalog = dict(self.catalog)
            staged_catalog[model_id] = updated
            registry.save_catalog(self.settings.models_file, staged_catalog)
            self.catalog = staged_catalog
            self.advisor.catalog = self.catalog
            return updated

    def delete(self, model_id: str) -> dict:
        cfg = self.catalog[model_id]
        load_task = self.load_tasks.get(model_id)
        convert_task = self.convert_tasks.get(model_id)
        if model_id in self.engines:
            raise ValueError(f"Model '{model_id}' is loaded. Unload it before deleting.")
        if load_task is not None and not load_task.done():
            raise ValueError(f"Model '{model_id}' is still loading and cannot be deleted.")
        if convert_task is not None and not convert_task.done():
            raise ValueError(f"Model '{model_id}' is still converting and cannot be deleted.")
        result = super().delete(model_id)
        self.advisor.forget_model_size(cfg)
        return result

    def reload_catalog(self) -> None:
        with self._catalog_lock:
            super().reload_catalog()
            self.advisor.catalog = self.catalog

    @staticmethod
    async def _await_resilient_future(future):
        """Await one future to completion while retaining any cancellation request."""

        pending_cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                return await asyncio.shield(future), pending_cancellation
            except asyncio.CancelledError as exc:
                pending_cancellation = pending_cancellation or exc
                if future.done():
                    return future.result(), pending_cancellation

    async def _finish_stream_handle(
        self,
        engine: BaseEngine,
        handle: StreamHandle,
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.CancelledError | None:
        """Stop a worker and resist task cancellation until the native request exits."""

        handle.request_stop()
        waiter = loop.run_in_executor(None, handle.wait_closed)
        closed, pending_cancellation = await self._await_resilient_future(waiter)

        if not closed:
            model_id = engine.model_id
            if self.engines.get(model_id) is engine:
                self.engines.pop(model_id, None)
                self.devices.pop(model_id, None)
                message = (
                    "The cancelled generation did not stop within 30 seconds. The model was "
                    "quarantined; reload it before sending another request."
                )
                self._set_status(model_id, "error", error=message)
                self._set_progress(model_id, "error", message)
                self.emit_event(
                    "error", f"Quarantined {model_id} after stream cancellation timeout"
                )
                _core.logger.error("Quarantined '%s' after stream cancellation timeout", model_id)

        return pending_cancellation

    async def _recover_cancelled_npu_engine(
        self,
        engine: BaseEngine,
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.CancelledError | None:
        """Rebuild a direct-NPU pipeline after an interrupted stream.

        OpenVINO GenAI may report that the next NPU infer request is still busy even
        after the streamer callback stops and ``generate`` returns. Recreating the
        pipeline under the same model lock gives the next request a clean infer state.
        """

        if self.force_mock or str(engine.device).split(".", 1)[0].upper() != "NPU":
            return None
        model_id = engine.model_id
        if self.engines.get(model_id) is not engine:
            return None
        draft_model_path = getattr(engine, "_ovllm_draft_model_path", None)

        def rebuild() -> BaseEngine:
            try:
                engine.close()
            finally:
                gc.collect()
            return self._build_engine(model_id, engine.device, draft_model_path)

        future = loop.run_in_executor(None, rebuild)
        try:
            replacement, pending_cancellation = await self._await_resilient_future(future)
        except Exception as exc:  # noqa: BLE001 - leave the model quarantined, not process-fatal
            self.engines.pop(model_id, None)
            self.devices.pop(model_id, None)
            message = (
                "The NPU pipeline could not be rebuilt after stream cancellation. "
                "Reload the model before retrying."
            )
            self._set_status(model_id, "error", error=message)
            self._set_progress(model_id, "error", message)
            self.emit_event("error", f"NPU cancellation recovery failed for {model_id}")
            _core.logger.exception(
                "Could not rebuild NPU engine '%s' after cancellation: %s", model_id, exc
            )
            return None

        if self.engines.get(model_id) is engine:
            self.engines[model_id] = replacement
            self.devices[model_id] = replacement.device
            cfg = self.catalog.get(model_id)
            name = cfg.name if cfg else model_id
            self._set_progress(
                model_id,
                "ready",
                f"{name} recovered after stream cancellation on {replacement.device}.",
                percent=100,
            )
            self._clear_status(model_id)
            self.emit_event("warning", f"Rebuilt {name} after NPU stream cancellation")
        else:
            try:
                replacement.close()
            except Exception:
                pass
        return pending_cancellation

    async def stream(self, engine: BaseEngine, prompt: str, params: GenParams):
        """Yield chunks while keeping cancellation recovery inside the model lock."""

        async with self._track_generation():
            loop = asyncio.get_running_loop()
            lock = self.get_lock(engine.model_id)
            async with lock:
                handle: StreamHandle = await loop.run_in_executor(
                    None, engine.stream, prompt, params
                )
                completed = False
                generation_failed = False
                pending_cancellation: asyncio.CancelledError | None = None
                try:
                    while True:
                        chunk = await loop.run_in_executor(None, handle.next_chunk)
                        if chunk is None:
                            completed = True
                            break
                        yield chunk
                    if handle.error is not None:
                        generation_failed = True
                        raise handle.error
                finally:
                    cleanup_cancellation = await self._finish_stream_handle(engine, handle, loop)
                    pending_cancellation = pending_cancellation or cleanup_cancellation
                    if not completed or generation_failed:
                        recovery_cancellation = await self._recover_cancelled_npu_engine(
                            engine, loop
                        )
                        pending_cancellation = pending_cancellation or recovery_cancellation
                    if pending_cancellation is not None:
                        raise pending_cancellation

    async def shutdown(self) -> None:
        await self.advisor.shutdown()
        await super().shutdown()


def __getattr__(name: str):
    """Preserve access to implementation details used by existing tests/extensions."""
    return getattr(_core, name)
