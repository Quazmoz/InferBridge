from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.release_models import (
    ApiCompatibility,
    DataCompatibility,
    OpenVinoCompatibility,
    ReleaseArtifact,
    ReleaseManifest,
    SemanticVersion,
    artifact_filename,
    channel_accepts,
    is_official_release_url,
    platform_compatibility,
    rollback_warning,
    schema_compatibility,
    sha256_file,
)


def artifact(version: str, kind: str, *, signed: bool = False, verified: bool = False):
    filename = artifact_filename(version, kind)
    return ReleaseArtifact(
        type=kind,
        filename=filename,
        url=f"https://github.com/Quazmoz/openvino-windows-llm/releases/download/v{version}/{filename}",
        sha256="a" * 64,
        size_bytes=12,
        signed=signed,
        signature_verified=verified,
    )


def manifest(version="0.5.0", channel="stable"):
    return ReleaseManifest(
        version=version,
        channel=channel,
        published_at=datetime(2026, 7, 22, tzinfo=UTC),
        minimum_windows_version="Windows 10 2004 (10.0.19041)",
        minimum_windows_build=19041,
        source_commit="a" * 40,
        source_tree_clean=True,
        artifacts=[artifact(version, "installer"), artifact(version, "portable")],
        api_compatibility=ApiCompatibility(),
        openvino=OpenVinoCompatibility(
            minimum_version="2025.1.0", bundled_version="2025.1.0", genai_version="2025.1.0"
        ),
        data_compatibility=DataCompatibility(
            minimum_supported_schema=1,
            current_schema=1,
            downgrade_compatible_from_schema=1,
            model_cache_compatible=True,
            compiled_cache_policy="invalidate on runtime change",
        ),
        release_notes_url=f"https://github.com/Quazmoz/openvino-windows-llm/releases/tag/v{version}",
        known_issues_url=f"https://github.com/Quazmoz/openvino-windows-llm/blob/v{version}/docs/KNOWN_ISSUES.md",
        compatibility_matrix_url=f"https://github.com/Quazmoz/openvino-windows-llm/blob/v{version}/docs/COMPATIBILITY_MATRIX.md",
        summary="Release summary",
        dependency_inventory_filename="inventory.json",
    )


def test_semantic_version_pre_release_ordering():
    assert SemanticVersion.parse("0.3.0-beta.1") < SemanticVersion.parse("0.3.0-beta.2")
    assert SemanticVersion.parse("0.3.0-beta.2") < SemanticVersion.parse("0.3.0-rc.1")
    assert SemanticVersion.parse("0.3.0-rc.1") < SemanticVersion.parse("0.3.0")
    assert SemanticVersion.parse("1.0.0+build.7") == SemanticVersion.parse("1.0.0+other")


def test_invalid_semantic_versions_are_rejected():
    for value in ("1", "1.2", "01.2.3", "1.2.3-", "v1.2.3"):
        with pytest.raises(ValueError):
            SemanticVersion.parse(value)


def test_release_channel_filtering():
    assert channel_accepts("stable", "0.3.0")
    assert not channel_accepts("stable", "0.3.0-beta.1")
    assert channel_accepts("beta", "0.3.0-beta.1")
    assert channel_accepts("beta", "0.3.0-rc.1")
    assert channel_accepts("beta", "0.3.0")


def test_artifact_names_are_deterministic():
    assert artifact_filename("0.3.0", "installer") == "InferBridge-0.3.0-windows-x64-installer.exe"
    assert artifact_filename("0.3.0", "portable") == "InferBridge-0.3.0-windows-x64-portable.zip"
    assert artifact_filename("0.3.0", "checksums") == "InferBridge-0.3.0-checksums.txt"


def test_signed_state_requires_verified_signature():
    with pytest.raises(ValidationError):
        artifact("0.5.0", "installer", signed=True, verified=False)
    trusted = artifact("0.5.0", "installer", signed=True, verified=True)
    assert trusted.signed and trusted.signature_verified


def test_official_release_urls_are_strict():
    assert is_official_release_url(
        "https://github.com/Quazmoz/openvino-windows-llm/releases/download/v0.5.0/file.zip"
    )
    assert not is_official_release_url(
        "http://github.com/Quazmoz/openvino-windows-llm/releases/download/v0.5.0/file.zip"
    )
    assert not is_official_release_url("https://evil.example/releases/download/v0.5.0/file.zip")
    assert not is_official_release_url(
        "https://github.com/Other/repo/releases/download/v0.5.0/file.zip"
    )


def test_manifest_selects_installation_mode_artifact():
    value = manifest()
    assert value.select_artifact("installed").type == "installer"
    assert value.select_artifact("portable").type == "portable"


def test_manifest_rejects_wrong_artifact_filename_and_stable_prerelease():
    wrong = artifact("0.5.0", "installer").model_copy(update={"filename": "wrong.exe"})
    with pytest.raises(ValidationError):
        manifest().model_copy(update={"artifacts": [wrong]}).__class__.model_validate(
            manifest().model_dump() | {"artifacts": [wrong.model_dump()]}
        )
    with pytest.raises(ValidationError):
        manifest("0.5.0-beta.1", "stable")


def test_data_schema_and_rollback_warnings():
    target = manifest().data_compatibility
    assert schema_compatibility(1, target) == (True, None)
    newer_target = target.model_copy(
        update={
            "minimum_supported_schema": 2,
            "current_schema": 2,
            "downgrade_compatible_from_schema": 2,
        }
    )
    assert not schema_compatibility(1, newer_target)[0]
    assert rollback_warning(2, target)


def test_sha256_generation(tmp_path: Path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"release")
    assert sha256_file(path) == "a4d451ec23463726f72c43d64c710968f6b602cd653b4de8adee1b556240a829"


def test_platform_compatibility_rejects_wrong_architecture_and_old_windows():
    value = manifest()
    assert platform_compatibility(value, os_name="posix") == (True, None)
    assert not platform_compatibility(value, os_name="nt", machine="arm64", windows_build=26000)[0]
    compatible, warning = platform_compatibility(
        value, os_name="nt", machine="AMD64", windows_build=19040
    )
    assert not compatible
    assert "19041" in warning
    assert platform_compatibility(value, os_name="nt", machine="AMD64", windows_build=19041) == (
        True,
        None,
    )
