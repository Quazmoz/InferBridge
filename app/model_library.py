"""Public API for the curated, evidence-aware model library."""

from __future__ import annotations

import httpx

from app import model_registry as registry
from app.model_library_conversion import (
    CONVERSION_SCHEMA_VERSION,
    conversion_health,
    conversion_marker_path as _conversion_marker_path,
    directory_size_bytes as _directory_size_bytes,
    is_reparse_point as _is_reparse_point,
    record_conversion_metadata,
)
from app.model_library_policy import ModelLibraryService
from app.model_library_schema import (
    ALLOWED_DEVICES as _ALLOWED_DEVICES,
    ALLOWED_PROFILES as _ALLOWED_PROFILES,
    MANIFEST_SCHEMA_VERSION,
    MAX_IMPORTED_DEFINITIONS,
    MAX_MANIFEST_BYTES,
    MODEL_ID_RE as _MODEL_ID_RE,
    ConvertedModelImportRequest,
    ManifestValidationError,
    ModelDefinitionImportRequest,
    canonical_json_bytes as _canonical_json_bytes,
    catalog_checksum,
    major_minor as _major_minor,
    optional_nonnegative_float as _optional_nonnegative_float,
    optional_nonnegative_int as _optional_nonnegative_int,
    package_version as _package_version,
    parse_manifest_bytes,
    safe_float as _safe_float,
    safe_int as _safe_int,
    utc_now as _utc_now,
    validate_manifest_document,
)
from app.model_library_service import (
    OFFICIAL_MANIFEST_URL,
    definition_to_config as _definition_to_config,
    model_definition,
)

__all__ = [
    "CONVERSION_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_IMPORTED_DEFINITIONS",
    "MAX_MANIFEST_BYTES",
    "OFFICIAL_MANIFEST_URL",
    "ConvertedModelImportRequest",
    "ManifestValidationError",
    "ModelDefinitionImportRequest",
    "ModelLibraryService",
    "_ALLOWED_DEVICES",
    "_ALLOWED_PROFILES",
    "_MODEL_ID_RE",
    "_canonical_json_bytes",
    "_conversion_marker_path",
    "_definition_to_config",
    "_directory_size_bytes",
    "_is_reparse_point",
    "_major_minor",
    "_optional_nonnegative_float",
    "_optional_nonnegative_int",
    "_package_version",
    "_safe_float",
    "_safe_int",
    "_utc_now",
    "catalog_checksum",
    "conversion_health",
    "httpx",
    "model_definition",
    "parse_manifest_bytes",
    "record_conversion_metadata",
    "registry",
    "validate_manifest_document",
]
