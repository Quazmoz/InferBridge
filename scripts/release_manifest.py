"""Build version resources, build metadata, and validated release manifests."""

from __future__ import annotations

import importlib.metadata
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from app.brand import LEGACY_REPOSITORY_NAME, REPOSITORY_NAME, REPOSITORY_OWNER
from app.build_info import BuildInfo
from app.release_models import (
    ApiCompatibility,
    DataCompatibility,
    OpenVinoCompatibility,
    ReleaseArtifact,
    ReleaseManifest,
    SemanticVersion,
    artifact_filename,
)
from app.version import DATA_SCHEMA_VERSION, MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION, __version__
from scripts.release_scan import sha256_file

_ALLOWED_RELEASE_REPOSITORIES = {
    f"{REPOSITORY_OWNER}/{REPOSITORY_NAME}",
    f"{REPOSITORY_OWNER}/{LEGACY_REPOSITORY_NAME}",
}
_DEFAULT_RELEASE_REPOSITORY = f"{REPOSITORY_OWNER}/{REPOSITORY_NAME}"


def release_repository(environment: dict[str, str] | None = None) -> str:
    values = os.environ if environment is None else environment
    configured = str(values.get("OV_LLM_RELEASE_REPOSITORY") or "").strip()
    automatic = str(values.get("GITHUB_REPOSITORY") or "").strip()
    selected = configured or automatic or _DEFAULT_RELEASE_REPOSITORY
    if selected not in _ALLOWED_RELEASE_REPOSITORIES:
        raise ValueError("Release repository must be an approved InferBridge repository.")
    return selected


def validate_version(version: str, channel: str) -> SemanticVersion:
    parsed = SemanticVersion.parse(version)
    if channel == "stable" and parsed.prerelease:
        raise ValueError("Stable releases cannot use a pre-release version.")
    if channel == "beta" and (
        not parsed.prerelease or not parsed.prerelease[0].lower().startswith(("beta", "rc"))
    ):
        raise ValueError("Beta releases must use a beta or rc pre-release identifier.")
    return parsed


def verify_version_consistency(root: Path, requested: str) -> None:
    if requested != __version__:
        raise RuntimeError(
            f"Requested version {requested} does not match canonical app.version {__version__}."
        )
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    if (
        'dynamic = ["version"]' not in pyproject
        or 'version = { attr = "app.version.__version__" }' not in pyproject
    ):
        raise RuntimeError(
            "pyproject.toml is not using app.version as the canonical version source."
        )
    installer = (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    if re.search(r'#define\s+MyAppVersion\s+"', installer):
        raise RuntimeError("Installer contains a duplicate hard-coded application version.")
    if (root / "packaging" / "version_info.txt").exists():
        raise RuntimeError("Remove packaging/version_info.txt; version metadata must be generated.")


def write_version_info(path: Path, version: str) -> None:
    parsed = SemanticVersion.parse(version)
    numeric = (parsed.major, parsed.minor, parsed.patch, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numeric!r}, prodvers={numeric!r}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Quazmoz'), StringStruct('FileDescription', 'InferBridge'),
    StringStruct('FileVersion', '{version}'), StringStruct('InternalName', 'InferBridge'),
    StringStruct('OriginalFilename', 'InferBridge.exe'), StringStruct('ProductName', 'InferBridge'),
    StringStruct('ProductVersion', '{version}')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""",
        encoding="utf-8",
    )


def write_build_info(
    path: Path, *, version: str, channel: str, commit: str, clean: bool, inventory: Path | None
) -> None:
    info = BuildInfo(
        application_version=version,
        source_commit=commit,
        build_channel=channel,
        build_date=datetime.now(UTC),
        source_tree_clean=clean,
        dependency_inventory_filename=inventory.name if inventory else None,
        dependency_inventory_sha256=sha256_file(inventory)
        if inventory and inventory.is_file()
        else None,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(info.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _notes(path: Path) -> tuple[str, list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    summary = next(
        (
            line.strip()
            for line in lines
            if line.strip() and not line.startswith("#") and not line.startswith("-")
        ),
        "See release notes.",
    )[:500]
    highlights = [line.lstrip("- ")[:200] for line in lines if line.strip().startswith("-")][:8]
    return summary, highlights


def build_manifest(
    output_dir: Path,
    *,
    version: str,
    channel: str,
    published_at: datetime,
    commit: str,
    clean: bool,
    signed_types: set[str],
    inventory_filename: str,
) -> ReleaseManifest:
    notes = output_dir / artifact_filename(version, "release_notes")
    if not notes.is_file():
        raise RuntimeError(f"Release notes are missing: {notes.name}")
    summary, highlights = _notes(notes)
    repository = release_repository()
    base = f"https://github.com/{repository}/releases/download/v{version}"
    artifacts = []
    for kind in ("installer", "portable", "third_party_licenses", "release_notes"):
        path = output_dir / artifact_filename(version, kind)
        if not path.is_file():
            if kind in {"installer", "portable"}:
                continue
            raise RuntimeError(f"Required release artifact is missing: {path.name}")
        installer_signed = kind == "installer" and kind in signed_types
        launcher_signed = kind == "portable" and kind in signed_types
        artifacts.append(
            ReleaseArtifact(
                type=kind,
                filename=path.name,
                url=f"{base}/{path.name}",
                sha256=sha256_file(path),
                size_bytes=path.stat().st_size,
                signed=installer_signed,
                signature_verified=installer_signed,
                contained_launcher_signed=launcher_signed,
                contained_launcher_signature_verified=launcher_signed,
            )
        )
    return ReleaseManifest(
        version=version,
        channel=channel,
        published_at=published_at,
        minimum_windows_version="Windows 10 2004 (10.0.19041)",
        minimum_windows_build=19041,
        source_commit=commit,
        source_tree_clean=clean,
        artifacts=artifacts,
        api_compatibility=ApiCompatibility(),
        openvino=OpenVinoCompatibility(
            minimum_version="2025.1.0",
            bundled_version=_package_version("openvino"),
            genai_version=_package_version("openvino-genai"),
        ),
        data_compatibility=DataCompatibility(
            minimum_supported_schema=MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION,
            current_schema=DATA_SCHEMA_VERSION,
            downgrade_compatible_from_schema=MINIMUM_SUPPORTED_DATA_SCHEMA_VERSION,
            model_cache_compatible=True,
            compiled_cache_policy="Invalidate compiled cache when OpenVINO, device, driver, or compilation properties change.",
        ),
        release_notes_url=f"https://github.com/{repository}/releases/tag/v{version}",
        known_issues_url=f"https://github.com/{repository}/blob/v{version}/docs/KNOWN_ISSUES.md",
        compatibility_matrix_url=f"https://github.com/{repository}/blob/v{version}/docs/COMPATIBILITY_MATRIX.md",
        summary=summary,
        highlights=highlights,
        dependency_inventory_filename=inventory_filename,
    )
