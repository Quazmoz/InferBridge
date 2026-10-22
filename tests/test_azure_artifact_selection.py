"""Regression coverage for Azure's explicit Windows output selector."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from scripts.select_azure_artifacts import MODES, select, verify_msix

FOUNDATION = "{http://schemas.microsoft.com/appx/manifest/foundation/windows10}"
VERSION = "1.2.3"
IDENTITY = "QuinnFavo.InferBridge"
PUBLISHER = "CN=CAC9A06A-996E-4DC7-9079-45803515E811"


def _make_msix(
    path: Path, *, identity=IDENTITY, publisher=PUBLISHER, version="1.2.3.0", signed=False
):
    root = ET.Element("Package")
    ET.SubElement(
        root, f"{FOUNDATION}Identity", Name=identity, Publisher=publisher, Version=version
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AppxManifest.xml", ET.tostring(root))
        archive.writestr("native/InferBridge.Native.exe", b"winui")
        archive.writestr("native/InferBridge.Native.runtimeconfig.json", b"{}")
        archive.writestr("native/InferBridge.Native.deps.json", b"{}")
        archive.writestr("native/coreclr.dll", b"runtime")
        archive.writestr("InferBridge.exe", b"engine")
        if signed:
            archive.writestr("AppxSignature.p7x", b"signature")


def _source(tmp_path):
    release = tmp_path / "internal"
    release.mkdir()
    (release / f"InferBridge-{VERSION}-windows-x64-installer.exe").write_bytes(b"installer")
    (release / f"InferBridge-{VERSION}-windows-x64-portable.zip").write_bytes(b"portable")
    (release / f"InferBridge-{VERSION}-checksums.txt").write_text("validated source checksums\n")
    (release / f"InferBridge-{VERSION}-release-manifest.json").write_text("{}")
    msix_dir = tmp_path / "msix"
    msix_dir.mkdir()
    _make_msix(msix_dir / f"InferBridge-{VERSION}-windows-x64.msix")
    return release, msix_dir


def _select(tmp_path, mode, *, release=None, msix_dir=None):
    if release is None:
        release, msix_dir = _source(tmp_path)
    return select(
        release_dir=release,
        publish_dir=tmp_path / "publish",
        msix_dir=msix_dir,
        version=VERSION,
        mode=mode,
        identity=IDENTITY,
        publisher=PUBLISHER,
        source_ref="refs/heads/dev",
        source_commit="ABC123",
        channel="nightly",
        pipeline_hash="DEADBEEF",
        run_id="42",
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("installer-portable", {"installer.exe", "portable.zip"}),
        ("installer", {"installer.exe"}),
        ("portable", {"portable.zip"}),
        ("msix", {".msix"}),
        ("all", {"installer.exe", "portable.zip", ".msix"}),
    ],
)
def test_selects_exact_requested_binary_types_and_checksums(tmp_path, mode, expected):
    chosen = _select(tmp_path, mode)
    binary_names = {
        p.name for p in (tmp_path / "publish").iterdir() if p.suffix in {".exe", ".zip", ".msix"}
    }
    assert len(binary_names) == len(expected)
    assert all(any(name.endswith(suffix) for name in binary_names) for suffix in expected)
    assert set(chosen) == binary_names
    lines = (tmp_path / "publish/azure-selected-checksums.txt").read_text().splitlines()
    assert len(lines) == len(binary_names)
    for line in lines:
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((tmp_path / "publish" / name).read_bytes()).hexdigest()
    info = json.loads((tmp_path / "publish/azure-build-provenance.json").read_text())
    assert info["artifact_type"] == mode
    assert info["source_commit"] == "abc123"
    assert info["unsigned"] is True
    assert info["selected_binaries"] == chosen
    assert "wack" in info["certification"].lower()
    if mode in ("installer-portable", "all"):
        assert (tmp_path / "publish" / f"InferBridge-{VERSION}-release-manifest.json").exists()
        assert (tmp_path / "publish" / f"InferBridge-{VERSION}-checksums.txt").exists()
    else:
        assert not (tmp_path / "publish" / f"InferBridge-{VERSION}-release-manifest.json").exists()


def test_missing_msix_does_not_publish_partial_installer(tmp_path):
    release, msix_dir = _source(tmp_path)
    (msix_dir / f"InferBridge-{VERSION}-windows-x64.msix").unlink()
    with pytest.raises(FileNotFoundError, match="missing or unsafe"):
        _select(tmp_path, "all", release=release, msix_dir=msix_dir)
    assert not (tmp_path / "publish").exists()
    assert (release / f"InferBridge-{VERSION}-windows-x64-installer.exe").is_file()


@pytest.mark.parametrize(
    "bad", ["identity", "publisher", "version", "signed", "native", "engine", "runtime"]
)
def test_msix_identity_unsigned_and_native_payload_are_mandatory(tmp_path, bad):
    path = tmp_path / "candidate.msix"
    _make_msix(
        path,
        identity="Wrong.Identity" if bad == "identity" else IDENTITY,
        version="1.2.4.0" if bad == "version" else "1.2.3.0",
        signed=bad == "signed",
        publisher="CN=other" if bad == "publisher" else PUBLISHER,
    )
    if bad in ("native", "engine", "runtime"):
        missing = {
            "native": "native/InferBridge.Native.exe",
            "engine": "InferBridge.exe",
            "runtime": "native/coreclr.dll",
        }[bad]
        with zipfile.ZipFile(path) as source:
            entries = {name: source.read(name) for name in source.namelist() if name != missing}
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
    with pytest.raises(ValueError):
        verify_msix(path, identity=IDENTITY, publisher=PUBLISHER, version=VERSION)


def test_invalid_mode_and_nested_output_are_rejected(tmp_path):
    release, msix_dir = _source(tmp_path)
    assert len(MODES) == 5
    with pytest.raises(ValueError, match="Unsupported"):
        _select(tmp_path, "unknown", release=release, msix_dir=msix_dir)
    with pytest.raises(ValueError, match="outside"):
        select(
            release_dir=release,
            publish_dir=release / "inside",
            version=VERSION,
            mode="installer",
            msix_dir=msix_dir,
            identity=IDENTITY,
            publisher=PUBLISHER,
            source_ref="refs/heads/dev",
            source_commit="a",
            channel="nightly",
            pipeline_hash="ab",
            run_id="1",
        )


@pytest.mark.parametrize("mode", ("installer-portable", "all"))
def test_full_selection_rejects_unreviewed_files_before_publication(tmp_path, mode):
    release, msix_dir = _source(tmp_path)
    unreviewed = release / "unreviewed-debug.log"
    unreviewed.write_text("this is not an approved release artifact", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected file"):
        _select(tmp_path, mode, release=release, msix_dir=msix_dir)

    assert not (tmp_path / "publish").exists()
    assert unreviewed.is_file()
    assert (release / f"InferBridge-{VERSION}-windows-x64-installer.exe").is_file()



def test_azure_selector_cli_imports_lightweight_allowlist(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/select_azure_artifacts.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
