"""Publish only the Azure-selected unsigned Windows artifacts from a validated build.

The source release set is validated before this script runs. Its original
manifest/checksums are retained unchanged when publishing the full set. A
separate selection checksum covers the binaries Azure actually publishes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Also support direct 'python scripts/select_azure_artifacts.py' execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_artifact_names import expected_release_filenames  # noqa: E402

MODES = ("installer-portable", "installer", "portable", "msix", "all")
FOUNDATION = "{http://schemas.microsoft.com/appx/manifest/foundation/windows10}"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_msix(path: Path, *, identity: str, publisher: str, version: str) -> None:
    if not zipfile.is_zipfile(path):
        raise ValueError("MSIX output is not a valid package archive")
    with zipfile.ZipFile(path) as package:
        names = {name.replace("\\", "/").lower() for name in package.namelist()}
        for required in (
            "appxmanifest.xml",
            "native/inferbridge.native.exe",
            "native/inferbridge.native.runtimeconfig.json",
            "native/inferbridge.native.deps.json",
            "native/coreclr.dll",
            "inferbridge.exe",
        ):
            if required not in names:
                raise ValueError(f"MSIX is missing the required {required} entry")
        if "appxsignature.p7x" in names:
            raise ValueError("Azure Store MSIX output must remain unsigned")
        if "AppxManifest.xml" not in package.namelist():
            raise ValueError("MSIX is missing AppxManifest.xml")
        root = ET.fromstring(package.read("AppxManifest.xml"))
    element = root.find(f"{FOUNDATION}Identity")
    if element is None:
        raise ValueError("MSIX package identity is missing")
    numeric = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    if numeric is None:
        raise ValueError("Invalid application version for Store MSIX")
    expected_version = ".".join(str(int(part)) for part in numeric.groups()) + ".0"
    if any(int(part) > 65535 for part in numeric.groups()):
        raise ValueError("MSIX version exceeds Windows package limits")
    if (element.get("Name"), element.get("Publisher"), element.get("Version")) != (
        identity,
        publisher,
        expected_version,
    ):
        raise ValueError(
            "MSIX manifest does not match the selected Partner Center identity/version"
        )


def select(
    *,
    release_dir: Path,
    publish_dir: Path,
    version: str,
    mode: str,
    msix_dir: Path | None,
    identity: str,
    publisher: str,
    source_ref: str,
    source_commit: str,
    channel: str,
    pipeline_hash: str,
    run_id: str,
) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"Unsupported Azure artifact type: {mode}")
    if (
        release_dir.resolve() == publish_dir.resolve()
        or release_dir.resolve() in publish_dir.resolve().parents
    ):
        raise ValueError(
            "Published artifacts must be outside the original validated release directory"
        )
    if publish_dir.exists():
        raise FileExistsError("Azure publication directory must not already exist")
    installer = f"InferBridge-{version}-windows-x64-installer.exe"
    portable = f"InferBridge-{version}-windows-x64-portable.zip"
    msix = f"InferBridge-{version}-windows-x64.msix"
    selections = {
        "installer-portable": [installer, portable],
        "installer": [installer],
        "portable": [portable],
        "msix": [msix],
        "all": [installer, portable, msix],
    }
    chosen = selections[mode]
    # Check every requested artifact before creating any publish-directory output.
    sources = []
    for name in chosen:
        directory = msix_dir if name == msix else release_dir
        source = directory / name if directory is not None else None
        if source is None or source.is_symlink() or not source.is_file():
            raise FileNotFoundError(f"Requested {mode} artifact is missing or unsafe: {name}")
        if name == msix:
            verify_msix(source, identity=identity, publisher=publisher, version=version)
        sources.append(source)
    if mode in {"installer-portable", "all"}:
        extra = sorted(path for path in release_dir.iterdir() if path.is_file())
        if any(path.is_symlink() for path in extra):
            raise ValueError("Validated release directory contains symbolic links")
        # The upstream provenance check validates expected files, but does not
        # reject extras. Never publish unreviewed files from staging.
        allowed = expected_release_filenames(version)
        unexpected = sorted(path.name for path in extra if path.name not in allowed)
        if unexpected:
            raise ValueError(
                f"Validated release directory contains unexpected file: {unexpected[0]}"
            )
        sources = extra + ([sources[-1]] if mode == "all" else [])

    publish_dir.mkdir(parents=True)
    for source in sources:
        # Inputs live in the Azure artifact staging root; an atomic filesystem
        # rename avoids redundant multi-GB copies on hosted Windows agents.
        shutil.move(str(source), str(publish_dir / source.name))
    checksums = [f"{_digest(publish_dir / name)}  {name}" for name in chosen]
    (publish_dir / "azure-selected-checksums.txt").write_text(
        "\n".join(checksums) + "\n", encoding="ascii"
    )
    metadata = {
        "schema_version": 1,
        "artifact_type": mode,
        "selected_binaries": chosen,
        "version": version,
        "channel": channel,
        "repository": "https://github.com/Quazmoz/InferBridge",
        "source_ref": source_ref,
        "source_commit": source_commit.lower(),
        "pipeline_config": "azure-pipelines.yml",
        "pipeline_config_sha256": pipeline_hash.lower(),
        "pipeline_run": run_id,
        "unsigned": True,
        "certification": (
            "evaluation only; Store installation, WACK, hardware and installed behavior unverified"
        ),
        "store_submission": (
            "manual Partner Center review required" if msix in chosen else "not applicable"
        ),
    }
    if msix in chosen:
        metadata["msix_identity_name"] = identity
        metadata["msix_publisher"] = publisher
    (publish_dir / "azure-build-provenance.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--publish-dir", type=Path, required=True)
    parser.add_argument("--msix-dir", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact-type", required=True, choices=MODES)
    parser.add_argument("--identity-name", default="")
    parser.add_argument("--publisher", default="")
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--pipeline-hash", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        chosen = select(
            release_dir=args.release_dir,
            publish_dir=args.publish_dir,
            msix_dir=args.msix_dir,
            version=args.version,
            mode=args.artifact_type,
            identity=args.identity_name,
            publisher=args.publisher,
            source_ref=args.source_ref,
            source_commit=args.source_commit,
            channel=args.channel,
            pipeline_hash=args.pipeline_hash,
            run_id=args.run_id,
        )
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        print(f"Azure artifact selection failed: {error}", file=sys.stderr)
        return 2
    print("Selected Azure artifacts: " + ", ".join(chosen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
