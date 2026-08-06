"""Privacy-safe last-use tracking and lifecycle exclusion for storage cleanup."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from app.storage_safety import StorageConflict

_USAGE_SCHEMA_VERSION = 1


class StorageRuntimeState:
    def __init__(self, *, manager: Any, usage_file: Path) -> None:
        self.manager = manager
        self._guard_lock = threading.RLock()
        self._cleaning_models: set[str] = set()
        self._global_cleanup = False
        self._usage_lock = threading.RLock()
        self._usage_file = usage_file
        self._usage = self._load_usage()
        self._usage_flushed_at: dict[str, int] = dict(self._usage)
        self._install_usage_observer()
        self._install_lifecycle_guard()

    def _load_usage(self) -> dict[str, int]:
        try:
            payload = json.loads(self._usage_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != _USAGE_SCHEMA_VERSION:
            return {}
        values = payload.get("models")
        if not isinstance(values, dict):
            return {}
        output: dict[str, int] = {}
        for model_id, raw in values.items():
            if model_id not in self.manager.catalog:
                continue
            try:
                timestamp = int(raw)
            except (TypeError, ValueError):
                continue
            if timestamp > 0:
                output[model_id] = timestamp
        return output

    def _persist_usage_locked(self) -> None:
        self._usage_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._usage_file.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": _USAGE_SCHEMA_VERSION,
                    "models": dict(sorted(self._usage.items())),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._usage_file)

    def mark_model_used(self, model_id: str) -> None:
        if model_id not in self.manager.catalog:
            return
        now = int(time.time())
        with self._usage_lock:
            self._usage[model_id] = now
            if now - self._usage_flushed_at.get(model_id, 0) < 60:
                return
            try:
                self._persist_usage_locked()
            except OSError:
                return
            self._usage_flushed_at[model_id] = now

    def clear_model_usage(self, model_id: str) -> None:
        with self._usage_lock:
            changed = self._usage.pop(model_id, None) is not None
            self._usage_flushed_at.pop(model_id, None)
            if changed:
                with contextlib.suppress(OSError):
                    self._persist_usage_locked()

    def last_used(self, model_id: str, *, loaded: bool) -> dict[str, Any]:
        with self._usage_lock:
            timestamp = self._usage.get(model_id)
        if loaded:
            return {"timestamp": timestamp, "status": "loaded_now"}
        if timestamp:
            return {"timestamp": timestamp, "status": "recorded"}
        return {"timestamp": None, "status": "never_recorded"}

    def _install_usage_observer(self) -> None:
        if getattr(self.manager, "_storage_usage_observer_installed", False):
            return
        upstream = self.manager.record_request

        def record_request(
            model_id: str,
            prompt_tokens: int,
            completion_tokens: int,
            latency_s: float,
        ) -> None:
            upstream(model_id, prompt_tokens, completion_tokens, latency_s)
            self.mark_model_used(model_id)

        self.manager.record_request = record_request
        self.manager._storage_usage_observer_installed = True

    def cleanup_blocks(self, model_id: str | None = None) -> bool:
        with self._guard_lock:
            return self._global_cleanup or (
                model_id is not None and model_id in self._cleaning_models
            )

    def _install_lifecycle_guard(self) -> None:
        if getattr(self.manager, "_storage_cleanup_guard_installed", False):
            return
        upstream_load = self.manager.schedule_load
        upstream_convert = self.manager.schedule_convert

        def schedule_load(model_id: str, *args: Any, **kwargs: Any):
            if self.cleanup_blocks(model_id):
                raise ValueError(
                    f"Storage cleanup is active for model '{model_id}'. Retry after it finishes."
                )
            return upstream_load(model_id, *args, **kwargs)

        def schedule_convert(model_id: str, *args: Any, **kwargs: Any):
            if self.cleanup_blocks(model_id):
                raise ValueError(
                    f"Storage cleanup is active for model '{model_id}'. Retry after it finishes."
                )
            return upstream_convert(model_id, *args, **kwargs)

        self.manager.schedule_load = schedule_load
        self.manager.schedule_convert = schedule_convert
        upstream_temporary = getattr(self.manager, "build_temporary_engine", None)
        if callable(upstream_temporary):

            async def build_temporary_engine(model_id: str, *args: Any, **kwargs: Any):
                if self.cleanup_blocks(model_id):
                    raise ValueError(
                        f"Storage cleanup is active for model '{model_id}'. "
                        "Retry after it finishes."
                    )
                return await upstream_temporary(model_id, *args, **kwargs)

            self.manager.build_temporary_engine = build_temporary_engine
        self.manager._storage_cleanup_guard_installed = True

    @contextlib.contextmanager
    def cleanup_scope(
        self,
        *,
        model_ids: tuple[str, ...] = (),
        global_cleanup: bool = False,
    ):
        with self._guard_lock:
            if self._global_cleanup or any(
                model_id in self._cleaning_models for model_id in model_ids
            ):
                raise StorageConflict(
                    "cleanup_active",
                    "Another storage cleanup is already active. Wait for it to finish.",
                )
            if global_cleanup and self._cleaning_models:
                raise StorageConflict(
                    "cleanup_active",
                    "Another storage cleanup is already active. Wait for it to finish.",
                )
            self._global_cleanup = global_cleanup
            self._cleaning_models.update(model_ids)
        try:
            yield
        finally:
            with self._guard_lock:
                for model_id in model_ids:
                    self._cleaning_models.discard(model_id)
                if global_cleanup:
                    self._global_cleanup = False


__all__ = ["StorageRuntimeState"]
