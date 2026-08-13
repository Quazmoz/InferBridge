"""Failure-safe persistent-data schema marker and migration boundary."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.paths import RuntimePaths
from app.version import DATA_SCHEMA_VERSION, MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION

_SCHEMA_FILE = "data-schema.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_schema(path: Path) -> int:
    if not path.exists():
        return MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        version = int(payload["schema_version"])
    except (LookupError, OSError, TypeError, ValueError) as exc:
        # A truncated, empty, or hand-edited marker reaches the desktop launcher during
        # startup. Every malformed shape must surface the same actionable failure rather
        # than a raw decoding traceback.
        raise RuntimeError("The persistent data schema marker is invalid.") from exc
    if version < 1:
        raise RuntimeError("The persistent data schema marker is invalid.")
    return version


def _backup_small_configuration(paths: RuntimePaths, from_version: int, to_version: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = paths.config_dir / "backups" / f"schema-{from_version}-to-{to_version}-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    for candidate in (paths.models_file, paths.onboarding_file):
        if candidate.is_file() and candidate.stat().st_size <= 8 * 1024 * 1024:
            shutil.copy2(candidate, backup / candidate.name)
    return backup


def ensure_data_schema(paths: RuntimePaths) -> int:
    """Create or migrate the data marker. Repeated calls are idempotent."""

    marker = paths.data_root / _SCHEMA_FILE
    current = _read_schema(marker)
    if current > DATA_SCHEMA_VERSION:
        raise RuntimeError(
            "This application is older than the persistent data schema. Install a compatible newer release or restore a compatible configuration backup."
        )
    if current < MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION:
        raise RuntimeError("The persistent data schema is too old for an automatic upgrade.")
    if current < DATA_SCHEMA_VERSION:
        _backup_small_configuration(paths, current, DATA_SCHEMA_VERSION)
        current = DATA_SCHEMA_VERSION
    _atomic_json(
        marker,
        {
            "schema_version": current,
            "minimum_supported_schema": MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    return current
