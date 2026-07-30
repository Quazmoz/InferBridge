"""Deterministic distribution metadata helpers used by build scripts and tests."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.brand import ARTIFACT_PREFIX

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def validate_version(value: str) -> str:
    version = str(value or "").strip()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("Version must use semantic version syntax.")
    return version


def artifact_names(version: str, *, signed: bool) -> dict[str, str]:
    version = validate_version(version)
    suffix = "signed" if signed else "unsigned"
    stem = f"{ARTIFACT_PREFIX}-{version}-windows-x64"
    return {
        "portable": f"{stem}-portable-{suffix}.zip",
        "installer": f"{stem}-setup-{suffix}.exe",
        "checksums": f"{ARTIFACT_PREFIX}-{version}-SHA256SUMS.txt",
    }


@dataclass(frozen=True)
class SigningConfiguration:
    enabled: bool
    signtool_path: str | None
    certificate_sha1: str | None
    timestamp_url: str | None


def signing_configuration(environment: dict[str, str]) -> SigningConfiguration:
    tool = str(environment.get("OV_LLM_SIGNTOOL_PATH") or "").strip() or None
    certificate = str(environment.get("OV_LLM_SIGN_CERT_SHA1") or "").strip() or None
    timestamp = str(environment.get("OV_LLM_SIGN_TIMESTAMP_URL") or "").strip() or None
    return SigningConfiguration(
        enabled=bool(tool and certificate),
        signtool_path=tool,
        certificate_sha1=certificate,
        timestamp_url=timestamp,
    )
