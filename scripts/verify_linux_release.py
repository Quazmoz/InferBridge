"""Verify a directory of Linux release assets before it is uploaded anywhere.

Checks the exact file set, every SHA256SUMS line, that the archive was built from the
expected commit and version, that the SBOM describes the same build, and that the
.deb/.rpm carry the mapped package version. Exits non-zero on the first mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_linux_packages import artifact_names, package_versions  # noqa: E402


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory: Path, version: str, commit: str | None) -> list[str]:
    names = artifact_names(version)
    expected = {names[key] for key in ("portable", "deb", "rpm", "checksums", "sbom")}
    present = {path.name for path in directory.iterdir() if path.is_file()}
    if present != expected:
        raise SystemExit(
            f"Unexpected Linux asset set. Missing: {sorted(expected - present)}; "
            f"extra: {sorted(present - expected)}"
        )
    listed = {}
    for line in (directory / names["checksums"]).read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    if set(listed) != expected - {names["checksums"]}:
        raise SystemExit(f"SHA256SUMS lists {sorted(listed)}")
    for name, digest in listed.items():
        if sha256(directory / name) != digest:
            raise SystemExit(f"Checksum mismatch: {name}")
    with tarfile.open(directory / names["portable"]) as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith("linux-build.json"))
        build = json.loads(archive.extractfile(member).read())
    if build["version"] != version or (commit and build["commit"] != commit):
        raise SystemExit(f"Archive is {build['version']} at {build['commit']}")
    sbom = json.loads((directory / names["sbom"]).read_text(encoding="utf-8"))
    root = next(p for p in sbom["packages"] if p["SPDXID"] == "SPDXRef-InferBridge")
    if root["versionInfo"] != version or not root["downloadLocation"].endswith(build["commit"]):
        raise SystemExit("The SBOM does not describe this build.")
    native = package_versions(version)
    if shutil.which("dpkg-deb"):
        found = subprocess.run(
            ["dpkg-deb", "-f", str(directory / names["deb"]), "Version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if found != native["deb"]:
            raise SystemExit(f".deb version {found} != {native['deb']}")
    if shutil.which("rpm"):
        found = subprocess.run(
            ["rpm", "-qp", "--qf", "%{VERSION}", str(directory / names["rpm"])],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if found != native["rpm"]:
            raise SystemExit(f".rpm version {found} != {native['rpm']}")
    return sorted(expected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit")
    args = parser.parse_args()
    for name in verify(args.directory, args.version, args.commit):
        print(f"verified {name}")
