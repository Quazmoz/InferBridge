"""Linux .deb/.rpm layout, versions, SBOM and checksums from a small fake bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from app.version import __version__
from scripts import build_linux_packages as packages


def _archive(tmp_path: Path, version: str = "1.1.1-dev.1") -> Path:
    bundle = tmp_path / "src" / f"InferBridge-{version}-linux-x86_64"
    resources = bundle / "resources"
    resources.mkdir(parents=True)
    (bundle / "InferBridge").write_text("#!/bin/sh\nexit 0\n")
    (bundle / "InferBridge").chmod(0o755)
    (bundle / "README.md").write_text("readme\n")
    (resources / "LICENSE").write_text("MIT License\n")
    (resources / "InferBridge.png").write_bytes(b"\x89PNG")
    (resources / "build-info.json").write_text(
        json.dumps({"application_version": version, "build_date": "2026-11-04T19:32:00+00:00"})
    )
    (resources / "linux-build.json").write_text(
        json.dumps({"version": version, "commit": "a" * 40, "python": "3.12.12"})
    )
    (resources / "dependencies.txt").write_text("fastapi==0.143.0\nopenvino==2026.2.1\n")
    (resources / "THIRD-PARTY-NOTICES.txt").write_text(
        "header\n\nfastapi==0.143.0: MIT\nopenvino==2026.2.1: Apache License 2.0\n"
    )
    archive = tmp_path / f"InferBridge-{version}-linux-x86_64-portable.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(bundle, arcname=bundle.name)
    return archive


@pytest.mark.parametrize(
    "version,expected",
    [("1.1.1", "1.1.1"), ("1.1.1-dev.1", "1.1.1~dev.1"), ("2.0.0-beta-2", "2.0.0~beta.2")],
)
def test_package_versions_sort_prereleases_first(version, expected):
    assert packages.package_versions(version) == {"deb": expected, "rpm": expected}


def test_current_version_maps_and_names_share_one_stem():
    packages.package_versions(__version__)
    names = packages.artifact_names(__version__)
    assert names["deb"].startswith(f"InferBridge-{__version__}-linux-x86_64")
    assert names["portable"].endswith("-portable.tar.gz")


posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks and modes")


@posix_only
def test_package_tree_runs_as_user_and_ships_a_user_unit(tmp_path):
    root, info = packages.stage(_archive(tmp_path), tmp_path / "work")
    assert info["application_version"] == "1.1.1-dev.1"
    assert (root / "usr/bin/inferbridge").readlink() == Path("/opt/inferbridge/InferBridge")
    unit = (root / "usr/lib/systemd/user/inferbridge.service").read_text()
    assert "ExecStart=/opt/inferbridge/InferBridge --service" in unit
    assert "User=" not in unit and "WantedBy=default.target" in unit
    entry = (root / "usr/share/applications/inferbridge.desktop").read_text()
    assert "Exec=inferbridge\n" in entry and "Exec=inferbridge stop" in entry
    assert not any(path.name in {".env", "data"} for path in root.rglob("*"))
    if shutil.which("desktop-file-validate"):
        subprocess.run(
            ["desktop-file-validate", str(root / "usr/share/applications/inferbridge.desktop")],
            check=True,
        )


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb")
def test_deb_metadata_and_maintainer_scripts(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793820720")
    archive = _archive(tmp_path)
    (deb,) = packages.build(archive, tmp_path / "out", ["deb"])
    control = subprocess.run(
        ["dpkg-deb", "-f", str(deb)], check=True, capture_output=True, text=True
    ).stdout
    assert "Version: 1.1.1~dev.1" in control and "libtbb12" in control
    listing = subprocess.run(
        ["dpkg-deb", "-c", str(deb)], check=True, capture_output=True, text=True
    ).stdout
    assert "./opt/inferbridge/InferBridge" in listing
    assert "root/root" in listing
    script = tmp_path / "postinst"
    subprocess.run(
        ["dpkg-deb", "--ctrl-tarfile", str(deb)], check=True, stdout=(tmp_path / "c.tar").open("wb")
    )
    with tarfile.open(tmp_path / "c.tar") as control_tar:
        script.write_bytes(control_tar.extractfile("./postinst").read())
    text = script.read_text()
    assert "systemctl" not in text and "useradd" not in text  # Nothing runs as root at runtime.
    # Reproducible: the same input yields the same package bytes.
    (again,) = packages.build(archive, tmp_path / "again", ["deb"])
    assert hashlib.sha256(deb.read_bytes()).digest() == hashlib.sha256(again.read_bytes()).digest()

    sums = packages.finalize(archive, tmp_path / "out")
    assert sums.name == "InferBridge-1.1.1-dev.1-linux-SHA256SUMS.txt"


@posix_only
def test_sbom_lists_runtime_and_wheels_with_purls(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793820720")
    archive = _archive(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    shutil.copy2(archive, output / archive.name)
    sums = packages.finalize(archive, output)
    document = json.loads((output / "InferBridge-1.1.1-dev.1-linux-sbom.spdx.json").read_text())
    names = {package["name"]: package for package in document["packages"]}
    assert document["spdxVersion"] == "SPDX-2.3"
    assert names["fastapi"]["licenseDeclared"] == "MIT"
    assert names["openvino"]["licenseDeclared"] == "NOASSERTION"  # Not an SPDX expression.
    assert names["openvino"]["externalRefs"][0]["referenceLocator"] == "pkg:pypi/openvino@2026.2.1"
    assert document["creationInfo"]["created"] == "2026-11-04T19:32:00Z"
    lines = sums.read_text().splitlines()
    assert [line.split("  ")[1] for line in lines] == [
        archive.name,
        "InferBridge-1.1.1-dev.1-linux-sbom.spdx.json",
    ]
    digest = hashlib.sha256((output / archive.name).read_bytes()).hexdigest()
    assert lines[0].startswith(digest)


@posix_only
@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb")
def test_release_verifier_accepts_the_built_set_and_rejects_tampering(tmp_path, monkeypatch):
    from scripts import verify_linux_release as verifier

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793820720")
    archive = _archive(tmp_path)
    output = tmp_path / "out"
    packages.build(archive, output, ["deb"])
    shutil.copy2(archive, output / archive.name)
    (output / "InferBridge-1.1.1-dev.1-linux-x86_64.rpm").write_bytes(b"rpm placeholder")
    packages.finalize(archive, output)
    real_which = shutil.which
    monkeypatch.setattr(
        verifier.shutil, "which", lambda name: None if name == "rpm" else real_which(name)
    )
    assert len(verifier.verify(output, "1.1.1-dev.1", "a" * 40)) == 5
    with pytest.raises(SystemExit):
        verifier.verify(output, "1.1.1-dev.1", "b" * 40)
    (output / archive.name).write_bytes(b"tampered")
    with pytest.raises(SystemExit):
        verifier.verify(output, "1.1.1-dev.1", "a" * 40)
