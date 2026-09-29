"""Schemas and validation for curated model-library manifests and imports."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.openai_api import ModelRegisterRequest
from runtime import device_check

MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1_000_000
MAX_IMPORTED_DEFINITIONS = 50
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_PROFILES = {"fastest", "balanced", "best_quality", "lowest_memory"}
ALLOWED_DEVICES = ("CPU", "GPU", "NPU")


class ModelDefinitionImportRequest(BaseModel):
    payload: dict[str, Any]
    overwrite: bool = False


class ConvertedModelImportRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=128, pattern=MODEL_ID_RE.pattern)
    name: str = Field(min_length=1, max_length=160)
    source_path: str = Field(min_length=1, max_length=1024)
    source_model: str = Field(default="", max_length=240)
    description: str = Field(default="Imported converted OpenVINO model.", max_length=2000)
    backend: str = Field(
        default="openvino-genai",
        pattern=r"^(openvino-genai|openvino-embeddings|openvino-vlm)$",
    )
    weight_format: str = Field(default="fp16", pattern=r"^(int4|int8|fp16)$")
    recommended_device: str = Field(default="CPU", min_length=1, max_length=64)
    max_context_len: int = Field(default=2048, ge=128, le=262144)
    max_output_tokens: int = Field(default=512, ge=0, le=65536)
    overwrite: bool = False

    @field_validator("recommended_device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        return device_check.validate_device_expression(value)


class ManagedModelAdoptRequest(BaseModel):
    directory_name: str = Field(min_length=1, max_length=255)
    model_id: str = Field(min_length=1, max_length=128, pattern=MODEL_ID_RE.pattern)
    name: str = Field(min_length=1, max_length=160)
    source_model: str = Field(default="", max_length=240)
    description: str = Field(default="Adopted pre-converted OpenVINO model.", max_length=2000)
    backend: str = Field(
        default="openvino-genai",
        pattern=r"^(openvino-genai|openvino-embeddings|openvino-vlm)$",
    )
    weight_format: str = Field(default="fp16", pattern=r"^(int4|int8|fp16)$")
    recommended_device: str = Field(default="CPU", min_length=1, max_length=64)
    max_context_len: int = Field(default=2048, ge=128, le=262144)
    max_output_tokens: int = Field(default=512, ge=0, le=65536)

    @field_validator("recommended_device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        return device_check.validate_device_expression(value)


class ManifestValidationError(ValueError):
    """Raised when a remote or cached model-library manifest is invalid."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def catalog_checksum(catalog: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(catalog)).hexdigest()


def safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def optional_nonnegative_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(parsed, 0.0) if math.isfinite(parsed) else None


def optional_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return None


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def major_minor(version: str | None) -> tuple[int, int] | None:
    if not version:
        return None
    match = re.match(r"^(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _normalize_certifications(raw: Any) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {device: [] for device in ALLOWED_DEVICES}
    if not isinstance(raw, dict):
        return output
    for device in ALLOWED_DEVICES:
        records = raw.get(device)
        if not isinstance(records, list):
            continue
        for record in records[:20]:
            if not isinstance(record, dict) or record.get("status") != "verified":
                continue
            certified_at = str(record.get("certified_at") or "")[:32]
            openvino_version = str(record.get("openvino_version") or "")[:64]
            driver_value = record.get("driver_version")
            driver_version = str(driver_value)[:128] if driver_value not in (None, "") else None
            requested_device = str(record.get("requested_device") or "")[:64]
            actual_device = str(record.get("actual_device") or "")[:64]
            report_reference = str(record.get("report_reference") or "")[:500]
            report_sha256 = str(record.get("report_sha256") or "").lower()
            load_time_ms = optional_nonnegative_float(record.get("load_time_ms"))
            tokens_sec = optional_nonnegative_float(record.get("tokens_sec"))
            time_to_first_token_ms = optional_nonnegative_float(
                record.get("time_to_first_token_ms")
            )
            max_tested_context = optional_nonnegative_int(record.get("max_tested_context"))
            if (
                not certified_at
                or not openvino_version
                or requested_device != device
                or actual_device != device
                or not report_reference
                or not re.fullmatch(r"[0-9a-f]{64}", report_sha256)
                or max_tested_context is None
                or max_tested_context <= 0
            ):
                continue
            normalized = {
                "status": "verified",
                "certified_at": certified_at,
                "openvino_version": openvino_version,
                "openvino_genai_version": str(record.get("openvino_genai_version") or "")[:64],
                "driver_version": driver_version,
                "requested_device": requested_device,
                "actual_device": actual_device,
                "max_tested_context": max_tested_context,
                "report_reference": report_reference,
                "report_sha256": report_sha256,
            }
            for field, value in (
                ("load_time_ms", load_time_ms),
                ("tokens_sec", tokens_sec),
                ("time_to_first_token_ms", time_to_first_token_ms),
            ):
                if value is not None:
                    normalized[field] = value
            output[device].append(normalized)
        output[device].sort(key=lambda item: item["certified_at"])
    return output


def validate_manifest_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ManifestValidationError("Model library manifest must be a JSON object.")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestValidationError("Unsupported model library manifest schema.")
    catalog = document.get("catalog")
    if not isinstance(catalog, dict) or not catalog or len(catalog) > MAX_IMPORTED_DEFINITIONS:
        raise ManifestValidationError("Manifest catalog must contain between 1 and 50 models.")
    expected = str(document.get("catalog_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ManifestValidationError("Manifest catalog checksum is missing or malformed.")
    actual = catalog_checksum(catalog)
    if not secrets.compare_digest(actual, expected):
        raise ManifestValidationError("Manifest catalog checksum verification failed.")

    normalized: dict[str, Any] = {}
    for model_id, raw in catalog.items():
        if not isinstance(model_id, str) or not MODEL_ID_RE.fullmatch(model_id):
            raise ManifestValidationError(f"Unsafe model id in manifest: {model_id!r}.")
        if not isinstance(raw, dict):
            raise ManifestValidationError(f"Manifest entry '{model_id}' must be an object.")
        definition_value = raw.get("definition")
        if not isinstance(definition_value, dict):
            raise ManifestValidationError(
                f"Manifest definition for '{model_id}' must be an object."
            )
        definition_raw = dict(definition_value)
        definition_raw["model_id"] = model_id
        try:
            definition = ModelRegisterRequest.model_validate(definition_raw)
        except Exception as exc:
            raise ManifestValidationError(
                f"Manifest definition for '{model_id}' is invalid."
            ) from exc
        definition_data = definition.model_dump()
        definition_data["trust_remote_code"] = False
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        profiles = [
            value
            for value in metadata.get("profiles", [])
            if isinstance(value, str) and value in ALLOWED_PROFILES
        ]
        max_tested_context = min(
            max(safe_int(metadata.get("max_tested_context")), 0),
            definition.max_context_len,
        )
        certifications = _normalize_certifications(metadata.get("certifications"))
        for records in certifications.values():
            for record in records:
                record["max_tested_context"] = min(
                    record["max_tested_context"], definition.max_context_len
                )
        normalized[model_id] = {
            "definition": definition_data,
            "metadata": {
                "curated": bool(metadata.get("curated", True)),
                "profiles": list(dict.fromkeys(profiles)),
                "minimum_ram_gb": max(safe_float(metadata.get("minimum_ram_gb")), 0.0),
                "minimum_disk_gb": max(safe_float(metadata.get("minimum_disk_gb")), 0.0),
                "license": str(metadata.get("license") or "Unknown")[:120],
                "gated": bool(metadata.get("gated", False)),
                "quality_score": min(max(safe_float(metadata.get("quality_score")), 0.0), 100.0),
                "speed_score": min(max(safe_float(metadata.get("speed_score")), 0.0), 100.0),
                "max_tested_context": max_tested_context,
                "maintainer_note": str(metadata.get("maintainer_note") or "")[:500],
                "certifications": certifications,
            },
        }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": str(document.get("generated_at") or "")[:40],
        "source": str(document.get("source") or "")[:200],
        "catalog": normalized,
        "catalog_sha256": catalog_checksum(normalized),
    }


def parse_manifest_bytes(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ManifestValidationError("Model library manifest exceeds the 1 MB limit.")
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestValidationError("Model library manifest is not valid UTF-8 JSON.") from exc
    return validate_manifest_document(document)
