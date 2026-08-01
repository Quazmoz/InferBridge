"""Fingerprint and validate reusable InferBridge release environments."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_EXACT_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==(?P<version>[^\s;]+)"
    r"(?:\s*;\s*(?P<marker>.+))?$"
)


def requirements_sha256(path: Path) -> str:
    """Return the exact SHA-256 digest of the pinned requirements file."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_identity() -> dict[str, Any]:
    """Return stable interpreter and platform fields that affect a Windows venv."""

    return {
        "implementation": sys.implementation.name,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "cache_tag": sys.implementation.cache_tag or "python",
        "platform_system": platform.system().lower() or "unknown",
        "machine": platform.machine().lower() or "unknown",
        "pointer_bits": struct.calcsize("P") * 8,
    }


def environment_key(requirements: Path) -> str:
    """Build a filesystem-safe key for one reusable release environment."""

    identity = runtime_identity()
    machine = re.sub(r"[^a-z0-9_.-]+", "-", str(identity["machine"]))
    system = re.sub(r"[^a-z0-9_.-]+", "-", str(identity["platform_system"]))
    cache_tag = re.sub(r"[^a-z0-9_.-]+", "-", str(identity["cache_tag"]).lower())
    python_version = re.sub(r"[^a-z0-9_.-]+", "-", str(identity["python_version"]).lower())
    digest = requirements_sha256(requirements)[:20]
    return (
        f"v{_SCHEMA_VERSION}-{cache_tag}-py{python_version}-{system}-{machine}-"
        f"{identity['pointer_bits']}-{digest}"
    )


def _marker_applies(marker: str | None) -> bool:
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ImportError:
        match = re.fullmatch(
            r"platform_system\s*==\s*['\"](?P<value>[^'\"]+)['\"]",
            marker.strip(),
        )
        if not match:
            raise RuntimeError(f"Unsupported requirement marker without packaging: {marker}")
        return platform.system() == match.group("value")
    return bool(Marker(marker).evaluate())


def _versions_match(installed: str, expected: str) -> bool:
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return installed == expected
    try:
        return Version(installed) == Version(expected)
    except InvalidVersion:
        return installed == expected


def exact_requirements(path: Path) -> dict[str, str]:
    """Return applicable top-level distribution pins from a release requirements file."""

    pins: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _EXACT_REQUIREMENT_RE.fullmatch(line)
        if not match:
            raise RuntimeError(f"Unsupported release requirement at {path.name}:{number}: {line}")
        if not _marker_applies(match.group("marker")):
            continue
        name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
        pins[name] = match.group("version")
    return pins


def metadata_payload(requirements: Path) -> dict[str, Any]:
    """Return non-secret metadata describing the current reusable environment."""

    return {
        "schema_version": _SCHEMA_VERSION,
        "environment_key": environment_key(requirements),
        "requirements_sha256": requirements_sha256(requirements),
        **runtime_identity(),
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


def write_metadata(metadata_path: Path, requirements: Path) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata_payload(requirements), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validation_errors(metadata_path: Path, requirements: Path) -> list[str]:
    """Return validation errors for the current interpreter environment."""

    errors: list[str] = []
    expected = metadata_payload(requirements)
    try:
        stored = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"release environment metadata is unavailable: {exc}"]
    if not isinstance(stored, dict):
        return ["release environment metadata must be a JSON object"]

    for field in (
        "schema_version",
        "environment_key",
        "requirements_sha256",
        "implementation",
        "python_version",
        "cache_tag",
        "platform_system",
        "machine",
        "pointer_bits",
    ):
        if stored.get(field) != expected.get(field):
            errors.append(f"release environment metadata mismatch: {field}")

    for distribution, expected_version in exact_requirements(requirements).items():
        try:
            installed = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"missing pinned distribution: {distribution}=={expected_version}")
            continue
        if not _versions_match(installed, expected_version):
            errors.append(
                f"pinned distribution mismatch: {distribution}=={installed} "
                f"(expected {expected_version})"
            )
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fingerprint = subparsers.add_parser("fingerprint")
    fingerprint.add_argument("--requirements", type=Path, required=True)

    write = subparsers.add_parser("write-metadata")
    write.add_argument("--requirements", type=Path, required=True)
    write.add_argument("--metadata", type=Path, required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--requirements", type=Path, required=True)
    validate.add_argument("--metadata", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "fingerprint":
        print(environment_key(args.requirements))
        return 0
    if args.command == "write-metadata":
        write_metadata(args.metadata, args.requirements)
        return 0

    errors = validation_errors(args.metadata, args.requirements)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Reusable release environment validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
