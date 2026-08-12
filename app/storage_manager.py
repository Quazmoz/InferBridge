"""Privacy-safe storage inventory and cleanup for the InferBridge desktop app."""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app import model_recovery
from app.local_request_security import require_safe_browser_origin
from app.model_library_conversion import conversion_health
from app.storage_safety import (
    StorageConflict,
    TreeMeasurement,
    _all_lifecycle_idle,
    _measure_tree,
    _model_activity,
    _path_exists,
    _remove_tree,
    cleanup_capability,
)
from app.storage_state import StorageRuntimeState
from runtime.model_output_transaction import model_output_transaction_paths

_STORAGE_SCHEMA_VERSION = 1
_MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
CleanupAction = Literal[
    "delete_converted_model",
    "remove_huggingface_cache",
    "remove_incomplete_data",
    "clear_compiled_cache",
]


class StorageCleanupRequest(BaseModel):
    action: CleanupAction
    model_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_MODEL_ID_PATTERN,
    )

    @model_validator(mode="after")
    def validate_target(self) -> StorageCleanupRequest:
        requires_model = self.action != "clear_compiled_cache"
        if requires_model and not self.model_id:
            raise ValueError(f"Action '{self.action}' requires model_id.")
        if not requires_model and self.model_id is not None:
            raise ValueError("clear_compiled_cache does not accept model_id.")
        return self


class StorageManagerService:
    """Inspect and clean only writable storage roots managed by InferBridge."""

    def __init__(self, *, settings: Any, manager: Any, paths: Any) -> None:
        self.settings = settings
        self.manager = manager
        self.paths = paths
        self._cleanup_lock = asyncio.Lock()
        self._state = StorageRuntimeState(
            manager=manager,
            usage_file=Path(paths.config_dir) / "storage-usage.json",
        )

    def _cleanup_scope(
        self,
        *,
        model_ids: tuple[str, ...] = (),
        global_cleanup: bool = False,
    ):
        return self._state.cleanup_scope(
            model_ids=model_ids,
            global_cleanup=global_cleanup,
        )

    def _source_cache_path(self, source_model: str) -> Path | None:
        """Return the lexical cache path after the existing source identifier validation."""

        if model_recovery._source_cache_path(source_model) is None:
            return None
        root = model_recovery._hub_cache_root()
        return root / f"models--{source_model.replace('/', '--')}"

    def _safe_model_measurement(self, model_id: str) -> tuple[Path, TreeMeasurement, bool]:
        cfg = self.manager.catalog[model_id]
        model_dir = cfg.abs_path(model_recovery._base_dir())
        unsafe = False
        ensure_within = getattr(self.manager, "_ensure_within_models_dir", None)
        if callable(ensure_within):
            try:
                ensure_within(model_dir)
            except ValueError:
                unsafe = True
        measurement = (
            TreeMeasurement(present=_path_exists(model_dir), unsafe=True)
            if unsafe
            else _measure_tree(model_dir, root=Path(self.settings.models_dir))
        )
        return model_dir, measurement, unsafe

    def _model_row(
        self,
        model_id: str,
        cfg: Any,
        *,
        activity: dict[str, bool],
        measurement: TreeMeasurement,
        unsafe_model: bool,
    ) -> tuple[dict[str, Any], int]:
        health = (
            {
                "status": "unsafe_path",
                "label": "Unsafe managed path",
                "details": (
                    "The configured model path is outside managed storage or uses a symbolic "
                    "link or Windows junction."
                ),
            }
            if unsafe_model or measurement.unsafe
            else conversion_health(cfg)
        )
        complete = health.get("status") not in {"not_converted", "incomplete", "unsafe_path"}
        converted_size = measurement.size_bytes if complete else 0
        cleanup = cleanup_capability(
            reclaimable_bytes=converted_size,
            unsafe=measurement.unsafe,
            unreadable=measurement.unreadable,
            loaded=activity["loaded"],
            preparing=activity["preparing"],
        )
        return (
            {
                "model_id": model_id,
                "name": cfg.name,
                "state": "converted" if complete else health.get("status", "not_converted"),
                "converted_size_bytes": converted_size,
                "conversion_health": health,
                "last_used": self._state.last_used(model_id, loaded=activity["loaded"]),
                **activity,
                "cleanup": {"action": "delete_converted_model", **cleanup},
            },
            converted_size if cleanup["available"] else 0,
        )

    def _recovery_row(
        self,
        model_id: str,
        cfg: Any,
        *,
        activity: dict[str, bool],
        model_dir: Path,
        model_measurement: TreeMeasurement,
        health_status: str,
        models_root: Path,
        recovery_root: Path,
    ) -> tuple[dict[str, Any] | None, int, int, int]:
        staging_dir, backup_dir = model_output_transaction_paths(model_dir)
        staging = _measure_tree(staging_dir, root=models_root)
        backup = _measure_tree(backup_dir, root=models_root)
        record = _measure_tree(
            recovery_root / f"{model_id}.json",
            root=recovery_root,
            allow_file=True,
        )
        incomplete = model_measurement if health_status == "incomplete" else TreeMeasurement(False)
        reclaimable_size = incomplete.size_bytes + staging.size_bytes + record.size_bytes
        protected_size = backup.size_bytes
        inspected = (incomplete, staging, backup, record)
        if not (
            reclaimable_size
            or protected_size
            or any(item.unsafe or item.unreadable for item in inspected)
        ):
            return None, 0, 0, 0

        unsafe = any(item.unsafe for item in (incomplete, staging, record))
        unreadable = any(item.unreadable for item in (incomplete, staging, record))
        cleanup = cleanup_capability(
            reclaimable_bytes=reclaimable_size,
            unsafe=unsafe,
            unreadable=unreadable,
            loaded=activity["loaded"],
            preparing=activity["preparing"],
            protected=reclaimable_size == 0 and protected_size > 0,
        )
        row = {
            "model_id": model_id,
            "name": cfg.name,
            "state": (
                "interrupted_preparation"
                if incomplete.present or staging.present
                else "recovery_metadata"
                if record.present
                else "protected_transaction_backup"
            ),
            "size_bytes": reclaimable_size,
            "protected_backup_bytes": protected_size,
            "parts": {
                "incomplete_output_bytes": incomplete.size_bytes,
                "staging_bytes": staging.size_bytes,
                "recovery_record_bytes": record.size_bytes,
            },
            **activity,
            "cleanup": {"action": "remove_incomplete_data", **cleanup},
        }
        reclaimable = reclaimable_size if cleanup["available"] else 0
        return row, reclaimable_size, protected_size, reclaimable

    def _source_cache_rows(
        self,
        groups: dict[str, dict[str, Any]],
        *,
        hub_root: Path,
    ) -> tuple[list[dict[str, Any]], int, int]:
        rows: list[dict[str, Any]] = []
        total = 0
        reclaimable = 0
        for group in groups.values():
            measurement = _measure_tree(group["path"], root=hub_root)
            total += measurement.size_bytes
            cleanup = cleanup_capability(
                reclaimable_bytes=measurement.size_bytes,
                unsafe=measurement.unsafe,
                unreadable=measurement.unreadable,
                preparing=group["preparing"],
            )
            if cleanup["available"]:
                reclaimable += measurement.size_bytes
            rows.append(
                {
                    "source_model": group["source_model"],
                    "model_ids": sorted(group["model_ids"]),
                    "model_names": sorted(group["model_names"]),
                    "size_bytes": measurement.size_bytes,
                    "state": "reusable" if measurement.present else "not_found",
                    "shared": len(group["model_ids"]) > 1,
                    "cleanup": {
                        "action": "remove_huggingface_cache",
                        "model_id": sorted(group["model_ids"])[0],
                        **cleanup,
                    },
                }
            )
        rows.sort(key=lambda item: (-item["size_bytes"], item["source_model"]))
        return rows, total, reclaimable

    def _snapshot_sync(self) -> dict[str, Any]:
        models_root = Path(self.settings.models_dir)
        recovery_root = models_root / ".inferbridge-recovery"
        hub_root = model_recovery._hub_cache_root()
        all_idle = _all_lifecycle_idle(self.manager)

        models: list[dict[str, Any]] = []
        recovery_items: list[dict[str, Any]] = []
        cache_groups: dict[str, dict[str, Any]] = {}
        converted_total = converted_reclaimable = 0
        recovery_total = recovery_reclaimable = protected_backup_total = 0

        for model_id, cfg in self.manager.catalog.items():
            activity = _model_activity(self.manager, model_id)
            model_dir, measurement, unsafe_model = self._safe_model_measurement(model_id)
            model_row, model_reclaimable = self._model_row(
                model_id,
                cfg,
                activity=activity,
                measurement=measurement,
                unsafe_model=unsafe_model,
            )
            models.append(model_row)
            converted_total += model_row["converted_size_bytes"]
            converted_reclaimable += model_reclaimable

            if cfg.source_model:
                cache_path = self._source_cache_path(cfg.source_model)
                if cache_path is not None:
                    key = os.path.normcase(str(cache_path))
                    group = cache_groups.setdefault(
                        key,
                        {
                            "path": cache_path,
                            "source_model": cfg.source_model,
                            "model_ids": [],
                            "model_names": [],
                            "preparing": False,
                        },
                    )
                    group["model_ids"].append(model_id)
                    group["model_names"].append(cfg.name)
                    group["preparing"] = group["preparing"] or activity["preparing"]

            recovery_row, recovery_size, protected_size, reclaimable_size = self._recovery_row(
                model_id,
                cfg,
                activity=activity,
                model_dir=model_dir,
                model_measurement=measurement,
                health_status=model_row["conversion_health"].get("status", ""),
                models_root=models_root,
                recovery_root=recovery_root,
            )
            if recovery_row is not None:
                recovery_items.append(recovery_row)
            recovery_total += recovery_size
            protected_backup_total += protected_size
            recovery_reclaimable += reclaimable_size

        source_caches, hf_total, hf_reclaimable = self._source_cache_rows(
            cache_groups,
            hub_root=hub_root,
        )
        compiled = _measure_tree(Path(self.settings.cache_dir), root=Path(self.settings.cache_dir))
        compiled_cleanup = cleanup_capability(
            reclaimable_bytes=compiled.size_bytes,
            unsafe=compiled.unsafe,
            unreadable=compiled.unreadable,
            require_all_idle=True,
            all_idle=all_idle,
        )
        compiled_reclaimable = compiled.size_bytes if compiled_cleanup["available"] else 0
        reclaimable = (
            converted_reclaimable + hf_reclaimable + recovery_reclaimable + compiled_reclaimable
        )
        total_managed = (
            converted_total
            + hf_total
            + recovery_total
            + protected_backup_total
            + compiled.size_bytes
        )
        return {
            "schema_version": _STORAGE_SCHEMA_VERSION,
            "generated_at": int(time.time()),
            "totals": {
                "converted_models_bytes": converted_total,
                "huggingface_cache_bytes": hf_total,
                "incomplete_recovery_bytes": recovery_total,
                "protected_transaction_backup_bytes": protected_backup_total,
                "compiled_cache_bytes": compiled.size_bytes,
                "managed_storage_bytes": total_managed,
                "currently_reclaimable_bytes": reclaimable,
            },
            "models": sorted(
                models,
                key=lambda item: (-item["converted_size_bytes"], item["name"]),
            ),
            "source_caches": source_caches,
            "recovery_items": sorted(
                recovery_items,
                key=lambda item: (
                    -(item["size_bytes"] + item["protected_backup_bytes"]),
                    item["name"],
                ),
            ),
            "compiled_cache": {
                "size_bytes": compiled.size_bytes,
                "state": "available" if compiled.present else "empty",
                "cleanup": {"action": "clear_compiled_cache", **compiled_cleanup},
            },
            "notes": {
                "paths_exposed": False,
                "transaction_backups_protected": True,
                "last_used_definition": (
                    "Last successful generation recorded by this installation; loaded models "
                    "are shown as active even before their first generation."
                ),
            },
        }

    async def snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._snapshot_sync)

    def _require_model(self, model_id: str | None) -> Any:
        if not model_id or model_id not in self.manager.catalog:
            raise KeyError(model_id or "")
        return self.manager.catalog[model_id]

    def _reject_model_activity(self, model_id: str, *, loaded: bool) -> None:
        activity = _model_activity(self.manager, model_id)
        if activity["preparing"]:
            raise StorageConflict(
                "operation_active",
                "Wait for model loading or conversion to finish before cleaning its files.",
            )
        if loaded and activity["loaded"]:
            raise StorageConflict(
                "model_loaded",
                "Unload the model before removing its managed files.",
            )

    async def _delete_model(self, model_id: str) -> dict[str, Any]:
        self._require_model(model_id)
        self._reject_model_activity(model_id, loaded=True)
        _model_dir, measurement, unsafe_model = self._safe_model_measurement(model_id)
        if unsafe_model or measurement.unsafe:
            raise StorageConflict(
                "unsafe_path",
                (
                    "InferBridge refused to remove this model through an unsafe managed "
                    "path. Remove the symbolic link or Windows junction manually."
                ),
            )
        if measurement.unreadable:
            raise StorageConflict(
                "storage_unreadable",
                "InferBridge could not inspect all model files safely. Close programs using them.",
            )
        try:
            result = await asyncio.to_thread(self._state.delete_model, model_id)
        except ValueError as exc:
            text = str(exc).lower()
            code = (
                "unsafe_path"
                if "symlink" in text or "junction" in text or "outside" in text
                else "cleanup_conflict"
            )
            raise StorageConflict(
                code,
                "InferBridge refused to remove this model because its state is not safe.",
            ) from exc
        except OSError as exc:
            raise StorageConflict(
                "cleanup_failed",
                (
                    "InferBridge could not remove the converted model. Close programs using "
                    "the model files, then retry."
                ),
            ) from exc
        self._state.clear_model_usage(model_id)
        return {
            "freed_bytes": int(result.get("freed_bytes") or 0),
            "message": "Removed the converted model files.",
        }

    async def _remove_source_cache(self, model_id: str) -> dict[str, Any]:
        cfg = self._require_model(model_id)
        related_ids = [
            related_id
            for related_id, related_cfg in self.manager.catalog.items()
            if related_cfg.source_model and related_cfg.source_model == cfg.source_model
        ]
        for related_id in related_ids:
            self._reject_model_activity(related_id, loaded=False)
        cache_path = self._source_cache_path(cfg.source_model)
        if cache_path is None:
            raise StorageConflict(
                "cache_unavailable",
                "The reusable source cache cannot be identified safely for this model.",
            )
        freed = await asyncio.to_thread(
            _remove_tree,
            cache_path,
            root=model_recovery._hub_cache_root(),
            description="the reusable Hugging Face source cache",
        )
        return {
            "freed_bytes": freed,
            "message": (
                "Removed the reusable Hugging Face source cache. "
                "A future conversion will download it again."
            ),
        }

    async def _remove_incomplete_data(self, model_id: str) -> dict[str, Any]:
        cfg = self._require_model(model_id)
        self._reject_model_activity(model_id, loaded=True)
        models_root = Path(self.settings.models_dir)
        model_dir, model_measurement, unsafe_model = self._safe_model_measurement(model_id)
        if unsafe_model or model_measurement.unsafe:
            raise StorageConflict(
                "unsafe_path",
                (
                    "InferBridge refused to inspect this model through an unsafe managed "
                    "path. Remove the symbolic link or Windows junction manually."
                ),
            )
        staging_dir, _backup_dir = model_output_transaction_paths(model_dir)
        record_path = models_root / ".inferbridge-recovery" / f"{model_id}.json"
        health = conversion_health(cfg)
        measurements = (
            model_measurement if health.get("status") == "incomplete" else TreeMeasurement(False),
            _measure_tree(staging_dir, root=models_root),
            _measure_tree(record_path, root=record_path.parent, allow_file=True),
        )
        if any(measurement.unsafe for measurement in measurements):
            raise StorageConflict(
                "unsafe_path",
                (
                    "InferBridge refused to remove incomplete preparation data through "
                    "a symbolic link or Windows junction."
                ),
            )
        if any(measurement.unreadable for measurement in measurements):
            raise StorageConflict(
                "storage_unreadable",
                "InferBridge could not inspect all incomplete preparation files safely.",
            )
        before = sum(measurement.size_bytes for measurement in measurements)
        if health.get("status") == "incomplete":
            try:
                await asyncio.to_thread(
                    model_recovery._remove_incomplete_output,
                    self.manager,
                    cfg,
                )
            except model_recovery.RecoveryConflict as exc:
                raise StorageConflict(exc.code, str(exc)) from exc
        else:
            await asyncio.to_thread(
                _remove_tree,
                staging_dir,
                root=models_root,
                description="the incomplete staged OpenVINO files",
            )
        await asyncio.to_thread(
            _remove_tree,
            record_path,
            root=record_path.parent,
            description="the model recovery metadata",
            allow_file=True,
        )
        records = getattr(self.manager, "_model_recovery_records", None)
        if isinstance(records, dict):
            records.pop(model_id, None)
        return {
            "freed_bytes": before,
            "message": "Removed incomplete preparation files and their recovery metadata.",
        }

    async def _clear_compiled_cache(self) -> dict[str, Any]:
        if not _all_lifecycle_idle(self.manager):
            raise StorageConflict(
                "models_active",
                (
                    "Unload all models and wait for active operations to finish before clearing "
                    "the compiled cache."
                ),
            )
        cache_dir = Path(self.settings.cache_dir)
        freed = await asyncio.to_thread(
            _remove_tree,
            cache_dir,
            root=cache_dir,
            description="the OpenVINO compiled cache",
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        return {
            "freed_bytes": freed,
            "message": (
                "Cleared the OpenVINO compiled cache. Future model loads may take longer once."
            ),
        }

    async def cleanup(self, request: StorageCleanupRequest) -> dict[str, Any]:
        async with self._cleanup_lock:
            model_id = request.model_id or ""
            if request.action == "remove_huggingface_cache":
                cfg = self._require_model(model_id)
                related_ids = tuple(
                    related_id
                    for related_id, related_cfg in self.manager.catalog.items()
                    if related_cfg.source_model and related_cfg.source_model == cfg.source_model
                )
            elif request.action == "clear_compiled_cache":
                related_ids = ()
            else:
                related_ids = (model_id,)

            with self._state.cleanup_scope(
                model_ids=related_ids,
                global_cleanup=request.action
                in {"clear_compiled_cache", "remove_huggingface_cache"},
            ):
                if request.action == "delete_converted_model":
                    result = await self._delete_model(model_id)
                elif request.action == "remove_huggingface_cache":
                    result = await self._remove_source_cache(model_id)
                elif request.action == "remove_incomplete_data":
                    result = await self._remove_incomplete_data(model_id)
                else:
                    result = await self._clear_compiled_cache()
        return {
            "status": "completed",
            "action": request.action,
            "model_id": request.model_id,
            **result,
        }


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


def _require_local_ui(request: Request) -> None:
    if request.headers.get("X-OV-LLM-UI") != "1":
        raise HTTPException(status_code=403, detail="This cleanup action requires the local UI.")


def register_storage_manager_routes(
    app: FastAPI,
    *,
    service: StorageManagerService,
) -> None:
    """Register desktop-only inventory and cleanup routes."""

    if getattr(app.state, "storage_manager_routes_registered", False):
        return

    @app.get(
        "/v1/storage",
        dependencies=[Depends(_require_access)],
        tags=["desktop"],
        include_in_schema=False,
    )
    async def storage_snapshot():
        return JSONResponse(
            await service.snapshot(),
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/v1/storage/cleanup",
        dependencies=[Depends(_require_access), Depends(_require_local_ui)],
        tags=["desktop"],
        include_in_schema=False,
    )
    async def storage_cleanup(body: StorageCleanupRequest):
        try:
            return await service.cleanup(body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown model identifier.") from exc
        except StorageConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc)[:300]},
            ) from exc

    app.state.storage_manager_service = service
    app.state.storage_manager_routes_registered = True


__all__ = [
    "StorageCleanupRequest",
    "StorageConflict",
    "StorageManagerService",
    "_measure_tree",
    "register_storage_manager_routes",
]
