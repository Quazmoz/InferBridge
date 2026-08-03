"""Stateful curated model-library service and import/export operations."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app import __version__, model_registry as registry
from app.brand import DISPLAY_NAME
from app.model_library_conversion import (
    conversion_health,
    directory_size_bytes,
    is_reparse_point,
    record_conversion_metadata,
)
from app.model_library_schema import (
    ALLOWED_DEVICES,
    ALLOWED_PROFILES,
    MANIFEST_SCHEMA_VERSION,
    MAX_IMPORTED_DEFINITIONS,
    MAX_MANIFEST_BYTES,
    MODEL_ID_RE,
    ConvertedModelImportRequest,
    ManifestValidationError,
    ModelDefinitionImportRequest,
    catalog_checksum,
    parse_manifest_bytes,
    safe_float,
    utc_now,
)
from app.openai_api import ModelRegisterRequest
from app.paths import packaged_resource_root

OFFICIAL_MANIFEST_URL = (
    "https://github.com/Quazmoz/openvino-windows-llm/"
    "releases/latest/download/model-library-manifest.json"
)
_ALLOWED_RELEASE_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


def definition_to_config(
    definition: dict[str, Any],
    model_path: Path,
    *,
    existing: registry.ModelConfig | None = None,
) -> registry.ModelConfig:
    request = ModelRegisterRequest.model_validate(definition)
    if existing is not None:
        if existing.id != request.model_id:
            raise ValueError("An existing model definition cannot change its model ID.")
        return replace(
            existing,
            name=request.name,
            description=request.description or "",
            backend=request.backend,
            source_model=request.source_model,
            weight_format=request.weight_format,
            recommended_device=request.recommended_device,
            max_context_len=request.max_context_len,
            max_output_tokens=request.max_output_tokens,
            trust_remote_code=request.trust_remote_code,
        )
    return registry.ModelConfig(
        id=request.model_id,
        name=request.name,
        description=request.description or "",
        backend=request.backend,
        model_path=str(model_path.resolve()),
        source_model=request.source_model,
        weight_format=request.weight_format,
        recommended_device=request.recommended_device,
        max_context_len=request.max_context_len,
        max_output_tokens=request.max_output_tokens,
        trust_remote_code=request.trust_remote_code,
    )


def model_definition(cfg: registry.ModelConfig) -> dict[str, Any]:
    return {
        "model_id": cfg.id,
        "name": cfg.name,
        "description": cfg.description,
        "source_model": cfg.source_model,
        "backend": cfg.backend,
        "weight_format": cfg.weight_format,
        "recommended_device": cfg.recommended_device,
        "max_context_len": cfg.max_context_len,
        "max_output_tokens": cfg.max_output_tokens,
        "trust_remote_code": cfg.trust_remote_code,
    }


class ModelLibraryService:
    def __init__(self, settings: Any, manager: Any) -> None:
        self.settings = settings
        self.manager = manager
        self.cache_file = Path(settings.models_file).parent / "model-library-manifest.json"
        self.user_file = Path(settings.models_file).parent / "model-library-user.json"
        self.bundled_file = packaged_resource_root() / "model_library_manifest.json"
        catalog_lock = getattr(manager, "_catalog_lock", None)
        if catalog_lock is None:
            catalog_lock = threading.RLock()
            manager._catalog_lock = catalog_lock
        self._catalog_lock = catalog_lock

    def _read_manifest(self) -> tuple[dict[str, Any], str]:
        for path, source in ((self.cache_file, "official-cache"), (self.bundled_file, "bundled")):
            try:
                return parse_manifest_bytes(path.read_bytes()), source
            except (OSError, ManifestValidationError):
                continue
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "generated_at": "",
            "source": "empty-fallback",
            "catalog": {},
            "catalog_sha256": catalog_checksum({}),
        }, "empty-fallback"

    def _read_user_ids(self) -> set[str]:
        try:
            data = json.loads(self.user_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        values = data.get("model_ids") if isinstance(data, dict) else []
        return {
            value for value in values if isinstance(value, str) and MODEL_ID_RE.fullmatch(value)
        }

    def _write_user_ids(self, values: set[str]) -> None:
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.user_file.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                {"schema_version": 1, "model_ids": sorted(values), "updated_at": utc_now()},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temp.replace(self.user_file)

    def _snapshot_user_file(self) -> str | None:
        """Capture the exact user-index bytes so a failed import can be a true no-op."""

        try:
            return self.user_file.read_text(encoding="utf-8")
        except OSError:
            return None

    def _restore_user_file(self, snapshot: str | None) -> None:
        """Restore the user index to its pre-mutation content, byte for byte.

        Re-serializing on rollback would stamp a fresh ``updated_at`` and leave the
        file changed even though the import failed. Writing the captured snapshot
        keeps a rolled-back import a true no-op on disk.
        """

        try:
            if snapshot is None:
                self.user_file.unlink(missing_ok=True)
                return
            self.user_file.parent.mkdir(parents=True, exist_ok=True)
            temp = self.user_file.with_suffix(".json.tmp")
            temp.write_text(snapshot, encoding="utf-8")
            temp.replace(self.user_file)
        except OSError:
            pass

    def _restore_catalog(self, catalog: dict[str, registry.ModelConfig]) -> None:
        try:
            registry.save_catalog(self.settings.models_file, catalog)
            self.manager.reload_catalog()
        except Exception:
            self.manager.catalog = dict(catalog)
            advisor = getattr(self.manager, "advisor", None)
            if advisor is not None:
                advisor.catalog = self.manager.catalog

    def _is_model_active(self, model_id: str) -> bool:
        if model_id in getattr(self.manager, "engines", {}):
            return True
        for tasks in (
            getattr(self.manager, "load_tasks", {}),
            getattr(self.manager, "convert_tasks", {}),
        ):
            task = tasks.get(model_id)
            if task is not None and not task.done():
                return True
        return False

    async def refresh_official(self) -> dict[str, Any]:
        source_url = OFFICIAL_MANIFEST_URL
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                OFFICIAL_MANIFEST_URL,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"OpenVINO-Windows-LLM/{__version__}",
                },
            ) as response:
                response.raise_for_status()
                source_url = str(response.url)
                host = (urlparse(source_url).hostname or "").lower()
                if host not in _ALLOWED_RELEASE_HOSTS:
                    raise ManifestValidationError(
                        "Official manifest redirected to an untrusted host."
                    )
                content_length = response.headers.get("Content-Length")
                try:
                    declared_length = int(content_length) if content_length else None
                except (TypeError, ValueError):
                    declared_length = None
                if declared_length is not None and declared_length > MAX_MANIFEST_BYTES:
                    raise ManifestValidationError("Model library manifest exceeds the 1 MB limit.")
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_MANIFEST_BYTES:
                        raise ManifestValidationError(
                            "Model library manifest exceeds the 1 MB limit."
                        )
                manifest = parse_manifest_bytes(bytes(payload))

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        try:
            merge = await asyncio.to_thread(self.apply_official_definitions, manifest)
            temp.replace(self.cache_file)
        except Exception:
            # The locked catalog mutation rolls itself back if it fails. If only the
            # cache replacement fails, keep the valid catalog update rather than
            # restoring a stale snapshot that could erase an unrelated registration.
            temp.unlink(missing_ok=True)
            raise
        return {"source": source_url, "manifest": manifest, **merge}

    def apply_official_definitions(self, manifest: dict[str, Any]) -> dict[str, Any]:
        with self._catalog_lock:
            return self._apply_official_definitions_locked(manifest)

    def _apply_official_definitions_locked(self, manifest: dict[str, Any]) -> dict[str, Any]:
        added: list[str] = []
        updated: list[str] = []
        conflicts: list[str] = []
        user_ids = self._read_user_ids()
        staged_catalog = dict(self.manager.catalog)
        for model_id, entry in manifest.get("catalog", {}).items():
            definition = dict(entry["definition"])
            definition["trust_remote_code"] = False
            existing = staged_catalog.get(model_id)
            target = Path(self.settings.models_dir) / model_id
            cfg = definition_to_config(definition, target, existing=existing)
            if existing is not None:
                if model_definition(existing) == model_definition(cfg):
                    continue
                if (
                    self._is_model_active(model_id)
                    or model_id in user_ids
                    or (
                        existing.source_model
                        and existing.source_model != definition["source_model"]
                    )
                ):
                    conflicts.append(model_id)
                    continue
                updated.append(model_id)
            else:
                added.append(model_id)
            staged_catalog[model_id] = cfg
        if added or updated:
            original_catalog = dict(self.manager.catalog)
            try:
                registry.save_catalog(self.settings.models_file, staged_catalog)
                self.manager.reload_catalog()
            except Exception:
                self._restore_catalog(original_catalog)
                raise
        return {"added": added, "updated": updated, "conflicts": conflicts}

    def _local_evidence(self, model_id: str) -> dict[str, dict[str, Any]]:
        advisor = getattr(self.manager, "advisor", None)
        evidence: dict[str, dict[str, Any]] = {}
        if advisor is None:
            return evidence
        snapshot = advisor.hardware_snapshot()
        details = snapshot.get("devices", [])
        for device in ALLOWED_DEVICES:
            row = advisor._latest_benchmark(model_id, device)
            if not row:
                continue
            device_info = next(
                (item for item in details if str(item.get("base") or "").upper() == device),
                {},
            )
            evidence[device] = {
                "status": "locally_verified",
                "actual_device": row.get("actual_device"),
                "openvino_version": snapshot.get("runtime", {}).get("openvino"),
                "driver_version": device_info.get("driver_version") or "Unavailable",
                "load_time_ms": row.get("load_time_ms"),
                "tokens_sec": row.get("tokens_sec"),
                "time_to_first_token_ms": row.get("time_to_first_token_ms"),
                "tested_at": row.get("created_at") or row.get("timestamp"),
            }
        return evidence

    def _verification(self, metadata: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
        output = {}
        certifications = metadata.get("certifications", {})
        if not isinstance(certifications, dict):
            certifications = {}
        for device in ALLOWED_DEVICES:
            records = certifications.get(device)
            official = (
                [record for record in records if isinstance(record, dict)]
                if isinstance(records, list)
                else []
            )
            if device in local:
                output[device] = {
                    "status": "locally_verified",
                    "label": f"Local benchmark: {device}",
                    **local[device],
                }
            elif official:
                latest = max(
                    official,
                    key=lambda record: str(record.get("certified_at") or ""),
                )
                output[device] = {
                    "status": "verified",
                    "label": f"Bundled certification: {device}",
                    **latest,
                }
            else:
                output[device] = {
                    "status": "expected_unverified",
                    "label": f"Expected compatibility: {device}",
                }
        return output

    @staticmethod
    def _evidence_device(
        preferred: str,
        available: set[str],
        local: dict[str, Any],
        verification: dict[str, Any],
    ) -> tuple[str, str]:
        direct = available & {"CPU", "GPU", "NPU"}
        for device in (preferred, "NPU", "GPU", "CPU"):
            if device in direct and device in local:
                return device, f"{device} is recommended from a successful local benchmark."
        for device in (preferred, "GPU", "CPU", "NPU"):
            if device in direct and verification.get(device, {}).get("status") == "verified":
                return device, f"{device} is recommended from bundled certification."
        if preferred in direct:
            return (
                preferred,
                f"{preferred} is an expected compatibility option without verification.",
            )
        if "GPU" in direct and verification.get("GPU", {}).get("status") == "verified":
            return "GPU", "GPU is recommended from bundled certification."
        if "CPU" in direct and verification.get("CPU", {}).get("status") == "verified":
            return "CPU", "CPU is recommended from bundled certification."
        return "CPU", "CPU is the safe fallback when no matching device evidence is available."

    def _quantization(
        self,
        cfg: registry.ModelConfig,
        estimate: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, str]:
        available = {
            str(value).split(".", 1)[0].upper() for value in snapshot.get("available_devices", [])
        }
        ram = safe_float(snapshot.get("memory", {}).get("total_gb"))
        params = safe_float(estimate.get("parameter_count_b"))
        preferred = str(cfg.recommended_device or "CPU").split(":", 1)[0].upper()
        target = preferred if preferred in available else ("GPU" if "GPU" in available else "CPU")
        if target == "NPU" and params <= 4.5:
            return {
                "format": "fp16",
                "device": target,
                "reason": "FP16 is the conservative compatibility choice for the maintained NPU set.",
            }
        if (ram and ram < 12) or params >= 3:
            return {
                "format": "int4",
                "device": target,
                "reason": "INT4 reduces memory and disk pressure for this model and hardware.",
            }
        if target == "GPU" and ram >= 24 and params <= 3:
            return {
                "format": "fp16",
                "device": target,
                "reason": "Available memory supports the higher-fidelity FP16 path.",
            }
        return {
            "format": "int8",
            "device": target,
            "reason": "INT8 is a balanced default when no stronger local evidence is available.",
        }

    def _entry(self, model_id: str, manifest_entry: dict[str, Any] | None) -> dict[str, Any] | None:
        cfg = self.manager.catalog.get(model_id)
        if cfg is None:
            return None
        runtime_entry = self.manager.catalog_entry(model_id)
        advisor = getattr(self.manager, "advisor", None)
        snapshot = (
            advisor.hardware_snapshot()
            if advisor
            else {"memory": {}, "disk": {}, "available_devices": []}
        )
        estimate = advisor.estimate_model(cfg) if advisor else {}
        manifest_definition = (manifest_entry or {}).get("definition")
        manifest_identity_matches = bool(
            not isinstance(manifest_definition, dict)
            or all(
                str(getattr(cfg, field) or "") == str(manifest_definition.get(field) or "")
                for field in ("source_model", "backend", "weight_format")
            )
        )
        metadata = (
            dict((manifest_entry or {}).get("metadata") or {}) if manifest_identity_matches else {}
        )
        local = self._local_evidence(model_id)
        verification = self._verification(metadata, local)
        recommendation = self._quantization(cfg, estimate, snapshot)
        preferred_device = str(cfg.recommended_device or "CPU").split(":", 1)[0].upper()
        available = {
            str(value).split(".", 1)[0].upper() for value in snapshot.get("available_devices", [])
        }
        evidence_device, evidence_reason = self._evidence_device(
            preferred_device, available, local, verification
        )
        recommendation["device"] = evidence_device
        recommendation["reason"] = evidence_reason
        target_device = str(recommendation.get("device") or preferred_device).upper()
        local_device = next(
            (
                device
                for device in (target_device, preferred_device, "NPU", "GPU", "CPU")
                if device in local
            ),
            None,
        )
        measured = local.get(local_device, {}) if local_device else {}
        certifications = metadata.get("certifications", {})
        if not isinstance(certifications, dict):
            certifications = {}
        official_device = next(
            (
                device
                for device in (target_device, preferred_device, "NPU", "GPU", "CPU")
                if isinstance(certifications.get(device), list)
                and any(isinstance(record, dict) for record in certifications[device])
            ),
            None,
        )
        official_records = (
            [
                record
                for record in certifications.get(official_device, [])
                if isinstance(record, dict)
            ]
            if official_device
            else []
        )
        latest_official = max(
            official_records,
            key=lambda record: str(record.get("certified_at") or ""),
            default={},
        )
        local_measurement = any(
            measured.get(field) is not None
            for field in ("load_time_ms", "tokens_sec", "time_to_first_token_ms")
        )

        def metric(field: str) -> Any:
            value = measured.get(field)
            return value if value is not None else latest_official.get(field)

        maximum_tested_context = latest_official.get("max_tested_context")
        if maximum_tested_context is None:
            maximum_tested_context = metadata.get("max_tested_context") or None
        min_ram = safe_float(metadata.get("minimum_ram_gb"))
        min_disk = safe_float(metadata.get("minimum_disk_gb"))
        available_ram = safe_float(snapshot.get("memory", {}).get("available_gb"))
        free_disk = safe_float(snapshot.get("disk", {}).get("free_gb"))
        profiles = list(metadata.get("profiles") or [])
        return {
            "id": model_id,
            "name": cfg.name,
            "description": cfg.description,
            "source_model": cfg.source_model,
            "backend": cfg.backend,
            "weight_format": cfg.weight_format,
            "recommended_device": cfg.recommended_device,
            "max_context_len": cfg.max_context_len,
            "runtime": runtime_entry,
            "conversion_health": conversion_health(cfg),
            "requirements": {
                "minimum_ram_gb": min_ram or estimate.get("runtime_memory_gb"),
                "minimum_disk_gb": min_disk or estimate.get("converted_size_gb"),
                "available_ram_gb": available_ram,
                "free_disk_gb": free_disk,
                "ram_ok": not min_ram or available_ram >= min_ram,
                "disk_ok": not min_disk or free_disk >= min_disk,
            },
            "estimate": estimate,
            "license": metadata.get("license") or "Unknown",
            "gated": bool(metadata.get("gated")),
            "verification": verification,
            "metrics": {
                "time_to_first_load_ms": metric("load_time_ms"),
                "tokens_sec": metric("tokens_sec"),
                "time_to_first_token_ms": metric("time_to_first_token_ms"),
                "maximum_tested_context": maximum_tested_context,
                "tested_openvino_version": (
                    measured.get("openvino_version")
                    if local_measurement
                    else latest_official.get("openvino_version")
                ),
                "tested_driver_version": (
                    measured.get("driver_version")
                    if local_measurement
                    else latest_official.get("driver_version")
                ),
                "last_certification_date": latest_official.get("certified_at") or None,
                "measurement_source": (
                    "local" if local_measurement else "official" if latest_official else None
                ),
                "measurement_device": (
                    local_device
                    if local_measurement
                    else official_device
                    if latest_official
                    else None
                ),
            },
            "profiles": profiles,
            "quality_score": safe_float(metadata.get("quality_score")),
            "speed_score": safe_float(metadata.get("speed_score")),
            "maintainer_note": metadata.get("maintainer_note") or "",
            "recommended_quantization": recommendation,
            "curated": bool(metadata.get("curated", False)),
            "manifest_definition_match": manifest_identity_matches,
        }

    def snapshot(
        self,
        *,
        profile: str = "balanced",
        query: str = "",
        include_all: bool = False,
    ) -> dict[str, Any]:
        if profile not in ALLOWED_PROFILES:
            raise ValueError(f"Unknown model-library profile '{profile}'.")
        manifest, source = self._read_manifest()
        curated = manifest.get("catalog", {})
        selected_ids = set(curated)
        selected_ids.update(self._read_user_ids())
        if include_all:
            selected_ids.update(self.manager.catalog)
        items = [
            entry
            for model_id in selected_ids
            if (entry := self._entry(model_id, curated.get(model_id))) is not None
        ]
        needle = query.strip().lower()
        if needle:
            items = [
                item
                for item in items
                if needle
                in " ".join(
                    str(value or "")
                    for value in (
                        item["id"],
                        item["name"],
                        item["description"],
                        item["source_model"],
                    )
                ).lower()
            ]
        if not include_all:
            profiled = [
                item
                for item in items
                if profile in item.get("profiles", []) or not item.get("curated")
            ]
            if profiled:
                items = profiled

        if profile == "fastest":
            items.sort(
                key=lambda item: (
                    -safe_float(item["metrics"].get("tokens_sec")),
                    -item["speed_score"],
                    safe_float(item["estimate"].get("runtime_memory_gb"), 9999),
                    item["id"],
                )
            )
        elif profile == "best_quality":
            items.sort(
                key=lambda item: (-item["quality_score"], -item["max_context_len"], item["id"])
            )
        elif profile == "lowest_memory":
            items.sort(
                key=lambda item: (
                    safe_float(item["estimate"].get("runtime_memory_gb"), 9999),
                    -item["quality_score"],
                    item["id"],
                )
            )
        else:
            items.sort(
                key=lambda item: (
                    -(item["quality_score"] * 0.55 + item["speed_score"] * 0.45),
                    safe_float(item["estimate"].get("runtime_memory_gb"), 9999),
                    item["id"],
                )
            )
        return {
            "schema_version": 1,
            "profile": profile,
            "manifest": {
                "source": source,
                "generated_at": manifest.get("generated_at"),
                "catalog_sha256": manifest.get("catalog_sha256"),
                "official_url": OFFICIAL_MANIFEST_URL,
            },
            "profiles": ["fastest", "balanced", "best_quality", "lowest_memory"],
            "items": items,
            "count": len(items),
            "include_all": include_all,
            "caveat": (
                "Bundled recommendations are expected-but-unverified until a retained certification "
                "record or local benchmark provides evidence for a specific model and device."
            ),
        }

    def export_definitions(self, *, include_all: bool = False) -> dict[str, Any]:
        manifest, _ = self._read_manifest()
        ids = set(manifest.get("catalog", {})) | self._read_user_ids()
        if include_all:
            ids.update(self.manager.catalog)
        models = {
            model_id: model_definition(self.manager.catalog[model_id])
            for model_id in sorted(ids)
            if model_id in self.manager.catalog
        }
        return {
            "schema_version": 1,
            "application": DISPLAY_NAME,
            "exported_at": utc_now(),
            "models": models,
        }

    def import_definitions(self, request: ModelDefinitionImportRequest) -> dict[str, Any]:
        with self._catalog_lock:
            return self._import_definitions_locked(request)

    def _import_definitions_locked(self, request: ModelDefinitionImportRequest) -> dict[str, Any]:
        payload = request.payload
        raw_models = payload.get("models") if isinstance(payload.get("models"), dict) else payload
        if (
            not isinstance(raw_models, dict)
            or not raw_models
            or len(raw_models) > MAX_IMPORTED_DEFINITIONS
        ):
            raise ValueError("Definition import must contain between 1 and 50 models.")
        added: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        seen_ids: set[str] = set()
        staged_catalog = dict(self.manager.catalog)
        for key, raw in raw_models.items():
            if not isinstance(raw, dict):
                raise ValueError(f"Definition '{key}' must be an object.")
            definition = dict(raw)
            definition.setdefault("model_id", key)
            parsed = ModelRegisterRequest.model_validate(definition)
            if parsed.model_id in seen_ids:
                raise ValueError(
                    f"Definition import contains duplicate model id '{parsed.model_id}'."
                )
            seen_ids.add(parsed.model_id)
            existing = staged_catalog.get(parsed.model_id)
            target = Path(self.settings.models_dir) / parsed.model_id
            candidate = definition_to_config(parsed.model_dump(), target, existing=existing)
            if existing is not None:
                if model_definition(existing) == model_definition(candidate):
                    unchanged.append(parsed.model_id)
                    continue
                if self._is_model_active(parsed.model_id):
                    raise ValueError(f"Model '{parsed.model_id}' is active and cannot be replaced.")
                if not request.overwrite:
                    raise ValueError(
                        f"Model '{parsed.model_id}' already exists with a different definition."
                    )
                updated.append(parsed.model_id)
            else:
                added.append(parsed.model_id)
            staged_catalog[parsed.model_id] = candidate
        previous_user_ids = self._read_user_ids()
        previous_user_snapshot = self._snapshot_user_file()
        user_ids = set(previous_user_ids)
        user_ids.update(added)
        user_ids.update(updated)
        user_ids.update(unchanged)
        if added or updated:
            original_catalog = dict(self.manager.catalog)
            try:
                registry.save_catalog(self.settings.models_file, staged_catalog)
                self._write_user_ids(user_ids)
                self.manager.reload_catalog()
            except Exception:
                self._restore_user_file(previous_user_snapshot)
                self._restore_catalog(original_catalog)
                raise
        else:
            self._write_user_ids(user_ids)
        return {"added": added, "updated": updated, "unchanged": unchanged}

    def import_converted(self, request: ConvertedModelImportRequest) -> dict[str, Any]:
        if request.overwrite:
            raise ValueError(
                "Converted-model replacement is intentionally disabled. Import with a new model ID."
            )
        source_input = Path(request.source_path).expanduser()
        if not source_input.is_absolute():
            raise ValueError("Converted model source_path must be absolute.")
        if is_reparse_point(source_input):
            raise ValueError("Imported model directories may not be symbolic links or junctions.")
        source = source_input.resolve()
        if not source.is_dir() or not registry.is_openvino_model_dir(source):
            raise ValueError("source_path is not a converted OpenVINO model directory.")
        size_bytes = directory_size_bytes(source)
        models_root = Path(self.settings.models_dir)
        models_root.mkdir(parents=True, exist_ok=True)
        models_root = models_root.resolve()
        target = (models_root / request.model_id).resolve()
        if target.parent != models_root:
            raise ValueError("Imported model target escaped the managed model directory.")
        if request.model_id in self.manager.catalog:
            raise ValueError(f"Model ID '{request.model_id}' is already registered.")
        if target.exists() or source == target:
            raise ValueError(f"Managed model directory already exists for '{request.model_id}'.")
        free_bytes = shutil.disk_usage(models_root).free
        if free_bytes < size_bytes + 256 * 1024 * 1024:
            raise ValueError("Not enough free disk space to import this converted model safely.")

        temp = models_root / f".{request.model_id}.import-{secrets.token_hex(6)}"
        previous_user_ids: set[str] = set()
        previous_user_snapshot: str | None = None
        original_catalog: dict[str, registry.ModelConfig] = {}
        state_captured = False
        target_installed = False
        try:
            # Preserve links instead of following them, then reject the copied tree.
            # This closes the race where a link is inserted after source validation.
            shutil.copytree(source, temp, symlinks=True)
            directory_size_bytes(temp)
            if not registry.is_openvino_model_dir(temp):
                raise ValueError("Copied model failed OpenVINO IR validation.")
            with self._catalog_lock:
                # Another request may have completed while this model was being copied.
                if request.model_id in self.manager.catalog:
                    raise ValueError(f"Model ID '{request.model_id}' is already registered.")
                if target.exists():
                    raise ValueError(
                        f"Managed model directory already exists for '{request.model_id}'."
                    )
                previous_user_ids = self._read_user_ids()
                previous_user_snapshot = self._snapshot_user_file()
                original_catalog = dict(self.manager.catalog)
                state_captured = True
                temp.replace(target)
                target_installed = True
                definition = {
                    "model_id": request.model_id,
                    "name": request.name,
                    "description": request.description,
                    "source_model": request.source_model or f"local-openvino:{source.name}",
                    "backend": request.backend,
                    "weight_format": request.weight_format,
                    "recommended_device": request.recommended_device,
                    "max_context_len": request.max_context_len,
                    "max_output_tokens": request.max_output_tokens,
                    "trust_remote_code": False,
                }
                cfg = definition_to_config(definition, target)
                record_conversion_metadata(cfg, self.settings)
                user_ids = set(previous_user_ids)
                user_ids.add(request.model_id)
                self._write_user_ids(user_ids)
                staged_catalog = dict(self.manager.catalog)
                staged_catalog[request.model_id] = cfg
                registry.save_catalog(self.settings.models_file, staged_catalog)
                self.manager.reload_catalog()
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            if target_installed:
                shutil.rmtree(target, ignore_errors=True)
            if state_captured:
                with self._catalog_lock:
                    self._restore_user_file(previous_user_snapshot)
                    self._restore_catalog(original_catalog)
            raise
        return {
            "model_id": request.model_id,
            "target_path": str(target),
            "size_bytes": size_bytes,
            "conversion_health": conversion_health(cfg),
        }
