from __future__ import annotations

import importlib.metadata
import json
import zipfile
from pathlib import Path

from scripts.create_portable_archive import create_archive, verify_archive
from scripts.release_environment import (
    environment_key,
    validation_errors,
    write_metadata,
)


ROOT = Path(__file__).resolve().parents[1]


def test_release_environment_fingerprint_changes_with_requirements(tmp_path: Path) -> None:
    requirements = tmp_path / "release.txt"
    requirements.write_text("pip==1.0\n", encoding="utf-8")
    first = environment_key(requirements)

    requirements.write_text("pip==2.0\n", encoding="utf-8")
    second = environment_key(requirements)

    assert first != second
    assert first.startswith("v1-")
    assert "/" not in first
    assert "\\" not in first


def test_release_environment_validation_checks_metadata_and_exact_pins(tmp_path: Path) -> None:
    requirements = tmp_path / "release.txt"
    requirements.write_text(
        f"pip=={importlib.metadata.version('pip')}\n"
        'unused-package==1.6.1; platform_system == "DefinitelyNotARealOS"\n',
        encoding="utf-8",
    )
    metadata = tmp_path / "environment.json"
    write_metadata(metadata, requirements)

    assert validation_errors(metadata, requirements) == []

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["requirements_sha256"] = "0" * 64
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    assert "release environment metadata mismatch: requirements_sha256" in validation_errors(
        metadata, requirements
    )


def test_portable_archive_preserves_versioned_root_without_staging_copy(tmp_path: Path) -> None:
    source = tmp_path / "dist" / "InferBridge"
    internal = source / "_internal" / "app"
    internal.mkdir(parents=True)
    (source / "InferBridge.exe").write_bytes(b"launcher")
    (source / "portable.flag").write_text("portable", encoding="ascii")
    (internal / "server.py").write_text("print('ok')\n", encoding="utf-8")
    archive = tmp_path / "InferBridge-1.2.3-windows-x64-portable.zip"

    create_archive(source, archive, "InferBridge-1.2.3")
    verify_archive(archive, "InferBridge-1.2.3", source)

    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
    assert "InferBridge-1.2.3/InferBridge.exe" in names
    assert "InferBridge-1.2.3/portable.flag" in names
    assert "InferBridge-1.2.3/_internal/app/server.py" in names
    assert source.is_dir()
    assert not (tmp_path / "portable-stage").exists()


def test_release_build_uses_fingerprinted_environment_and_direct_portable_archive() -> None:
    script = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $Root "build\\release-environments"' in script
    assert "release_environment.py fingerprint" in script
    assert "release_environment.py validate" in script
    assert "release_environment.py write-metadata" in script
    assert "$script:ReleaseEnvironmentReused = $true" in script
    assert 'Join-Path $BuildRoot "venv"' not in script

    assert "create_portable_archive.py create" in script
    assert "create_portable_archive.py verify" in script
    assert "Create portable ZIP without staging copy" in script
    assert "Expand-Archive" not in script
    assert "PortableStage" not in script
    assert 'Copy-Item (Join-Path $BuiltRoot "*")' not in script
    assert script.index("Compile Inno Setup installer") < script.index(
        "Create portable ZIP without staging copy"
    )


def test_release_build_emits_non_secret_timing_telemetry() -> None:
    script = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    publisher = (ROOT / "scripts" / "publish_release.ps1").read_text(encoding="utf-8")

    assert "Write-ReleaseTimingSnapshot" in script
    assert "duration_ms" in script
    assert "environment = [ordered]@{" in script
    assert '"InferBridge-$Version-release-timings.json"' in script
    assert "release_environment_reused" in script
    assert '"InferBridge-$Version-release-timings.json"' in publisher
