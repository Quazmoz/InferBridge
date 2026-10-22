"""Pure filename policy shared by the Azure selector and release verifier.

Keep this dependency-light: the selector's fast preflight runs without the
full OpenVINO/Pydantic release environment.
"""

from __future__ import annotations

from app.brand import ARTIFACT_PREFIX

_RELEASE_SUFFIXES = (
    "-windows-x64-installer.exe",
    "-windows-x64-portable.zip",
    "-third-party-licenses.zip",
    "-release-notes.md",
    "-checksums.txt",
    "-release-manifest.json",
    "-dependency-inventory.json",
    "-dependency-freeze.txt",
    "-release-summary.json",
    "-release-timings.json",
)


def expected_release_filenames(version: str) -> set[str]:
    prefix = f"{ARTIFACT_PREFIX}-{version}"
    return {f"{prefix}{suffix}" for suffix in _RELEASE_SUFFIXES} | {"model-library-manifest.json"}
