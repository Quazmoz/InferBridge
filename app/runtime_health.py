"""Runtime upgrade and converted-model maintenance workflow.

This service turns existing conversion provenance into explicit maintenance actions.
It never mutates conversion metadata during validation. Successful validation is stored
separately so an older conversion can be proven against the current runtime without
rewriting the version that originally produced the OpenVINO IR.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app import __version__, model_load_safety, model_recovery
from app.local_request_security import (
    matches_any_secret,
    require_safe_browser_origin,
)
from app.model_library_conversion import conversion_health, conversion_marker_path
from app.model_library_schema import package_version
from app.storage_manager import StorageCleanupRequest, StorageManagerService
from app.storage_safety import StorageConflict, _all_lifecycle_idle, _model_activity
from runtime import device_check

logger = logging.getLogger("ov-llm.runtime-health")

_RUNTIME_HEALTH_SCHEMA_VERSION = 1
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HARD_RECONVERT_STATES = frozenset({"incomplete", "invalid_metadata", "incompatible_definition"})
MaintenanceAction = Literal[
    "revalidate",
    "rebuild_compiled_cache",
    "reconvert",
    "leave_unchanged",
]
BatchMaintenanceAction = Literal["revalidate", "rebuild_compiled_cache"]


class RuntimeHealthActionRequest(BaseModel):
    action: MaintenanceAction
    model_id: str = Field(min_length=1, max_length=128, pattern=_MODEL_ID_RE.pattern)
    device: str | None = Field(default=None, min_length=1, max_length=160)


class RuntimeHealthBatchRequest(BaseModel):
    action: BatchMaintenanceAction
    model_ids: list[str] = Field(min_length=1, max_length=128)
    device: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("model_ids")
    @classmethod
    def validate_model_ids(cls, values: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not _MODEL_ID_RE.fullmatch(value):
                raise ValueError(
                    "Every model_id must be filesystem-safe and at most 128 characters."
                )
            if value not in seen:
                unique.append(value)
                seen.add(value)
        return unique


def current_runtime_versions() -> dict[str, str | None]:
    """Return the local runtime versions relevant to model compatibility."""

    return {
        "application": __version__,
        "openvino": package_version("openvino"),
        "openvino_genai": package_version("openvino-genai"),
    }


def _reconvert_label(source_cache_reusable: bool) -> str:
    return "Reconvert from existing HF cache" if source_cache_reusable else "Reconvert"


def _reconvert_reason(prefix: str, source_cache_reusable: bool) -> str:
    cache = (
        "The reusable Hugging Face source cache is already available."
        if source_cache_reusable
        else "The source may need to be downloaded again."
    )
    return f"{prefix} {cache}"


def maintenance_recommendation(
    health_status: str,
    *,
    validation_current: bool = False,
    validation_failed: bool = False,
    acknowledged_current: bool = False,
    source_cache_reusable: bool = False,
) -> dict[str, Any]:
    """Map conversion provenance to one conservative maintenance recommendation."""

    # Hard artifact/definition failures always win over historical validation evidence.
    # A previously valid model may have been edited, truncated, or redefined afterward.
    if health_status in _HARD_RECONVERT_STATES:
        return {
            "action": "reconvert",
            "label": _reconvert_label(source_cache_reusable),
            "reason": _reconvert_reason(
                "The converted artifact or its definition cannot be trusted as current.",
                source_cache_reusable,
            ),
            "safe_batch": False,
        }
    if acknowledged_current and health_status in {"legacy_untracked", "stale_runtime"}:
        return {
            "action": "leave_unchanged",
            "label": "Leave unchanged",
            "reason": "This warning was explicitly acknowledged for the current OpenVINO runtime.",
            "safe_batch": False,
        }
    if validation_current:
        return {
            "action": "leave_unchanged",
            "label": "Validated for current runtime",
            "reason": (
                "A local load validation succeeded for this exact conversion and OpenVINO runtime."
            ),
            "safe_batch": False,
        }
    if health_status == "legacy_untracked":
        if validation_failed:
            return {
                "action": "reconvert",
                "label": _reconvert_label(source_cache_reusable),
                "reason": _reconvert_reason(
                    "The legacy artifact failed validation under the current runtime.",
                    source_cache_reusable,
                ),
                "safe_batch": False,
            }
        return {
            "action": "revalidate",
            "label": "Revalidate",
            "reason": (
                "This conversion predates provenance metadata. Validate it without rewriting it."
            ),
            "safe_batch": True,
        }
    if health_status == "stale_runtime":
        if validation_failed:
            return {
                "action": "reconvert",
                "label": _reconvert_label(source_cache_reusable),
                "reason": _reconvert_reason(
                    "The artifact still failed after a current-runtime maintenance attempt.",
                    source_cache_reusable,
                ),
                "safe_batch": False,
            }
        return {
            "action": "rebuild_compiled_cache",
            "label": "Rebuild compiled cache",
            "reason": (
                "OpenVINO changed since conversion. Rebuild the shared compiled cache, then "
                "load-validate this model with the current runtime."
            ),
            "safe_batch": True,
        }
    return {
        "action": "leave_unchanged",
        "label": "Leave unchanged",
        "reason": (
            "No converted artifact is present yet."
            if health_status == "not_converted"
            else "Conversion metadata matches the current OpenVINO runtime."
        ),
        "safe_batch": False,
    }


class RuntimeHealthService:
    """Inspect and safely maintain converted models after runtime upgrades."""

    def __init__(
        self,
        *,
        settings: Any,
        manager: Any,
        paths: Any,
        storage: StorageManagerService,
    ) -> None:
        self.settings = settings
        self.manager = manager
        self.paths = paths
        self.storage = storage
        self.state_file = Path(paths.config_dir) / "runtime-health.json"
        self._operation_lock = asyncio.Lock()

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": _RUNTIME_HEALTH_SCHEMA_VERSION, "models": {}}
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != _RUNTIME_HEALTH_SCHEMA_VERSION
        ):
            return {"schema_version": _RUNTIME_HEALTH_SCHEMA_VERSION, "models": {}}
        models = value.get("models")
        if not isinstance(models, dict):
            models = {}
        return {"schema_version": _RUNTIME_HEALTH_SCHEMA_VERSION, "models": models}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.state_file)

    def _require_model(self, model_id: str) -> Any:
        cfg = self.manager.catalog.get(model_id)
        if cfg is None:
            raise KeyError(model_id)
        return cfg

    def _conversion_fingerprint(self, cfg: Any) -> str:
        """Fingerprint provenance plus bounded artifact metadata.

        Large BIN weights are never hashed. File names, sizes, and nanosecond mtimes are
        enough to invalidate local maintenance evidence after normal file replacement,
        while keeping snapshots inexpensive even for multi-gigabyte models.
        """

        digest = hashlib.sha256()
        digest.update(cfg.id.encode("utf-8"))
        for value in (cfg.source_model, cfg.backend, cfg.weight_format):
            digest.update(b"\0")
            digest.update(str(value or "").encode("utf-8"))

        marker = conversion_marker_path(cfg)
        try:
            digest.update(b"\0marker\0")
            digest.update(marker.read_bytes())
        except OSError:
            digest.update(b"\0marker-missing")

        model_dir = cfg.abs_path(model_recovery._base_dir())
        found = False
        for filename in (
            "openvino_model.xml",
            "openvino_model.bin",
            "openvino_language_model.xml",
            "openvino_language_model.bin",
            "config.json",
        ):
            candidate = model_dir / filename
            try:
                stat = candidate.stat()
            except OSError:
                continue
            if not candidate.is_file():
                continue
            found = True
            digest.update(b"\0artifact\0")
            digest.update(filename.encode("utf-8"))
            digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}".encode("ascii"))
        if not found:
            digest.update(b"\0artifacts-missing")
        return digest.hexdigest()

    def _recorded_runtime(self, cfg: Any) -> dict[str, Any]:
        try:
            value = json.loads(conversion_marker_path(cfg).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            "application": value.get("application_version"),
            "openvino": value.get("openvino_version"),
            "openvino_genai": value.get("openvino_genai_version"),
            "recorded_at": value.get("recorded_at"),
        }

    def _source_cache_state(self, cfg: Any) -> str:
        try:
            return model_recovery._download_state(cfg)
        except Exception:
            return "unknown"

    @staticmethod
    def _runtime_matches(record: dict[str, Any], runtime: dict[str, Any]) -> bool:
        """Tie maintenance evidence to the inference runtime, not app patch releases."""

        recorded = record.get("runtime")
        return isinstance(recorded, dict) and all(
            recorded.get(key) == runtime.get(key) for key in ("openvino", "openvino_genai")
        )

    def _record_matches(
        self,
        record: dict[str, Any] | None,
        *,
        runtime: dict[str, Any],
        conversion_fingerprint: str,
        status: str | None = None,
    ) -> bool:
        if not isinstance(record, dict):
            return False
        if status is not None and record.get("status") != status:
            return False
        return bool(
            record.get("conversion_fingerprint") == conversion_fingerprint
            and self._runtime_matches(record, runtime)
        )

    def _action_capability(
        self,
        action: str,
        *,
        model_id: str,
        health_status: str,
        all_idle: bool,
    ) -> tuple[bool, str]:
        activity = _model_activity(self.manager, model_id)
        if action == "leave_unchanged":
            return True, ""
        if activity["preparing"]:
            return False, "Wait for model preparation to finish."
        if activity["loaded"]:
            return False, "Unload the model before maintenance."
        if action in {"revalidate", "rebuild_compiled_cache"} and not all_idle:
            return False, "Unload all models and wait for active operations before maintenance."
        if action in {"revalidate", "rebuild_compiled_cache"} and health_status in {
            "not_converted",
            "incomplete",
        }:
            return False, "A complete converted model is required before runtime validation."
        if action == "reconvert" and not self.manager.catalog[model_id].source_model:
            return False, "No Hugging Face source model is configured."
        return True, ""

    def _snapshot_sync(self) -> dict[str, Any]:
        runtime = current_runtime_versions()
        state = self._read_state()
        records = state.get("models", {})
        all_idle = _all_lifecycle_idle(self.manager)
        rows: list[dict[str, Any]] = []
        counts = {
            "revalidate": 0,
            "rebuild_compiled_cache": 0,
            "reconvert": 0,
            "leave_unchanged": 0,
        }

        for model_id, cfg in self.manager.catalog.items():
            health = conversion_health(cfg)
            health_status = str(health.get("status") or "not_converted")
            conversion_fingerprint = self._conversion_fingerprint(cfg)
            model_state = records.get(model_id) if isinstance(records, dict) else None
            model_state = model_state if isinstance(model_state, dict) else {}
            validation = model_state.get("validation")
            acknowledgment = model_state.get("acknowledgment")
            validation_current = self._record_matches(
                validation,
                runtime=runtime,
                conversion_fingerprint=conversion_fingerprint,
                status="passed",
            )
            validation_failed = self._record_matches(
                validation,
                runtime=runtime,
                conversion_fingerprint=conversion_fingerprint,
                status="failed",
            )
            acknowledged_current = self._record_matches(
                acknowledgment,
                runtime=runtime,
                conversion_fingerprint=conversion_fingerprint,
            )
            source_cache_state = self._source_cache_state(cfg)
            recommendation = maintenance_recommendation(
                health_status,
                validation_current=validation_current,
                validation_failed=validation_failed,
                acknowledged_current=acknowledged_current,
                source_cache_reusable=source_cache_state == "reusable",
            )
            available, blocked_reason = self._action_capability(
                recommendation["action"],
                model_id=model_id,
                health_status=health_status,
                all_idle=all_idle,
            )
            recommendation = {
                **recommendation,
                "available": available,
                "blocked_reason": blocked_reason,
            }
            counts[recommendation["action"]] += 1
            activity = _model_activity(self.manager, model_id)
            can_acknowledge = health_status in {"legacy_untracked", "stale_runtime"}
            rows.append(
                {
                    "model_id": model_id,
                    "name": cfg.name,
                    "conversion_health": health,
                    "recorded_runtime": self._recorded_runtime(cfg),
                    "current_runtime": runtime,
                    "source_cache": source_cache_state,
                    "loaded": activity["loaded"],
                    "preparing": activity["preparing"],
                    "last_validation": validation if isinstance(validation, dict) else None,
                    "acknowledged_current_runtime": acknowledged_current,
                    "can_leave_unchanged": can_acknowledge,
                    "recommendation": recommendation,
                }
            )

        attention = counts["revalidate"] + counts["rebuild_compiled_cache"] + counts["reconvert"]
        unresolved_runtime_changes = sum(
            1
            for row in rows
            if row["conversion_health"].get("status") == "stale_runtime"
            and row["recommendation"]["action"] != "leave_unchanged"
        )
        return {
            "schema_version": _RUNTIME_HEALTH_SCHEMA_VERSION,
            "generated_at": int(time.time()),
            "runtime": runtime,
            "summary": {
                "models": len(rows),
                "needs_attention": attention,
                **counts,
                "runtime_change_detected": any(
                    row["conversion_health"].get("status") == "stale_runtime" for row in rows
                ),
                "unresolved_runtime_changes": unresolved_runtime_changes,
                "all_models_idle": all_idle,
            },
            "models": sorted(
                rows,
                key=lambda row: (
                    row["recommendation"]["action"] == "leave_unchanged",
                    row["recommendation"]["action"],
                    row["name"],
                ),
            ),
            "shared_compiled_cache": {
                "scope": "shared",
                "requires_all_models_idle": True,
                "note": (
                    "OpenVINO currently uses one shared compiled-cache root. Rebuild maintenance "
                    "clears it once, then warms only the selected models. Other models compile "
                    "again on their next load."
                ),
            },
            "notes": {
                "conversion_provenance_rewritten": False,
                "batch_reconversion_supported": False,
                "paths_exposed": False,
            },
        }

    async def snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._snapshot_sync)

    def _select_device(self, cfg: Any, requested: str | None) -> str:
        target = device_check.normalize_device(
            requested or cfg.recommended_device or self.settings.device
        )
        if self.manager.force_mock:
            return target
        available = device_check.available_devices()
        if device_check.is_device_available(target, available):
            return target
        if device_check.is_device_available("CPU", available):
            return "CPU"
        raise StorageConflict(
            "device_unavailable",
            "The recommended validation device is unavailable and no CPU fallback was detected.",
        )

    def _write_model_record(self, model_id: str, key: str, value: dict[str, Any]) -> None:
        state = self._read_state()
        models = state.setdefault("models", {})
        record = models.setdefault(model_id, {})
        record[key] = value
        self._write_state(state)

    def _ensure_revalidation_candidate(self, cfg: Any) -> dict[str, Any]:
        """Reject states that require file replacement before any cache mutation."""

        health = conversion_health(cfg)
        health_status = str(health.get("status") or "not_converted")
        if health_status in {"not_converted", "incomplete"}:
            raise StorageConflict(
                "model_incomplete",
                "A complete converted model is required before runtime validation.",
            )
        if health_status in {"invalid_metadata", "incompatible_definition"}:
            raise StorageConflict(
                "reconversion_required",
                "This model requires reconversion because its conversion metadata cannot be trusted.",
            )
        return health

    async def _validate_one(self, model_id: str, device: str | None) -> dict[str, Any]:
        cfg = self._require_model(model_id)
        if not _all_lifecycle_idle(self.manager):
            raise StorageConflict(
                "models_active",
                "Unload all models and wait for active operations before runtime validation.",
            )
        self._ensure_revalidation_candidate(cfg)
        requested_device = self._select_device(cfg, device)
        runtime = current_runtime_versions()
        fingerprint = self._conversion_fingerprint(cfg)
        started = time.perf_counter()
        engine = None
        actual_device = requested_device
        try:
            engine, load_seconds = await self.manager.build_temporary_engine(
                model_id, requested_device
            )
            actual_device = str(getattr(engine, "device", requested_device) or requested_device)
        except Exception as exc:  # noqa: BLE001 - native details stay in server logs
            logger.exception("Runtime validation failed for '%s' on %s", model_id, requested_device)
            self._write_model_record(
                model_id,
                "validation",
                {
                    "status": "failed",
                    "runtime": runtime,
                    "conversion_fingerprint": fingerprint,
                    "requested_device": requested_device,
                    "device": actual_device,
                    "validated_at": int(time.time()),
                    "error_code": "load_validation_failed",
                },
            )
            raise StorageConflict(
                "validation_failed",
                "The model did not load successfully with the current runtime. Review server logs for the request ID.",
            ) from exc
        finally:
            if engine is not None:
                try:
                    await asyncio.to_thread(engine.close)
                except Exception:  # noqa: BLE001 - validation result is already known
                    logger.warning("Could not close temporary validation engine for '%s'", model_id)
        elapsed = max(load_seconds, time.perf_counter() - started)
        record = {
            "status": "passed",
            "runtime": runtime,
            "conversion_fingerprint": fingerprint,
            "requested_device": requested_device,
            "device": actual_device,
            "validated_at": int(time.time()),
            "load_time_ms": round(elapsed * 1000.0, 1),
        }
        self._write_model_record(model_id, "validation", record)
        self.manager.emit_event(
            "info", f"Validated {cfg.name} with the current OpenVINO runtime on {actual_device}"
        )
        return {
            "model_id": model_id,
            "status": "validated",
            "device": actual_device,
            "requested_device": requested_device,
            "load_time_ms": record["load_time_ms"],
        }

    async def _rebuild_compiled_cache(
        self,
        model_ids: list[str],
        device: str | None,
    ) -> dict[str, Any]:
        if not _all_lifecycle_idle(self.manager):
            raise StorageConflict(
                "models_active",
                "Unload all models and wait for active operations before rebuilding compiled cache.",
            )

        # Preflight every selected model before the destructive shared-cache cleanup.
        # If any target requires reconversion, nothing is deleted.
        for model_id in model_ids:
            cfg = self._require_model(model_id)
            self._ensure_revalidation_candidate(cfg)

        cleanup = await self.storage.cleanup(StorageCleanupRequest(action="clear_compiled_cache"))
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

    async def _schedule_reconvert(self, model_id: str, device: str | None) -> dict[str, Any]:
        cfg = self._require_model(model_id)
        activity = _model_activity(self.manager, model_id)
        if activity["preparing"]:
            raise StorageConflict(
                "operation_active",
                "Wait for the active model operation to finish before reconverting.",
            )
        if activity["loaded"]:
            raise StorageConflict("model_loaded", "Unload the model before reconverting it.")
        if not cfg.source_model:
            raise StorageConflict(
                "source_unavailable", "No Hugging Face source model is configured."
            )
        target = self._select_device(cfg, device)
        profile = model_load_safety._read_profile(cfg, model_recovery._base_dir()) or {}
        try:
            task = self.manager.schedule_convert(
                model_id,
                target,
                load_after=False,
                weight_format=cfg.weight_format,
                group_size=profile.get("group_size"),
                ratio=profile.get("ratio"),
                sym=profile.get("symmetric"),
            )
        except ValueError as exc:
            raise StorageConflict(
                "conversion_conflict",
                (
                    "The reconversion conflicts with the current model lifecycle. "
                    "Retry after active operations finish."
                ),
            ) from exc
        if task is None:
            raise StorageConflict(
                "conversion_not_scheduled",
                "The reconversion could not be scheduled in the current model state.",
            )
        state = self._read_state()
        models = state.setdefault("models", {})
        models.pop(model_id, None)
        self._write_state(state)
        self.manager.emit_event("info", f"Queued runtime-maintenance reconversion for {cfg.name}")
        return {
            "status": "scheduled",
            "action": "reconvert",
            "model_id": model_id,
            "source_cache": self._source_cache_state(cfg),
        }

    def _acknowledge(self, model_id: str) -> dict[str, Any]:
        cfg = self._require_model(model_id)
        health = conversion_health(cfg)
        if health.get("status") not in {"legacy_untracked", "stale_runtime"}:
            raise StorageConflict(
                "acknowledgment_unavailable",
                "Only legacy or runtime-change warnings can be explicitly left unchanged.",
            )
        record = {
            "status": "acknowledged",
            "runtime": current_runtime_versions(),
            "conversion_fingerprint": self._conversion_fingerprint(cfg),
            "acknowledged_at": int(time.time()),
        }
        self._write_model_record(model_id, "acknowledgment", record)
        return {"status": "acknowledged", "action": "leave_unchanged", "model_id": model_id}

    async def perform(self, request: RuntimeHealthActionRequest) -> dict[str, Any]:
        async with self._operation_lock:
            if request.action == "revalidate":
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
            if not _all_lifecycle_idle(self.manager):
                raise StorageConflict(
                    "models_active",
                    "Unload all models and wait for active operations before batch validation.",
                )
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


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="Unknown model.")
    if isinstance(exc, StorageConflict):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(
        status_code=500,
        detail={
            "code": "maintenance_failed",
            "message": "Runtime maintenance failed; see server logs for the request ID.",
        },
    )


def register_runtime_health_routes(app: FastAPI, *, service: RuntimeHealthService) -> None:
    """Register desktop-only runtime health endpoints."""

    if getattr(app.state, "runtime_health_routes_installed", False):
        return
    app.state.runtime_health_routes_installed = True
    auth = [Depends(_require_access)]

    @app.get("/v1/runtime-health", dependencies=auth)
    async def runtime_health_snapshot():
        try:
            return await service.snapshot()
        except Exception as exc:  # noqa: BLE001 - sanitize local runtime/filesystem details
            logger.exception("Runtime health snapshot failed")
            raise _http_error(exc) from exc

    @app.post("/v1/runtime-health/action", dependencies=auth)
    async def runtime_health_action(request: RuntimeHealthActionRequest):
        try:
            return await service.perform(request)
        except Exception as exc:  # noqa: BLE001 - sanitize local runtime/filesystem details
            if not isinstance(exc, (KeyError, StorageConflict)):
                logger.exception("Runtime health action failed")
            raise _http_error(exc) from exc

    @app.post("/v1/runtime-health/batch", dependencies=auth)
    async def runtime_health_batch(request: RuntimeHealthBatchRequest):
        try:
            return await service.perform_batch(request)
        except Exception as exc:  # noqa: BLE001 - sanitize local runtime/filesystem details
            if not isinstance(exc, (KeyError, StorageConflict)):
                logger.exception("Runtime health batch failed")
            raise _http_error(exc) from exc


__all__ = [
    "RuntimeHealthActionRequest",
    "RuntimeHealthBatchRequest",
    "RuntimeHealthService",
    "current_runtime_versions",
    "maintenance_recommendation",
    "register_runtime_health_routes",
]
