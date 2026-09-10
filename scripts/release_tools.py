#!/usr/bin/env python3
"""CLI for deterministic release validation and metadata generation."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Ensure the repository root is importable when this file is invoked directly as
# `python scripts/release_tools.py` (the isolated release venv installs `app` but
# never `scripts`, and script invocation only puts the scripts/ dir on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# `app.release_models` and `scripts.release_manifest` pull in pydantic. CI validates the
# pinned requirements file *before* installing from it, so the stdlib-only commands
# (verify-requirements, scan, verify-native, verify-checksums) must import lazily.
from app.version import __version__  # noqa: E402
from scripts.release_scan import (  # noqa: E402
    scan_release_path,
    verify_checksums,
    verify_native_distribution,
    verify_release_requirements,
    write_checksums as _write_checksums,
)


def write_checksums(output_dir: Path, version: str) -> Path:
    from app.release_models import artifact_filename

    return _write_checksums(output_dir, version, artifact_filename)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("canonical-version")

    item = commands.add_parser("verify-requirements")
    item.add_argument("--path", type=Path, required=True)

    item = commands.add_parser("validate-version")
    item.add_argument("--version", required=True)
    item.add_argument("--channel", choices=("stable", "beta", "nightly"), required=True)

    item = commands.add_parser("verify-version-consistency")
    item.add_argument("--root", type=Path, required=True)
    item.add_argument("--version", required=True)

    item = commands.add_parser("write-version-info")
    item.add_argument("--path", type=Path, required=True)
    item.add_argument("--version", required=True)

    item = commands.add_parser("write-build-info")
    item.add_argument("--path", type=Path, required=True)
    item.add_argument("--version", required=True)
    item.add_argument("--channel", required=True)
    item.add_argument("--commit", required=True)
    item.add_argument("--clean", choices=("true", "false"), required=True)
    item.add_argument("--dependency-inventory", type=Path)

    item = commands.add_parser("verify-native")
    item.add_argument("--path", type=Path, required=True)

    item = commands.add_parser("scan")
    item.add_argument("--path", type=Path, required=True)

    item = commands.add_parser("manifest")
    item.add_argument("--output-dir", type=Path, required=True)
    item.add_argument("--version", required=True)
    item.add_argument("--channel", choices=("stable", "beta", "nightly"), required=True)
    item.add_argument("--published-at", required=True)
    item.add_argument("--commit", required=True)
    item.add_argument("--clean", choices=("true", "false"), required=True)
    item.add_argument("--signed-types", default="")
    item.add_argument("--inventory-filename", required=True)

    item = commands.add_parser("checksums")
    item.add_argument("--output-dir", type=Path, required=True)
    item.add_argument("--version", required=True)

    item = commands.add_parser("verify-checksums")
    item.add_argument("--path", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "canonical-version":
            print(__version__)
        elif args.command == "verify-requirements":
            verify_release_requirements(args.path)
        elif args.command == "validate-version":
            from scripts.release_manifest import validate_version

            print(validate_version(args.version, args.channel))
        elif args.command == "verify-version-consistency":
            from scripts.release_manifest import verify_version_consistency

            verify_version_consistency(args.root, args.version)
        elif args.command == "write-version-info":
            from scripts.release_manifest import write_version_info

            write_version_info(args.path, args.version)
        elif args.command == "write-build-info":
            from scripts.release_manifest import write_build_info

            write_build_info(
                args.path,
                version=args.version,
                channel=args.channel,
                commit=args.commit,
                clean=args.clean == "true",
                inventory=args.dependency_inventory,
            )
        elif args.command == "verify-native":
            verify_native_distribution(args.path)
        elif args.command == "scan":
            scan_release_path(args.path)
        elif args.command == "checksums":
            print(write_checksums(args.output_dir, args.version))
        elif args.command == "verify-checksums":
            verify_checksums(args.path)
        elif args.command == "manifest":
            from app.release_models import ReleaseManifest, artifact_filename
            from scripts.release_manifest import build_manifest

            value = build_manifest(
                args.output_dir,
                version=args.version,
                channel=args.channel,
                published_at=datetime.fromisoformat(args.published_at.replace("Z", "+00:00")),
                commit=args.commit,
                clean=args.clean == "true",
                signed_types={value for value in args.signed_types.split(",") if value},
                inventory_filename=args.inventory_filename,
            )
            path = args.output_dir / artifact_filename(args.version, "manifest")
            path.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")
            ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
            print(path)
        return 0
    except Exception as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
