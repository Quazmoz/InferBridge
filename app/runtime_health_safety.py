"""Second-pass safety layer for runtime-health maintenance operations.

The base runtime-health service owns classification and maintenance policy. This
subclass strengthens execution guarantees for the packaged desktop application:
maintenance batches exclude concurrent lifecycle/catalog mutations for their full
duration, device parse failures are returned as controlled conflicts, and persisted
validation evidence is bounded and tied to the complete managed model file tree.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app import model_recovery
from app.model_library_conversion import is_reparse_point
from app.runtime_health import (
    RuntimeHealthActionRequest,
    RuntimeHealthBatchRequest,
    RuntimeHealthService,
)
from app.storage_safety import StorageConflict, _all_lifecycle_idle
from runtime import device_check

logger = logging.getLogger("ov-llm.runtime-health-safety")

_MAX_STATE_BYTES = 1_000_000


class HardenedRuntimeHealthService(RuntimeHealthService):
    """Runtime-health service with an exclusive lifecycle gate around validation."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._maintenance_active = False
        self._maintenance_owner: asyncio.Task[Any] | None = None
        self._install_manager_guard()

    def _install_manager_guard(self) -> None:
        """Block new model/catalog work while one health maintenance batch is active.

        Storage cleanup already prevents lifecycle work while files are being deleted,
        but a runtime-health batch also performs temporary engine loads after deletion.
        Keeping one guard around the whole sequence closes the gap where a normal load,
        conversion, recovery, benchmark, or catalog refresh could otherwise start.
        """

        manager = self.manager
        manager._runtime_health_guard_service = self
        if getattr(manager, "_runtime_health_lifecycle_guard_installed", False):
            return

        upstream_load = manager.schedule_load
        upstream_convert = manager.schedule_convert
        upstream_delete = manager.delete
        upstream_register = getattr(manager, "register_model", None)
        upstream_reload = getattr(manager, "reload_catalog", None)
        upstream_recover = getattr(manager, "recover_model", None)
        upstream_temporary = getattr(manager, "build_temporary_engine", None)

        def service() -> HardenedRuntimeHealthService | None:
            current = getattr(manager, "_runtime_health_guard_service", None)
            return current if isinstance(current, HardenedRuntimeHealthService) else None

        def blocked() -> bool:
            current = service()
            return bool(current is not None and current._maintenance_active)

        def conflict() -> ValueError:
            return ValueError(
                "Runtime model maintenance is active. Retry after revalidation finishes."
            )

        def schedule_load(model_id: str, *args: Any, **kwargs: Any):
            if blocked():
                raise conflict()
            return upstream_load(model_id, *args, **kwargs)

        def schedule_convert(model_id: str, *args: Any, **kwargs: Any):
            if blocked():
                raise conflict()
            return upstream_convert(model_id, *args, **kwargs)

        def delete(model_id: str, *args: Any, **kwargs: Any):
            if blocked():
                raise conflict()
            return upstream_delete(model_id, *args, **kwargs)

        manager.schedule_load = schedule_load
        manager.schedule_convert = schedule_convert
        manager.delete = delete

        if callable(upstream_register):

            def register_model(*args: Any, **kwargs: Any):
                if blocked():
                    raise conflict()
                return upstream_register(*args, **kwargs)

            manager.register_model = register_model

        if callable(upstream_reload):

            def reload_catalog(*args: Any, **kwargs: Any):
                if blocked():
                    raise conflict()
                return upstream_reload(*args, **kwargs)

            manager.reload_catalog = reload_catalog

        if callable(upstream_recover):

            async def recover_model(model_id: str, *args: Any, **kwargs: Any):
                if blocked():
                    raise model_recovery.RecoveryConflict(
                        "maintenance_active",
                        "Runtime model maintenance is active. Retry recovery afterward.",
                    )
                return await upstream_recover(model_id, *args, **kwargs)

            manager.recover_model = recover_model

        if callable(upstream_temporary):

            async def build_temporary_engine(model_id: str, *args: Any, **kwargs: Any):
                current = service()
                if current is not None and current._maintenance_active:
                    owner = current._maintenance_owner
                    if asyncio.current_task() is not owner:
                        raise conflict()
                return await upstream_temporary(model_id, *args, **kwargs)

            manager.build_temporary_engine = build_temporary_engine

        manager._runtime_health_lifecycle_guard_installed = True

    def _storage_temporary_or_mutation_active(self) -> bool:
        state = getattr(self.manager, "_storage_runtime_state", None)
        guard = getattr(state, "_guard_lock", None)
        if state is None or guard is None:
            return False
        with guard:
            return bool(
                getattr(state, "_global_cleanup", False)
                or getattr(state, "_cleaning_models", set())
                or getattr(state, "_mutating_models", set())
                or getattr(state, "_temporary_models", {})
            )

    @asynccontextmanager
    async def _exclusive_maintenance(self):
        """Exclude storage and lifecycle mutations for an entire validation sequence."""

        # Storage cleanup uses this lock for every destructive action. Holding it for
        # the whole maintenance sequence prevents a second cleanup from interleaving
        # between cache deletion and temporary load validation.
        async with self.storage._cleanup_lock:
            if not _all_lifecycle_idle(self.manager) or self._storage_temporary_or_mutation_active():
                raise StorageConflict(
                    "maintenance_conflict",
                    "Unload all models and wait for active model or storage operations to finish.",
                )

            self._maintenance_owner = asyncio.current_task()
            self._maintenance_active = True
            try:
                yield
            finally:
                self._maintenance_active = False
                self._maintenance_owner = None

    def _read_state(self) -> dict[str, Any]:
        """Ignore unsafe or unexpectedly large local maintenance-state files."""

        try:
            if is_reparse_point(self.state_file):
                logger.warning("Ignoring reparse-point runtime health state file")
                return {"schema_version": 1, "models": {}}
            if self.state_file.stat().st_size > _MAX_STATE_BYTES:
                logger.warning("Ignoring oversized runtime health state file")
                return {"schema_version": 1, "models": {}}
        except FileNotFoundError:
            pass
        except OSError:
            return {"schema_version": 1, "models": {}}
        return super()._read_state()

    def _conversion_fingerprint(self, cfg: Any) -> str:
        """Extend provenance evidence across every ordinary file in the model tree.

        The base fingerprint covers the primary OpenVINO IR and configuration. The
        complete metadata walk also invalidates prior validation after tokenizer,
        detokenizer, generation-config, or other converted support files change. File
        contents are not read, so multi-gigabyte weights remain cheap to fingerprint.
        """

        digest = hashlib.sha256(super()._conversion_fingerprint(cfg).encode("ascii"))
        root = Path(cfg.abs_path(model_recovery._base_dir()))
        if not root.is_dir() or is_reparse_point(root):
            digest.update(b"\0unsafe-or-missing-root")
            return digest.hexdigest()

        try:
            for current, directories, files in os.walk(root, topdown=True, followlinks=False):
                current_path = Path(current)
                safe_directories: list[str] = []
                for name in sorted(directories):
                    candidate = current_path / name
                    if is_reparse_point(candidate):
                        relative = candidate.relative_to(root).as_posix()
                        digest.update(f"\0reparse-dir\0{relative}".encode("utf-8"))
                        continue
                    safe_directories.append(name)
                directories[:] = safe_directories

                for name in sorted(files):
                    candidate = current_path / name
                    relative = candidate.relative_to(root).as_posix()
                    if is_reparse_point(candidate):
                        digest.update(f"\0reparse-file\0{relative}".encode("utf-8"))
                        continue
                    try:
                        stat = candidate.stat()
                    except OSError:
                        digest.update(f"\0unreadable\0{relative}".encode("utf-8"))
                        continue
                    if not candidate.is_file():
                        digest.update(f"\0non-file\0{relative}".encode("utf-8"))
                        continue
                    digest.update(f"\0file\0{relative}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8"))
        except OSError:
            digest.update(b"\0walk-failed")
        return digest.hexdigest()

    def _select_device(self, cfg: Any, requested: str | None) -> str:
        try:
            return super()._select_device(cfg, requested)
        except device_check.DeviceValidationError as exc:
            raise StorageConflict(
                "invalid_device",
                "Choose a valid OpenVINO device target before running model maintenance.",
            ) from exc

    async def _rebuild_compiled_cache(
        self,
        model_ids: list[str],
        device: str | None,
    ) -> dict[str, Any]:
        """Clear once and warm selected models under one uninterrupted maintenance gate."""

        async with self._exclusive_maintenance():
            # Preflight every target before deleting the shared cache. A single model
            # that requires reconversion aborts the operation without changing files.
            for model_id in model_ids:
                cfg = self._require_model(model_id)
                self._ensure_revalidation_candidate(cfg)

            # The storage cleanup lock and lifecycle exclusion are already held here.
            # Reuse the storage service's guarded removal implementation directly so
            # the global gate remains active while selected models are warmed.
            cleanup = await self.storage._clear_compiled_cache()
            results: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []
            for model_id in model_ids:
                try:
                    results.append(await self._validate_one(model_id, device))
                except (StorageConflict, KeyError) as exc:
                    failures.append(
                        {
                            "model_id": model_id,
                            "code": getattr(exc, "code", "validation_failed"),
                            "message": str(exc),
                        }
                    )
            return {
                "status": "completed_with_errors" if failures else "completed",
                "action": "rebuild_compiled_cache",
                "freed_bytes": int(cleanup.get("freed_bytes") or 0),
                "validated": results,
                "failures": failures,
            }

    async def perform(self, request: RuntimeHealthActionRequest) -> dict[str, Any]:
        async with self._operation_lock:
            if request.action == "revalidate":
                async with self._exclusive_maintenance():
                    return await self._validate_one(request.model_id, request.device)
            if request.action == "rebuild_compiled_cache":
                return await self._rebuild_compiled_cache([request.model_id], request.device)
            if request.action == "reconvert":
                return await self._schedule_reconvert(request.model_id, request.device)
            return await asyncio.to_thread(self._acknowledge, request.model_id)

    async def perform_batch(self, request: RuntimeHealthBatchRequest) -> dict[str, Any]:
        async with self._operation_lock:
            if request.action == "rebuild_compiled_cache":
                return await self._rebuild_compiled_cache(request.model_ids, request.device)

            async with self._exclusive_maintenance():
                results: list[dict[str, Any]] = []
                failures: list[dict[str, str]] = []
                for model_id in request.model_ids:
                    try:
                        results.append(await self._validate_one(model_id, request.device))
                    except (StorageConflict, KeyError) as exc:
                        failures.append(
                            {
                                "model_id": model_id,
                                "code": getattr(exc, "code", "validation_failed"),
                                "message": str(exc),
                            }
                        )
                return {
                    "status": "completed_with_errors" if failures else "completed",
                    "action": "revalidate",
                    "validated": results,
                    "failures": failures,
                }


__all__ = ["HardenedRuntimeHealthService"]
