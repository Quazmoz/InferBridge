#!/usr/bin/env python3
"""Verify that an immutable release artifact set belongs to one source commit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.release_models import ReleaseManifest, artifact_filename  # noqa: E402
from scripts.release_scan import verify_checksums  # noqa: E402


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is missing or malformed.") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object.")
    return value


def expected_release_filenames(version: str) -> set[str]:
    return {
        artifact_filename(version, kind)
        for kind in ("installer", "portable", "third_party_licenses", "release_notes")
    } | {
        artifact_filename(version, "checksums"),
        artifact_filename(version, "manifest"),
        f"InferBridge-{version}-dependency-inventory.json",
        f"InferBridge-{version}-dependency-freeze.txt",
        f"InferBridge-{version}-release-summary.json",
        f"InferBridge-{version}-release-timings.json",
        "model-library-manifest.json",
    }


def verify_release_provenance(
    artifact_directory: Path,
    *,
    version: str,
    channel: str,
    expected_commit: str,
    source_model_manifest: Path,
) -> None:
    manifest_path = artifact_directory / artifact_filename(version, "manifest")
    summary_path = artifact_directory / (f"InferBridge-{version}-release-summary.json")
    raw_manifest = _object(manifest_path, "Release manifest")
    raw_summary = _object(summary_path, "Release summary")
    try:
        manifest = ReleaseManifest.model_validate(raw_manifest)
    except ValidationError as exc:
        raise RuntimeError("Release manifest is malformed.") from exc

    for label, value in (("manifest", manifest.version), ("summary", raw_summary.get("version"))):
        if value != version:
            raise RuntimeError(f"Release {label} version does not match the requested version.")
    for label, value in (("manifest", manifest.channel), ("summary", raw_summary.get("channel"))):
        if value != channel:
            raise RuntimeError(f"Release {label} channel does not match the requested channel.")
    if manifest.source_commit != raw_summary.get("source_commit"):
        raise RuntimeError("Release manifest and summary source commits differ.")
    if manifest.source_commit != expected_commit:
        raise RuntimeError("Release artifacts were built from a different source commit; rebuild.")
    if manifest.source_tree_clean is not True:
        raise RuntimeError("Release manifest records a dirty source tree; rebuild.")
    if raw_summary.get("source_tree_clean") is not True:
        raise RuntimeError("Release summary records a dirty source tree; rebuild.")

    expected = expected_release_filenames(version)
    existing = {path.name for path in artifact_directory.iterdir() if path.is_file()}
    missing = sorted(expected - existing)
    if missing:
        raise RuntimeError(f"Release artifact set is incomplete: missing {missing[0]}.")
    summary_artifacts = raw_summary.get("artifacts")
    if not isinstance(summary_artifacts, list) or not all(
        isinstance(item, str) for item in summary_artifacts
    ):
        raise RuntimeError("Release summary artifact list is malformed.")
    built_expected = expected - {
        artifact_filename(version, "checksums"),
        f"InferBridge-{version}-release-summary.json",
    }
    if not built_expected.issubset(set(summary_artifacts)):
        raise RuntimeError("Release summary does not record the expected built artifacts.")

    built_library = artifact_directory / "model-library-manifest.json"
    try:
        if built_library.read_bytes() != source_model_manifest.read_bytes():
            raise RuntimeError(
                "Built model-library manifest differs from the source manifest; rebuild."
            )
    except OSError as exc:
        raise RuntimeError("Model-library manifest is missing; rebuild.") from exc
    verify_checksums(artifact_directory / artifact_filename(version, "checksums"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "beta", "nightly"), required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--source-model-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        verify_release_provenance(
            args.artifact_directory,
            version=args.version,
            channel=args.channel,
            expected_commit=args.expected_commit,
            source_model_manifest=args.source_model_manifest,
        )
    except Exception as exc:
        print(f"release provenance validation failed: {exc}", file=sys.stderr)
        return 2
    print("Release provenance verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
