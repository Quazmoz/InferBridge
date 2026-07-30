"""Typed release metadata, semantic versions, and compatibility decisions."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import total_ordering
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.brand import (
    ARTIFACT_PREFIX,
    LEGACY_ARTIFACT_PREFIXES,
    LEGACY_REPOSITORY_NAME,
    REPOSITORY_NAME,
    REPOSITORY_OWNER,
)

_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_ALLOWED_RELEASE_HOST = "github.com"
_ALLOWED_REPOSITORY_NAMES = (REPOSITORY_NAME, LEGACY_REPOSITORY_NAME)
_ALLOWED_RELEASE_PREFIXES = tuple(
    f"/{REPOSITORY_OWNER}/{repository}/releases/download/"
    for repository in _ALLOWED_REPOSITORY_NAMES
)


@total_ordering
@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> SemanticVersion:
        match = _VERSION_RE.fullmatch(value.strip())
        if not match:
            raise ValueError(f"Invalid semantic version: {value}")
        pre = tuple(match.group(4).split(".")) if match.group(4) else ()
        build = tuple(match.group(5).split(".")) if match.group(5) else ()
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), pre, build)

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def _precedence(self):
        return self.major, self.minor, self.patch

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._precedence() == other._precedence() and self.prerelease == other.prerelease

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        if self._precedence() != other._precedence():
            return self._precedence() < other._precedence()
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return int(left) < int(right)
            if left_numeric != right_numeric:
                return left_numeric
            return left < right
        return len(self.prerelease) < len(other.prerelease)


ReleaseChannel = Literal["stable", "beta", "nightly"]
ArtifactType = Literal[
    "installer",
    "portable",
    "checksums",
    "manifest",
    "third_party_licenses",
    "release_notes",
]
InstallationMode = Literal["installed", "portable", "source"]


def channel_accepts(channel: ReleaseChannel, version: str) -> bool:
    parsed = SemanticVersion.parse(version)
    if channel == "stable":
        return not parsed.prerelease
    if channel == "beta":
        if not parsed.prerelease:
            return True
        label = parsed.prerelease[0].lower()
        return label.startswith("beta") or label.startswith("rc")
    return True


def artifact_filename(version: str, artifact_type: ArtifactType) -> str:
    SemanticVersion.parse(version)
    prefix = f"{ARTIFACT_PREFIX}-{version}"
    suffixes = {
        "installer": "-windows-x64-installer.exe",
        "portable": "-windows-x64-portable.zip",
        "checksums": "-checksums.txt",
        "manifest": "-release-manifest.json",
        "third_party_licenses": "-third-party-licenses.zip",
        "release_notes": "-release-notes.md",
    }
    return prefix + suffixes[artifact_type]


def artifact_filenames(version: str, artifact_type: ArtifactType) -> tuple[str, ...]:
    canonical = artifact_filename(version, artifact_type)
    suffix = canonical.removeprefix(f"{ARTIFACT_PREFIX}-{version}")
    aliases = tuple(f"{prefix}-{version}{suffix}" for prefix in LEGACY_ARTIFACT_PREFIXES)
    return (canonical, *aliases)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_github_url(value: str) -> tuple[bool, str]:
    parsed = urlparse(value)
    valid = (
        parsed.scheme == "https"
        and parsed.hostname == _ALLOWED_RELEASE_HOST
        and not parsed.username
        and not parsed.password
        and parsed.port is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    return valid, parsed.path


def is_official_release_url(value: str) -> bool:
    valid, path = _safe_github_url(value)
    return valid and any(path.startswith(prefix) for prefix in _ALLOWED_RELEASE_PREFIXES)


def is_official_repository_url(value: str) -> bool:
    valid, path = _safe_github_url(value)
    if not valid:
        return False
    return any(
        path == f"/{REPOSITORY_OWNER}/{repository}"
        or path.startswith(f"/{REPOSITORY_OWNER}/{repository}/")
        for repository in _ALLOWED_REPOSITORY_NAMES
    )


class ReleaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ArtifactType
    filename: str
    url: HttpUrl
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    signed: bool = False
    signature_verified: bool = False
    contained_launcher_signed: bool = False
    contained_launcher_signature_verified: bool = False

    @model_validator(mode="after")
    def validate_trust_state(self):
        if self.signed and not self.signature_verified:
            raise ValueError("signed artifacts must have a verified signature")
        if self.contained_launcher_signed and not self.contained_launcher_signature_verified:
            raise ValueError("a signed contained launcher must have a verified signature")
        if not is_official_release_url(str(self.url)):
            raise ValueError("artifact URL is outside the official GitHub release location")
        return self


class ApiCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_completions: bool = True
    responses: bool = True
    embeddings: bool = True


class OpenVinoCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_version: str
    bundled_version: str
    genai_version: str


class DataCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_supported_schema: int = Field(ge=1)
    current_schema: int = Field(ge=1)
    downgrade_compatible_from_schema: int = Field(ge=1)
    model_cache_compatible: bool
    compiled_cache_policy: str

    @model_validator(mode="after")
    def validate_schema_range(self):
        if self.minimum_supported_schema > self.current_schema:
            raise ValueError("minimum data schema cannot exceed current schema")
        if self.downgrade_compatible_from_schema > self.current_schema:
            raise ValueError("downgrade-compatible schema cannot exceed current schema")
        return self


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    version: str
    channel: ReleaseChannel
    published_at: datetime
    minimum_windows_version: str
    minimum_windows_build: int = Field(default=19041, ge=1)
    architecture: Literal["x64"] = "x64"
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_clean: bool
    artifacts: list[ReleaseArtifact]
    api_compatibility: ApiCompatibility
    openvino: OpenVinoCompatibility
    data_compatibility: DataCompatibility
    release_notes_url: HttpUrl
    known_issues_url: HttpUrl
    compatibility_matrix_url: HttpUrl
    summary: str = Field(max_length=500)
    highlights: list[str] = Field(default_factory=list, max_length=12)
    dependency_inventory_filename: str

    @model_validator(mode="after")
    def validate_release(self):
        parsed = SemanticVersion.parse(self.version)
        if self.channel == "stable" and parsed.prerelease:
            raise ValueError("a stable manifest cannot contain a pre-release version")
        if self.channel == "beta":
            if not parsed.prerelease or not parsed.prerelease[0].lower().startswith(("beta", "rc")):
                raise ValueError("a beta manifest must use a beta or rc pre-release version")
        if self.channel == "nightly" and not parsed.prerelease:
            raise ValueError("a nightly manifest must use a pre-release version")
        seen: set[ArtifactType] = set()
        for artifact in self.artifacts:
            if artifact.type in seen:
                raise ValueError(f"duplicate artifact type: {artifact.type}")
            seen.add(artifact.type)
            if artifact.filename not in artifact_filenames(self.version, artifact.type):
                raise ValueError(f"unexpected filename for {artifact.type}")
        if not {"installer", "portable"}.intersection(seen):
            raise ValueError("manifest must contain an installer or portable artifact")
        for value in (
            self.release_notes_url,
            self.known_issues_url,
            self.compatibility_matrix_url,
        ):
            if not is_official_repository_url(str(value)):
                raise ValueError(
                    "release documentation URLs must use an approved InferBridge repository"
                )
        return self

    def select_artifact(self, installation_mode: InstallationMode) -> ReleaseArtifact | None:
        desired = "portable" if installation_mode == "portable" else "installer"
        return next((item for item in self.artifacts if item.type == desired), None)


def platform_compatibility(
    manifest: ReleaseManifest,
    *,
    os_name: str | None = None,
    machine: str | None = None,
    windows_build: int | None = None,
) -> tuple[bool, str | None]:
    resolved_os = os.name if os_name is None else os_name
    if resolved_os != "nt":
        return True, None
    resolved_machine = (platform.machine() if machine is None else machine).strip().lower()
    if resolved_machine not in {"amd64", "x86_64"}:
        return False, "This release supports Windows x64 only."
    if windows_build is None:
        try:
            windows_build = int(sys.getwindowsversion().build)
        except (AttributeError, OSError, ValueError):
            return False, "The Windows build could not be verified for this release."
    if windows_build < manifest.minimum_windows_build:
        return False, (
            f"This release requires {manifest.minimum_windows_version} or newer "
            f"(build {manifest.minimum_windows_build}+)."
        )
    return True, None


def schema_compatibility(local_schema: int, target: DataCompatibility) -> tuple[bool, str | None]:
    if local_schema < target.minimum_supported_schema:
        return False, "The installed data schema is too old for this release."
    if local_schema > target.current_schema:
        return False, "This release cannot safely read data created by a newer application."
    return True, None


def rollback_warning(local_schema: int, target: DataCompatibility) -> str | None:
    if local_schema > target.current_schema:
        return "The older release may not understand the current data schema. Restore a compatible configuration backup before rollback."
    if local_schema < target.downgrade_compatible_from_schema:
        return "Rollback compatibility is not verified for this data schema."
    return None
